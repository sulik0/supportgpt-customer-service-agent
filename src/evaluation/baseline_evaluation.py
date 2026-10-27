"""第一版 Baseline Workflow Replay 与确定性 Agent 行为评测。"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Mapping, Optional, Sequence

from src.config import settings
from src.evaluation.baseline_error_analysis import write_baseline_error_analysis
from src.evaluation.baseline_diff import generate_baseline_diff
from src.evaluation.offline_rag import (
    EvaluationRecord,
    WorkflowRunner,
    collect_workflow_records,
    load_evaluation_dataset,
)
from src.models.intents import IntentType, normalize_intent
from src.observability.sanitization import sanitize_value
from src.promptops.runtime import active_bundle, prompt_scope
from src.skills import skill_registry


ENABLED_BEHAVIOR_METRICS = (
    "intent_accuracy",
    "department_accuracy",
    "required_tool_hit_rate",
    "forbidden_tool_violation_rate",
    "hitl_accuracy",
    "approval_accuracy",
)
METRIC_TARGETS = {
    "intent_accuracy": 1.0,
    "department_accuracy": 1.0,
    "required_tool_hit_rate": 1.0,
    "forbidden_tool_violation_rate": 0.0,
    "hitl_accuracy": 1.0,
    "approval_accuracy": 1.0,
}
IGNORED_DATASET_FIELDS = (
    "reference_answer",
    "expected_priority",
    "expected_nodes",
    "max_workflow_errors",
    "expected_sources",
    "risk_level",
    "security_expectations",
    "security_tags",
)
NODE_SPAN_NAMES = {
    "analyzer": "agent.analyzer",
    "skill_selector": "agent.skill_selector",
    "tool": "agent.tooling",
    "rag": "agent.retriever",
    "resolver": "agent.resolver",
    "qa": "agent.qa",
}


@dataclass(frozen=True)
class BaselineExpectations:
    """只解析第一版 Case Pass 实际启用的 Dataset 字段。"""

    expected_department: Optional[str] = None
    expected_intent: Optional[IntentType] = None
    required_tools: tuple[str, ...] = field(default_factory=tuple)
    forbidden_tools: tuple[str, ...] = field(default_factory=tuple)
    should_escalate: Optional[bool] = None
    should_require_approval: Optional[bool] = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BaselineExpectations":
        """忽略 priority、nodes、安全和语义字段并保留原 Dataset。"""
        raw_intent = value.get("expected_intent")
        return cls(
            expected_department=value.get("expected_department"),
            expected_intent=(
                normalize_intent(raw_intent) if raw_intent is not None else None
            ),
            required_tools=tuple(str(item) for item in value.get("required_tools", [])),
            forbidden_tools=tuple(
                str(item) for item in value.get("forbidden_tools", [])
            ),
            should_escalate=value.get("should_escalate"),
            should_require_approval=value.get("should_require_approval"),
        )


@dataclass(frozen=True)
class BaselineBehaviorResult:
    """保存第一版启用指标的逐 Case 确定性比较结果。"""

    passed: bool
    checks: Dict[str, Optional[float]]
    failures: tuple[str, ...]
    expected: Dict[str, Any]
    actual: Dict[str, Any]
    counters: Dict[str, int]


def evaluate_baseline_behavior(
    output: Mapping[str, Any], expectations: BaselineExpectations
) -> BaselineBehaviorResult:
    """只使用第一版启用字段判断 Case 是否通过。"""
    actual_intent = normalize_intent(output.get("intent"))
    actual_department = str(output.get("department", ""))
    actual_tools = {
        str(call.get("tool_name", ""))
        for call in output.get("tool_calls", [])
        if isinstance(call, Mapping) and call.get("tool_name")
    }
    required_tools = set(expectations.required_tools)
    forbidden_tools = set(expectations.forbidden_tools)
    required_hits = required_tools & actual_tools
    forbidden_violations = forbidden_tools & actual_tools

    intent_match = (
        None
        if expectations.expected_intent is None
        else float(actual_intent == expectations.expected_intent)
    )
    department_match = (
        None
        if expectations.expected_department is None
        else float(actual_department == expectations.expected_department)
    )
    required_hit_rate = (
        None
        if not required_tools
        else len(required_hits) / len(required_tools)
    )
    forbidden_violation_rate = (
        None
        if not forbidden_tools
        else len(forbidden_violations) / len(forbidden_tools)
    )
    actual_hitl = bool(output.get("escalation_recommended", False))
    actual_approval = bool(output.get("approval_required", False))
    hitl_match = (
        None
        if expectations.should_escalate is None
        else float(actual_hitl is expectations.should_escalate)
    )
    approval_match = (
        None
        if expectations.should_require_approval is None
        else float(actual_approval is expectations.should_require_approval)
    )

    failures = []
    if intent_match == 0.0:
        failures.append(
            f"intent expected={expectations.expected_intent} actual={actual_intent}"
        )
    if department_match == 0.0:
        failures.append(
            "department expected="
            f"{expectations.expected_department} actual={actual_department}"
        )
    missing_tools = sorted(required_tools - actual_tools)
    if missing_tools:
        failures.append("missing required tools: " + ", ".join(missing_tools))
    if forbidden_violations:
        failures.append(
            "forbidden tools called: " + ", ".join(sorted(forbidden_violations))
        )
    if hitl_match == 0.0:
        failures.append(
            f"HITL expected={expectations.should_escalate} actual={actual_hitl}"
        )
    if approval_match == 0.0:
        failures.append(
            "approval expected="
            f"{expectations.should_require_approval} actual={actual_approval}"
        )

    return BaselineBehaviorResult(
        passed=not failures,
        checks={
            "intent_accuracy": intent_match,
            "department_accuracy": department_match,
            "required_tool_hit_rate": required_hit_rate,
            "forbidden_tool_violation_rate": forbidden_violation_rate,
            "hitl_accuracy": hitl_match,
            "approval_accuracy": approval_match,
        },
        failures=tuple(failures),
        expected={
            "intent": (
                str(expectations.expected_intent)
                if expectations.expected_intent is not None
                else None
            ),
            "department": expectations.expected_department,
            "required_tools": sorted(required_tools),
            "forbidden_tools": sorted(forbidden_tools),
            "hitl": expectations.should_escalate,
            "approval": expectations.should_require_approval,
        },
        actual={
            "intent": str(actual_intent),
            "department": actual_department,
            "tools": sorted(actual_tools),
            "hitl": actual_hitl,
            "approval": actual_approval,
        },
        counters={
            "intent_matches": int(intent_match == 1.0),
            "intent_checks": int(intent_match is not None),
            "department_matches": int(department_match == 1.0),
            "department_checks": int(department_match is not None),
            "required_tool_hits": len(required_hits),
            "required_tool_checks": len(required_tools),
            "forbidden_tool_violations": len(forbidden_violations),
            "forbidden_tool_checks": len(forbidden_tools),
            "hitl_matches": int(hitl_match == 1.0),
            "hitl_checks": int(hitl_match is not None),
            "approval_matches": int(approval_match == 1.0),
            "approval_checks": int(approval_match is not None),
        },
    )


def build_case_performance(record: EvaluationRecord) -> Dict[str, Any]:
    """从本次 OTel Trace 同源采集结果构造逐 Case 性能数据。"""
    spans = list(record.trace_performance.get("spans", []))
    llm_calls = list(record.trace_performance.get("llm_calls", []))
    node_latency = {}
    for node, span_name in NODE_SPAN_NAMES.items():
        durations = [
            _safe_float(span.get("duration_seconds"))
            for span in spans
            if span.get("name") == span_name
        ]
        node_latency[node] = round(sum(durations), 6) if durations else None

    input_tokens = _safe_int(record.workflow_output.get("tokens_input", 0))
    output_tokens = _safe_int(record.workflow_output.get("tokens_output", 0))
    workflow_latency = _safe_float(
        record.workflow_output.get("latency_seconds", 0.0)
    )
    if workflow_latency <= 0.0:
        workflow_spans = [
            _safe_float(span.get("duration_seconds"))
            for span in spans
            if span.get("name") == "supportgpt.langgraph.workflow"
        ]
        workflow_latency = max(workflow_spans, default=0.0)

    return {
        "end_to_end_latency_seconds": round(workflow_latency, 6),
        "node_latency_seconds": node_latency,
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        },
        "models": sorted(
            {str(call.get("model")) for call in llm_calls if call.get("model")}
        ),
        "analyzer_strategy": str(
            record.workflow_output.get("analyzer_strategy", "not_run")
        ),
        "llm_call_count": len(llm_calls),
        "llm_calls": sanitize_value(llm_calls),
        "trace_span_count": len(spans),
    }


def aggregate_behavior(
    results: Sequence[BaselineBehaviorResult],
) -> Dict[str, Any]:
    """按真实检查分母汇总第一版 Agent 行为指标。"""
    totals = Counter()
    for result in results:
        totals.update(result.counters)
    return {
        "intent_accuracy": _ratio(totals["intent_matches"], totals["intent_checks"]),
        "department_accuracy": _ratio(
            totals["department_matches"], totals["department_checks"]
        ),
        "required_tool_hit_rate": _ratio(
            totals["required_tool_hits"], totals["required_tool_checks"]
        ),
        "forbidden_tool_violation_rate": _ratio(
            totals["forbidden_tool_violations"],
            totals["forbidden_tool_checks"],
        ),
        "hitl_accuracy": _ratio(totals["hitl_matches"], totals["hitl_checks"]),
        "approval_accuracy": _ratio(
            totals["approval_matches"], totals["approval_checks"]
        ),
        "case_pass_rate": _ratio(
            sum(result.passed for result in results), len(results)
        ),
        "passed_cases": sum(result.passed for result in results),
        "failed_cases": sum(not result.passed for result in results),
        "denominators": {
            "intent_cases": totals["intent_checks"],
            "department_cases": totals["department_checks"],
            "required_tools": totals["required_tool_checks"],
            "forbidden_tools": totals["forbidden_tool_checks"],
            "hitl_cases": totals["hitl_checks"],
            "approval_cases": totals["approval_checks"],
        },
    }


def aggregate_performance(cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总延迟分位数、Token、模型和 Analyzer 路径。"""
    end_to_end = [
        _safe_float(case["end_to_end_latency_seconds"]) for case in cases
    ]
    node_summary = {}
    for node in NODE_SPAN_NAMES:
        values = [
            _safe_float(case["node_latency_seconds"][node])
            for case in cases
            if case["node_latency_seconds"][node] is not None
        ]
        node_summary[node] = _latency_summary(values)

    token_input = [_safe_int(case["tokens"]["input"]) for case in cases]
    token_output = [_safe_int(case["tokens"]["output"]) for case in cases]
    token_total = [_safe_int(case["tokens"]["total"]) for case in cases]
    strategy_counts = Counter(case["analyzer_strategy"] for case in cases)
    analyzer_decisions = strategy_counts["rule"] + strategy_counts["llm"]
    model_counts = Counter(
        str(call.get("model"))
        for case in cases
        for call in case["llm_calls"]
        if call.get("model")
    )
    operation_counts = Counter(
        str(call.get("operation"))
        for case in cases
        for call in case["llm_calls"]
        if call.get("operation")
    )
    llm_call_count = sum(_safe_int(case["llm_call_count"]) for case in cases)
    return {
        "end_to_end_latency_seconds": _latency_summary(end_to_end),
        "node_latency_seconds": node_summary,
        "tokens": {
            "input_total": sum(token_input),
            "output_total": sum(token_output),
            "total": sum(token_total),
            "average_input": _average(token_input),
            "average_output": _average(token_output),
            "average_total": _average(token_total),
        },
        "analyzer": {
            "rule_hit_rate": _ratio(strategy_counts["rule"], analyzer_decisions),
            "strategy_counts": dict(sorted(strategy_counts.items())),
        },
        "llm": {
            "call_count": llm_call_count,
            "average_calls_per_case": _ratio(llm_call_count, len(cases)),
            "models": dict(sorted(model_counts.items())),
            "operations": dict(sorted(operation_counts.items())),
        },
    }


def build_metric_failure_index(
    case_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """按启用指标聚合失败 Case，支持从汇总分数反查执行详情。"""
    index: Dict[str, Any] = {}
    for metric in ENABLED_BEHAVIOR_METRICS:
        target = METRIC_TARGETS[metric]
        evaluated_cases = 0
        failures = []
        for row in case_rows:
            evaluation = row["behavior_evaluation"]
            value = evaluation["checks"].get(metric)
            if value is None:
                continue
            evaluated_cases += 1
            if float(value) == target:
                continue
            expected, actual, reason = _metric_failure_detail(metric, evaluation)
            failures.append(
                {
                    "case_id": row["id"],
                    "query": row.get("dataset_case", {}).get("query", ""),
                    "metric_value": value,
                    "target_value": target,
                    "expected": expected,
                    "actual": actual,
                    "reason": reason,
                    "trace_id": row.get("trace_id"),
                }
            )
        index[metric] = {
            "target_value": target,
            "evaluated_case_count": evaluated_cases,
            "failed_case_count": len(failures),
            "failure_rate": _ratio(len(failures), evaluated_cases),
            "failed_case_ids": [item["case_id"] for item in failures],
            "cases": failures,
        }
    return index


def _metric_failure_detail(
    metric: str, evaluation: Mapping[str, Any]
) -> tuple[Any, Any, str]:
    """将指标失败转换为可读的期望、实际值和原因。"""
    expected = evaluation["expected"]
    actual = evaluation["actual"]
    if metric == "intent_accuracy":
        return (
            expected["intent"],
            actual["intent"],
            f"intent expected={expected['intent']} actual={actual['intent']}",
        )
    if metric == "department_accuracy":
        return (
            expected["department"],
            actual["department"],
            "department expected="
            f"{expected['department']} actual={actual['department']}",
        )
    if metric == "required_tool_hit_rate":
        required = set(expected["required_tools"])
        called = set(actual["tools"])
        missing = sorted(required - called)
        return (
            sorted(required),
            sorted(called),
            "missing required tools: " + ", ".join(missing),
        )
    if metric == "forbidden_tool_violation_rate":
        forbidden = set(expected["forbidden_tools"])
        called = set(actual["tools"])
        violations = sorted(forbidden & called)
        return (
            sorted(forbidden),
            sorted(called),
            "forbidden tools called: " + ", ".join(violations),
        )
    if metric == "hitl_accuracy":
        return (
            expected["hitl"],
            actual["hitl"],
            f"HITL expected={expected['hitl']} actual={actual['hitl']}",
        )
    if metric == "approval_accuracy":
        return (
            expected["approval"],
            actual["approval"],
            "approval expected="
            f"{expected['approval']} actual={actual['approval']}",
        )
    raise ValueError(f"Unsupported behavior metric: {metric}")


def build_baseline_report(
    records: Sequence[EvaluationRecord],
    *,
    dataset_path: Path,
    execution_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """生成只以第一版启用指标判定通过的统一 JSON 报告。"""
    behavior_results = [
        evaluate_baseline_behavior(
            record.workflow_output,
            BaselineExpectations.from_mapping(record.case.agent_expectations),
        )
        for record in records
    ]
    performance_cases = [build_case_performance(record) for record in records]
    case_rows = []
    for record, behavior, performance in zip(
        records, behavior_results, performance_cases
    ):
        case_rows.append(
            {
                "id": record.case.id,
                "prompt_bundle_id": record.workflow_output.get("prompt_bundle_id")
                or record.ticket_state.get("prompt_bundle_id") or active_bundle().bundle_id,
                "dataset_case": asdict(record.case),
                "ticket_state": sanitize_value(record.ticket_state),
                "trace_id": record.trace_id,
                "actual_execution": sanitize_value(
                    {
                        "intent": record.workflow_output.get("intent"),
                        "department": record.workflow_output.get("department"),
                        "priority": record.workflow_output.get("priority"),
                        "tool_calls": record.workflow_output.get("tool_calls", []),
                        "tool_planner": record.workflow_output.get("tool_planner", {}),
                        "workflow_path": record.workflow_path,
                        "escalation_recommended": record.workflow_output.get(
                            "escalation_recommended", False
                        ),
                        "approval_required": record.workflow_output.get(
                            "approval_required", False
                        ),
                        "analyzer_strategy": record.workflow_output.get(
                            "analyzer_strategy", "not_run"
                        ),
                        "skill_name": record.workflow_output.get(
                            "skill_name", "unselected"
                        ),
                        "skill_version": record.workflow_output.get(
                            "skill_version", "unselected"
                        ),
                        "skill_selection_strategy": record.workflow_output.get(
                            "selection_strategy", "not_run"
                        ),
                        "skill_registry_id": record.workflow_output.get(
                            "skill_registry_id", "unselected"
                        ),
                        "qa_strategy": record.workflow_output.get(
                            "qa_strategy", "not_run"
                        ),
                        "risk_level": record.workflow_output.get("risk_level"),
                        "risk_score": record.workflow_output.get("risk_score"),
                        "qa_score": record.workflow_output.get("qa_score"),
                        "response_grounded": record.workflow_output.get(
                            "response_grounded", False
                        ),
                        "response_requires_human": record.workflow_output.get(
                            "response_requires_human", False
                        ),
                        "suggested_response": record.response,
                        "context_citations": record.workflow_output.get(
                            "context_citations", []
                        ),
                        "errors": record.workflow_errors,
                    }
                ),
                "behavior_evaluation": {
                    "passed": behavior.passed,
                    "checks": behavior.checks,
                    "expected": behavior.expected,
                    "actual": behavior.actual,
                    "failures": list(behavior.failures),
                },
                "performance": performance,
            }
        )
    return {
        "schema_version": "1.1",
        "evaluation_type": "baseline_workflow_replay_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "case_count": len(records),
        "enabled_behavior_metrics": list(ENABLED_BEHAVIOR_METRICS),
        "ignored_dataset_fields": list(IGNORED_DATASET_FIELDS),
        "execution": sanitize_value(execution_metadata or {}),
        "experiment_config": _build_experiment_config(
            dataset_path, execution_metadata or {}
        ),
        "behavior_summary": aggregate_behavior(behavior_results),
        "metric_failure_index": build_metric_failure_index(case_rows),
        "performance_summary": aggregate_performance(performance_cases),
        "cases": case_rows,
    }


def write_baseline_report(report: Dict[str, Any], output_dir: Path) -> Dict[str, Path]:
    """同时写入不可变快照，并让 latest 仅指向最新一次正式结果。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_stem = _next_snapshot_stem(output_dir, report.get("generated_at"))
    report["run_id"] = snapshot_stem.removeprefix("baseline_v1_")
    snapshot_json = output_dir / f"{snapshot_stem}.json"
    snapshot_markdown = output_dir / f"{snapshot_stem}.md"
    latest_json = output_dir / "baseline_v1_latest.json"
    latest_markdown = output_dir / "baseline_v1_latest.md"
    report["artifacts"] = {
        "snapshot_json": snapshot_json.name,
        "snapshot_markdown": snapshot_markdown.name,
        "latest_json": latest_json.name,
        "latest_markdown": latest_markdown.name,
        "error_analysis_snapshot": f"error_analysis_{report['run_id']}.md",
        "error_analysis_latest": "error_analysis_latest.md",
    }
    snapshot_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    snapshot_markdown.write_text(_render_markdown(report), encoding="utf-8")
    _replace_latest_copy(latest_json, snapshot_json)
    _replace_latest_copy(latest_markdown, snapshot_markdown)
    return {
        "json": latest_json,
        "markdown": latest_markdown,
        "snapshot_json": snapshot_json,
        "snapshot_markdown": snapshot_markdown,
    }


async def run_baseline_evaluation_v1(
    dataset_path: Path,
    output_dir: Path,
    *,
    limit: Optional[int] = None,
    execution_metadata: Optional[Dict[str, Any]] = None,
    workflow_runner: Optional[WorkflowRunner] = None,
) -> Dict[str, Path]:
    """整次实验固定 Prompt，防止逐 Case 回放时版本漂移。"""
    with prompt_scope():
        return await _run_baseline_evaluation_pinned(
            dataset_path, output_dir, limit=limit,
            execution_metadata=execution_metadata, workflow_runner=workflow_runner,
        )


async def _run_baseline_evaluation_pinned(
    dataset_path: Path,
    output_dir: Path,
    *,
    limit: Optional[int] = None,
    execution_metadata: Optional[Dict[str, Any]] = None,
    workflow_runner: Optional[WorkflowRunner] = None,
) -> Dict[str, Path]:
    """逐条构造完整 State、回放 Workflow 并生成第一版报告。"""
    cases = load_evaluation_dataset(
        dataset_path, validate_agent=False, validate_security=False
    )
    if len(cases) != 100:
        raise ValueError("Baseline Evaluation V1 requires exactly 100 dataset cases.")
    if limit is not None:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100.")
        cases = cases[:limit]
    for case in cases:
        BaselineExpectations.from_mapping(case.agent_expectations)
    records = await collect_workflow_records(
        cases,
        workflow_runner,
        include_security_metadata=False,
    )
    report = build_baseline_report(
        records,
        dataset_path=dataset_path,
        execution_metadata=execution_metadata,
    )
    paths = write_baseline_report(report, output_dir)
    error_paths = write_baseline_error_analysis(paths["snapshot_json"], output_dir)
    output_paths = {
        **paths,
        "error_analysis": error_paths["latest"],
        "error_analysis_snapshot": error_paths["snapshot"],
    }
    diff_paths = generate_baseline_diff(paths["snapshot_json"], output_dir)
    if diff_paths is not None:
        output_paths.update(
            {
                "diff": diff_paths["markdown"],
                "diff_json": diff_paths["json"],
                "diff_snapshot": diff_paths["snapshot_markdown"],
                "diff_snapshot_json": diff_paths["snapshot_json"],
                "diff_previous_snapshot_json": diff_paths[
                    "previous_snapshot_json"
                ],
            }
        )
    return output_paths


def _render_markdown(report: Dict[str, Any]) -> str:
    behavior = report["behavior_summary"]
    performance = report["performance_summary"]
    latency = performance["end_to_end_latency_seconds"]
    lines = [
        "# Baseline Workflow Replay V1 评测报告",
        "",
        f"- Run ID：`{report['run_id']}`",
        f"- Dataset：`{report['dataset']}`",
        f"- Case 数：{report['case_count']}",
        "- Case Pass 仅由当前启用的六项确定性行为指标决定。",
        "",
        "## 实验配置",
        "",
        "```json",
        json.dumps(report["experiment_config"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Agent 行为汇总",
        "",
        "| Intent Accuracy | Department Accuracy | Required Tool Hit Rate | Forbidden Tool Violation Rate | HITL Accuracy | Approval Accuracy | Case Pass Rate |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {_metric(behavior['intent_accuracy'])} "
            f"| {_metric(behavior['department_accuracy'])} "
            f"| {_metric(behavior['required_tool_hit_rate'])} "
            f"| {_metric(behavior['forbidden_tool_violation_rate'])} "
            f"| {_metric(behavior['hitl_accuracy'])} "
            f"| {_metric(behavior['approval_accuracy'])} "
            f"| {_metric(behavior['case_pass_rate'])} |"
        ),
        "",
        "## 性能汇总",
        "",
        "| Average | P50 | P95 | Average Tokens | Analyzer Rule Hit Rate | LLM Calls |",
        "|---:|---:|---:|---:|---:|---:|",
        (
            f"| {latency['average']:.4f}s | {latency['p50']:.4f}s "
            f"| {latency['p95']:.4f}s "
            f"| {performance['tokens']['average_total']:.2f} "
            f"| {_metric(performance['analyzer']['rule_hit_rate'])} "
            f"| {performance['llm']['call_count']} |"
        ),
        "",
        "### 节点耗时",
        "",
        "| Node | Executions | Average | P50 | P95 |",
        "|---|---:|---:|---:|---:|",
    ]
    for node, summary in performance["node_latency_seconds"].items():
        lines.append(
            f"| {node} | {summary['count']} | {summary['average']:.4f}s "
            f"| {summary['p50']:.4f}s | {summary['p95']:.4f}s |"
        )
    lines.extend(
        [
            "",
            "## 按指标定位失败 Case",
            "",
            "| Metric | Target | Evaluated Cases | Failed Cases | Failure Rate | Case IDs |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for metric, summary in report["metric_failure_index"].items():
        case_ids = ", ".join(summary["failed_case_ids"]) or "-"
        lines.append(
            f"| `{metric}` | {_metric(summary['target_value'])} "
            f"| {summary['evaluated_case_count']} | {summary['failed_case_count']} "
            f"| {_metric(summary['failure_rate'])} | {case_ids} |"
        )
    for metric, summary in report["metric_failure_index"].items():
        if not summary["cases"]:
            continue
        lines.extend(
            [
                "",
                f"### `{metric}` 失败 Case",
                "",
                "| Case ID | Query | Value | Expected | Actual | Reason | Trace ID |",
                "|---|---|---:|---|---|---|---|",
            ]
        )
        for failure in summary["cases"]:
            lines.append(
                f"| {failure['case_id']} | {_markdown_value(failure['query'])} "
                f"| {_metric(failure['metric_value'])} "
                f"| {_markdown_value(failure['expected'])} "
                f"| {_markdown_value(failure['actual'])} "
                f"| {_markdown_value(failure['reason'])} "
                f"| `{failure['trace_id'] or 'unavailable'}` |"
            )
    lines.extend(
        [
            "",
            "## Case 明细",
            "",
            "| ID | Pass | Intent | Department | Required Tool | Forbidden Tool | HITL | Approval | Latency | Tokens | Analyzer | LLM Calls | Trace ID |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|",
        ]
    )
    for row in report["cases"]:
        checks = row["behavior_evaluation"]["checks"]
        perf = row["performance"]
        lines.append(
            f"| {row['id']} "
            f"| {'PASS' if row['behavior_evaluation']['passed'] else 'FAIL'} "
            f"| {_metric(checks['intent_accuracy'])} "
            f"| {_metric(checks['department_accuracy'])} "
            f"| {_metric(checks['required_tool_hit_rate'])} "
            f"| {_metric(checks['forbidden_tool_violation_rate'])} "
            f"| {_metric(checks['hitl_accuracy'])} "
            f"| {_metric(checks['approval_accuracy'])} "
            f"| {perf['end_to_end_latency_seconds']:.4f}s "
            f"| {perf['tokens']['total']} "
            f"| {perf['analyzer_strategy']} "
            f"| {perf['llm_call_count']} "
            f"| `{row['trace_id'] or 'unavailable'}` |"
        )
    lines.extend(
        [
            "",
            "## 本版暂不参与通过判断的字段",
            "",
            ", ".join(f"`{field}`" for field in report["ignored_dataset_fields"]),
            "",
        ]
    )
    return "\n".join(lines)


def _latency_summary(values: Sequence[float]) -> Dict[str, Any]:
    clean = [max(float(value), 0.0) for value in values]
    return {
        "count": len(clean),
        "average": _average(clean),
        "p50": _percentile(clean, 0.50),
        "p95": _percentile(clean, 0.95),
    }


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * fraction,
        6,
    )


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _average(values: Sequence[int | float]) -> float:
    return round(mean(values), 6) if values else 0.0


def _safe_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _metric(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.4f}"


def _markdown_value(value: Any) -> str:
    """压缩并转义 Markdown 表格中的结构化值。"""
    rendered = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )
    return str(rendered).replace("|", "\\|").replace("\n", " ")


def _build_experiment_config(
    dataset_path: Path, execution_metadata: Mapping[str, Any]
) -> Dict[str, Any]:
    """固定记录复现实验所需的 Dataset、模型、Workflow 与阈值。"""
    dataset_bytes = dataset_path.read_bytes()
    dataset_payload = json.loads(dataset_bytes.decode("utf-8"))
    provider = settings.LLM_PROVIDER.lower()
    if provider == "azure":
        resolver_model = settings.AZURE_OPENAI_DEPLOYMENT or "azure"
    elif provider == "openai":
        resolver_model = settings.LLM_MODEL_NAME or "openai-compatible"
    else:
        resolver_model = "mock"
    analyzer_model = (
        settings.LLM_ANALYZER_MODEL_NAME
        or settings.LLM_FAST_MODEL_NAME
        or resolver_model
    )
    qa_model = (
        settings.LLM_QA_MODEL_NAME or settings.LLM_FAST_MODEL_NAME or resolver_model
    )
    source_revision = _source_revision()
    if not re.fullmatch(r"(?:[0-9a-fA-F]{7,40}|unknown)", source_revision):
        source_revision = "unknown"
    config = {
            "dataset": {
                "dataset_name": dataset_payload.get("name"),
                "version": dataset_payload.get("version"),
                "sha256": hashlib.sha256(dataset_bytes).hexdigest(),
                "declared_case_count": len(dataset_payload.get("cases", [])),
                "kb_versions": sorted(
                    {
                        str(case.get("kb_version", "v1"))
                        for case in dataset_payload.get("cases", [])
                    }
                ),
            },
            "evaluator": {
                "version": "baseline_v1",
                "enabled_behavior_metrics": list(ENABLED_BEHAVIOR_METRICS),
                "ignored_dataset_fields": list(IGNORED_DATASET_FIELDS),
            },
            "workflow": {
                "version": settings.AGENT_WORKFLOW_VERSION,
                "prompt_version": active_bundle().version,
                "source_revision": source_revision,
            },
            "skills": skill_registry.snapshot(),
            "tool_selection": {
                "mode": settings.TOOL_SELECTION_MODE,
                "react_max_steps": settings.TOOL_REACT_MAX_STEPS,
                "react_max_tool_calls": settings.TOOL_REACT_MAX_TOOL_CALLS,
                "planner_model": settings.LLM_TOOL_PLANNER_MODEL_NAME or resolver_model,
            },
            "models": {
                "provider": provider,
                "resolver": resolver_model,
                "analyzer": analyzer_model,
                "qa": qa_model,
                "qwen3_guard_enabled": settings.QWEN3_GUARD_ENABLED,
                "qwen3_guard_model": settings.QWEN3_GUARD_MODEL_NAME,
            },
            "limits": {
                "temperature": 0.0,
                "analyzer_max_tokens": settings.LLM_ANALYZER_MAX_TOKENS,
                "resolver_max_tokens": settings.LLM_RESOLVER_MAX_TOKENS,
                "qa_max_tokens": settings.LLM_QA_MAX_TOKENS,
                "resolver_max_rag_chars": settings.LLM_RESOLVER_MAX_RAG_CHARS,
                "resolver_max_tool_chars": settings.LLM_RESOLVER_MAX_TOOL_CHARS,
                "qa_max_context_chars": settings.LLM_QA_MAX_CONTEXT_CHARS,
            },
            "risk_thresholds": {
                "medium": settings.RISK_MEDIUM_THRESHOLD,
                "high": settings.RISK_HIGH_THRESHOLD,
                "critical": settings.RISK_CRITICAL_THRESHOLD,
                "low_confidence": settings.RISK_LOW_CONFIDENCE_THRESHOLD,
                "qa_score": settings.RISK_QA_SCORE_THRESHOLD,
            },
            "observability": {
                "otel_enabled": settings.OTEL_ENABLED,
                "service_name": settings.OTEL_SERVICE_NAME,
                "langsmith_project": settings.OTEL_COLLECTOR_LANGSMITH_PROJECT,
            },
            "execution": dict(execution_metadata),
        }
    sanitized = sanitize_value(config)
    # Git SHA 是复现实验的非敏感标识，避免被电话号规则误脱敏。
    sanitized["workflow"]["source_revision"] = source_revision
    sanitized["dataset"]["sha256"] = config["dataset"]["sha256"]
    # 模板快照只含开发者静态内容，Hash 不经过 PII 数字脱敏。
    sanitized["prompts"] = {
        **active_bundle().metadata(), "snapshot": active_bundle().payload()
    }
    return sanitized


def _next_snapshot_stem(
    output_dir: Path, generated_at: Optional[str] = None
) -> str:
    """按报告生成时间创建快照名，同秒重复运行时追加序号。"""
    generated_time = datetime.now().astimezone()
    if generated_at:
        try:
            generated_time = datetime.fromisoformat(generated_at).astimezone()
        except ValueError:
            pass
    base = "baseline_v1_" + generated_time.strftime("%Y%m%d_%H%M%S")
    candidate = base
    sequence = 2
    while (output_dir / f"{candidate}.json").exists():
        candidate = f"{base}_{sequence:02d}"
        sequence += 1
    return candidate


def _replace_latest_copy(latest: Path, snapshot: Path) -> None:
    """原子替换 latest 普通文件，兼容 Typora 等桌面编辑器。"""
    temporary = latest.with_name(f".{latest.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    try:
        shutil.copy2(snapshot, temporary)
        os.replace(temporary, latest)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def _source_revision() -> str:
    """优先读取 CI Revision，本地则记录当前 Git Commit。"""
    configured = os.getenv("GIT_COMMIT_SHA") or os.getenv("GITHUB_SHA")
    if configured:
        return configured[:40]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip()[:40] or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
