"""应用内 Span 过滤：直连 LangSmith 时只导出 Agent 链路，避免噪音 Span。"""

import logging
from typing import Any, Optional
from urllib.parse import urlparse

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)

logger = logging.getLogger("supportgpt.observability.span_filter")

AGENT_EXPORT_ATTRIBUTE = "langsmith.export"
# 在 Agent 上下文内创建的 Span 打此标记，导出时据此保留整条链路的子 Span。
AGENT_SCOPE_ATTRIBUTE = "supportgpt.agent_scope"
# 即使处于 Agent 上下文，Redis / SQL 的存取细节也只是噪音，除非显式标记导出。
_NOISY_SCOPE_PREFIXES = (
    "opentelemetry.instrumentation.redis",
    "opentelemetry.instrumentation.sqlalchemy",
)


def should_export_agent_spans_only(
    configured: Optional[bool], traces_endpoint: Optional[str]
) -> bool:
    """显式配置优先；未配置时，直连 LangSmith 才默认开启过滤。"""
    if configured is not None:
        return configured
    try:
        host = urlparse(traces_endpoint or "").hostname or ""
    except ValueError:
        return False
    return host == "smith.langchain.com" or host.endswith(".smith.langchain.com")


def should_export_span(span: ReadableSpan) -> bool:
    """只放行显式标记的 Agent Span，以及 Agent 上下文内的非噪音子 Span。"""
    attributes = span.attributes or {}
    if attributes.get(AGENT_EXPORT_ATTRIBUTE) is True:
        return True
    if not attributes.get(AGENT_SCOPE_ATTRIBUTE):
        return False
    scope = getattr(span, "instrumentation_scope", None)
    scope_name = getattr(scope, "name", "") or ""
    return not scope_name.startswith(_NOISY_SCOPE_PREFIXES)


class AgentSpansOnlyProcessor(SpanProcessor):
    """包装下游 Processor，仅把 Agent 链路 Span 交给它；自身绝不抛异常。"""

    def __init__(self, delegate: SpanProcessor) -> None:
        self._delegate = delegate

    def on_start(self, span: Span, parent_context: Optional[Context] = None) -> None:
        try:
            from src.observability.tracing import is_langsmith_agent_context

            if is_langsmith_agent_context():
                span.set_attribute(AGENT_SCOPE_ATTRIBUTE, True)
        except Exception as exc:
            logger.debug("Unable to mark agent span scope: %s", exc)

    def on_end(self, span: ReadableSpan) -> None:
        try:
            if should_export_span(span):
                self._delegate.on_end(span)
        except Exception as exc:
            logger.debug("Agent span filter failed: %s", exc)

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)


class DropOptionsSampler(Sampler):
    """丢弃 CORS 预检（OPTIONS）请求的 Span，其余交给原采样器。"""

    def __init__(self, delegate: Sampler) -> None:
        self._delegate = delegate

    def should_sample(
        self, parent_context, trace_id, name, kind=None, attributes=None, links=None, trace_state=None
    ) -> SamplingResult:
        method = (attributes or {}).get("http.request.method") or (
            attributes or {}
        ).get("http.method")
        if str(method or "").upper() == "OPTIONS":
            return SamplingResult(Decision.DROP)
        return self._delegate.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return f"DropOptionsSampler({self._delegate.get_description()})"


def build_sampler(ratio: float) -> Sampler:
    """构造默认采样器：ParentBased(TraceIdRatio) 外加 OPTIONS 丢弃。"""
    return DropOptionsSampler(ParentBased(TraceIdRatioBased(ratio)))
