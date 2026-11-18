"""输入与间接 Prompt Injection 的统一阻断策略。"""

from typing import Any, Iterable, Mapping

from src.agents.signals import security_error
from src.models.language import reply_language
from src.risk.engine import risk_engine


def build_security_block(
    state: Mapping[str, Any],
    *,
    threat_type: str,
    source: str,
    risk_score: float,
    findings: Iterable[str],
) -> dict[str, Any]:
    """生成不暴露敏感原文的统一安全阻断状态。"""
    user_text = f"{state.get('subject', '')} {state.get('description', '')}"
    use_chinese = reply_language(user_text) == "zh"
    response = (
        "出于系统安全限制，我无法执行该请求，已转交人工审核。"
        if use_chinese
        else "I cannot fulfill this request due to system security constraints. It has been sent for human review."
    )
    safe_findings = sorted({str(item)[:80] for item in findings if item})
    blocked = {
        **state,
        "errors": list(state.get("errors", []))
        + [security_error(threat_type, source)],
        "sentiment": "negative",
        "priority": "urgent",
        "security_threat_detected": True,
        "security_risk_score": min(max(float(risk_score), 0.0), 1.0),
        "security_source": source,
        "security_findings": safe_findings,
        "tool_context": {},
        "context_citations": [],
        "escalation_recommended": True,
        "escalation_reason": "Security violation block",
        "suggested_response": response,
    }
    assessment = risk_engine.assess(blocked, stage="input")
    return {**blocked, **assessment.state_updates()}
