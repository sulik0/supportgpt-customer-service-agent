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
        (B, "What processing fee is deducted from a refund under corporate policy v2?"),
        (B, "Check the payment amount for order ORD-8002."),
        # 询问争议相关政策不等于表达争议
        (B, "Can a refund be approved while my account still has an active dispute?"),
        (B, "有未结争议的账户可以申请退款吗？"),
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
        (B, "I want to dispute this charge. Can you help?"),
        (B, "我对这笔扣款有异议，怎么处理？"),
        (B, "Can I file a dispute for order ORD-7001?"),
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


# --- 以提问口气提出的办理要求：关键词规则不能把它们当成只读咨询 ---


@pytest.mark.parametrize(
    "text",
    [
        "Can you refund my order ORD-7001?",
        "Could you please refund me?",
        "Refund me now, okay?",
        "I'd like my money back, is that possible?",
        "Would you reverse the charge on my card?",
        "Can you credit my account for the outage?",
        "Please make it right and return my payment, can you?",
        "Is it possible to get a refund? I want one.",
        "Can I get a refund? Please process it today.",
        "How do I dispute this charge?",
        "能帮我把钱退回来吗？",
        "这单不要了，钱能退给我吗？",
        "麻烦处理一下退款可以吗？",
        "能不能给我退了？",
        "帮我办一下退货退款好吗？",
        "什么时候把钱退给我？",
        "可以安排退款吗？",
        "把这笔订单退掉可以吗？",
        "能把扣的钱还给我吗？",
        "这笔扣款我不认，怎么办？",
        "请尽快退款，多久能到？",
        "我要求全额退款，可以吗？",
        "帮我撤销这笔支付行吗？",
        "订单 ORD-12001 可以退款吗？帮我操作一下",
    ],
)
def test_action_requests_phrased_as_questions_are_not_read_only(text):
    assert is_read_only_inquiry(B, text) is False


@pytest.mark.parametrize(
    "text",
    [
        "可以申请退款吗？",
        "有未结争议的账户可以申请退款吗？",
        "帮我查一下退款进度到哪了？",
        "请问退款手续费是多少？",
        "请查询订单 ORD-12003 的付款记录和发票进度，告诉我是否已付款、发票是否已开好。",
        "麻烦帮我看一下发票开了没有？",
        "Please check the card payment and current record for order ORD-7001.",
        "Could you tell me how long a refund takes?",
    ],
)
def test_lookups_and_policy_questions_stay_read_only(text):
    assert is_read_only_inquiry(B, text) is True


def _jev_says(monkeypatch, intent, operation_mode):
    from src.decision.models import DecisionResult, TicketIntentDecision

    async def decide(_text):
        return TicketIntentDecision(
            result=DecisionResult(
                enabled=False, available=False, provider="x", operation="o",
                question_set_version="v", model="m",
            ),
            accepted=True,
            analysis={
                "intent": intent, "priority": "high", "department": "billing",
                "sentiment": "neutral", "confidence_score": 0.95,
                "operation_mode": operation_mode,
            },
        )

    monkeypatch.setattr("src.agents.analyzer.decision_service.classify_ticket", decide)


@pytest.mark.asyncio
async def test_model_can_veto_read_only_but_cannot_grant_it(monkeypatch):
    # 关键词规则没认出来的说法：模型判断是办理要求，就不算只读，照常转人工。
    text = "这笔订单的钱什么时候能回到我卡上？我等着用。"
    _jev_says(monkeypatch, B, "information")
    assert (await _final_state(text))["read_only_inquiry"] is True
    _jev_says(monkeypatch, B, "action")
    vetoed = await _final_state(text)
    assert vetoed["read_only_inquiry"] is False
    assert vetoed["escalation_recommended"] is True

    # 反过来不行：规则认定是办理要求时，模型说“只是咨询”也不能放行。
    _jev_says(monkeypatch, B, "information")
    kept = await _final_state("我要退款 1299 元，请直接帮我退，可以吗？")
    assert kept["read_only_inquiry"] is False
    assert kept["escalation_recommended"] is True


@pytest.mark.asyncio
async def test_model_veto_applies_even_when_rule_intent_wins(monkeypatch):
    # Jev 把账务问题判成通用咨询时意图用规则结论，但它对“办理要求”的判断仍然生效。
    _jev_says(monkeypatch, IntentType.INFORMATION_REQUEST, "action")
    state = await _final_state("发票上的金额什么时候能改过来？")
    assert state["intent"] is B
    assert state["read_only_inquiry"] is False


# --- 问“我这笔款能不能退”：转人工，但不算办理要求，用户仍先看到政策答复 ---


@pytest.mark.parametrize(
    "text",
    [
        "我的订阅付款发生在 20 天前，按照 v1 政策现在可以申请退款吗？",
        "我买了 20 天了，还能退款吗？",
        "Can I still request a refund under policy v1?",
        "Can I get a refund for a subscription payment made 31 days ago under v1?",
        "My initial subscription payment was exactly 30 days ago. May I file a refund request under policy v1?",
    ],
)
def test_asking_about_own_refund_eligibility_is_not_read_only(text):
    from src.agents.inquiry import ACTION_OR_DISPUTE

    assert is_read_only_inquiry(B, text) is False
    # 不算办理要求：公开入口可以先展示政策答复。
    assert not ACTION_OR_DISPUTE.search(text)


@pytest.mark.parametrize(
    "text",
    ["可以申请退款吗？", "有未结争议的账户可以申请退款吗？", "Can my v2 refund be sent to a different card?"],
)
def test_generic_policy_questions_are_still_read_only(text):
    assert is_read_only_inquiry(B, text) is True


@pytest.mark.asyncio
async def test_own_eligibility_question_escalates_and_allows_preliminary_answer():
    from src.agents.public_reply import preliminary_answer_allowed

    text = "我买了 20 天了，还能退款吗？"
    state = await _final_state(text)
    assert state["read_only_inquiry"] is False
    assert state["escalation_recommended"] is True
    output = {**state, "suggested_response": "按政策，付款后 30 天内可以申请退款。", "qa_score": 0.95}
    assert preliminary_answer_allowed(output, text) is True
    # 模型判断是办理要求时不展示草稿。
    assert preliminary_answer_allowed({**output, "model_sees_action": True}, text) is False
