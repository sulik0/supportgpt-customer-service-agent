"""线上真实模型回归发现的问题：规则兜底与投诉升级。"""

import pytest

from src.agents.analyzer import TicketAnalyzerAgent, ticket_analyzer_agent
from src.decision.models import TicketIntentDecision
from src.models.intents import IntentType


C05 = "你们服务太差了，我要投诉，SLA 完全没达到"
C06 = "我登录不了账号，提示密码错误，怎么重置密码？"
C12 = "我们的 AWS Gateway 集群从早上开始就无法访问了，生产环境全挂了"


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        (C05, IntentType.FEEDBACK),
        (C06, IntentType.ACCOUNT_SUPPORT),
        (C12, IntentType.OUTAGE_REPORT),
        ("Our production cluster is down", IntentType.OUTAGE_REPORT),
        ("I want to file a complaint about your SLA", IntentType.FEEDBACK),
    ],
)
def test_rules_cover_online_regression_expressions(text, intent):
    assert TicketAnalyzerAgent._match_rule(text)["intent"] is intent


@pytest.mark.parametrize("text", ["怎么重置密码", "怎么投诉你们", "How can I complain?"])
def test_rules_keep_pure_questions_as_information(text):
    assert TicketAnalyzerAgent._match_rule(text)["intent"] is IntentType.INFORMATION_REQUEST


@pytest.mark.asyncio
@pytest.mark.parametrize("jev_intent", [IntentType.FEEDBACK, IntentType.INFORMATION_REQUEST])
async def test_complaint_requires_human_even_when_model_says_feedback(
    monkeypatch, jev_intent
):
    from src.decision.models import DecisionResult

    async def decide(_text):
        return TicketIntentDecision(
            result=DecisionResult(
                enabled=False, available=False, provider="x", operation="o",
                question_set_version="v", model="m",
            ),
            accepted=True,
            analysis={
                "intent": jev_intent,
                "priority": "low",
                "department": "general",
                "sentiment": "positive",
                "confidence_score": 0.91,
            },
        )

    monkeypatch.setattr("src.agents.analyzer.decision_service.classify_ticket", decide)
    state = await ticket_analyzer_agent.analyze(
        {"subject": "投诉", "description": C05, "customer_id": "cust_103"}
    )

    assert state["priority"] == "high"
    assert state["sentiment"] == "negative"
    assert state["risk_requires_human"] is True


@pytest.mark.asyncio
async def test_plain_feedback_is_not_escalated(monkeypatch):
    state = await ticket_analyzer_agent.analyze(
        {"subject": "thanks", "description": "Thank you, great service", "customer_id": "cust_101"}
    )

    assert state["priority"] == "low"
    assert state["risk_requires_human"] is False
