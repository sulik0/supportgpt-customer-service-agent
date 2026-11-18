"""State 里 `errors` 列表的统一解读。

`errors` 同时装三类记录：安全拦截、QA 幻觉告警、真实处理异常。下游只通过这里判断，
不要再各自对字符串做 `in` 匹配。写入端的前缀也在这里定义。
"""

from typing import Any, Mapping

SECURITY_ERROR_PREFIX = "Security threat"
QA_ALERT_PREFIX = "QA score alert"
QA_HALLUCINATION_ALERT = f"{QA_ALERT_PREFIX}: potential hallucination detected."


def security_error(threat_type: str, source: str) -> str:
    return f"{SECURITY_ERROR_PREFIX}: {threat_type} detected in {source}."


def _errors(state: Mapping[str, Any]) -> list[str]:
    return [str(item) for item in state.get("errors", []) or []]


def is_security_blocked(state: Mapping[str, Any]) -> bool:
    """安全拦截：看标志位；旧 Checkpoint 只写了错误记录时也算。"""
    return bool(state.get("security_threat_detected")) or any(
        item.startswith(SECURITY_ERROR_PREFIX) for item in _errors(state)
    )


def service_errors(state: Mapping[str, Any]) -> list[str]:
    """真实的处理异常，不含安全拦截和 QA 告警。"""
    return [
        item
        for item in _errors(state)
        if not item.startswith(SECURITY_ERROR_PREFIX)
        and not item.startswith(QA_ALERT_PREFIX)
    ]
