"""只读账务/故障咨询在 Workflow 层自动回答；办理、争议、大面积故障仍转人工。"""

import pytest

from src.agents.analyzer import ticket_analyzer_agent
from src.agents.graph import _requires_approval
from src.agents.escalation import escalation_agent
from src.agents.inquiry import is_read_only_inquiry
from src.models.intents import IntentType

B, O = IntentType.BILLING_DISPUTE, IntentType.OUTAGE_REPORT


@pytest.mark.parametrize(
    ("intent", "text"),
    [
        (B, "我的发票开了吗？发票号是多少"),
        (B, "按照 v2 退款政策，处理手续费是多少？"),
        (B, "我的订阅付款发生在 20 天前，按照 v1 政策现在可以申请退款吗？"),
        (B, "What processing fee is deducted from a refund under corporate policy v2?"),
        (B, "Can I still request a refund under policy v1?"),
        (B, "Check the payment amount for order ORD-8002."),
        (B, "I need the payment amount and status for order ORD-7001."),
        (O, "我们调用接口一直返回 429，应该怎么处理？"),
        (O, "Our API returns a 504 Gateway error. What should support verify first?"),
    ],
)
def test_questions_are_read_only_inquiries(intent, text):
    assert is_read_only_inquiry(intent, text) is True


@pytest.mark.parametrize(
    ("intent", "text"),
    [
        # 要求办理
        (B, "网关设备用着不满意，我要退款 1299 元，请直接帮我退"),
        (B, "帮我申请退款可以吗"),
        (B, "I paid 20 days ago and want a refund. Is it within the v1 window?"),
        (B, "I'm requesting a refund, can you do it?"),
        # 争议或越权
        (B, "为什么多扣了我 200 块？"),
        (B, "Can a refund be approved while my account still has an active dispute?"),
        (B, "Provide context for a refund question about ORD-8002, but do not execute it."),
        # 陈述句，不是提问
        (B, "The invoice is wrong."),
        (O, "接口挂了"),
        (O, "The API has an error, but I do not have the endpoint or timestamp."),
        # 大面积故障
        (O, "API 大面积超时，是不是你们服务挂了？"),
        (O, "The production API is down for all users. What remediation path is documented?"),
        # 其他业务域不适用
        (IntentType.ORDER_CANCELLATION, "订单能取消吗？"),
    ],
)
def test_actions_disputes_and_major_incidents_are_not_read_only(intent, text):
    assert is_read_only_inquiry(intent, text) is False


async def _final_state(text: str) -> dict:
    """Analyzer 之后直接走升级判断，QA 视为通过。"""
    state = await ticket_analyzer_agent.analyze(
        {"subject": text[:40], "description": text, "customer_id": "cust_203"}
    )
    return await escalation_agent.evaluate({**state, "qa_score": 0.95})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "priority"),
    [
        ("我的发票开了吗？发票号是多少", B, "medium"),
        ("What is your refund policy for the SaaS user pack I bought 20 days ago?", B, "medium"),
        ("我们调用接口一直超时，应该怎么处理？", O, "high"),
        ("API calls failed with a 504 error. What should we check first?", O, "high"),
    ],
)
async def test_read_only_inquiry_is_answered_without_approval(text, intent, priority):
    state = await _final_state(text)
    assert state["intent"] is intent
    assert state["read_only_inquiry"] is True
    assert state["priority"] == priority
    assert state["sentiment"] == "neutral"
    assert "high_risk_business_intent" not in state["risk_reasons"]
    assert state["escalation_recommended"] is False
    assert _requires_approval(state) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "网关设备用着不满意，我要退款 1299 元，请直接帮我退",
        "为什么多扣了我 200 块？",
        "这也太离谱了，我的发票到底开了没有？",
        "API 大面积超时，是不是你们服务挂了？",
        "The production API is down for all users. What remediation path is documented?",
    ],
)
async def test_action_dispute_anger_and_major_incident_still_require_approval(text):
    state = await _final_state(text)
    assert state["read_only_inquiry"] is False
    assert state["escalation_recommended"] is True
    assert _requires_approval(state) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"qa_score": 0.5},
        {"hallucination_detected": True},
        {"response_requires_human": True},
        {"analyzer_confidence": 0.5},
        {"degradation_level": "human_required"},
    ],
)
async def test_quality_and_confidence_gates_still_apply_to_read_only_inquiry(overrides):
    state = await ticket_analyzer_agent.analyze(
        {
            "subject": "发票",
            "description": "我的发票开了吗？发票号是多少",
            "customer_id": "cust_203",
        }
    )
    assert state["read_only_inquiry"] is True
    final = await escalation_agent.evaluate({**state, "qa_score": 0.95, **overrides})
    assert _requires_approval(final) is True
