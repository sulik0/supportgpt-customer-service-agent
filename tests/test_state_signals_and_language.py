"""errors 列表的统一解读与回复语言的统一判断。"""

from src.agents.public_reply import public_review_feedback
from src.agents.resolver import ResolutionAgent
from src.agents.scope import scope_boundary_response
from src.agents.signals import (
    QA_HALLUCINATION_ALERT,
    is_security_blocked,
    security_error,
    service_errors,
)
from src.guardrails.security_policy import build_security_block
from src.llm.provider import resolution_language_directive
from src.models.language import reply_language, requested_language
from src.risk.engine import risk_engine


def test_security_blocked_reads_flag_or_old_error_record():
    assert is_security_blocked({"security_threat_detected": True})
    # 旧 Checkpoint 只有错误记录、没有标志位。
    assert is_security_blocked({"errors": [security_error("Jailbreak", "user_input")]})
    assert not is_security_blocked({"errors": ["Resolver dependency failure: X"]})


def test_service_errors_excludes_security_and_qa_alert():
    state = {
        "errors": [
            security_error("Prompt injection", "rag_document"),
            QA_HALLUCINATION_ALERT,
            "Resolver dependency failure: TimeoutError",
        ]
    }
    assert service_errors(state) == ["Resolver dependency failure: TimeoutError"]


def test_hallucination_alert_is_not_counted_as_workflow_error():
    state = {
        "hallucination_detected": True,
        "qa_score": 0.4,
        "errors": [QA_HALLUCINATION_ALERT],
    }
    reasons = risk_engine.assess(state, stage="output").reasons
    assert "hallucination_detected" in reasons
    assert "workflow_error" not in reasons


def test_reply_language_rules():
    assert reply_language("我的订单到哪了") == "zh"
    assert reply_language("Where is my order?") == "en"
    assert reply_language("请用英语回答：我的订单到哪了") == "en"
    assert reply_language("Please reply in Chinese, where is my order") == "zh"
    assert requested_language("我的订单到哪了") is None


def test_explicit_english_request_is_followed_by_every_system_text():
    """以前只有范围外说明认显式要求，降级、安全拦截和公开说明按原文是否含中文。"""
    text = "请用英语回答：我的订单 ORD-7001 到哪了"
    state = {"subject": "", "description": text, "scope_reason": "weather"}
    assert scope_boundary_response(state).startswith("I help")
    assert ResolutionAgent._failure_response(text).startswith("I")
    assert build_security_block(
        state, threat_type="t", source="user_input", risk_score=1.0, findings=[]
    )["suggested_response"].startswith("I cannot")
    _, response, _ = public_review_feedback({"intent": "order_status"}, text)
    assert response.startswith("We have received")
    # 显式要求时 Resolver 不追加语言指令，交给 Prompt 规则处理。
    assert resolution_language_directive(text) == ""
