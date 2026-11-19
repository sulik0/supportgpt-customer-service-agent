"""漏调诊断：react 模式漏调订单历史时，最终回复是否仍然正确。

同一批题，在 react 和 rules 两种工具选择方式下各回放一次完整 Workflow（含回复生成），
再用“回复必须包含的关键事实”做确定性检查，判断漏调是“必需工具设置过严”还是“模型的问题”：

  react 漏调，回复仍正确        -> 必需工具对这类题过严（这次不需要订单数据）
  react 漏调，回复错，rules 对   -> 漏调造成了错误回复（模型的问题，或需要强制取数）
  两种方式回复都错              -> 题、知识库或其他环节的问题，不能归因于漏调

回复生成失败（依赖异常的降级回复）的样本单独列出，不参与判断。

  --mock / --dry-run / --confirm-live  与隐蔽注入脚本相同
"""

import argparse
import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
DATASET_PATH = PROJECT_ROOT / "evaluation" / "tool_coverage" / "supportgpt_tool_coverage.json"
HISTORY = "orders.get_order_history"

from src.config import settings
from src.database import init_db
from src.evaluation.offline_rag import collect_workflow_records, load_evaluation_dataset
from src.evaluation.real_llm_regression import (
    build_real_llm_run_plan,
    require_live_confirmation,
)
from src.observability.tracing import init_tracing
from src.promptops.runtime import prompt_scope


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "evaluation" / "reports" / "tool_coverage",
    )
    parser.add_argument("--max-workflow-calls", type=int, default=600)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    return parser.parse_args()


def check_facts(reply: str, groups: list[list[str]]) -> list[list[str]]:
    """返回没命中的关键事实组；每组任一写法命中即可。"""
    return [
        group
        for group in groups
        if not any(re.search(pattern, reply, re.IGNORECASE) for pattern in group)
    ]


def summarize(case, output: dict) -> dict:
    expectations = case.agent_expectations
    reply = str(output.get("suggested_response") or "")
    errors = [str(item) for item in output.get("errors", [])]
    generation_failed = any("Resolver dependency failure" in item for item in errors)
    executed = sorted(
        {
            str(call["tool_name"])
            for call in output.get("tool_calls", [])
            if call.get("tool_name") and call.get("allowed") is not False
        }
    )
    missed = check_facts(reply, expectations.get("reply_facts", []))
    return {
        "id": case.id,
        "group": case.tags[1],
        "query": case.query,
        "need_order_data": bool(expectations.get("need_order_data")),
        "intent": str(output.get("intent")),
        "tools": executed,
        "called_history": HISTORY in executed,
        "generation_failed": generation_failed,
        "escalated": bool(output.get("escalation_recommended")),
        "facts_ok": not missed and not generation_failed,
        "missed_facts": missed,
        "reply": reply[:700],
    }


def verdict(react: dict, rules: dict) -> str:
    """只对 react 漏调、rules 调了订单历史的题下结论。"""
    if react["generation_failed"] or rules["generation_failed"]:
        return "回复生成失败，不判断"
    if react["called_history"] or not rules["called_history"]:
        return "无漏调"
    if react["facts_ok"]:
        return "漏调但回复正确（必需工具过严）"
    if rules["facts_ok"]:
        return "漏调导致回复错误（模型问题）"
    return "两种方式回复都错（非漏调原因）"


def render(meta: dict, pairs: list[tuple[dict, dict, str]]) -> str:
    by_group: dict[str, list] = defaultdict(list)
    for react, rules, v in pairs:
        by_group[react["group"]].append((react, rules, v))
    lines = [
        "# 漏调诊断",
        "",
        f"- 时间：{meta['generated_at']}；模型：{meta['llm_provider']} / {meta['llm_model']}；共 {len(pairs)} 题，每题 react、rules 各回放一次",
        "",
        "| 组 | 题数 | react 漏调订单历史 | 漏调但回复正确 | 漏调导致回复错 | 两种都错 | 生成失败 |",
        "|---|---|---|---|---|---|---|",
    ]
    totals = Counter()
    for group, items in sorted(by_group.items()):
        c = Counter(v for _, _, v in items)
        skipped = sum(1 for r, k, _ in items if not r["called_history"] and k["called_history"])
        lines.append(
            f"| {group} | {len(items)} | {skipped} | {c['漏调但回复正确（必需工具过严）']} | "
            f"{c['漏调导致回复错误（模型问题）']} | {c['两种方式回复都错（非漏调原因）']} | {c['回复生成失败，不判断']} |"
        )
        totals.update(c)
    lines += ["", "## 逐题", ""]
    for react, rules, v in pairs:
        lines += [
            f"### {react['id']}（{react['group']}）— {v}",
            f"- 问题：{react['query']}",
            f"- react：意图 {react['intent']}；调了订单历史 {react['called_history']}；事实命中 {react['facts_ok']}"
            + (f"；缺 {react['missed_facts']}" if react["missed_facts"] else ""),
            f"- rules：调了订单历史 {rules['called_history']}；事实命中 {rules['facts_ok']}"
            + (f"；缺 {rules['missed_facts']}" if rules["missed_facts"] else ""),
            f"- react 回复：{react['reply'][:350]!r}",
            f"- rules 回复：{rules['reply'][:350]!r}",
            "",
        ]
    return "\n".join(lines) + "\n"


async def main() -> None:
    args = parse_args()
    cases = load_evaluation_dataset(args.dataset, validate_agent=False, validate_security=False)
    if args.limit is not None:
        cases = cases[: args.limit]
    meta = {
        "mode": "mock" if args.mock else "real_llm",
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL_NAME,
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
        meta["estimated_workflow_llm_calls"] *= 2  # react 和 rules 各一遍
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("Dry run completed; no Workflow or model request was sent.")
        return
    if not args.mock:
        require_live_confirmation(args.confirm_live)

    init_tracing()
    await init_db()
    results: dict[str, list[dict]] = {}
    for mode in ("react", "rules"):
        settings.TOOL_SELECTION_MODE = mode
        with prompt_scope():
            records = await collect_workflow_records(cases, include_security_metadata=False)
        results[mode] = [summarize(r.case, r.workflow_output) for r in records]
        print(f"{mode} 完成", flush=True)
    pairs = [
        (react, rules, verdict(react, rules))
        for react, rules in zip(results["react"], results["rules"])
    ]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    markdown = render(meta, pairs)
    payload = {
        "meta": meta,
        "cases": [{"react": r, "rules": k, "verdict": v} for r, k, v in pairs],
    }
    for name in (f"tool_coverage_{stamp}", "tool_coverage_latest"):
        (args.output_dir / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.output_dir / f"{name}.md").write_text(markdown, encoding="utf-8")
    print(markdown[: markdown.index("## 逐题")])
    print(f"Report: {args.output_dir / 'tool_coverage_latest.md'}")


if __name__ == "__main__":
    asyncio.run(main())
