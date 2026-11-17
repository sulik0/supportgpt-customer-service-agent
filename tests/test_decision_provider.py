import httpx
import pytest
from pydantic import ValidationError

from src.config import Settings, settings
from src.agents.analyzer import ticket_analyzer_agent
from src.agents.quality_assurance import quality_assurance_agent
from src.decision.models import (
    DecisionAnswer,
    DecisionResult,
    QAReviewDecision,
    TicketIntentDecision,
)
from src.decision.provider import JevDecisionProvider
from src.decision.questions import intent_questions
from src.decision.service import DecisionService
from src.models.intents import IntentType
from src.models.schemas import Citation


class _FakeHTTPResponse:
    """模拟 Jev HTTP 响应，避免单测访问外网。"""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeHTTPClient:
    """记录请求以验证脱敏和 System One 协议。"""

    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return _FakeHTTPResponse(self.payload)


class _StubDecisionProvider:
    """为领域映射测试返回固定结构化决策。"""

    enabled = True

    def __init__(self, result):
        self.result = result

    async def evaluate(self, **kwargs):
        return self.result


def _result(operation, answers):
    return DecisionResult(
        enabled=True,
        available=True,
        provider="jev",
        operation=operation,
        question_set_version="test-v1",
        model="jev-test",
        answers=answers,
        input_tokens=20,
        output_tokens=5,
    )


def _intent_answers(intent, confidence):
    return {
        "intent": DecisionAnswer(
            kind="choice", value=intent, confidence=confidence
        ),
        "operation_mode": DecisionAnswer(
            kind="choice", value="information", confidence=confidence
        ),
        "needs_clarification": DecisionAnswer(kind="noul", value=0.05),
    }


def test_jev_configuration_requires_nonempty_api_key():
    with pytest.raises(ValidationError):
        Settings(DECISION_PROVIDER="jev", JEV_API_KEY=" ", _env_file=None)


@pytest.mark.asyncio
async def test_jev_provider_calls_system_one_and_sanitizes_state(monkeypatch):
    payload = {
        "model": "jev-test",
        "answers": {
            "intent": {
                "type": "choice",
                "choice": "order_status",
                "confidence": 0.94,
                "probabilities": {
                    "order_status": 0.94,
                    "information_request": 0.06,
                },
            },
            "operation_mode": {
                "type": "choice",
                "choice": "information",
                "confidence": 0.97,
                "probabilities": {"information": 0.97, "action": 0.03},
            },
            "needs_clarification": {"type": "noul", "noul": 0.03},
        },
        "usage": {"input_tokens": 33, "output_tokens": 4},
    }
    client = _FakeHTTPClient(payload=payload)
    provider = JevDecisionProvider(client=client)
    monkeypatch.setattr(settings, "RESILIENCE_ENABLED", False)
    monkeypatch.setattr(settings, "JEV_BASE_URL", "https://jev.example")

    result = await provider.evaluate(
        state={
            "message": "Check ORDER-123 for alice@example.com",
            "order_id": "ORDER-123",
        },
        questions=intent_questions(),
        operation="intent_test_success",
        question_set_version="intent-test-v1",
    )

    assert result.available is True
    assert result.answers["intent"].value == "order_status"
    assert result.input_tokens == 33
    assert client.calls[0][0] == "https://jev.example/v1/systemone"
    request_json = client.calls[0][1]["json"]
    assert "ORDER-123" not in str(request_json)
    assert "alice@example.com" not in str(request_json)
    alias = request_json["state"]["order_id"]
    assert alias.startswith("[BUSINESS_ID_")
    assert alias in request_json["state"]["message"]
    assert request_json["questions"]["intent"]["type"] == "choice"


@pytest.mark.asyncio
async def test_jev_provider_failure_returns_fallback_result(monkeypatch):
    request = httpx.Request("POST", "https://jev.example/v1/systemone")
    client = _FakeHTTPClient(error=httpx.ConnectError("offline", request=request))
    provider = JevDecisionProvider(client=client)
    monkeypatch.setattr(settings, "RESILIENCE_ENABLED", False)
    monkeypatch.setattr(settings, "JEV_BASE_URL", "https://jev.example")

    result = await provider.evaluate(
        state="ambiguous ticket",
        questions=intent_questions(),
        operation="intent_test_failure",
        question_set_version="intent-test-v1",
    )

    assert result.available is False
    assert result.error_code == "connection"


@pytest.mark.asyncio
async def test_jev_provider_rejects_malformed_probability(monkeypatch):
    client = _FakeHTTPClient(
        payload={
            "model": "jev-test",
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "safe",
                    "confidence": 1.2,
                }
            },
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }
    )
    provider = JevDecisionProvider(client=client)
    monkeypatch.setattr(settings, "RESILIENCE_ENABLED", False)

    result = await provider.evaluate(
        state="test",
        questions={
            "route": {
                "type": "choice",
                "instructions": "Choose a route.",
                "criteria": {"safe": None, "review": None},
            }
        },
        operation="malformed_probability_test",
        question_set_version="test-v1",
    )

    assert result.available is False
    assert result.error_code == "malformed_response"


@pytest.mark.asyncio
async def test_decision_service_accepts_high_confidence_intent():
    provider = _StubDecisionProvider(
        _result(
            "intent_classification",
            _intent_answers("outage_report", 0.92),
        )
    )

    decision = await DecisionService(provider).classify_ticket("API is unavailable")

    assert decision.accepted is True
    assert decision.analysis["intent"].value == "outage_report"
    # 部门和优先级由 Taxonomy 与处理原则推导，DecisionService 只给 Jev 的结论。
    assert "priority" not in decision.analysis


@pytest.mark.asyncio
async def test_decision_service_rejects_low_confidence_intent():
    provider = _StubDecisionProvider(
        _result(
            "intent_classification",
            _intent_answers("order_status", 0.51),
        )
    )

    decision = await DecisionService(provider).classify_ticket("Please help")

    assert decision.accepted is False
    assert decision.fallback_reason == "low_confidence"


@pytest.mark.asyncio
async def test_decision_service_rejects_ambiguous_intent():
    answers = _intent_answers("information_request", 0.95)
    answers["needs_clarification"] = DecisionAnswer(kind="noul", value=0.91)
    provider = _StubDecisionProvider(
        _result("intent_classification", answers)
    )

    decision = await DecisionService(provider).classify_ticket("Please help")

    assert decision.accepted is False
    assert decision.fallback_reason == "needs_clarification"


@pytest.mark.asyncio
async def test_decision_service_maps_qa_answers():
    provider = _StubDecisionProvider(
        _result(
            "response_judgment",
            {
                "grounding": DecisionAnswer(
                    kind="choice", value="supported", confidence=0.95
                ),
                "task_completion": DecisionAnswer(
                    kind="score", value=3.0, confidence=0.91
                ),
                "citation_status": DecisionAnswer(
                    kind="choice", value="verified", confidence=0.96
                ),
                "unsupported_commitment": DecisionAnswer(
                    kind="noul", value=0.04
                ),
                "needs_human_review": DecisionAnswer(kind="noul", value=0.03),
            },
        )
    )

    decision = await DecisionService(provider).judge_response(
        query="Where is the order?",
        evidence=["[S1] The order is in transit."],
        response="The order is in transit [S1].",
    )

    assert decision.accepted is True
    assert decision.evaluation["score"] > 0.9
    assert decision.evaluation["hallucination_detected"] is False
    assert decision.evaluation["citation_verified"] is True
    assert decision.evaluation["response_requires_human"] is False


@pytest.mark.asyncio
async def test_analyzer_uses_jev_for_ambiguous_intent(monkeypatch):
    result = _result("intent_classification", {})

    async def decide(_text):
        return TicketIntentDecision(
            result=result,
            accepted=True,
            analysis={
                "intent": IntentType.BILLING_DISPUTE,
                "priority": "high",
                "department": "billing",
                "sentiment": "negative",
                "confidence_score": 0.9,
            },
        )

    async def unexpected_llm(_text):
        raise AssertionError("accepted Jev decision must skip Analyzer LLM")

    monkeypatch.setattr(
        "src.agents.analyzer.decision_service.classify_ticket", decide
    )
    monkeypatch.setattr(
        "src.agents.analyzer.llm_provider.analyze_ticket", unexpected_llm
    )

    state = await ticket_analyzer_agent.analyze(
        {
            "subject": "Missing order and refund",
            "description": "My order was not received and I also need a refund.",
        }
    )

    assert state["analyzer_strategy"] == "jev"
    assert state["intent"] is IntentType.BILLING_DISPUTE
    assert state["department"] == "billing"
    assert state["decision_records"][0]["accepted"] is True


@pytest.mark.asyncio
async def test_analyzer_uses_jev_to_review_rule_candidate(monkeypatch):
    result = _result("intent_classification", {})

    async def decide(_text):
        return TicketIntentDecision(
            result=result,
            accepted=True,
            analysis={
                "intent": IntentType.BILLING_DISPUTE,
                "priority": "high",
                "department": "billing",
                "sentiment": "negative",
                "confidence_score": 0.93,
            },
        )

    async def unexpected_llm(_text):
        raise AssertionError("accepted Jev decision must skip Analyzer LLM")

    monkeypatch.setattr(
        "src.agents.analyzer.decision_service.classify_ticket", decide
    )
    monkeypatch.setattr(
        "src.agents.analyzer.llm_provider.analyze_ticket", unexpected_llm
    )

    state = await ticket_analyzer_agent.analyze(
        {"subject": "Refund", "description": "I need a refund for this charge."}
    )

    assert state["analyzer_strategy"] == "jev"
    assert state["intent"] is IntentType.BILLING_DISPUTE
    assert state["decision_records"][0]["accepted"] is True


@pytest.mark.asyncio
async def test_analyzer_keeps_rule_candidate_when_jev_is_rejected(monkeypatch):
    result = _result("intent_classification", {})

    async def decide(_text):
        return TicketIntentDecision(
            result=result,
            accepted=False,
            fallback_reason="low_confidence",
        )

    async def unexpected_llm(_text):
        raise AssertionError("a valid rule candidate must remain the fallback")

    monkeypatch.setattr(
        "src.agents.analyzer.decision_service.classify_ticket", decide
    )
    monkeypatch.setattr(
        "src.agents.analyzer.llm_provider.analyze_ticket", unexpected_llm
    )

    state = await ticket_analyzer_agent.analyze(
        {"subject": "Refund", "description": "I need a refund for this charge."}
    )

    assert state["analyzer_strategy"] == "rule"
    assert state["intent"] is IntentType.BILLING_DISPUTE
    assert state["decision_records"][0]["accepted"] is False
    assert state["decision_records"][0]["fallback_reason"] == "low_confidence"


@pytest.mark.asyncio
async def test_qa_uses_jev_when_rules_are_inconclusive(monkeypatch):
    result = _result("response_judgment", {})

    async def decide(**_kwargs):
        return QAReviewDecision(
            result=result,
            accepted=True,
            evaluation={
                "score": 0.92,
                "hallucination_detected": False,
                "citation_verified": True,
                "response_grounded": True,
                "response_requires_human": False,
            },
        )

    async def unexpected_llm(**_kwargs):
        raise AssertionError("accepted Jev decision must skip QA LLM")

    monkeypatch.setattr(
        "src.agents.quality_assurance.decision_service.judge_response", decide
    )
    monkeypatch.setattr(
        "src.agents.quality_assurance.llm_provider.evaluate_qa", unexpected_llm
    )

    state = await quality_assurance_agent.verify(
        {
            "description": "What is the result?",
            "suggested_response": "The final resolution is delta epsilon.",
            "context_citations": [
                Citation(source="policy", text="Policy alpha beta gamma.", score=0.8)
            ],
            "tool_context": {},
            "errors": [],
        }
    )

    assert state["qa_strategy"] == "jev"
    assert state["qa_score"] == 0.92
    assert state["risk_requires_human"] is False
    assert state["decision_records"][0]["accepted"] is True


@pytest.mark.asyncio
async def test_qa_uses_jev_to_review_rule_grounded_response(monkeypatch):
    result = _result("response_judgment", {})

    async def decide(**_kwargs):
        return QAReviewDecision(
            result=result,
            accepted=True,
            evaluation={
                "score": 0.91,
                "hallucination_detected": False,
                "citation_verified": True,
                "response_grounded": True,
                "response_requires_human": False,
            },
        )

    async def unexpected_llm(**_kwargs):
        raise AssertionError("accepted Jev decision must skip QA LLM")

    monkeypatch.setattr(
        "src.agents.quality_assurance.decision_service.judge_response", decide
    )
    monkeypatch.setattr(
        "src.agents.quality_assurance.llm_provider.evaluate_qa", unexpected_llm
    )

    state = await quality_assurance_agent.verify(
        {
            "description": "Where is my order?",
            "suggested_response": "The order is in transit [S1].",
            "context_citations": [
                Citation(source="order", text="The order is in transit.", score=0.9)
            ],
            "tool_context": {},
            "errors": [],
        }
    )

    assert state["qa_strategy"] == "jev"
    assert state["qa_score"] == 0.91
    assert state["decision_records"][0]["accepted"] is True


def _mode_answers(intent, intent_conf, mode, mode_conf, scope=None):
    answers = {
        "intent": DecisionAnswer(kind="choice", value=intent, confidence=intent_conf),
        "operation_mode": DecisionAnswer(
            kind="choice", value=mode, confidence=mode_conf
        ),
        "needs_clarification": DecisionAnswer(kind="noul", value=0.05),
    }
    if scope:
        answers["support_scope"] = DecisionAnswer(
            kind="choice", value=scope, confidence=0.95
        )
    return answers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "mode"),
    [
        ("feedback", "unclear"),
        ("account_support", "action"),
        ("outage_report", "unclear"),
    ],
)
async def test_decision_service_keeps_confident_intent_when_mode_is_unsure(
    intent, mode
):
    provider = _StubDecisionProvider(
        _result("intent_classification", _mode_answers(intent, 0.92, mode, 0.28))
    )

    decision = await DecisionService(provider).classify_ticket("text")

    assert decision.accepted is True
    assert decision.analysis["intent"].value == intent
    # “办事还是问”没把握时记为没有结论，由分类优先级表按“说不清”处理（第 21 条）。
    assert decision.analysis["operation_mode"] is None


@pytest.mark.asyncio
async def test_decision_service_still_rejects_low_intent_confidence_with_sure_mode():
    provider = _StubDecisionProvider(
        _result(
            "intent_classification",
            _mode_answers("order_status", 0.5, "information", 0.99),
        )
    )

    decision = await DecisionService(provider).classify_ticket("text")

    assert decision.accepted is False
    assert decision.fallback_reason == "low_confidence"
    # 逐项算没把握：类别不采纳，有把握的“只是问”照常保留。
    assert decision.analysis["intent"] is None
    assert decision.analysis["operation_mode"] == "information"


@pytest.mark.asyncio
async def test_decision_service_maps_eligibility_tone_and_mixed_request():
    answers = _mode_answers("billing_dispute", 0.95, "eligibility", 0.9)
    answers["customer_tone"] = DecisionAnswer(
        kind="choice", value="negative_emotion", confidence=0.9
    )
    answers["mixed_request"] = DecisionAnswer(kind="noul", value=0.85)
    decision = await DecisionService(
        _StubDecisionProvider(_result("intent_classification", answers))
    ).classify_ticket("text")

    assert decision.analysis["operation_mode"] == "eligibility"
    assert decision.analysis["customer_tone"] == "negative_emotion"
    assert decision.analysis["mixed_request"] is True


@pytest.mark.asyncio
async def test_decision_service_out_of_scope_needs_confident_operation_mode():
    unsure = _StubDecisionProvider(
        _result(
            "intent_classification",
            _mode_answers(
                "information_request", 0.95, "information", 0.4, "benign_out_of_scope"
            ),
        )
    )
    sure = _StubDecisionProvider(
        _result(
            "intent_classification",
            _mode_answers(
                "information_request", 0.95, "information", 0.95, "benign_out_of_scope"
            ),
        )
    )

    unsure_decision = await DecisionService(unsure).classify_ticket("weather?")
    sure_decision = await DecisionService(sure).classify_ticket("weather?")

    assert unsure_decision.accepted is True
    assert unsure_decision.analysis["out_of_scope"] is False
    assert sure_decision.analysis["out_of_scope"] is True
