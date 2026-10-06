"""从实际节点、SDK 响应和导出的 Span 验证线上 Trace 问题。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import BaseModel

from src.agents.evidence import build_resolution_evidence
from src.agents.graph import build_ticket_state, create_agent_graph
from src.agents.quality_assurance import quality_assurance_agent
from src.agents.resolver import resolution_agent
from src.agents.tooling import tooling_agent
from src.config import settings
from src.decision.provider import JevDecisionProvider, _sanitize_decision_state
from src.decision.service import DecisionService
from src.llm.provider import AzureOpenAILLMProvider, OpenAILLMProvider
from src.models.schemas import Citation
from src.observability.sanitization import (
    business_id_alias,
    redact_text,
    sanitize_value,
)
from src.observability.tracing import (
    bind_request_id,
    get_request_id,
    langsmith_agent_trace_context,
    reset_request_id,
    serialize_llm_content,
)
from src.promptops.defaults import RESOLUTION_OUTPUT_POLICY, default_payload
from src.promptops.registry import PromptBundle
from src.promptops.runtime import prompt_scope
from src.tools.registry import ToolDefinition, ToolRegistry, tool_registry


QUESTION = "我的订单还没有收到，能帮我查一下吗？"
ANSWER = "您的订单 ORD-8002 仍在运输中，目前配送延迟。建议联系承运商调查配送进度。[S1]"
FACTS = ("in_transit", "delivery_delayed", "carrier_investigation")


@pytest.fixture
async def evidence_judge(monkeypatch):
    """模拟只根据收到的证据判断物流回答，走真实 Jev Adapter 和评分映射。"""
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        if "current_ticket" in payload["state"]:
            return httpx.Response(
                200,
                json={
                    "answers": {
                        "intent": {
                            "type": "choice",
                            "choice": "order_status",
                            "confidence": 0.97,
                        },
                        "operation_mode": {
                            "type": "choice",
                            "choice": "information",
                            "confidence": 0.97,
                        },
                        "needs_clarification": {"type": "noul", "noul": 0.02},
                    }
                },
            )
        calls.append(payload["state"])
        evidence = "\n".join(payload["state"]["evidence"])
        supported = all(fact in evidence for fact in FACTS) and "[S1]" in evidence
        supported = supported and "已送达" not in payload["state"]["answer"]
        return httpx.Response(
            200,
            json={
                "answers": {
                    "grounding": {
                        "type": "choice",
                        "choice": "supported" if supported else "unsupported",
                        "confidence": 0.94,
                    },
                    "task_completion": {
                        "type": "score",
                        "score": 3.0 if supported else 2.55,
                        "confidence": 0.95,
                    },
                    "citation_status": {
                        "type": "choice",
                        "choice": "verified",
                        "confidence": 0.95,
                    },
                    "unsupported_commitment": {"type": "noul", "noul": 0.02},
                    "needs_human_review": {
                        "type": "noul",
                        "noul": 0.03 if supported else 0.81,
                    },
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = DecisionService(JevDecisionProvider(client=client))
    monkeypatch.setattr("src.agents.quality_assurance.decision_service", service)
    monkeypatch.setattr("src.agents.analyzer.decision_service", service)
    monkeypatch.setattr(settings, "RESILIENCE_ENABLED", False)
    yield calls
    await client.aclose()


def _citations():
    return [
        Citation(
            source="shipping_policy",
            text="配送延迟时，建议联系承运商调查配送进度。" * 500,
            version="v1",
        )
    ]


@pytest.mark.asyncio
async def test_logistics_workflow_keeps_tool_facts_and_citation_for_jev(
    monkeypatch, evidence_judge, tmp_path
):
    generated = []

    async def generate(**kwargs):
        generated.append(kwargs["context"])
        return ANSWER, 50, 35

    async def retrieve(state):
        return {**state, "context_citations": _citations()}

    monkeypatch.setattr(
        "src.agents.resolver.llm_provider.generate_resolution", generate
    )
    monkeypatch.setattr(
        "src.agents.retriever.knowledge_retriever_agent.retrieve", retrieve
    )
    monkeypatch.setattr(settings, "PROMPT_REGISTRY_DIR", str(tmp_path))
    state = build_ticket_state(
        {
            "customer_id": "cust_102",
            "subject": "订单物流",
            "description": QUESTION,
            "durable_execution_enabled": False,
        }
    )
    result = await create_agent_graph().ainvoke(state)

    assert result["intent"] == "order_status"
    assert any(
        call["tool_name"] == "shipping.get_shipments" and call["status"] == "success"
        for call in result["tool_calls"]
    )
    assert generated == ["\n\n".join(result["resolution_evidence"])]
    assert evidence_judge[0]["evidence"] == _sanitize_decision_state(
        result["resolution_evidence"]
    )
    assert all(
        fact in generated[0] and fact in str(evidence_judge[0]["evidence"])
        for fact in FACTS
    )
    assert "[S1] shipping_policy (version=v1)" in generated[0]
    assert result["qa_strategy"] == "jev"
    assert result["hallucination_detected"] is False
    assert result["response_grounded"] is True
    assert result["citation_verified"] is True
    assert result["response_requires_human"] is False
    assert result["suggested_response"] == ANSWER


@pytest.mark.asyncio
async def test_qa_reuses_frozen_evidence_after_context_and_budget_change(
    monkeypatch, evidence_judge
):
    enriched = await tooling_agent.enrich(
        {"customer_id": "cust_102", "intent": "order_status", "description": QUESTION}
    )
    state = {
        **enriched,
        "subject": "物流",
        "context_citations": _citations(),
        "description": QUESTION,
    }
    monkeypatch.setattr(
        "src.agents.resolver.llm_provider.generate_resolution",
        AsyncMock(return_value=(ANSWER, 20, 30)),
    )
    resolved = await resolution_agent.resolve(state)
    original = list(resolved["resolution_evidence"])
    resolved["tool_context"] = {}
    resolved["context_citations"] = []
    monkeypatch.setattr(settings, "LLM_QA_MAX_CONTEXT_CHARS", 500)
    checked = await quality_assurance_agent.verify(resolved)

    assert checked["resolution_evidence"] == original
    assert evidence_judge[0]["evidence"] == _sanitize_decision_state(original)
    assert checked["response_grounded"] is True


@pytest.mark.asyncio
async def test_old_state_without_snapshot_still_passes_service_evidence_to_qa(
    evidence_judge,
):
    enriched = await tooling_agent.enrich(
        {"customer_id": "cust_102", "intent": "order_status", "description": QUESTION}
    )
    checked = await quality_assurance_agent.verify(
        {
            **enriched,
            "description": QUESTION,
            "context_citations": _citations(),
            "suggested_response": ANSWER,
        }
    )
    assert all(fact in str(evidence_judge[0]["evidence"]) for fact in FACTS)
    assert checked["response_grounded"] is True


@pytest.mark.asyncio
async def test_complete_evidence_does_not_override_unsupported_jev_result(
    evidence_judge,
):
    enriched = await tooling_agent.enrich(
        {"customer_id": "cust_102", "intent": "order_status", "description": QUESTION}
    )
    checked = await quality_assurance_agent.verify(
        {
            **enriched,
            "description": QUESTION,
            "context_citations": _citations(),
            "suggested_response": "您的订单已送达。[S1]",
        }
    )
    assert checked["qa_strategy"] == "jev"
    assert checked["hallucination_detected"] is True
    assert checked["response_requires_human"] is True
    assert checked["qa_score"] <= 0.49


def test_shared_budget_preserves_logistics_facts_and_valid_json(monkeypatch):
    monkeypatch.setattr(settings, "LLM_QA_MAX_CONTEXT_CHARS", 700)
    tool = {
        "service_query": {
            "tool": "shipping.get_shipments",
            "status": "success",
            "data": {
                "status": "found",
                "records": [
                    {
                        "order_id": "ORD-8002",
                        "status": FACTS[0],
                        "exception": FACTS[1],
                        "next_step": FACTS[2],
                    }
                ],
            },
        },
        "past_tickets": [{"resolution": "irrelevant history" * 1000}],
    }
    evidence = build_resolution_evidence(
        {"tool_context": tool, "context_citations": _citations()}
    )
    assert sum(map(len, evidence)) <= 700
    assert all(fact in evidence[0] for fact in FACTS)
    assert (
        json.loads(evidence[0][7:])["service_query"]["data"]["records"][0]["next_step"]
        == FACTS[2]
    )
    assert "irrelevant history" not in " ".join(evidence)
    assert tool["past_tickets"][0]["resolution"].startswith("irrelevant")


def _completion(content, reason, input_tokens=20, output_tokens=30):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason=reason
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=input_tokens, completion_tokens=output_tokens
        ),
    )


def _provider(kind, client, monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_MODEL_NAME", "test-model")
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODEL_NAME", None)
    monkeypatch.setattr(settings, "RESILIENCE_ENABLED", False)
    if kind == "openai":
        with patch("openai.AsyncOpenAI", return_value=client):
            return OpenAILLMProvider()
    with patch("openai.AsyncAzureOpenAI", return_value=client):
        return AzureOpenAILLMProvider()


@pytest.mark.parametrize("kind", ["openai", "azure"])
@pytest.mark.asyncio
async def test_resolver_rewrites_truncated_reply_with_same_evidence(kind, monkeypatch):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=[
            _completion("您的订单仍在运输中，下一步需要", "length", 40, 320),
            _completion(ANSWER, "stop", 45, 50),
        ]
    )
    provider = _provider(kind, client, monkeypatch)
    legacy = default_payload()
    legacy["templates"]["resolver"]["system"] = "Reply using only supplied context."
    with prompt_scope(PromptBundle(legacy)):
        reply, input_tokens, output_tokens = await provider.generate_resolution(
            "物流", QUESTION, "in_transit delivery_delayed carrier_investigation"
        )
    calls = client.chat.completions.create.await_args_list
    assert reply == ANSWER
    assert reply.split("[S1]")[0].endswith("。")
    assert input_tokens == 85 and output_tokens == 370
    assert len(calls) == 2
    assert calls[0].kwargs["messages"][1] == calls[1].kwargs["messages"][1]
    assert all(
        call.kwargs["max_tokens"] == settings.LLM_RESOLVER_MAX_TOKENS for call in calls
    )
    assert RESOLUTION_OUTPUT_POLICY in calls[0].kwargs["messages"][0]["content"]
    assert "at most 3 short sentences" in calls[1].kwargs["messages"][0]["content"]
    assert "下一步需要" not in str(calls[1].kwargs["messages"])


@pytest.mark.parametrize("kind", ["openai", "azure"])
@pytest.mark.asyncio
async def test_resolver_never_returns_second_truncated_reply(kind, monkeypatch):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=[
            _completion("您的订单仍在", "length"),
            _completion("建议联系承运商进一步", "length"),
        ]
    )
    provider = _provider(kind, client, monkeypatch)
    monkeypatch.setattr("src.agents.resolver.llm_provider", provider)
    result = await resolution_agent.resolve(
        {"subject": "物流", "description": QUESTION}
    )
    assert (
        result["suggested_response"]
        == "抱歉，当前暂时无法生成可靠回复，已转交人工客服复核。"
    )
    assert result["degradation_level"] == "human_required"
    assert client.chat.completions.create.await_count == 2


@pytest.mark.asyncio
async def test_resolver_complete_reply_does_not_add_a_call(monkeypatch):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_completion(ANSWER, "stop"))
    provider = _provider("openai", client, monkeypatch)
    reply, _, _ = await provider.generate_resolution("物流", QUESTION, "facts")
    assert reply == ANSWER
    assert client.chat.completions.create.await_count == 1


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-05T12:30:08+00:00",
        "2026-10-05T12:30:08.123456+08:00",
        "2026-10-05T12:30:08Z",
        "2026-10-05 12:30:08",
        "2026-10-05",
    ],
)
def test_redaction_preserves_datetime_and_masks_neighbor_phone(timestamp):
    redacted = redact_text(
        f"updated_at={timestamp} 更新时间{timestamp}。电话13800138000 邮箱alice@example.com"
    )
    assert timestamp in redacted
    assert f"更新时间{timestamp}。" in redacted
    assert "13800138000" not in redacted
    assert "alice@example.com" not in redacted


def test_redaction_keeps_ids_without_allowing_secrets():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123456"
    payload = {
        "request_id": "12345678-1234-1234-1234-123456789012",
        "trace_id": "1" * 32,
        "span_id": "2" * 16,
        "authorization": "Bearer secret-value",
        "llm_api_key": "sk-abcdefghijklmnopqrstuvwxyz",
        "jwt": jwt,
        "message": f"Bearer secret-value; {jwt}; api_key=1234567890123456",
    }
    safe = sanitize_value(payload)
    assert all(
        safe[key] == payload[key] for key in ("request_id", "trace_id", "span_id")
    )
    assert "secret-value" not in str(safe) and jwt not in str(safe)
    assert "1234567890123456" not in str(safe)
    encoded_ids = json.dumps({key: payload[key] for key in ("trace_id", "span_id")})
    assert redact_text(encoded_ids) == encoded_ids
    token = bind_request_id(payload["request_id"])
    try:
        assert get_request_id() == payload["request_id"]
    finally:
        reset_request_id(token)


def test_entity_alias_is_stable_across_jev_evidence_answer_and_trace():
    entity = "ORD-8002"
    state = {
        "order_id": entity,
        "customer_question": f"查询 {entity}",
        "evidence": [json.dumps({"order_id": entity, "status": "in_transit"})],
        "answer": f"订单 {entity} 仍在运输中。",
    }
    safe = _sanitize_decision_state(state)
    alias = safe["order_id"]
    assert alias.startswith("[BUSINESS_ID_")
    assert (
        alias in safe["customer_question"]
        and alias in safe["answer"]
        and alias in safe["evidence"][0]
    )
    assert entity not in str(safe)
    assert alias in serialize_llm_content(state)
    assert sanitize_value(safe, preserve_entity_links=True) == safe
    assert business_id_alias(entity.lower()) == alias
    assert business_id_alias("ORD-9003") != alias


def test_internal_memory_redaction_keeps_business_entities():
    content = "请查询订单 ORD-8002，order_id=ORD-8002，邮箱alice@example.com"
    internal = redact_text(content)
    assert "ORD-8002" in internal
    assert "order_id=ORD-8002" in internal
    assert "alice@example.com" not in internal
    assert "ORD-8002" not in serialize_llm_content(content)


def test_redaction_preserves_citation_names_and_never_protects_secret_datetimes():
    safe = serialize_llm_content(
        {
            "evidence": "[S1] invoice_policy (version=v1): 付款成功后可以申请发票。",
            "password": "2026-10-05T12:30:08+00:00",
            "message": 'password="2026-10-05T12:30:08+00:00"; Authorization: Bearer abc123',
        }
    )
    assert "invoice_policy" in safe
    assert "2026-10-05" not in safe
    assert "abc123" not in safe


@pytest.fixture
def span_exporter(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("trace-regression")
    monkeypatch.setattr("src.tools.registry.tracer", tracer)
    monkeypatch.setattr("src.resilience.executor.tracer", tracer)
    monkeypatch.setattr(settings, "RESILIENCE_ENABLED", True)
    monkeypatch.setattr(settings, "RESILIENCE_RETRY_BASE_DELAY_SECONDS", 0.0)
    yield exporter
    provider.shutdown()


@pytest.mark.asyncio
async def test_exported_tool_spans_are_distinct_and_keep_resilience(span_exporter):
    names = (
        "crm.get_customer_profile",
        "orders.get_order_history",
        "shipping.get_shipments",
        "tickets.get_past_tickets",
    )
    with langsmith_agent_trace_context():
        for name in names:
            result = await tool_registry.call_tool(name, {"customer_id": "cust_102"})
            assert result["status"] == "success"
    spans = span_exporter.get_finished_spans()
    tool_spans = [span for span in spans if span.name.startswith("tool.")]
    assert {span.name for span in tool_spans} == {f"tool.{name}" for name in names}
    assert {span.attributes["langsmith.trace.name"] for span in tool_spans} == set(
        names
    )
    assert all(
        span.attributes["tool.status"] == "success"
        and span.attributes["operation.duration_seconds"] >= 0
        for span in tool_spans
    )
    assert any(span.name == "supportgpt.resilience.call" for span in spans)
    assert "cust_102" not in str([span.attributes for span in spans])


@pytest.mark.parametrize("recover", [True, False])
@pytest.mark.asyncio
async def test_exported_tool_retry_and_failure_are_visible(
    recover, span_exporter, monkeypatch
):
    attempts = 0

    class EmptyInput(BaseModel):
        """测试工具不接收业务参数。"""

    def handler():
        nonlocal attempts
        attempts += 1
        if not recover or attempts == 1:
            raise TimeoutError("api_key=sk-abcdefghijklmnopqrstuvwxyz")
        return {"status": "in_transit"}

    registry = ToolRegistry()
    name = "shipping.trace_recovery" if recover else "shipping.trace_timeout"
    registry.register(
        ToolDefinition(
            name=name,
            description="trace regression",
            input_schema=EmptyInput,
            output_schema={},
            min_role="agent",
            timeout_seconds=1.0,
            mocked=True,
            handler=handler,
        )
    )
    monkeypatch.setattr(
        settings, "RESILIENCE_TOOL_READ_MAX_RETRIES", 1 if recover else 0
    )
    with langsmith_agent_trace_context():
        await registry.call_tool(name, {})
    span = next(
        span
        for span in span_exporter.get_finished_spans()
        if span.name.startswith("tool.")
    )
    if recover:
        assert "retry 1" in span.name
        assert span.attributes["tool.retry_count"] == 1
        assert span.attributes["tool.status"] == "success"
    else:
        assert "timeout" in span.name and "degraded" in span.name
        assert span.status.status_code is StatusCode.ERROR
        assert span.attributes["operation.status"] == "error"
        assert span.attributes["tool.degradation_level"] == "partial"
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in str(span.attributes)
