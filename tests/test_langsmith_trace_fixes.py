"""LangSmith 直连链路的确定性测试：Span 过滤、Span Link、计价、引用标记清理。"""

import logging
from datetime import datetime, timezone

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.sdk.trace.sampling import Decision

from src.agents.public_text import strip_internal_citations
from src.models.schemas import (
    PublicConversationMessageResponse,
    PublicSupportResponse,
)
from src.observability import cost_tracking
from src.observability.cost_tracking import calculate_llm_cost
from src.observability.span_filter import (
    AgentSpansOnlyProcessor,
    DropOptionsSampler,
    build_sampler,
    should_export_agent_spans_only,
)
from src.observability.tracing import (
    bind_request_id,
    langsmith_agent_trace_context,
    langsmith_span_attributes,
    observed_span,
    reset_request_id,
)


def _provider(agent_only=True):
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider = TracerProvider()
    provider.add_span_processor(
        AgentSpansOnlyProcessor(processor) if agent_only else processor
    )
    return provider, exporter


def test_filter_exports_agent_trace_only():
    provider, exporter = _provider()
    tracer = provider.get_tracer("app")
    with tracer.start_as_current_span("api.POST"):  # HTTP Span：不应导出
        with langsmith_agent_trace_context():
            with observed_span(
                tracer,
                "workflow",
                langsmith_span_attributes("chain", force=True),
                root=True,
            ):
                with tracer.start_as_current_span("tool.child"):
                    pass
                redis_tracer = provider.get_tracer(
                    "opentelemetry.instrumentation.redis"
                )
                with redis_tracer.start_as_current_span("GET"):
                    pass
    with tracer.start_as_current_span("staff.review-queue"):
        pass
    names = {span.name for span in exporter.get_finished_spans()}
    assert names == {"workflow", "tool.child"}


def test_filter_disabled_keeps_all_spans():
    provider, exporter = _provider(agent_only=False)
    tracer = provider.get_tracer("app")
    with tracer.start_as_current_span("api.GET"):
        pass
    assert [s.name for s in exporter.get_finished_spans()] == ["api.GET"]


def test_filter_never_raises():
    class Boom:
        def on_end(self, span):
            raise RuntimeError("boom")

        def shutdown(self):
            pass

        def force_flush(self, timeout_millis=30000):
            return True

    provider = TracerProvider()
    provider.add_span_processor(AgentSpansOnlyProcessor(Boom()))
    tracer = provider.get_tracer("app")
    with langsmith_agent_trace_context():
        with tracer.start_as_current_span("x"):
            pass


def test_agent_spans_only_default_depends_on_endpoint():
    langsmith = "https://api.smith.langchain.com/otel/v1/traces"
    assert should_export_agent_spans_only(None, langsmith) is True
    assert should_export_agent_spans_only(None, "http://collector:4318/v1/traces") is False
    assert should_export_agent_spans_only(None, None) is False
    assert should_export_agent_spans_only(False, langsmith) is False
    assert should_export_agent_spans_only(True, "http://collector:4318") is True


def test_options_requests_are_dropped_by_sampler():
    sampler = build_sampler(1.0)
    assert isinstance(sampler, DropOptionsSampler)
    drop = sampler.should_sample(
        None, 1, "OPTIONS /x", attributes={"http.request.method": "OPTIONS"}
    )
    keep = sampler.should_sample(
        None, 1, "POST /x", attributes={"http.method": "POST"}
    )
    assert drop.decision == Decision.DROP
    assert keep.decision == Decision.RECORD_AND_SAMPLE


def test_workflow_root_links_to_http_span():
    provider, exporter = _provider(agent_only=False)
    tracer = provider.get_tracer("app")
    token = bind_request_id("req-123")
    try:
        with tracer.start_as_current_span("api.POST") as http_span:
            http_ctx = http_span.get_span_context()
            with observed_span(
                tracer, "workflow", root=True, link_to_current=True
            ):
                pass
    finally:
        reset_request_id(token)
    workflow = next(s for s in exporter.get_finished_spans() if s.name == "workflow")
    assert workflow.context.trace_id != http_ctx.trace_id  # 仍是独立根
    assert workflow.parent is None
    assert len(workflow.links) == 1
    assert workflow.links[0].context.span_id == http_ctx.span_id
    assert workflow.attributes["http.trace_id"] == format(http_ctx.trace_id, "032x")
    assert workflow.attributes["request_id"] == "req-123"


def test_root_without_http_span_has_no_link():
    provider, exporter = _provider(agent_only=False)
    tracer = provider.get_tracer("app")
    with observed_span(tracer, "workflow", root=True, link_to_current=True):
        pass
    span = exporter.get_finished_spans()[0]
    assert not span.links
    assert "http.trace_id" not in span.attributes


def test_deepseek_flash_cost_is_not_zero():
    # 1M 输入 $0.30 + 1M 输出 $1.20（高峰价）
    assert calculate_llm_cost("deepseek-flash", 1_000_000, 1_000_000) == 1.5
    assert calculate_llm_cost("DeepSeek-V4-Flash", 1000, 0) == 0.0003
    assert calculate_llm_cost("deepseek-v4-pro", 1000, 1000) == 0.00528


def test_unknown_model_warns_once_and_mock_is_silent():
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    # 项目日志配置可能关闭传播，直接挂 Handler 捕获。
    logger = logging.getLogger("supportgpt.observability.cost")
    handler = Capture(level=logging.WARNING)
    logger.addHandler(handler)
    cost_tracking._warned_models.clear()
    try:
        assert calculate_llm_cost("brand-new-model", 1000, 1000) == 0.0
        assert calculate_llm_cost("brand-new-model", 1000, 1000) == 0.0
        assert calculate_llm_cost("mock-support-v1", 1000, 1000) == 0.0
    finally:
        logger.removeHandler(handler)
    assert len(records) == 1
    assert "brand-new-model" in records[0].getMessage()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("退款需要3天[S1]。", "退款需要3天。"),
        ("退款需要3天（S2）。", "退款需要3天。"),
        ("退款需要3天 (S2)。", "退款需要3天。"),
        ("按规定处理［S1］，谢谢", "按规定处理，谢谢"),
        ("见[S1][S2]。", "见。"),
        ("依据 S1、S2，已为您处理", "依据，已为您处理"),
        ("Refund in 3 days [S1] as stated.", "Refund in 3 days as stated."),
        ("Refund (S1, S2).", "Refund."),
        ("（来源：S1）好的", "好的"),
        ("S3 bucket 与订单 A1 无关", "S3 bucket 与订单 A1 无关"),
        ("", ""),
    ],
)
def test_strip_internal_citations(raw, expected):
    assert strip_internal_citations(raw) == expected


def test_public_schemas_strip_markers_but_keep_user_messages():
    now = datetime.now(timezone.utc)
    reply = PublicSupportResponse(
        ticket_id=1,
        session_id="s",
        status="answered",
        response="已为您登记（S2）。",
        message="ok",
        created_at=now,
    )
    assert reply.response == "已为您登记。"
    assistant = PublicConversationMessageResponse(
        id="1", role="assistant", status="final", content="好的[S1]", created_at=now
    )
    user = PublicConversationMessageResponse(
        id="2", role="user", status="final", content="什么是[S1]", created_at=now
    )
    assert assistant.content == "好的"
    assert user.content == "什么是[S1]"
