"""Shared data protection for every observability backend."""

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime
from typing import Any, Dict


EMAIL_PATTERN = re.compile(
    r"(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9])",
    re.IGNORECASE,
)
PHONE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:\+?\d[\d \t().-]{7,}\d)(?![A-Za-z0-9_])"
)
SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CREDIT_CARD_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
INLINE_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;]+",
        re.IGNORECASE,
    ),
)

SECRET_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "cookie",
    "set-cookie",
    "set_cookie",
    "token",
    "jwt",
    "jwt_token",
}

BUSINESS_ID_KEYS = {
    "customer_id",
    "order_id",
    "session_id",
    "tracking_id",
    "invoice_id",
}
SYSTEM_ID_KEYS = {
    "request_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "prompt_bundle_id",
}
BUSINESS_ID_PATTERN = re.compile(
    r"(?i)\b(?:ORD|ORDER|CUST|CUSTOMER|TKT|TICKET|TRK|TRACKING|SESSION|INV|INVOICE|DEMO)[-_][A-Z0-9-]+\b"
)
ISO_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?(?![A-Za-z0-9_])"
)
UUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")
INLINE_FIELD_PATTERN = re.compile(
    r"""(?P<prefix>["']?(?P<key>[A-Za-z_][\w.-]*)["']?\s*[:=]\s*)(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;{}\]\n]+)"""
)
_ALIAS_KEY = secrets.token_bytes(32)


def business_id_alias(value: Any) -> str:
    """用进程内随机密钥生成稳定别名，同一证据和 Trace 保留实体对应关系。"""
    text = str(value)
    if re.fullmatch(r"\[BUSINESS_ID_[0-9a-f]{16}\]", text):
        return text
    digest = hmac.new(_ALIAS_KEY, text.upper().encode(), hashlib.sha256).hexdigest()[
        :16
    ]
    return f"[BUSINESS_ID_{digest}]"


def _normalize_key(key: str) -> str:
    return key.lower().replace("-", "_").replace(".", "_")


def _safe_system_id(key: str, value: Any) -> bool:
    return (
        _normalize_key(key) in SYSTEM_ID_KEYS
        and isinstance(value, str)
        and bool(
            re.fullmatch(
                r"(?:[0-9a-fA-F]{16}|[0-9a-fA-F]{32}|[0-9a-fA-F]{64}|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})",
                value,
            )
        )
    )


SENSITIVE_BUSINESS_KEYS = {
    "address",
    "bank_account",
    "card_number",
    "credit_card",
    "customer_id",
    "customer_email",
    "customer_name",
    "drafted_response",
    "email",
    "items",
    "modified_response",
    "name",
    "order_id",
    "payment_details",
    "payment_method",
    "phone",
    "session_id",
    "ssn",
    "total_amount",
}


def redact_text(value: str, *, preserve_entity_links: bool = False) -> str:
    protected: dict[str, str] = {}

    def protect(text: str) -> str:
        placeholder = f"[SAFE_VALUE_{len(protected)}]"
        protected[placeholder] = text
        return placeholder

    # 先处理密钥，防止数字匹配或日期保护留下部分 Secret。
    for pattern in INLINE_SECRET_PATTERNS:
        value = pattern.sub("[REDACTED_SECRET]", value)

    def redact_field(match: re.Match[str]) -> str:
        key = _normalize_key(match.group("key"))
        raw = match.group("value")
        try:
            item = json.loads(raw)
        except (ValueError, TypeError):
            item = raw.strip("\"'")
        if _safe_system_id(key, item):
            return match.group("prefix") + json.dumps(protect(item))
        if key in BUSINESS_ID_KEYS:
            if not preserve_entity_links:
                return match.group(0)
            replacement = business_id_alias(item)
        elif is_sensitive_key(key):
            replacement = "[FILTERED]"
        else:
            return match.group(0)
        return match.group("prefix") + json.dumps(replacement)

    value = INLINE_FIELD_PATTERN.sub(redact_field, value)

    def redact_identifier(match: re.Match[str]) -> str:
        text = match.group(0)
        # 字段名不是业务实体；仍保护它们的实际值。
        if (
            not any(char.isdigit() for char in text)
            or _normalize_key(text) in BUSINESS_ID_KEYS
            or re.match(r"""["']?\s*[:=]""", value[match.end() :])
        ):
            return text
        return business_id_alias(text)

    # 内部 Memory 依赖真实订单号；仅对外部观测/决策内容生成实体别名。
    if preserve_entity_links:
        value = BUSINESS_ID_PATTERN.sub(redact_identifier, value)

    def preserve(match: re.Match[str]) -> str:
        text = match.group(0)
        if ":" in text or re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            try:
                datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return text
        return protect(text)

    value = ISO_PATTERN.sub(preserve, value)
    value = UUID_PATTERN.sub(preserve, value)
    value = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", value)
    value = SSN_PATTERN.sub("[REDACTED_SSN]", value)
    value = CREDIT_CARD_PATTERN.sub("[REDACTED_CARD]", value)
    value = PHONE_PATTERN.sub("[REDACTED_PHONE]", value)
    for placeholder, original in protected.items():
        value = value.replace(placeholder, original)
    return value


def is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return (
        normalized in SECRET_KEYS
        or normalized in BUSINESS_ID_KEYS
        or normalized in SENSITIVE_BUSINESS_KEYS
        or normalized.endswith("_api_key")
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
        or normalized.endswith("_authorization")
        or normalized.endswith("_access_token")
        or normalized.endswith("_refresh_token")
        or normalized.endswith("_cookie")
    )


def sanitize_value(value: Any, *, preserve_entity_links: bool = False) -> Any:
    """Create a telemetry-safe representation without changing business data."""
    if isinstance(value, str):
        return redact_text(value, preserve_entity_links=preserve_entity_links)
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _safe_system_id(key_text, item):
                sanitized[key_text] = item
            elif preserve_entity_links and _normalize_key(key_text) in BUSINESS_ID_KEYS:
                sanitized[key_text] = (
                    business_id_alias(item) if item is not None else None
                )
            else:
                sanitized[key_text] = (
                    "[FILTERED]"
                    if is_sensitive_key(key_text)
                    else sanitize_value(
                        item, preserve_entity_links=preserve_entity_links
                    )
                )
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [
            sanitize_value(item, preserve_entity_links=preserve_entity_links)
            for item in value
        ]
    if hasattr(value, "model_dump"):
        return sanitize_value(
            value.model_dump(), preserve_entity_links=preserve_entity_links
        )
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return f"<{value.__class__.__name__}>"


def sanitize_attributes(attributes: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize values to OpenTelemetry-compatible scalar/sequence attributes."""
    output: Dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        # 仅允许固定字段中的合法内容 Hash 原样通过，避免长数字片段被当成电话。
        if _safe_system_id(key, value):
            output[key] = value
            continue
        safe_value = sanitize_value({key: value}, preserve_entity_links=True)[key]
        if isinstance(safe_value, (str, bool, int, float)):
            output[key] = safe_value
        elif isinstance(safe_value, list) and all(
            isinstance(item, (str, bool, int, float)) for item in safe_value
        ):
            output[key] = safe_value
        else:
            output[key] = str(safe_value)
    return output
