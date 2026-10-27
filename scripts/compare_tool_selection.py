"""离线对比 rules 与 react 两份 Baseline 报告的 Tool 选择效果和成本。"""

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", type=Path, required=True, help="rules 模式 Baseline JSON")
    parser.add_argument("--react", type=Path, required=True, help="react 模式 Baseline JSON")
    parser.add_argument("--output", type=Path, required=True, help="Markdown 输出路径")
    return parser.parse_args()


def _tool_metrics(report: Dict[str, Any]) -> Dict[str, Any]:
    """统计每条 Case 的 Tool 数、多余调用和规划器开销。"""
    tool_counts: List[int] = []
    extra_calls = 0
    required_total = 0
    planner_tokens: List[int] = []
    planner_latency: List[float] = []
    planner_steps: List[int] = []
    stop_reasons: Counter = Counter()
    for case in report["cases"]:
        evaluation = case["behavior_evaluation"]
        actual = set(evaluation["actual"]["tools"])
        required = set(evaluation["expected"]["required_tools"])
        tool_counts.append(len(case["actual_execution"]["tool_calls"]))
        if required:
            required_total += 1
            extra_calls += len(actual - required)
        planner = case["actual_execution"].get("tool_planner") or {}
        if planner.get("mode") == "react":
            planner_steps.append(int(planner.get("steps", 0)))
            stop_reasons[str(planner.get("stop_reason"))] += 1
        calls = [
            item
            for item in case["performance"]["llm_calls"]
            if item["operation"].endswith("plan_tool_calls")
        ]
        planner_tokens.append(sum(item["total_tokens"] for item in calls))
        planner_latency.append(sum(item["duration_seconds"] for item in calls))
    return {
        "avg_tool_calls": statistics.mean(tool_counts),
        "extra_calls_per_case": extra_calls / max(required_total, 1),
        "avg_planner_tokens": statistics.mean(planner_tokens),
        "avg_planner_latency": statistics.mean(planner_latency),
        "avg_planner_steps": statistics.mean(planner_steps) if planner_steps else 0.0,
        "stop_reasons": dict(stop_reasons),
    }


def _row(name: str, rules: Any, react: Any, digits: int = 3) -> str:
    def fmt(value: Any) -> str:
        return f"{value:.{digits}f}" if isinstance(value, float) else str(value)

    delta = ""
    if isinstance(rules, (int, float)) and isinstance(react, (int, float)):
        delta = f"{react - rules:+.{digits}f}"
    return f"| {name} | {fmt(rules)} | {fmt(react)} | {delta} |"


def build_markdown(rules: Dict[str, Any], react: Dict[str, Any]) -> str:
    rb, xb = rules["behavior_summary"], react["behavior_summary"]
    rp, xp = rules["performance_summary"], react["performance_summary"]
    rt, xt = _tool_metrics(rules), _tool_metrics(react)
    lines = [
        "# Tool Selection: rules vs react",
        "",
        f"- Dataset SHA256：`{rules['experiment_config']['dataset']['sha256']}`",
        f"- rules Run：`{rules['run_id']}`，react Run：`{react['run_id']}`",
        f"- 模型：{json.dumps(react['experiment_config']['models'], ensure_ascii=False)}",
        f"- react 配置：{json.dumps(react['experiment_config'].get('tool_selection'), ensure_ascii=False)}",
        "",
        "| Metric | rules | react | Delta |",
        "|---|---:|---:|---:|",
    ]
    for key in (
        "case_pass_rate",
        "required_tool_hit_rate",
        "forbidden_tool_violation_rate",
        "intent_accuracy",
        "hitl_accuracy",
        "approval_accuracy",
    ):
        lines.append(_row(key, rb[key], xb[key]))
    lines += [
        _row("avg_tool_calls_per_case", rt["avg_tool_calls"], xt["avg_tool_calls"], 2),
        _row("extra_calls_vs_required", rt["extra_calls_per_case"], xt["extra_calls_per_case"], 2),
        _row("avg_planner_steps", rt["avg_planner_steps"], xt["avg_planner_steps"], 2),
        _row("avg_planner_tokens", rt["avg_planner_tokens"], xt["avg_planner_tokens"], 1),
        _row("avg_planner_latency_s", rt["avg_planner_latency"], xt["avg_planner_latency"], 2),
        _row("avg_total_tokens", rp["tokens"]["average_total"], xp["tokens"]["average_total"], 1),
        _row("avg_llm_calls", rp["llm"]["average_calls_per_case"], xp["llm"]["average_calls_per_case"], 2),
        _row("e2e_latency_avg_s", rp["end_to_end_latency_seconds"]["average"], xp["end_to_end_latency_seconds"]["average"], 2),
        _row("e2e_latency_p95_s", rp["end_to_end_latency_seconds"]["p95"], xp["end_to_end_latency_seconds"]["p95"], 2),
        "",
        f"react stop reasons：{json.dumps(xt['stop_reasons'], ensure_ascii=False)}",
        "",
        "## Tool 选择不同的 Case",
        "",
        "| Case | rules tools | react tools | rules pass | react pass | react failures |",
        "|---|---|---|---|---|---|",
    ]
    rules_cases = {case["id"]: case for case in rules["cases"]}
    for case in react["cases"]:
        base = rules_cases.get(case["id"])
        if not base:
            continue
        r_tools = sorted(base["behavior_evaluation"]["actual"]["tools"])
        x_tools = sorted(case["behavior_evaluation"]["actual"]["tools"])
        r_pass = base["behavior_evaluation"]["passed"]
        x_pass = case["behavior_evaluation"]["passed"]
        if r_tools != x_tools or r_pass != x_pass:
            failures = "<br>".join(case["behavior_evaluation"]["failures"]) or "-"
            lines.append(
                f"| {case['id']} | {', '.join(r_tools) or '-'} | {', '.join(x_tools) or '-'} "
                f"| {r_pass} | {x_pass} | {failures} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    react = json.loads(args.react.read_text(encoding="utf-8"))
    if rules["experiment_config"]["dataset"]["sha256"] != react["experiment_config"]["dataset"]["sha256"]:
        raise ValueError("Both reports must replay the same Dataset.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_markdown(rules, react), encoding="utf-8")
    print(f"Comparison report: {args.output}")


if __name__ == "__main__":
    main()
