"""安全拦截与真实处理异常在用户侧的文案区分。"""

from src.main import _public_review_feedback


def test_security_block_is_risk_review_even_with_errors():
    output = {
        "security_threat_detected": True,
        "errors": ["Security threat: prompt injection"],
        "risk_requires_human": True,
    }
    assert _public_review_feedback(output)[0] == "risk_review"


def test_real_processing_error_stays_processing_exception():
    assert _public_review_feedback({"errors": ["Resolver dependency failure: X"]})[0] == (
        "processing_exception"
    )
