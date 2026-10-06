"""SupportGPT 领域决策问题与 Jev 结果映射。"""

from __future__ import annotations

from typing import Any, Sequence

from src.config import settings
from src.decision.models import QAReviewDecision, TicketIntentDecision
from src.decision.provider import DecisionProvider, decision_provider
from src.decision.questions import (
    INTENT_QUESTION_SET_VERSION,
    QA_QUESTION_SET_VERSION,
    intent_questions,
    qa_questions,
)
from src.models.intents import IntentType, intent_defaults, normalize_intent


_NEGATIVE_INTENTS = {
    IntentType.BILLING_DISPUTE,
    IntentType.OUTAGE_REPORT,
    IntentType.ORDER_CANCELLATION,
    IntentType.ACCOUNT_SUPPORT,
}


class DecisionService:
    """把通用 DecisionProvider 限制在客服领域的封闭决策中。"""

    def __init__(self, provider: DecisionProvider) -> None:
        self.provider = provider

    async def classify_ticket(self, text: str) -> TicketIntentDecision:
        """只输出统一 Intent，部门和优先级继续由 Taxonomy 推导。"""
        result = await self.provider.evaluate(
            state={"current_ticket": text},
            questions=intent_questions(),
            operation="intent_classification",
            question_set_version=INTENT_QUESTION_SET_VERSION,
        )
        if not result.available:
            return TicketIntentDecision(
                result=result,
                accepted=False,
                fallback_reason=result.error_code or "provider_unavailable",
            )
        answer = result.answers.get("intent")
        operation_mode = result.answers.get("operation_mode")
        needs_clarification = result.answers.get("needs_clarification")
        if (
            answer is None
            or answer.kind != "choice"
            or operation_mode is None
            or operation_mode.kind != "choice"
            or needs_clarification is None
            or needs_clarification.kind != "noul"
        ):
            return TicketIntentDecision(
                result=result,
                accepted=False,
                fallback_reason="missing_intent_answers",
            )
        raw_intent = str(answer.value)
        if raw_intent not in IntentType.values():
            return TicketIntentDecision(
                result=result,
                accepted=False,
                fallback_reason="unknown_intent",
            )
        if str(operation_mode.value) not in {"action", "information", "unclear"}:
            return TicketIntentDecision(
                result=result,
                accepted=False,
                fallback_reason="unknown_operation_mode",
            )
        if _bounded(needs_clarification.value) >= settings.JEV_NOUL_THRESHOLD:
            return TicketIntentDecision(
                result=result,
                accepted=False,
                fallback_reason="needs_clarification",
            )
        confidence = min(
            float(answer.confidence or 0.0),
            float(operation_mode.confidence or 0.0),
        )
        if confidence < settings.JEV_INTENT_CONFIDENCE_THRESHOLD:
            return TicketIntentDecision(
                result=result,
                accepted=False,
                fallback_reason="low_confidence",
            )
        intent = normalize_intent(raw_intent)
        defaults = intent_defaults(intent)
        scope = result.answers.get("support_scope")
        outside_support = bool(
            intent == IntentType.INFORMATION_REQUEST
            and str(operation_mode.value) == "information"
            and scope is not None
            and scope.kind == "choice"
            and scope.value == "benign_out_of_scope"
            and float(scope.confidence or 0.0)
            >= settings.JEV_INTENT_CONFIDENCE_THRESHOLD
        )
        return TicketIntentDecision(
            result=result,
            accepted=True,
            analysis={
                "intent": intent,
                "priority": defaults.priority,
                "department": defaults.department,
                "sentiment": (
                    "negative"
                    if intent in _NEGATIVE_INTENTS
                    else "positive" if intent is IntentType.FEEDBACK else "neutral"
                ),
                "confidence_score": confidence,
                **(
                    {
                        "request_scope": "out_of_scope",
                        "scope_reason": "no_support_capability",
                    }
                    if outside_support
                    else {}
                ),
            },
        )

    async def judge_response(
        self,
        *,
        query: str,
        evidence: Sequence[str],
        response: str,
    ) -> QAReviewDecision:
        """将 Grounding、完成度和承诺风险一次并行评判。"""
        result = await self.provider.evaluate(
            state={
                "customer_question": query,
                "evidence": list(evidence),
                "answer": response,
            },
            questions=qa_questions(),
            operation="response_judgment",
            question_set_version=QA_QUESTION_SET_VERSION,
        )
        if not result.available:
            return QAReviewDecision(
                result=result,
                accepted=False,
                fallback_reason=result.error_code or "provider_unavailable",
            )
        required = {
            "grounding",
            "task_completion",
            "citation_status",
            "unsupported_commitment",
            "needs_human_review",
        }
        if not required.issubset(result.answers):
            return QAReviewDecision(
                result=result,
                accepted=False,
                fallback_reason="missing_qa_answers",
            )

        grounding = result.answers["grounding"]
        completion = result.answers["task_completion"]
        citation = result.answers["citation_status"]
        confidences = [
            float(answer.confidence or 0.0)
            for answer in (grounding, completion, citation)
        ]
        if min(confidences) < settings.JEV_QA_CONFIDENCE_THRESHOLD:
            return QAReviewDecision(
                result=result,
                accepted=False,
                fallback_reason="low_confidence",
            )

        grounding_label = str(grounding.value)
        citation_label = str(citation.value)
        if grounding_label not in {
            "supported",
            "insufficient_evidence",
            "unsupported",
        } or citation_label not in {"verified", "unverified", "not_applicable"}:
            return QAReviewDecision(
                result=result,
                accepted=False,
                fallback_reason="unknown_qa_label",
            )
        try:
            raw_completion_score = float(completion.value)
        except (TypeError, ValueError):
            raw_completion_score = -1.0
        if not 0.0 <= raw_completion_score <= 3.0:
            return QAReviewDecision(
                result=result,
                accepted=False,
                fallback_reason="invalid_completion_score",
            )
        commitment_probability = _bounded(
            result.answers["unsupported_commitment"].value
        )
        human_probability = _bounded(result.answers["needs_human_review"].value)
        completion_score = _bounded(raw_completion_score / 3.0)
        grounding_score = {
            "supported": 1.0,
            "insufficient_evidence": 0.4,
            "unsupported": 0.0,
        }.get(grounding_label, 0.0)
        policy_score = 1.0 - commitment_probability
        citation_score = 0.0 if citation_label == "unverified" else 1.0
        qa_score = round(
            0.45 * grounding_score
            + 0.3 * completion_score
            + 0.15 * policy_score
            + 0.1 * citation_score,
            4,
        )
        hallucinated = (
            grounding_label == "unsupported"
            or commitment_probability >= settings.JEV_NOUL_THRESHOLD
        )
        if hallucinated:
            qa_score = min(qa_score, 0.49)
        return QAReviewDecision(
            result=result,
            accepted=True,
            evaluation={
                "score": qa_score,
                "hallucination_detected": hallucinated,
                "citation_verified": citation_label == "verified",
                "response_grounded": grounding_label == "supported"
                and not hallucinated,
                "response_requires_human": (
                    human_probability >= settings.JEV_NOUL_THRESHOLD
                    or grounding_label == "insufficient_evidence"
                    or citation_label == "unverified"
                    or hallucinated
                ),
            },
        )


def _bounded(value: Any) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


decision_service = DecisionService(decision_provider)
