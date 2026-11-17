"""“要办事还是只是问”：规则信号和处理结果（docs/10_HANDLING_PRINCIPLES.md 第 5–8、12、20、21 条）。"""

import pytest

from src.agents.analyzer import ticket_analyzer_agent
from src.agents.classification import rule_signals
from src.agents.escalation import escalation_agent
from src.agents.graph import _requires_approval
from src.decision.models import DecisionResult, TicketIntentDecision
from src.models.intents import IntentType

B, O = IntentType.BILLING_DISPUTE, IntentType.OUTAGE_REPORT


@pytest.mark.parametrize(
    "text",
    [
        "我的发票开了吗？发票号是多少",
        "按照 v2 退款政策，处理手续费是多少？",
        "What processing fee is deducted from a refund under corporate policy v2?",
        "Check the payment amount for order ORD-8002.",
        # 询问争议相关政策不等于表达争议
        "Can a refund be approved while my account still has an active dispute?",
        "有未结争议的账户可以申请退款吗？",
        "I need the payment amount and status for order ORD-7001.",
        "我们调用接口一直返回 429，应该怎么处理？",
        "可以申请退款吗？",
        "帮我查一下退款进度到哪了？",
        "请问退款手续费是多少？",
        "请查询订单 ORD-12003 的付款记录和发票进度，告诉我是否已付款、发票是否已开好。",
        "麻烦帮我看一下发票开了没有？",
        "Please check the card payment and current record for order ORD-7001.",
        "Could you tell me how long a refund takes?",
    ],
)
def test_questions_and_lookups_have_no_action_signal(text):
    assert rule_signals(text)["explicit_action"] is False


@pytest.mark.parametrize(
    "text",
    [
        # 要求办理
        "网关设备用着不满意，我要退款 1299 元，请直接帮我退",
        "帮我申请退款可以吗",
        "I paid 20 days ago and want a refund. Is it within the v1 window?",
        "I'm requesting a refund, can you do it?",
        # 争议或越权
        "为什么多扣了我 200 块？",
        "I want to dispute this charge. Can you help?",
        "我对这笔扣款有异议，怎么处理？",
        "Can I file a dispute for order ORD-7001?",
        "Provide context for a refund question about ORD-8002, but do not execute it.",
        # 以提问口气提出的办理要求（第 5 条）
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
def test_action_and_dispute_phrases_have_action_signal(text):
    assert rule_signals(text)["explicit_action"] is True


@pytest.mark.parametrize(
    "text",
    [
        "API 大面积超时，是不是你们服务挂了？",
        "The production API is down for all users. What remediation path is documented?",
        "接口一直报错，已经影响我们的业务了",
    ],
)
def test_wide_impact_and_business_impact_signals(text):
    assert rule_signals(text)["wide_impact"] is True


@pytest.mark.parametrize(
    "text",
    [
        "我的订阅付款发生在 20 天前，按照 v1 政策现在可以申请退款吗？",
        "我买了 20 天了，还能退款吗？",
        "Can I still request a refund under policy v1?",
        "Can I get a refund for a subscription payment made 31 days ago under v1?",
    ],
)
def test_own_refund_eligibility_signal_is_not_an_action(text):
    signals = rule_signals(text)
    assert signals["eligibility"] is True
    assert signals["explicit_action"] is False


@pytest.mark.parametrize(
    "text",
    ["可以申请退款吗？", "有未结争议的账户可以申请退款吗？", "Can my v2 refund be sent to a different card?"],
)
def test_generic_policy_questions_are_not_eligibility(text):
    assert rule_signals(text)["eligibility"] is False


def _jev_says(monkeypatch, intent, operation_mode, **extra):
    async def decide(_text):
        return TicketIntentDecision(
            result=DecisionResult(
                enabled=False, available=True, provider="x", operation="o",
                question_set_version="v", model="m",
            ),
            accepted=True,
            analysis={
                "intent": intent, "confidence_score": 0.95,
                "operation_mode": operation_mode, "customer_tone": "neutral",
                "mixed_request": False, "out_of_scope": False, **extra,
            },
        )

    monkeypatch.setattr("src.agents.analyzer.decision_service.classify_ticket", decide)


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
async def test_jev_information_question_is_answered_without_approval(
    monkeypatch, text, intent, priority
):
    _jev_says(monkeypatch, intent, "information")
    state = await _final_state(text)
    assert state["intent"] is intent
    assert state["classification"]["handling"] == "auto_answer"
    assert state["read_only_inquiry"] is True
    assert state["priority"] == priority
    assert state["escalation_recommended"] is False
    assert _requires_approval(state) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["按照 v2 退款政策，处理手续费是多少？", "我们调用接口一直超时，应该怎么处理？"],
)
async def test_without_jev_billing_and_outage_answer_then_review(text):
    # 第 21 条：Jev 不可用时“办事还是问”按说不清，账务/故障先答后审。
    state = await _final_state(text)
    assert state["classification"]["mode"] == "unclear"
    assert state["classification"]["handling"] == "answer_then_review"
    assert state["classification"]["principle"] == 21
    assert state["escalation_recommended"] is True


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
        {"degradation_level": "human_required"},
    ],
)
async def test_quality_gates_still_apply_to_auto_answers(monkeypatch, overrides):
    _jev_says(monkeypatch, B, "information")
    state = await ticket_analyzer_agent.analyze(
        {
            "subject": "发票",
            "description": "我的发票开了吗？发票号是多少",
            "customer_id": "cust_203",
        }
    )
    assert state["classification"]["handling"] == "auto_answer"
    final = await escalation_agent.evaluate({**state, "qa_score": 0.95, **overrides})
    assert _requires_approval(final) is True


@pytest.mark.asyncio
async def test_rule_action_phrase_overrides_jev_information(monkeypatch):
    # 第 20 条：原话有明确办事说法时，规则否决“只是问”。
    _jev_says(monkeypatch, B, "information")
    state = await _final_state("我要退款 1299 元，请直接帮我退，可以吗？")
    assert state["classification"]["mode"] == "action"
    assert state["classification"]["mode_source"] == "rule"
    assert state["escalation_recommended"] is True

    # 规则没认出来的说法，Jev 判为办事同样转人工。
    _jev_says(monkeypatch, B, "action")
    vetoed = await _final_state("这笔订单的钱什么时候能回到我卡上？我等着用。")
    assert vetoed["classification"]["handling"] == "human"
    assert vetoed["model_sees_action"] is True


@pytest.mark.asyncio
async def test_jev_category_wins_over_rule_keywords(monkeypatch):
    # 第 20 条：Jev 有把握时由 Jev 定业务类别，规则不再改判。
    _jev_says(monkeypatch, IntentType.INFORMATION_REQUEST, "information")
    state = await _final_state("发票上的金额什么时候能改过来？")
    assert state["intent"] is IntentType.INFORMATION_REQUEST
    assert state["classification"]["category_source"] == "jev"


@pytest.mark.asyncio
async def test_mixed_question_and_action_is_answer_then_review(monkeypatch):
    # 第 8 条：既问又办，能答的先答，办事部分转人工。
    _jev_says(monkeypatch, B, "action", mixed_request=True)
    state = await _final_state("退款一般几天能到账？另外请帮我把订单 ORD-7001 退掉。")
    assert state["classification"]["handling"] == "answer_then_review"
    assert state["classification"]["principle"] == 8


@pytest.mark.asyncio
async def test_own_eligibility_question_escalates_and_allows_preliminary_answer(
    monkeypatch,
):
    from src.agents.public_reply import preliminary_answer_allowed

    _jev_says(monkeypatch, B, "eligibility")
    text = "我买了 20 天了，还能退款吗？"
    state = await _final_state(text)
    assert state["classification"]["handling"] == "answer_then_review"
    assert state["classification"]["principle"] == 7
    assert state["escalation_recommended"] is True
    output = {**state, "suggested_response": "按政策，付款后 30 天内可以申请退款。", "qa_score": 0.95}
    assert preliminary_answer_allowed(output, text) is True
