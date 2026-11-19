"""生效设置摘要：只含非密钥项，用来在日志和评测报告里对照本地与线上的差异。"""

from typing import Any, Dict, List
from urllib.parse import urlparse

SUMMARY_FIELDS = (
    "LLM_PROVIDER",
    "LLM_MODEL_NAME",
    "LLM_FAST_MODEL_NAME",
    "LLM_THINKING",
    "LLM_RESOLVER_MAX_TOKENS",
    "LLM_QA_MAX_TOKENS",
    "TOOL_SELECTION_MODE",
    "DECISION_PROVIDER",
    "RESILIENCE_LLM_TIMEOUT_SECONDS",
    "RESILIENCE_LLM_MAX_RETRIES",
)


def _host(url: Any) -> str | None:
    """只留主机名，不带路径、查询和账号信息。"""
    text = str(url or "").strip()
    return (urlparse(text).hostname or None) if text else None


def effective_config(settings: Any) -> Dict[str, Any]:
    """返回影响模型行为的关键设置的实际生效值；LLM_THINKING 未设置时标明"由服务默认"。"""
    summary: Dict[str, Any] = {
        name: getattr(settings, name, None) for name in SUMMARY_FIELDS
    }
    if summary["LLM_THINKING"] in (None, ""):
        summary["LLM_THINKING"] = "unset(provider default)"
    summary["LLM_ENDPOINT_HOST"] = _host(getattr(settings, "LLM_BASE_URL", None))
    summary["LLM_FAST_ENDPOINT_HOST"] = _host(getattr(settings, "LLM_FAST_BASE_URL", None))
    return summary


def config_warnings(settings: Any) -> List[str]:
    """已知会出问题的设置组合。"""
    warnings: List[str] = []
    host = _host(getattr(settings, "LLM_BASE_URL", None)) or ""
    if "deepseek" in host and not getattr(settings, "LLM_THINKING", None):
        warnings.append(
            "LLM_THINKING 未设置：DeepSeek 默认开启思考，思考内容会占满回复字数上限，"
            "导致回复被截断、生成失败。建议设为 disabled。"
        )
    return warnings
