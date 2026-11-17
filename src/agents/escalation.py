import time
import logging
from typing import Dict, Any

from src.observability.metrics import (
    AGENT_EXECUTION_DURATION_SECONDS,
    RISK_ASSESSMENTS_TOTAL,
    RISK_SCORE_HISTOGRAM,
    TICKET_ESCALATIONS_TOTAL,
)
from src.risk.engine import risk_engine

logger = logging.getLogger("supportgpt.agents.escalation")


_SLA_HOURS = {"urgent": 2.0, "high": 12.0, "medium": 24.0, "low": 48.0}
_REASON_TEXT = {
    "security_threat_detected": "Security guardrails violation block.",
    "semantic_safety_unsafe": "Semantic safety guard flagged the request.",
    "dependency_failed": "A required dependency failed.",
    "dependency_requires_human": "A required dependency needs human verification.",
    "untrusted_context_guard_unavailable": "Untrusted context could not be checked.",
    "hallucination_detected": "AI quality assurance detected a hallucination.",
    "qa_score_below_threshold": "AI quality assurance score below threshold.",
    "authoritative_answer_unavailable": (
        "Authoritative business guidance is unavailable; human review required."
    ),
}


def _escalation_reason(state: Dict[str, Any], reasons: tuple[str, ...]) -> str:
    """转人工原因：先列安全、依赖和质量原因，再列处理原则。"""
    for key, text in _REASON_TEXT.items():
        if key in reasons:
            return text
    classification = state.get("classification") or {}
    if "handling_human" in reasons or "handling_answer_then_review" in reasons:
        mode = (
            "answer then human review"
            if "handling_answer_then_review" in reasons
            else "human handling"
        )
        return (
            f"Handling principle {classification.get('principle')} requires {mode}."
        )
    return f"Risk Engine policy threshold: {', '.join(reasons) or 'unspecified'}."


class EscalationAgent:
    """把 Risk Engine 的结论落成是否升级人工，并按优先级给出处理时限。"""

    async def evaluate(self, state: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(
            f"Escalation Agent started verifying state rules for ticket: {state.get('ticket_id')}"
        )

        priority = str(state.get("priority", "medium")).lower()
        department = state.get("department", "general")
        assessment = risk_engine.assess(state, stage="final")

        # 优先级只决定人工处理时限（第 22 条）。
        sla_hours = _SLA_HOURS.get(priority, 24.0)

        # 是否转人工只看 Risk Engine 的结论，这里不再有第二套规则。
        escalate = assessment.requires_human
        reason = _escalation_reason(state, assessment.reasons) if escalate else ""

        # 3. Handle telemetry updates
        try:
            RISK_ASSESSMENTS_TOTAL.add(
                1,
                {
                    "level": assessment.level,
                    "requires_human": str(assessment.requires_human).lower(),
                },
            )
            RISK_SCORE_HISTOGRAM.record(
                assessment.score,
                {"stage": "final", "level": assessment.level},
            )
        except Exception:
            logger.debug("Unable to record Risk Engine metrics")
        if escalate:
            TICKET_ESCALATIONS_TOTAL.add(
                1, {"department": department, "priority": priority}
            )
            logger.info(f"Escalation recommended for ticket: {reason}")

        duration = time.time() - start_time
        AGENT_EXECUTION_DURATION_SECONDS.record(
            duration, {"agent_name": "escalation_agent"}
        )

        return {
            **state,
            **assessment.state_updates(),
            "escalation_recommended": escalate,
            "escalation_reason": reason if escalate else None,
            "sla_hours": sla_hours,
        }


escalation_agent = EscalationAgent()
