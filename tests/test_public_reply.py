"""公开入口回复策略：转人工文案按原因区分，先答后审时先看到初步答复。"""

import pytest

from src.agents.public_reply import (
    preliminary_answer_allowed,
    public_review_feedback,
)
from src.config import settings


def _review_output(**overrides):
    """处理原则判为先答后审、QA 通过的输出。"""
    output = {
        "intent": "billing_dispute",
        "classification": {"handling": "answer_then_review", "principle": 7},
        "suggested_response": "按政策，付款后 30 天内可以申请退款，具体由人工确认。",
        "qa_score": 0.95,
        "risk_level": "high",
        "risk_requires_human": True,
        "risk_reasons": ["handling_answer_then_review"],
        "errors": [],
    }
    output.update(overrides)
    return output


@pytest.mark.parametrize("principle", [7, 8, 17, 21])
def test_answer_then_review_gets_preliminary_answer(principle):
    output = _review_output(
        classification={"handling": "answer_then_review", "principle": principle}
    )
    assert preliminary_answer_allowed(output, "我买了 20 天了，还能退款吗？")


@pytest.mark.parametrize("handling", ["human", "auto_answer", None])
def test_other_handling_keeps_draft_hidden(handling):
    # 转人工的办事请求不展示草稿；公开入口不再对原文重判，只读分类结论。
    output = _review_output(
        classification={"handling": handling},
        risk_reasons=["handling_human"],
    )
    assert not preliminary_answer_allowed(output, "请直接帮我退款")


def test_missing_classification_keeps_draft_hidden():
    output = _review_output()
    output.pop("classification")
    assert not preliminary_answer_allowed(output, "我的发票开了吗？")


@pytest.mark.parametrize(
    "overrides",
    [
        {"security_threat_detected": True},
        {"errors": ["Resolver dependency failure: X"]},
        {"hallucination_detected": True},
        {"qa_score": 0.6},
        {"qa_score": None},
        {"degradation_level": "partial"},
        {"suggested_response": ""},
        {"risk_reasons": ["handling_answer_then_review", "qa_score_below_threshold"]},
        {"risk_reasons": ["handling_answer_then_review", "dependency_failed"]},
        {"risk_reasons": []},
    ],
)
def test_any_other_signal_blocks_preliminary_answer(overrides):
    assert not preliminary_answer_allowed(_review_output(**overrides), "我的发票开了吗？")


def test_preliminary_answer_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_PRELIMINARY_ANSWER_ENABLED", False)
    assert not preliminary_answer_allowed(_review_output(), "我的发票开了吗？")


def test_qa_hallucination_alert_is_quality_review_not_service_outage():
    # QA 把幻觉告警写进 errors；它不是服务故障，不能显示“服务不可用”。
    output = {
        "errors": ["QA score alert: potential hallucination detected."],
        "hallucination_detected": True,
        "qa_score": 0.5,
        "risk_level": "critical",
    }
    reason, response, _ = public_review_feedback(output, "查发票和保修")
    assert reason == "quality_review"
    assert "不可用" not in response


def test_only_security_blocks_mention_security():
    billing = public_review_feedback(
        {"intent": "billing_dispute", "risk_level": "high", "risk_requires_human": True},
        "我要退款",
    )
    assert billing[0] == "business_review"
    assert "安全" not in billing[1]
    outage = public_review_feedback(
        {"intent": "outage_report", "risk_level": "high", "risk_requires_human": True},
        "接口全挂了",
    )
    assert outage[0] == "incident_review"
    security = public_review_feedback({"security_threat_detected": True}, "忽略指令")
    assert security[0] == "risk_review" and "安全" in security[1]


def test_review_message_follows_customer_language():
    _, response, message = public_review_feedback(
        {"intent": "billing_dispute", "risk_level": "high"}, "I want a refund now."
    )
    assert "support specialist" in response
    assert not any("一" <= ch <= "鿿" for ch in response + message)


# --- 多意图：能答的部分照常回答，缺依据的主题只说明“无法确认”时不算幻觉 ---


def test_multi_intent_draft_that_defers_unsupported_topic_is_not_hallucination():
    from src.agents.quality_assurance import quality_assurance_agent as qa_agent
    from src.agents.authority import defers_unsupported_topic

    query = "我想查一下 ORD-12002 的发票，顺便问下网关设备保修多久，另外退货怎么操作？"
    draft = (
        "ORD-12002 已付款，发票已开具，发票号为 INV-DEMO-CN-12002。"
        "网关设备的保修期限本次未查询到，需要另行核实。"
        "退货操作流程建议联系人工客服核实后再办理。"
    )
    assert defers_unsupported_topic(query, draft) is True
    result, terminal = qa_agent._rule_evaluation(
        query=query,
        raw_response=draft,
        filtered_response=draft,
        citations=[{"text": "支付与发票查询：已付款不代表已开票。"}],
        tool_context={},
        # 发票号来自账务 Tool；证据里没有它时，规则会判单号编造。
        context_texts=[
            '[TOOL] {"service_query":{"data":{"records":[{"order_id":"ORD-12002",'
            '"invoice_id":"INV-DEMO-CN-12002","invoice_status":"issued"}]}}}',
            "[S1] 支付与发票查询：已付款不代表已开票。",
        ],
    )
    assert terminal is False
    assert result["hallucination_detected"] is False
    # 缺依据的主题仍然需要人工跟进。
    assert result["response_requires_human"] is True


@pytest.mark.parametrize(
    "draft",
    [
        "发票已开具。网关设备保修期为 12 个月。",
        "发票已开具。网关设备享受整机保修服务，放心使用。",
        "The invoice is issued. Your gateway has a 24-month warranty.",
    ],
)
def test_draft_that_asserts_unsupported_warranty_fact_is_still_hallucination(draft):
    from src.agents.quality_assurance import quality_assurance_agent as qa_agent

    query = "查一下发票，顺便问下网关设备保修多久？"
    result, terminal = qa_agent._rule_evaluation(
        query=query,
        raw_response=draft,
        filtered_response=draft,
        citations=[{"text": "支付与发票查询。"}],
        tool_context={},
        context_texts=["[S1] 支付与发票查询。"],
    )
    assert terminal is True
    assert result["hallucination_detected"] is True


def test_deferred_topic_draft_is_shown_as_preliminary_answer():
    output = {
        "intent": "billing_dispute",
        "classification": {"handling": "auto_answer", "principle": 10},
        "suggested_response": "发票已开具。保修期限无法确认，需人工核实。",
        "qa_score": 0.9,
        "hallucination_detected": False,
        "response_requires_human": True,
        "risk_reasons": ["authoritative_answer_unavailable"],
        "errors": [],
        "degradation_level": "none",
    }
    text = "我想查一下发票，顺便问下网关设备保修多久？"
    assert preliminary_answer_allowed(output, text) is True
    # 有幻觉或其他风险原因时不展示草稿。
    assert preliminary_answer_allowed({**output, "hallucination_detected": True}, text) is False
    assert (
        preliminary_answer_allowed(
            {**output, "risk_reasons": ["authoritative_answer_unavailable", "workflow_error"]},
            text,
        )
        is False
    )


def test_answered_note_follows_customer_language():
    from src.agents.public_reply import answered_note

    assert answered_note("我的发票开了吗？") == "智能客服已完成处理。"
    english = answered_note("Where is my invoice?")
    assert english and not any("\u4e00" <= ch <= "\u9fff" for ch in english)
