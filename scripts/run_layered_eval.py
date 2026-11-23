"""按层运行评测题集。

  --suite pr         读 evaluation/suites.json：pr 为离线回放（Mock 模型 + Jev 录音，免费）
  --suite release    全部层，真实模型，需要 --confirm-live
  --layers core,injection / all    不走套件，直接指定层（默认 Mock，不产生费用）

门禁按层判断，任何一层低于阈值，退出码为 1；合计分数不参与门禁。
目前 suites.json 没有设任何阈值（固定 100 条题已移出门禁），只出报告。
--dry-run 只估算真实模型调用次数。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default=None)
    parser.add_argument("--layers", default="core", help="层名，逗号分隔，或 all")
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "evaluation" / "reports" / "layered"
    )
    parser.add_argument("--decision-recording", type=Path, default=None)
    parser.add_argument("--max-workflow-calls", type=int, default=300)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    # 离线回放必须在导入 src.config 之前设好环境变量。
    from src.evaluation.manifest import load_suite

    suite = load_suite(args.suite) if args.suite else None
    offline = bool(suite and suite["mode"] == "replay")
    recording = None
    if offline:
        import tempfile

        from scripts.run_ci_quality_gate import (
            DECISION_RECORDING_PATH,
            configure_offline_environment,
        )

        recording = args.decision_recording or DECISION_RECORDING_PATH
        runtime = tempfile.TemporaryDirectory(prefix="supportgpt-layered-")
        configure_offline_environment(Path(runtime.name), recording)

    from src.config import settings
    from src.database import init_db
    from src.evaluation.layered_run import run_layered_evaluation, select_layers
    from src.evaluation.manifest import load_manifest
    from src.evaluation.offline_rag import load_evaluation_dataset
    from src.evaluation.real_llm_regression import (
        REAL_LLM_PROVIDERS,
        build_real_llm_run_plan,
        require_live_confirmation,
    )
    from src.observability.tracing import init_tracing

    manifest = load_manifest()
    wanted = suite["layers"] if suite else ("all" if args.layers == "all" else args.layers.split(","))
    layers = select_layers(manifest, wanted)
    gates = suite["gates"] if suite else None
    metadata = {"mode": "layered", "layers": layers}
    if suite:
        metadata["suite"] = suite["name"]
    if offline:
        from src.decision.replay import recording_sha256

        metadata.update(
            {"mode": "ci_offline_workflow_replay", "decision_provider": "jev_replay",
             "decision_recording_sha256": recording_sha256(recording)}
        )
    elif settings.LLM_PROVIDER.lower() in REAL_LLM_PROVIDERS:
        cases = [
            case
            for name in layers
            for case in load_evaluation_dataset(
                PROJECT_ROOT / manifest["layers"][name]["file"],
                validate_agent=False,
                validate_security=False,
            )
        ]
        plan = build_real_llm_run_plan(
            settings=settings,
            cases=cases,
            suite="full",
            explicit_case_ids=[case.id for case in cases],
            max_workflow_calls=args.max_workflow_calls,
        )
        metadata.update(plan.report_metadata())
    elif suite and suite["mode"] == "live":
        print("release 套件需要真实模型：先配置 LLM_PROVIDER 和密钥。")
        return 2
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("Dry run completed; no Workflow or model request was sent.")
        return 0
    if metadata.get("llm_provider"):
        require_live_confirmation(args.confirm_live)
    init_tracing()
    await init_db()
    result = await run_layered_evaluation(
        layers, args.output_dir, suite_name=args.suite, gates=gates,
        execution_metadata=metadata,
    )
    missing = []
    if offline:
        from src.decision import decision_service

        missing = list(getattr(decision_service.provider, "missing", []))
    print(f"JSON: {result['paths']['json']}")
    print(f"Markdown: {result['paths']['markdown']}")
    for name, item in result["summary"]["layers"].items():
        print(f"{name}: {item['passed']}/{item['total']}  失败: {item['failed_ids'] or '无'}")
    if missing:
        print(f"缺 Jev 录音 {len(missing)} 条：python scripts/record_jev_decisions.py --dry-run")
    if gates:
        print("门禁：" + ("通过" if result["gate"]["passed"] else "失败"))
    return 0 if result["gate"]["passed"] and not missing else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
