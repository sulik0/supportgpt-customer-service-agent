"""从持久化运行记录构造公开演示摘要。"""

import math

from src.models.db_models import AgentRun
from src.models.intents import IntentType
from src.models.schemas import PublicRunInsightResponse
from src.observability.sanitization import redact_text
from src.skills.definitions import ALL_TOOL_NAMES


PUBLIC_NODE_NAMES = frozenset(
    {
        "analyzer",
        "ticket_analyzer",
        "skill_selector",
        "context_enrichment",
        "tooling",
        "tool_call",
        "retriever",
        "resolver",
        "llm_generation",
        "qa",
        "escalation",
        "approval_gate",
        "human_approval",
    }
)
PUBLIC_REVIEW_REASONS = frozenset(
    {
        "security_threat_detected",
        "semantic_safety_unsafe",
        "semantic_safety_controversial",
        "semantic_guard_degraded",
        "untrusted_context_guard_unavailable",
        "dependency_partially_degraded",
        "dependency_requires_human",
        "dependency_failed",
        "urgent_priority",
        "high_priority",
        "negative_high_priority",
        "high_risk_business_intent",
        "low_analyzer_confidence",
        "medium_analyzer_confidence",
        "authoritative_answer_unavailable",
        "hallucination_detected",
        "qa_score_below_threshold",
        "workflow_error",
        "manual_review_policy",
    }
)


def _number(value, *, maximum=None):
    """缺失或非法观测值不冒充零。"""
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def build_public_run_insight(run: AgentRun) -> PublicRunInsightResponse:
    """逐字段投影，禁止透传内部对象和自由文本原因。"""
    path = [
        node for node in (run.workflow_path or []) if _enum(node, PUBLIC_NODE_NAMES)
    ]
    snapshot = run.execution_snapshot
    analyzer = snapshot.analyzer_result if snapshot else {}
    analyzer = analyzer if isinstance(analyzer, dict) else {}
    review = run.review_context
    reasons = (
        [
            reason
            for reason in (review.risk_reasons or [])
            if _enum(reason, PUBLIC_REVIEW_REASONS)
        ]
        if review
        else []
    )
    if review and review.response_requires_human:
        reasons.append("authoritative_answer_unavailable")
    if run.workflow_errors:
        reasons.append("workflow_error")
    if run.approval_required and not reasons:
        reasons.append("manual_review_policy")
    has_analyzer = any(node in path for node in ("analyzer", "ticket_analyzer"))
    skill = run.skill_selection
    tokens = [_number(run.tokens_input), _number(run.tokens_output)]
    return PublicRunInsightResponse(
        workflow_path=path,
        intent=(
            _enum(analyzer.get("intent"), IntentType.values()) if has_analyzer else None
        ),
        department=(
            _enum(
                analyzer.get("department"),
                {"billing", "technical", "shipping", "general"},
            )
            if has_analyzer
            else None
        ),
        priority=(
            _enum(analyzer.get("priority"), {"low", "medium", "high", "urgent"})
            if has_analyzer
            else None
        ),
        analyzer_confidence=(
            _number(analyzer.get("confidence"), maximum=1) if has_analyzer else None
        ),
        skill_selection=(
            {
                "skill_name": redact_text(skill.skill_name),
                "skill_version": redact_text(skill.skill_version),
            }
            if skill
            else None
        ),
        tool_calls=[
            {
                "tool_name": call["tool_name"],
                "status": _enum(
                    call.get("status"),
                    {"success", "error", "failed", "denied", "blocked"},
                )
                or "unknown",
                "latency_ms": _number(call.get("latency_ms")),
            }
            for call in (run.tool_calls or [])
            if isinstance(call, dict) and _enum(call.get("tool_name"), ALL_TOOL_NAMES)
        ],
        citations=[
            {"source": redact_text(citation["source"])[:200]}
            for citation in (run.citations or [])
            if isinstance(citation, dict) and isinstance(citation.get("source"), str)
        ],
        qa_score=_number(run.qa_score, maximum=1) if "qa" in path else None,
        risk_level=(
            _enum(review.risk_level, {"low", "medium", "high", "critical"})
            if review
            else None
        ),
        review_reasons=list(dict.fromkeys(reasons)),
        approval_required=run.approval_required,
        latency_seconds=_number(run.latency_seconds),
        tokens_total=(
            int(sum(tokens)) if all(value is not None for value in tokens) else None
        ),
    )
