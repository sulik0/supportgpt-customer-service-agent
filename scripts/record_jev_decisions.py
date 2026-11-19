"""把 100 条 Baseline 的 Jev 分类结果录下来，供 PR 门禁回放（QA 评判不录）。

流程与 PR 门禁完全相同（Mock LLM、临时库），只是 Jev 改为：已有录音直接复用，
缺的才真实调用。先 --dry-run 看要真实调用多少次；--confirm-live 才会调用 Jev。
改了 Jev 分类问题集或题目，或者改动让分类输入变了，都要重录。
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_ci_quality_gate import (  # noqa: E402
    BASELINE_PATH,
    DECISION_RECORDING_PATH,
    configure_offline_environment,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record Jev answers for the PR gate.")
    parser.add_argument("--output", type=Path, default=DECISION_RECORDING_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Acknowledge that missing entries are sent to Jev (paid).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore existing entries and record everything again.",
    )
    parser.add_argument("--max-calls", type=int, default=150)
    parser.add_argument(
        "--layers",
        default="core",
        help="要录的题集层，逗号分隔或 all。录音文件只保留所选层用到的条目，"
        "所以套件要用到哪些层，就一起录哪些层。",
    )
    return parser.parse_args()


def _jev_settings_from_dotenv() -> dict[str, str]:
    """录制要用本机 .env 的 Jev 配置；门禁环境会把它清空。"""
    from dotenv import dotenv_values

    values = dotenv_values(PROJECT_ROOT / ".env")
    keys = ("JEV_API_KEY", "JEV_BASE_URL", "JEV_MODEL")
    return {key: str(values[key]) for key in keys if values.get(key)}


async def run() -> int:
    args = parse_args()
    if not args.dry_run and not args.confirm_live:
        print("Refusing live calls without --confirm-live. Run --dry-run first.")
        return 2
    jev = _jev_settings_from_dotenv()
    with tempfile.TemporaryDirectory(prefix="supportgpt-jev-record-") as runtime:
        configure_offline_environment(Path(runtime), args.output)
        # 先按关闭导入 Settings，再把 Provider 换成录制模式。
        os.environ.update(
            {
                "DECISION_PROVIDER": "disabled",
                "JEV_TIMEOUT_SECONDS": "15",
                "JEV_MAX_RETRIES": "2",
                **({} if args.dry_run else jev),
            }
        )
        if not args.dry_run and not os.environ.get("JEV_API_KEY"):
            print("JEV_API_KEY is missing in .env.")
            return 2

        from src.config import settings
        from src.database import init_db
        from src.decision import decision_service
        from src.decision.provider import JevDecisionProvider
        from src.decision.questions import INTENT_QUESTION_SET_VERSION
        from src.decision.replay import (
            RECORDING_SCHEMA_VERSION,
            RecordingDecisionProvider,
            load_recording,
        )
        from src.evaluation.baseline_evaluation import run_baseline_evaluation_v1

        existing = load_recording(args.output)
        live = None if args.dry_run else _CallBudget(JevDecisionProvider(), args.max_calls)
        recorder = RecordingDecisionProvider(
            live, existing["entries"], refresh=args.refresh
        )
        decision_service.provider = recorder
        await init_db()
        if args.layers == "core":
            await run_baseline_evaluation_v1(
                BASELINE_PATH,
                Path(runtime) / "reports",
                execution_metadata={"mode": "jev_recording", "llm_provider": "mock"},
            )
        else:
            from src.evaluation.layered_run import run_layered_evaluation

            await run_layered_evaluation(
                "all" if args.layers == "all" else args.layers.split(","),
                Path(runtime) / "reports",
                execution_metadata={"mode": "jev_recording", "llm_provider": "mock"},
            )
        await recorder.aclose()

    stats = recorder.stats
    by_operation: dict[str, int] = {}
    for item in stats.failed:
        by_operation[item.operation] = by_operation.get(item.operation, 0) + 1
    if args.dry_run:
        print(f"Reusable recordings: {stats.replayed}")
        print(f"Live Jev calls needed (estimate): {len(stats.failed)} {by_operation}")
        print(
            "One classification per non-blocked case; the live count should match."
        )
        return 0

    print(f"Reused: {stats.replayed}, recorded: {stats.recorded}")
    if stats.failed:
        print(f"Failed live calls: {len(stats.failed)} {by_operation}; file not written.")
        for item in stats.failed[:20]:
            print(f"  - {item.operation} {item.preview}")
        return 1
    if recorder.used == existing["entries"]:
        print(f"Unchanged: {args.output}")
        return 0
    payload = {
        "schema_version": RECORDING_SCHEMA_VERSION,
        "provider": "jev",
        "model": settings.JEV_MODEL,
        "question_sets": {"intent_classification": INTENT_QUESTION_SET_VERSION},
        "dataset": BASELINE_PATH.relative_to(PROJECT_ROOT).as_posix(),
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "entry_count": len(recorder.used),
        "entries": dict(sorted(recorder.used.items())),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    from src.decision.replay import recording_sha256

    print(f"Wrote {len(recorder.used)} entries: {args.output}")
    print(
        "Set decision_recording_sha256 in evaluation/quality_gate_policy.json to "
        f"{recording_sha256(args.output)}"
    )
    return 0


class _CallBudget:
    """真实调用次数上限，超过后按调用失败处理。"""

    def __init__(self, provider, limit: int) -> None:
        self.provider = provider
        self.remaining = limit

    async def evaluate(self, **kwargs):
        if self.remaining <= 0:
            from src.decision.models import DecisionResult

            return DecisionResult(
                enabled=True,
                available=False,
                provider="jev",
                operation=kwargs["operation"],
                question_set_version=kwargs["question_set_version"],
                model="budget",
                error_code="call_budget_exhausted",
            )
        self.remaining -= 1
        return await self.provider.evaluate(**kwargs)

    async def aclose(self) -> None:
        await self.provider.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
