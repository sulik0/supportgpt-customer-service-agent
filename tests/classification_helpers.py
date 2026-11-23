"""测试用：模拟 Jev 和 LLM 对分类问题的回答。"""

from src.agents.classification import normalize_answers
from src.decision.models import DecisionResult, TicketIntentDecision


def answers(**overrides):
    """默认是一条有把握的普通售后咨询；传 None 表示该项没把握。"""
    base = {
        "support_scope": "support",
        "request": "information",
        "complaint": False,
        "account_security": False,
        "urgent_outage": False,
        "negative_emotion": False,
        "answerable_question": False,
        "intent": "information_request",
    }
    base.update(overrides)
    return {key: (value.value if hasattr(value, "value") else value) for key, value in base.items()}


def _result(available=True):
    return DecisionResult(
        enabled=available,
        available=available,
        provider="test",
        operation="intent_classification",
        question_set_version="v",
        model="m",
    )


def patch_jev(monkeypatch, **overrides):
    """Jev 给出指定答案。"""
    analysis = normalize_answers(answers(**overrides))

    async def decide(_text):
        return TicketIntentDecision(result=_result(), accepted=True, analysis=analysis)

    monkeypatch.setattr("src.agents.analyzer.decision_service.classify_ticket", decide)


def patch_jev_unavailable(monkeypatch):
    async def decide(_text):
        return TicketIntentDecision(
            result=_result(available=False),
            accepted=False,
            fallback_reason="provider_unavailable",
        )

    monkeypatch.setattr("src.agents.analyzer.decision_service.classify_ticket", decide)


def patch_llm(monkeypatch, calls=None, fail=False, **overrides):
    """LLM 复核给出指定答案；calls 记录调用次数。"""
    result = normalize_answers(answers(**overrides))

    async def classify(_text):
        if calls is not None:
            calls.append(_text)
        if fail:
            raise RuntimeError("llm down")
        return result, 10, 10

    monkeypatch.setattr("src.agents.analyzer.llm_provider.classify_message", classify)
