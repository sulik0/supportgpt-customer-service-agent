"""基于 Dataset + Workflow Replay 的 RAG 与 Agent 统一离线评测。"""

import asyncio
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from src.config import settings
from src.evaluation.agent_evaluation import (
    AGENT_METRIC_KEYS,
    AgentExpectations,
    score_agent_behavior,
)
from src.evaluation.response_metrics import response_metrics_evaluator
from src.evaluation.retrieval_metrics import retrieval_metrics_evaluator
from src.evaluation.security_evaluation import (
    SecurityExpectations,
    case_result_payload,
    evaluate_security_records,
)
from src.observability.sanitization import redact_text, sanitize_value
from src.observability.tracing import (
    capture_trace_performance,
    get_current_trace_id,
    get_tracer,
    observed_span,
    set_span_attributes,
)


METRIC_KEYS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)
tracer = get_tracer(__name__)


@dataclass(frozen=True)
class EvaluationCase:
    """定义一条离线 RAG 评测样本及其标准答案和期望来源。"""

    id: str
    query: str
    reference_answer: str
    expected_sources: List[str]
    category: str
    risk_level: str
    kb_version: str = "v1"
    customer_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    agent_expectations: Dict[str, Any] = field(default_factory=dict)
    security_expectations: Dict[str, Any] = field(default_factory=dict)
    agent_run_id: Optional[str] = None


@dataclass
class EvaluationRecord:
    """保存单条样本的 Workflow 输出、检索上下文和指标结果。"""

    case: EvaluationCase
    response: str
    contexts: List[str]
    retrieved_sources: List[str]
    metrics: Dict[str, float]
    agent_metrics: Dict[str, float]
    agent_passed: bool
    agent_failures: List[str]
    trace_id: Optional[str]
    workflow_path: List[str]
    workflow_output: Dict[str, Any]
    workflow_errors: List[str]
    ticket_state: Dict[str, Any]
    trace_performance: Dict[str, Any]


WorkflowRunner = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


def load_evaluation_dataset(
    path: Path, *, validate_agent: bool = True, validate_security: bool = True
) -> List[EvaluationCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Evaluation dataset must contain a non-empty 'cases' list.")

    cases = [EvaluationCase(**item) for item in raw_cases]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Evaluation case ids must be unique.")
    for case in cases:
        if not case.agent_expectations:
            raise ValueError(
                f"Evaluation case '{case.id}' must define agent_expectations."
            )
        if validate_agent:
            AgentExpectations.from_mapping(case.agent_expectations)
        if validate_security:
            SecurityExpectations.from_case(case)
    return cases


INFRASTRUCTURE_RETRY_LIMIT = 2
_INFRASTRUCTURE_ERROR = (
    "dependency failure",
    "ResilienceExhaustedError",
    "circuit_open",
    "Workflow replay failed",
    "failed: timeout",
)


def is_infrastructure_failure(output: Dict[str, Any]) -> bool:
    """依赖调用耗尽重试、熔断或回放本身抛错；QA 幻觉告警和安全拦截不算。"""
    return any(
        marker in str(error)
        for error in output.get("errors", []) or []
        for marker in _INFRASTRUCTURE_ERROR
    )


async def collect_workflow_records(
    cases: Sequence[EvaluationCase],
    workflow_runner: Optional[WorkflowRunner] = None,
    *,
    include_security_metadata: bool = True,
) -> List[EvaluationRecord]:
    from src.agents.graph import build_ticket_state

    if workflow_runner is None:
        from src.agents.graph import run_agent_workflow

        workflow_runner = run_agent_workflow

    records: List[EvaluationRecord] = []
    for index, case in enumerate(cases):
        trace_id = None
        security_expectations = (
            SecurityExpectations.from_case(case)
            if include_security_metadata
            else None
        )
        ticket_state = build_ticket_state(
            {
                "request_id": f"offline-eval-{case.id}",
                "ticket_id": 10000 + index,
                "customer_id": case.customer_id or f"offline_eval_{case.id}",
                "subject": f"Evaluation case: {case.category}",
                "description": case.query,
                "kb_version": case.kb_version,
                "operator_role": "agent",
            }
        )
        # 模型服务抖动或熔断造成的失败不是行为问题：等熔断恢复后重跑这条用例。
        infrastructure_retries = 0
        while True:
            trace_performance: Dict[str, Any] = {"spans": [], "llm_calls": []}
            try:
                with capture_trace_performance() as trace_performance:
                    replay_attributes = {
                        "evaluation.case_id": case.id,
                        "evaluation.category": case.category,
                        "evaluation.kb_version": case.kb_version,
                        "request.id": ticket_state["request_id"],
                    }
                    if security_expectations is not None:
                        replay_attributes.update(
                            {
                                "evaluation.security.expected_attack": (
                                    security_expectations.expected_attack
                                ),
                                "evaluation.security.attack_type": (
                                    security_expectations.attack_type
                                ),
                            }
                        )
                    with observed_span(
                        tracer,
                        "evaluation.workflow_replay",
                        replay_attributes,
                    ) as span:
                        trace_id = get_current_trace_id()
                        output = await workflow_runner(ticket_state)
                        trace_id = output.get("trace_id") or trace_id
                        set_span_attributes(
                            span,
                            {
                                "evaluation.workflow_error_count": len(
                                    output.get("errors", [])
                                ),
                                "evaluation.workflow_path": output.get(
                                    "workflow_path", []
                                ),
                            },
                        )
            except Exception as exc:
                output = {
                    "suggested_response": "",
                    "context_citations": [],
                    "workflow_path": [],
                    "tool_calls": [],
                    "errors": [
                        f"Workflow replay failed: {exc.__class__.__name__}: "
                        f"{redact_text(str(exc))}"
                    ],
                }
            if (
                infrastructure_retries >= INFRASTRUCTURE_RETRY_LIMIT
                or not is_infrastructure_failure(output)
            ):
                break
            infrastructure_retries += 1
            await asyncio.sleep(settings.RESILIENCE_CIRCUIT_RECOVERY_SECONDS + 1.0)
        output["evaluation_infrastructure_retries"] = infrastructure_retries
        citations = output.get("context_citations", [])
        records.append(
            EvaluationRecord(
                case=case,
                response=output.get("suggested_response", ""),
                contexts=[_citation_field(citation, "text") for citation in citations],
                retrieved_sources=[
                    _citation_field(citation, "source") for citation in citations
                ],
                metrics={},
                agent_metrics={},
                agent_passed=False,
                agent_failures=[],
                trace_id=trace_id,
                workflow_path=list(output.get("workflow_path", [])),
                workflow_output=dict(output),
                workflow_errors=list(output.get("errors", [])),
                ticket_state=dict(ticket_state),
                trace_performance=dict(trace_performance),
            )
        )
    return records


def _citation_field(citation: Any, field: str) -> str:
    if isinstance(citation, dict):
        return str(citation.get(field, ""))
    return str(getattr(citation, field, ""))


async def score_records(records: Sequence[EvaluationRecord], engine: str) -> None:
    if engine == "local":
        for record in records:
            record.metrics = _local_scores(record)
        return
    if engine != "ragas":
        raise ValueError("engine must be either 'ragas' or 'local'.")
    _validate_ragas_environment()
    scores = await asyncio.to_thread(_run_ragas, records)
    if len(scores) != len(records):
        raise RuntimeError(
            f"Ragas returned {len(scores)} result rows for "
            f"{len(records)} evaluation cases."
        )
    for record, row in zip(records, scores):
        record.metrics = row


async def score_agent_records(records: Sequence[EvaluationRecord], engine: str) -> None:
    """使用 DeepEval 或本地代理指标评估 Workflow 行为。"""
    for record in records:
        result = await score_agent_behavior(
            query=record.case.query,
            reference_answer=record.case.reference_answer,
            contexts=record.contexts,
            workflow_output=record.workflow_output,
            expectations=AgentExpectations.from_mapping(record.case.agent_expectations),
            engine=engine,
        )
        record.agent_metrics = result.metrics
        record.agent_passed = result.passed
        record.agent_failures = result.failures


def _validate_ragas_environment() -> None:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or api_key.startswith("your-"):
        raise RuntimeError(
            "Ragas engine requires a valid OPENAI_API_KEY for its evaluator "
            "LLM and embeddings. "
            "Use --engine local only for an offline smoke test."
        )


def _run_ragas(records: Sequence[EvaluationRecord]) -> List[Dict[str, float]]:
    """Run the version-pinned Ragas 0.1 batch API and normalize its column names."""
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import (
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Ragas dependencies are unavailable or incompatible. "
            "Install requirements/eval.txt."
        ) from exc

    dataset = Dataset.from_dict(
        {
            "question": [record.case.query for record in records],
            "answer": [record.response for record in records],
            "contexts": [record.contexts for record in records],
            "ground_truth": [record.case.reference_answer for record in records],
        }
    )
    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        raise_exceptions=False,
    )
    rows = result.to_pandas().to_dict(orient="records")
    return [
        {
            "faithfulness": _finite_score(row.get("faithfulness")),
            "answer_relevancy": _finite_score(
                row.get("answer_relevancy", row.get("answer_relevance"))
            ),
            "context_precision": _finite_score(row.get("context_precision")),
            "context_recall": _finite_score(row.get("context_recall")),
        }
        for row in rows
    ]


def _finite_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(score, 4) if score == score else 0.0


def _local_scores(record: EvaluationRecord) -> Dict[str, float]:
    """Deterministic proxies for CI; these are explicitly not reported as Ragas."""
    return {
        "faithfulness": response_metrics_evaluator.calculate_faithfulness(
            record.contexts, record.response
        ),
        "answer_relevancy": response_metrics_evaluator.calculate_relevance(
            record.case.query, record.response
        ),
        "context_precision": retrieval_metrics_evaluator.calculate_precision(
            record.case.query, record.contexts
        ),
        "context_recall": retrieval_metrics_evaluator.calculate_recall(
            record.case.reference_answer, record.contexts
        ),
    }


def build_report(
    records: Sequence[EvaluationRecord],
    *,
    rag_engine: str,
    agent_engine: str,
    dataset_path: Path,
    execution_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    security_summary, security_results = evaluate_security_records(records)
    usage = _aggregate_workflow_usage(records)
    rag_aggregates = {
        metric: round(mean(record.metrics.get(metric, 0.0) for record in records), 4)
        for metric in METRIC_KEYS
    }
    agent_aggregates = {
        metric: round(
            mean(record.agent_metrics.get(metric, 0.0) for record in records), 4
        )
        for metric in AGENT_METRIC_KEYS
    }
    case_rows = []
    for record in records:
        expected = set(record.case.expected_sources)
        actual = set(record.retrieved_sources)
        case_rows.append(
            {
                **asdict(record.case),
                "response": record.response,
                "contexts": record.contexts,
                "retrieved_sources": record.retrieved_sources,
                "trace_id": record.trace_id,
                "workflow_replay": {
                    "request_id": f"offline-eval-{record.case.id}",
                    "workflow_path": record.workflow_path,
                    "tool_calls": record.workflow_output.get("tool_calls", []),
                    "routing": {
                        "department": record.workflow_output.get("department"),
                        "intent": record.workflow_output.get("intent"),
                        "priority": record.workflow_output.get("priority"),
                    },
                    "escalation_recommended": record.workflow_output.get(
                        "escalation_recommended", False
                    ),
                    "approval_required": record.workflow_output.get(
                        "approval_required", False
                    ),
                    "llm_usage": {
                        "tokens_input": _safe_int(
                            record.workflow_output.get("tokens_input", 0)
                        ),
                        "tokens_output": _safe_int(
                            record.workflow_output.get("tokens_output", 0)
                        ),
                        "cost_usd": _safe_float(
                            record.workflow_output.get("cost_usd", 0.0)
                        ),
                        "latency_seconds": _safe_float(
                            record.workflow_output.get("latency_seconds", 0.0)
                        ),
                    },
                    "errors": record.workflow_errors,
                },
                "rag_evaluation": {
                    "citation_hit": not expected or bool(expected & actual),
                    "metrics": record.metrics,
                },
                "agent_evaluation": {
                    "passed": record.agent_passed,
                    "metrics": record.agent_metrics,
                    "overall_score": round(mean(record.agent_metrics.values()), 4),
                    "failures": record.agent_failures,
                },
                "security_evaluation": case_result_payload(
                    security_results[record.case.id]
                ),
                "workflow_errors": record.workflow_errors,
            }
        )

    citation_hit_rate = round(
        mean(
            1.0 if row["rag_evaluation"]["citation_hit"] else 0.0 for row in case_rows
        ),
        4,
    )
    return {
        "schema_version": "3.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engines": {
            "rag": rag_engine,
            "agent": agent_engine,
            "security": "deterministic",
        },
        "dataset": str(dataset_path),
        "case_count": len(records),
        "execution": {
            "mode": "offline_evaluation",
            **sanitize_value(execution_metadata or {}),
            "usage": usage,
        },
        "rag_evaluation": {
            "aggregates": rag_aggregates,
            "citation_hit_rate": citation_hit_rate,
        },
        "agent_evaluation": {
            "aggregates": agent_aggregates,
            "overall_score": round(
                mean(mean(record.agent_metrics.values()) for record in records),
                4,
            ),
            "pass_rate": round(
                mean(1.0 if record.agent_passed else 0.0 for record in records),
                4,
            ),
            "trace_linked_cases": sum(
                1 for record in records if record.trace_id is not None
            ),
        },
        "security_evaluation": security_summary,
        "cases": case_rows,
    }


def write_report(report: Dict[str, Any], output_dir: Path) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "evaluation_latest.json"
    markdown_path = output_dir / "evaluation_latest.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def _render_markdown(report: Dict[str, Any]) -> str:
    rag = report["rag_evaluation"]
    agent = report["agent_evaluation"]
    security = report["security_evaluation"]
    execution = report["execution"]
    usage = execution["usage"]
    rag_aggregates = rag["aggregates"]
    agent_aggregates = agent["aggregates"]
    lines = [
        "# RAG + Agent + Security Evaluation 统一评测报告",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- RAG 引擎：`{report['engines']['rag']}`",
        f"- Agent 引擎：`{report['engines']['agent']}`",
        f"- Security 引擎：`{report['engines']['security']}`",
        f"- 执行模式：`{execution['mode']}`",
        f"- LLM Provider：`{execution.get('llm_provider', 'unspecified')}`",
        f"- LLM Model：`{execution.get('llm_model', 'unspecified')}`",
        f"- 数据集：`{report['dataset']}`",
        f"- 用例数：`{report['case_count']}`",
        (
            f"- Workflow Token：`{usage['tokens_input']} input / "
            f"{usage['tokens_output']} output`，估算成本："
            f"`${usage['cost_usd']:.6f}`"
        ),
        "",
        "## RAG Evaluation",
        "",
        (
            "| Faithfulness | Answer Relevancy | Context Precision | "
            "Context Recall | Citation Hit Rate |"
        ),
        "|---:|---:|---:|---:|---:|",
        (
            f"| {rag_aggregates['faithfulness']:.4f} "
            f"| {rag_aggregates['answer_relevancy']:.4f} "
            f"| {rag_aggregates['context_precision']:.4f} "
            f"| {rag_aggregates['context_recall']:.4f} "
            f"| {rag['citation_hit_rate']:.4f} |"
        ),
        "",
        "## Agent Evaluation",
        "",
        (
            "| Overall | Task Completion | Policy | Routing | Tool | Workflow | Escalation | "
            "Pass Rate | Trace Linked |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {agent['overall_score']:.4f} "
            f"| {agent_aggregates['task_completion']:.4f} "
            f"| {agent_aggregates['policy_compliance']:.4f} "
            f"| {agent_aggregates['routing_correctness']:.4f} "
            f"| {agent_aggregates['tool_correctness']:.4f} "
            f"| {agent_aggregates['workflow_correctness']:.4f} "
            f"| {agent_aggregates['escalation_correctness']:.4f} "
            f"| {agent['pass_rate']:.4f} | {agent['trace_linked_cases']} |"
        ),
        "",
        "## Security Evaluation",
        "",
        (
            "| Precision | Recall | F1 | Accuracy | False Positive Rate | "
            "Case Pass Rate |"
        ),
        "|---:|---:|---:|---:|---:|---:|",
        (
            f"| {_format_metric(security['detection']['precision'])} "
            f"| {_format_metric(security['detection']['recall'])} "
            f"| {_format_metric(security['detection']['f1_score'])} "
            f"| {_format_metric(security['detection']['accuracy'])} "
            f"| {_format_metric(security['detection']['false_positive_rate'])} "
            f"| {_format_metric(security['case_pass_rate'])} |"
        ),
        "",
        (
            "| TP | FP | TN | FN | Block Automation | Safe Short Circuit | "
            "Context Isolation | Human Intervention | Critical Risk |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {security['confusion_matrix']['true_positive']} "
            f"| {security['confusion_matrix']['false_positive']} "
            f"| {security['confusion_matrix']['true_negative']} "
            f"| {security['confusion_matrix']['false_negative']} "
            f"| {_format_metric(security['disposition']['block_automation_rate'])} "
            f"| {_format_metric(security['disposition']['safe_short_circuit_rate'])} "
            f"| {_format_metric(security['disposition']['context_isolation_rate'])} "
            f"| {_format_metric(security['disposition']['human_intervention_rate'])} "
            f"| {_format_metric(security['disposition']['critical_risk_rate'])} |"
        ),
        "",
        "## 用例明细与 Trace",
        "",
        "| ID | RAG | Agent | Security | Workflow | OTel Trace ID | Failure |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for row in report["cases"]:
        rag_metrics = row["rag_evaluation"]["metrics"]
        agent_metrics = row["agent_evaluation"]["metrics"]
        security_result = row["security_evaluation"]
        path = " → ".join(row["workflow_replay"]["workflow_path"]) or "-"
        failures = (
            "; ".join(
                [
                    *row["agent_evaluation"]["failures"],
                    *security_result["failures"],
                ]
            )
            or "-"
        )
        lines.append(
            f"| {row['id']} | {mean(rag_metrics.values()):.4f} "
            f"| {mean(agent_metrics.values()):.4f} "
            f"| {'PASS' if security_result['passed'] else 'FAIL'} | {path} "
            f"| `{row['trace_id'] or 'unavailable'}` | {failures} |"
        )
    lines.extend(
        [
            "",
            (
                "> `ragas` 与 `deepeval` 是正式离线评测引擎；`local` 仅用于无网络 "
                "CI 回归烟测。OTel 未启用时 Trace ID 显示为 `unavailable`。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _format_metric(value: Any) -> str:
    """统一格式化安全指标，无分母时明确标记 N/A。"""
    return "N/A" if value is None else f"{float(value):.4f}"


def _aggregate_workflow_usage(
    records: Sequence[EvaluationRecord],
) -> Dict[str, Any]:
    """汇总 Workflow 中真实或 Mock LLM 的 Token、成本和延迟。"""
    tokens_input = sum(
        _safe_int(record.workflow_output.get("tokens_input", 0)) for record in records
    )
    tokens_output = sum(
        _safe_int(record.workflow_output.get("tokens_output", 0)) for record in records
    )
    cost_usd = sum(
        _safe_float(record.workflow_output.get("cost_usd", 0.0)) for record in records
    )
    latency_seconds = sum(
        _safe_float(record.workflow_output.get("latency_seconds", 0.0))
        for record in records
    )
    return {
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "tokens_total": tokens_input + tokens_output,
        "cost_usd": round(cost_usd, 6),
        "workflow_latency_seconds": round(latency_seconds, 4),
        "average_workflow_latency_seconds": (
            round(latency_seconds / len(records), 4) if records else 0.0
        ),
    }


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


async def run_offline_evaluation(
    dataset_path: Path,
    output_dir: Path,
    *,
    rag_engine: str = "ragas",
    agent_engine: str = "deepeval",
    engine: Optional[str] = None,
    limit: Optional[int] = None,
    case_ids: Optional[Sequence[str]] = None,
    execution_metadata: Optional[Dict[str, Any]] = None,
    workflow_runner: Optional[WorkflowRunner] = None,
) -> Dict[str, Path]:
    # 兼容第一阶段调用；local 会同时切换两个评测器。
    if engine is not None:
        rag_engine = engine
        if engine == "local":
            agent_engine = "local"
    cases = load_evaluation_dataset(dataset_path)
    if case_ids is not None:
        requested = list(case_ids)
        if len(requested) != len(set(requested)):
            raise ValueError("case_ids must not contain duplicates.")
        case_index = {case.id: case for case in cases}
        missing = [case_id for case_id in requested if case_id not in case_index]
        if missing:
            raise ValueError("Unknown evaluation case ids: " + ", ".join(missing))
        cases = [case_index[case_id] for case_id in requested]
        if not cases:
            raise ValueError("case_ids must select at least one evaluation case.")
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be greater than zero.")
        cases = cases[:limit]
    records = await collect_workflow_records(cases, workflow_runner)
    await score_records(records, rag_engine)
    await score_agent_records(records, agent_engine)
    report = build_report(
        records,
        rag_engine=rag_engine,
        agent_engine=agent_engine,
        dataset_path=dataset_path,
        execution_metadata=execution_metadata,
    )
    return write_report(report, output_dir)
