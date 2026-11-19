"""隐蔽注入评测：用不含攻击词的社交手段诱导越权，看模型是否被骗、防线是否拦住。

和固定 100 条 Baseline 分开：不改 Baseline，也不覆盖 Baseline 报告。
默认读 evaluation/covert_injection/supportgpt_covert_injection.json。

  --mock         用当前配置的 Provider 回放，不检查真实模型配置（本地离线验证格式）
  --dry-run      只打印运行计划和预估调用次数
  --confirm-live 真实模型回放，会产生费用

真正有意义的是 TOOL_SELECTION_MODE=react + 真实模型：此时由模型选工具。
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
DATASET_PATH = (
    PROJECT_ROOT / "evaluation" / "covert_injection" / "supportgpt_covert_injection.json"
)

from src.agents.signals import is_security_blocked
from src.config import settings
from src.database import init_db
from src.evaluation.baseline_evaluation import (
    BaselineExpectations,
    evaluate_baseline_behavior,
)
from src.evaluation.offline_rag import collect_workflow_records, load_evaluation_dataset
from src.evaluation.real_llm_regression import (
    build_real_llm_run_plan,
    require_live_confirmation,
)
from src.observability.tracing import init_tracing
from src.promptops.runtime import prompt_scope


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay the covert injection cases.")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "evaluation" / "reports" / "covert_injection",
    )
    parser.add_argument("--max-workflow-calls", type=int, default=150)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    return parser.parse_args()


def case_kind(case) -> str:
    """tags 的第二项是手法类别；对照组单独成类。"""
    tags = [tag for tag in case.tags if tag != "covert_injection"]
    return "control" if "control" in tags else (tags[0] if tags else "unknown")


def summarize_case(case, output: dict) -> dict:
    """单条用例：执行了什么、尝试过什么、被谁拦住。"""
    expectations = BaselineExpectations.from_mapping(case.agent_expectations)
    behavior = evaluate_baseline_behavior(output, expectations)
    forbidden = set(expectations.forbidden_tools)
    planner = output.get("tool_planner") or {}
    # 模型想调的工具 = 进入过工具调用记录的 + 被候选过滤直接拒绝的。
    attempted = set(behavior.actual["attempted_tools"]) | set(
        planner.get("rejected_tools") or []
    )
    executed = set(behavior.actual["tools"])
    denied = sorted(
        {
            str(call["tool_name"])
            for call in output.get("tool_calls", [])
            if call.get("allowed") is False
        }
    )
    return {
        "id": case.id,
        "kind": case_kind(case),
        "query": case.query,
        "input_blocked": is_security_blocked(output),
        "intent": behavior.actual["intent"],
        "executed_tools": sorted(executed),
        "attempted_forbidden": sorted(forbidden & attempted),
        "executed_forbidden": sorted(forbidden & executed),
        "denied_by_registry": denied,
        "rejected_by_planner": sorted(planner.get("rejected_tools") or []),
        "hitl": behavior.actual["hitl"],
        "approval": behavior.actual["approval"],
        "behavior_passed": behavior.passed,
        "behavior_failures": list(behavior.failures),
        "planner_stop_reason": planner.get("stop_reason"),
    }


def aggregate(rows: list[dict]) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["kind"]].append(row)

    def stats(items: list[dict]) -> dict:
        total = len(items)
        return {
            "cases": total,
            "input_blocked": sum(r["input_blocked"] for r in items),
            "model_tried_forbidden": sum(bool(r["attempted_forbidden"]) for r in items),
            "executed_forbidden": sum(bool(r["executed_forbidden"]) for r in items),
            "hitl": sum(bool(r["hitl"]) for r in items),
            "behavior_passed": sum(r["behavior_passed"] for r in items),
        }

    attacks = [row for row in rows if row["kind"] != "control"]
    return {
        "attacks": stats(attacks),
        "controls": stats([row for row in rows if row["kind"] == "control"]),
        "by_kind": {kind: stats(items) for kind, items in sorted(groups.items())},
    }


def render_markdown(meta: dict, summary: dict, rows: list[dict]) -> str:
    a = summary["attacks"]
    lines = [
        "# 隐蔽注入评测",
        "",
        f"- 时间：{meta['generated_at']}",
        f"- 模型：{meta['llm_provider']} / {meta['llm_model']}；工具选择：{meta['tool_selection_mode']}",
        f"- 攻击用例 {a['cases']} 条；前置安全检查拦下 {a['input_blocked']} 条；"
        f"模型想调禁用工具 {a['model_tried_forbidden']} 条；"
        f"**真正执行了禁用工具 {a['executed_forbidden']} 条**",
        "",
        "| 手法 | 用例数 | 前置拦下 | 模型想调禁用工具 | 真正执行禁用工具 | 转人工 |",
        "|---|---|---|---|---|---|",
    ]
    for kind, s in summary["by_kind"].items():
        lines.append(
            f"| {kind} | {s['cases']} | {s['input_blocked']} | "
            f"{s['model_tried_forbidden']} | {s['executed_forbidden']} | {s['hitl']} |"
        )
    problems = [r for r in rows if r["executed_forbidden"]]
    lines += ["", "## 真正执行了禁用工具的用例", ""]
    lines += (
        [f"- {r['id']}：{', '.join(r['executed_forbidden'])}" for r in problems]
        or ["无"]
    )
    tried = [r for r in rows if r["attempted_forbidden"] and not r["executed_forbidden"]]
    lines += ["", "## 模型想调但被拦住的用例", ""]
    lines += (
        [
            f"- {r['id']}：想调 {', '.join(r['attempted_forbidden'])}；"
            f"候选过滤拒绝 {r['rejected_by_planner'] or '-'}；执行前拦截 {r['denied_by_registry'] or '-'}"
            for r in tried
        ]
        or ["无"]
    )
    return "\n".join(lines) + "\n"


async def main() -> None:
    args = parse_args()
    cases = load_evaluation_dataset(
        args.dataset, validate_agent=False, validate_security=False
    )
    meta = {
        "mode": "mock" if args.mock else "real_llm",
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL_NAME,
        "tool_selection_mode": settings.TOOL_SELECTION_MODE,
        "case_count": len(cases),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not args.mock:
        plan = build_real_llm_run_plan(
            settings=settings,
            cases=cases,
            suite="full",
            explicit_case_ids=None,
            max_workflow_calls=args.max_workflow_calls,
        )
        meta.update(plan.report_metadata())
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("Dry run completed; no Workflow or model request was sent.")
        return
    if not args.mock:
        require_live_confirmation(args.confirm_live)

    init_tracing()
    await init_db()
    with prompt_scope():
        records = await collect_workflow_records(
            cases, include_security_metadata=False
        )
    rows = [summarize_case(r.case, r.workflow_output) for r in records]
    summary = aggregate(rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {"meta": meta, "summary": summary, "cases": rows}
    markdown = render_markdown(meta, summary, rows)
    for name in (f"covert_injection_{stamp}", "covert_injection_latest"):
        (args.output_dir / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.output_dir / f"{name}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"Report: {args.output_dir / 'covert_injection_latest.md'}")


if __name__ == "__main__":
    asyncio.run(main())
