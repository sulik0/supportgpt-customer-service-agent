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
from src.agents.classification import normalize_answers


class DecisionService:
    """把通用 DecisionProvider 限制在客服领域的封闭决策中。"""

    def __init__(self, provider: DecisionProvider) -> None:
        self.provider = provider

    async def classify_ticket(self, text: str) -> TicketIntentDecision:
        """只输出 Jev 的逐项答案；没把握的项为 None，处理结果由分类模块查表。"""
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
        threshold = settings.JEV_INTENT_CONFIDENCE_THRESHOLD
        noul_threshold = settings.JEV_NOUL_THRESHOLD
        raw: dict[str, Any] = {}
        for key, question in intent_questions().items():
            answer = result.answers.get(key)
            if answer is None or answer.kind != question["type"]:
                continue
            if answer.kind == "choice":
                if float(answer.confidence or 0.0) >= threshold:
                    raw[key] = str(answer.value)
            elif answer.kind == "noul":
                probability = _bounded(answer.value)
                # 介于两个阈值之间算没把握。
                if probability >= noul_threshold:
                    raw[key] = True
                elif probability <= 1.0 - noul_threshold:
                    raw[key] = False
        answers = normalize_answers(raw)
        if all(value is None for value in answers.values()):
            return TicketIntentDecision(
                result=result,
                accepted=False,
                fallback_reason="missing_intent_answers",
            )
        unsure = [key for key, value in answers.items() if value is None]
        return TicketIntentDecision(
            result=result,
            accepted=True,
            fallback_reason=f"unsure:{','.join(unsure)}" if unsure else None,
            analysis=answers,
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
