"""独立、确定性的 Agent Risk Engine。"""

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from src.config import settings
from src.models.intents import IntentType, normalize_intent


_HIGH_RISK_INTENTS = frozenset(
    {IntentType.BILLING_DISPUTE, IntentType.ORDER_CANCELLATION}
)


@dataclass(frozen=True)
class RiskAssessment:
    """保存风险等级、分数、原因和自动化处置建议。"""

    level: str
    score: float
    reasons: tuple[str, ...]
    requires_human: bool
    block_automation: bool

    def state_updates(self) -> dict[str, Any]:
        return {
            "risk_level": self.level,
            "risk_score": self.score,
            "risk_reasons": list(self.reasons),
            "risk_requires_human": self.requires_human,
            "risk_block_automation": self.block_automation,
        }


class RiskEngine:
    """综合安全、业务、分类置信度和回复质量计算风险。"""

    def assess(
        self,
        state: Mapping[str, Any],
        *,
        stage: Literal["input", "output", "final"],
    ) -> RiskAssessment:
        """按处理阶段聚合已有风险信号，分数只升不降。"""
        score = float(state.get("risk_score", 0.0) or 0.0)
        reasons = set(str(item) for item in state.get("risk_reasons", []))
        errors = " ".join(str(item) for item in state.get("errors", []))
        security_threat = bool(state.get("security_threat_detected")) or (
            "Security threat" in errors
        )
        semantic_label = str(state.get("semantic_guard_label", "not_run")).lower()
        semantic_degraded = bool(state.get("semantic_guard_degraded", False))
        semantic_checks = list(state.get("semantic_guard_checks", []))
        degraded_untrusted_context = any(
            check.get("status") == "error"
            and check.get("source") in {"tool_result", "rag_document"}
            for check in semantic_checks
            if isinstance(check, Mapping)
        )

        if security_threat:
            score = max(
                score, float(state.get("security_risk_score", 1.0) or 1.0), 0.95
            )
            reasons.add("security_threat_detected")

        if semantic_label == "unsafe":
            score = max(score, 0.95)
            reasons.add("semantic_safety_unsafe")
        elif semantic_label == "controversial":
            score = max(score, 0.75)
            reasons.add("semantic_safety_controversial")

        if semantic_degraded:
            score = max(score, 0.75)
            reasons.add("semantic_guard_degraded")
        if degraded_untrusted_context:
            score = max(score, 0.85)
            reasons.add("untrusted_context_guard_unavailable")

        degradation_level = str(state.get("degradation_level", "none")).lower()
        if degradation_level == "partial":
            score = max(score, 0.45)
            reasons.add("dependency_partially_degraded")
        elif degradation_level == "human_required":
            score = max(score, 0.82)
            reasons.add("dependency_requires_human")
        elif degradation_level == "failed":
            score = max(score, 0.9)
            reasons.add("dependency_failed")

        priority = str(state.get("priority", "medium")).lower()
        sentiment = str(state.get("sentiment", "neutral")).lower()
        intent = normalize_intent(state.get("intent"))
        analyzer_confidence = self._bounded_score(
            state.get("analyzer_confidence", 1.0), default=1.0
        )

        if priority == "urgent":
            score = max(score, 0.85)
            reasons.add("urgent_priority")
        elif priority == "high":
            score = max(score, 0.55)
            reasons.add("high_priority")

        if sentiment == "negative" and priority == "high":
            score = max(score, 0.75)
            reasons.add("negative_high_priority")

        # 只读咨询由 Analyzer 用确定性规则标记，不按高风险业务域处理。
        if intent in _HIGH_RISK_INTENTS and not state.get("read_only_inquiry"):
            score = max(score, 0.78)
            reasons.add("high_risk_business_intent")

        if analyzer_confidence < settings.RISK_LOW_CONFIDENCE_THRESHOLD:
            score = max(score, 0.8)
            reasons.add("low_analyzer_confidence")
        elif analyzer_confidence < 0.8:
            score = max(score, 0.5)
            reasons.add("medium_analyzer_confidence")

        if stage in {"output", "final"}:
            if bool(state.get("response_requires_human", False)):
                score = max(score, 0.82)
                reasons.add("authoritative_answer_unavailable")
            qa_score = self._bounded_score(state.get("qa_score", 1.0), default=1.0)
            if bool(state.get("hallucination_detected", False)):
                score = max(score, 0.9)
                reasons.add("hallucination_detected")
            if qa_score < settings.RISK_QA_SCORE_THRESHOLD:
                score = max(score, 0.82 if qa_score >= 0.5 else 0.9)
                reasons.add("qa_score_below_threshold")

        non_security_errors = [
            item
            for item in state.get("errors", [])
            if "Security threat" not in str(item)
        ]
        if non_security_errors:
            score = max(score, 0.55)
            reasons.add("workflow_error")

        score = round(min(max(score, 0.0), 1.0), 4)
        if score >= settings.RISK_CRITICAL_THRESHOLD:
            level = "critical"
        elif score >= settings.RISK_HIGH_THRESHOLD:
            level = "high"
        elif score >= settings.RISK_MEDIUM_THRESHOLD:
            level = "medium"
        else:
            level = "low"

        return RiskAssessment(
            level=level,
            score=score,
            reasons=tuple(sorted(reasons)),
            requires_human=level in {"high", "critical"},
            block_automation=security_threat or semantic_label == "unsafe",
        )

    @staticmethod
    def _bounded_score(value: Any, *, default: float) -> float:
        try:
            return min(max(float(value), 0.0), 1.0)
        except (TypeError, ValueError):
            return default


risk_engine = RiskEngine()
