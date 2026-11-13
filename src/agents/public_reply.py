"""公开咨询入口的回复策略：转人工时给用户看什么。

Workflow 的升级与审批结论不在这里改，这里只决定终端用户看到的文案，
以及只读咨询能否在人工复核前先看到 AI 的初步答复。
"""

import re
from typing import Any, Mapping

from src.config import settings
from src.models.intents import IntentType, normalize_intent

# QA 把幻觉告警也写进 errors；它是质量问题，不是服务故障。
_QA_ALERT_PREFIX = "QA score alert"

# 只因业务域默认规则（账务高优先级、故障紧急）而转人工的风险原因。
# 出现其他原因（置信度、质量、依赖降级、安全等）就不能先答。
_DOMAIN_ONLY_REASONS = frozenset(
    {
        "high_priority",
        "urgent_priority",
        "high_risk_business_intent",
        "negative_high_priority",
    }
)
_PRELIMINARY_INTENTS = frozenset(
    {IntentType.BILLING_DISPUTE, IntentType.OUTAGE_REPORT}
)
# 要求实际办理、表达争议或越权的说法：这些必须等人工，不展示草稿。
_ACTION_OR_DISPUTE = re.compile(
    r"我要退|要求退|申请退|办理退|帮我退|给我退|直接退|立即退|马上退|退给我|退我|"
    r"退\s*\d|退款工具|无需审批|不用审批|跳过审批|免审批|"
    r"赔偿|赔付|补偿|投诉|举报|曝光|起诉|律师|消协|12315|"
    r"取消|撤销|撤回|拒付|争议|不认可|不承认|"
    r"多扣|乱扣|错扣|重复扣|扣错|盗刷|被骗|欺骗|骗子|"
    r"\b(?:i (?:want|need|demand|would like)|give me|issue|process|"
    r"please (?:refund|cancel|reverse|credit))\b.{0,30}"
    r"\b(?:refund|money back|chargeback|credit|compensation)\b|"
    r"\b(?:want|need|demand|request(?:ing)?) (?:a |my |the |full )*refund\b|"
    r"\b(?:chargeback|dispute|compensat\w+|complain\w*|lawsuit|lawyer|sue|fraud|scam|"
    r"cancel|reverse|overcharged|charged twice|double charged|without approval|"
    r"skip approval|do not execute|don't execute)\b",
    re.IGNORECASE,
)
# 账务类必须是明确的提问或查询，陈述句和指令不算只读咨询。
_QUESTION = re.compile(
    r"[?？]|吗|么|多少|多久|什么|怎么|如何|是否|能否|可否|哪|几|查|确认|核实|"
    r"\b(?:what|when|where|which|how|why|is|are|was|were|does|do|did|can|could|"
    r"may|will|would|should|check|confirm|verify|look up|tell me|show me)\b",
    re.IGNORECASE,
)


def _is_chinese(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text or ""))


def _service_errors(agent_output: Mapping[str, Any]) -> list[str]:
    """真实的处理异常，不含 QA 幻觉告警和安全拦截记录。"""
    return [
        str(item)
        for item in agent_output.get("errors", []) or []
        if not str(item).startswith(_QA_ALERT_PREFIX)
        and "Security threat" not in str(item)
    ]


def preliminary_answer_allowed(
    agent_output: Mapping[str, Any], customer_text: str
) -> bool:
    """只读的账务咨询和故障求助：人工复核照常进行，但用户先看到 AI 的初步答复。

    放行条件全部是确定性判断：转人工只因业务域默认规则、质量检查通过、
    没有任何异常或安全信号，且用户没有要求实际办理或表达争议。
    """
    if not settings.PUBLIC_PRELIMINARY_ANSWER_ENABLED:
        return False
    if not str(agent_output.get("suggested_response") or "").strip():
        return False
    intent = normalize_intent(agent_output.get("intent"))
    if intent not in _PRELIMINARY_INTENTS:
        return False
    if (
        agent_output.get("security_threat_detected")
        or agent_output.get("risk_block_automation")
        or agent_output.get("errors")
        or str(agent_output.get("degradation_level", "none")).lower() != "none"
        or agent_output.get("hallucination_detected")
        or agent_output.get("response_requires_human")
        or agent_output.get("semantic_guard_degraded")
        or str(agent_output.get("semantic_guard_label", "not_run")).lower()
        in {"unsafe", "controversial"}
    ):
        return False
    try:
        qa_score = float(agent_output.get("qa_score"))
    except (TypeError, ValueError):
        return False
    if qa_score < settings.PUBLIC_PRELIMINARY_ANSWER_MIN_QA_SCORE:
        return False
    reasons = {str(item) for item in agent_output.get("risk_reasons", []) or []}
    if not reasons or not reasons <= _DOMAIN_ONLY_REASONS:
        return False
    text = " ".join(str(customer_text or "").split())
    if not text or _ACTION_OR_DISPUTE.search(text):
        return False
    if intent == IntentType.BILLING_DISPUTE and not _QUESTION.search(text):
        return False
    return True


def preliminary_answer_note(customer_text: str) -> str:
    """初步答复下方的说明：人工复核仍在进行。"""
    if _is_chinese(customer_text):
        return "以上是根据现有资料给出的初步答复，工单已同步转交人工客服复核；如有出入，以人工回复为准。"
    return (
        "This is a preliminary answer based on the information available. "
        "Your ticket has also been sent to a support specialist for review; "
        "if anything differs, their reply takes precedence."
    )


_TEXTS = {
    "risk_review": {
        "zh": (
            "我已经收到您的请求。出于安全考虑，这条请求不会自动处理，已转交人工客服核验。",
            "为保护您的账户和业务权益，客服人员确认后会继续处理。",
        ),
        "en": (
            "We have received your request. For security reasons it was not processed "
            "automatically and has been sent to a support specialist for verification.",
            "A specialist will continue once the request has been verified.",
        ),
    },
    "processing_exception": {
        "zh": (
            "我已经收到您的问题，但部分处理服务暂时不可用。工单已保留并转交人工客服，您无需重复提交。",
            "系统会保留当前工单，由人工客服核对业务信息后继续处理。",
        ),
        "en": (
            "We have received your question, but part of the service is temporarily "
            "unavailable. Your ticket has been kept and sent to a support specialist; "
            "you do not need to submit it again.",
            "A specialist will review the ticket and continue from here.",
        ),
    },
    "quality_review": {
        "zh": (
            "我已完成初步查询，但当前结果的依据或置信度不足。为避免提供不准确的信息，工单已转交人工客服复核。",
            "人工客服会结合业务记录和服务政策确认后回复。",
        ),
        "en": (
            "I looked into this, but the evidence is not strong enough to give you a "
            "reliable answer. To avoid giving inaccurate information, your ticket has "
            "been sent to a support specialist for review.",
            "A specialist will check the business records and policy before replying.",
        ),
    },
    "incident_review": {
        "zh": (
            "我已经收到您反馈的故障，工单已按紧急级别转交技术支持跟进。",
            "技术支持会核对服务状态和您的环境信息后回复。",
        ),
        "en": (
            "We have received your incident report. The ticket has been escalated to "
            "technical support as urgent.",
            "Technical support will check service status and your environment before replying.",
        ),
    },
    "business_review": {
        "zh": (
            "我已经收到您的请求。退款、取消订单、投诉等业务需要人工客服核对后办理，工单已进入处理队列。",
            "客服人员会核对订单和账务记录后回复，您无需重复提交。",
        ),
        "en": (
            "We have received your request. Refunds, cancellations and complaints are "
            "handled by a support specialist after verification, and your ticket is now "
            "in the queue.",
            "A specialist will check your order and billing records before replying.",
        ),
    },
    "manual_review": {
        "zh": (
            "我已经收到您的问题。该请求需要人工客服进一步确认，工单已进入处理队列。",
            "客服人员会核对 AI 草稿和相关业务信息后继续处理。",
        ),
        "en": (
            "We have received your question. It needs to be confirmed by a support "
            "specialist, and your ticket is now in the queue.",
            "A specialist will review the draft and the related records before replying.",
        ),
    },
}


def _review_reason(agent_output: Mapping[str, Any]) -> str:
    degradation = str(agent_output.get("degradation_level", "none")).lower()
    # 安全拦截会往 errors 写入记录，必须先于处理异常判断。
    if agent_output.get("security_threat_detected") or agent_output.get(
        "risk_block_automation"
    ):
        return "risk_review"
    if _service_errors(agent_output) or degradation in {"human_required", "failed"}:
        return "processing_exception"
    try:
        qa_score = float(agent_output.get("qa_score", 1.0))
    except (TypeError, ValueError):
        qa_score = 1.0
    if (
        agent_output.get("hallucination_detected")
        or agent_output.get("response_requires_human")
        or qa_score < settings.RISK_QA_SCORE_THRESHOLD
    ):
        return "quality_review"
    intent = normalize_intent(agent_output.get("intent"))
    if intent == IntentType.OUTAGE_REPORT:
        return "incident_review"
    if agent_output.get("risk_requires_human") or str(
        agent_output.get("risk_level", "low")
    ).lower() in {"high", "critical"}:
        return "business_review"
    return "manual_review"


def public_review_feedback(
    agent_output: Mapping[str, Any], customer_text: str = ""
) -> tuple[str, str, str]:
    """把内部转人工原因转换为可展示给用户的说明，按用户输入语言输出。

    只有安全拦截才提“安全”；账务、故障、质量问题各有对应文案。
    """
    reason = _review_reason(agent_output)
    # 没有原文时（旧调用方）按中文输出。
    language = "zh" if not customer_text or _is_chinese(customer_text) else "en"
    response, message = _TEXTS[reason][language]
    return reason, response, message
