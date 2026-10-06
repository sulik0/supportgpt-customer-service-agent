import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from src.agents.analyzer import ticket_analyzer_agent
from src.agents.checkpointing import checkpoint_manager
from src.agents.escalation import escalation_agent
from src.agents.quality_assurance import quality_assurance_agent
from src.agents.resolver import resolution_agent
from src.agents.retriever import knowledge_retriever_agent
from src.agents.skill_selector import skill_selector_agent
from src.agents.scope import is_scope_boundary_request
from src.agents.tooling import tooling_agent
from src.config import settings
from src.models.intents import DEFAULT_INTENT, IntentType
from src.observability.cost_tracking import calculate_llm_cost
from src.observability.metrics import (
    AGENT_DECISION_STRATEGY_TOTAL,
    AGENT_NODE_DURATION_SECONDS,
    AGENT_NODE_EXECUTIONS_TOTAL,
    AGENT_REQUESTS_TOTAL,
    AGENT_SKILL_SELECTIONS_TOTAL,
    AGENT_WORKFLOW_INTERRUPTS_TOTAL,
    AGENT_WORKFLOW_RESUME_DURATION_SECONDS,
    AGENT_WORKFLOW_RESUMES_TOTAL,
    DEGRADED_AGENT_REQUESTS_TOTAL,
    LLM_COST_TOTAL,
)
from src.observability.tracing import (
    get_current_trace_id,
    get_request_id,
    get_tracer,
    langsmith_agent_trace_context,
    langsmith_span_attributes,
    observed_span,
    set_agent_trace_id,
    set_span_attributes,
)
from src.risk.engine import risk_engine
from src.resilience.context import begin_resilience_scope, finish_resilience_scope
from src.resilience.models import DEGRADATION_RANK, DependencyEvent
from src.promptops.runtime import active_bundle, prompt_scope

logger = logging.getLogger("supportgpt.agents.graph")
tracer = get_tracer(__name__)


class AgentState(TypedDict):
    """定义 LangGraph 各节点共享的客服任务状态。

    字段覆盖请求标识、分析结果、检索上下文、回复质量和升级决策。
    """

    request_id: str
    prompt_bundle_id: str
    prompt_version: str
    checkpoint_thread_id: str
    checkpoint_namespace: str
    durable_execution_enabled: bool
    execution_status: str
    approval_status: Optional[str]
    human_decision: Optional[str]
    ticket_id: int
    customer_id: str
    session_id: str
    subject: str
    description: str
    kb_version: str
    sentiment: str
    priority: str
    intent: IntentType
    department: str
    analyzer_confidence: float
    analyzer_strategy: str
    request_scope: str
    scope_reason: Optional[str]
    scope_strategy: str
    response_kind: str
    skill_name: str
    skill_version: str
    selection_strategy: str
    skill_registry_id: str
    skill_required_slots: List[str]
    skill_missing_slots: List[str]
    skill_allowed_tools: List[str]
    skill_forbidden_tools: List[str]
    skill_rag_categories: List[str]
    memory_recent_turns: List[Dict[str, str]]
    memory_summary: str
    memory_active_entities: Dict[str, str]
    memory_resolved_slots: Dict[str, Any]
    memory_last_intent: Optional[str]
    memory_last_department: Optional[str]
    memory_version: int
    memory_source: str
    memory_filtered_messages: int
    memory_prompt_context: str
    memory_retrieval_context: str
    security_threat_detected: bool
    security_risk_score: float
    security_source: Optional[str]
    security_findings: List[str]
    semantic_guard_label: str
    semantic_guard_categories: List[str]
    semantic_guard_checks: List[Dict[str, Any]]
    semantic_guard_degraded: bool
    semantic_guard_model: Optional[str]
    risk_level: str
    risk_score: float
    risk_reasons: List[str]
    risk_requires_human: bool
    risk_block_automation: bool
    operator_role: str
    tool_context: Dict[str, Any]
    tool_calls: List[Dict[str, Any]]
    context_citations: List[Any]
    resolution_evidence: List[str]
    suggested_response: str
    qa_score: float
    hallucination_detected: bool
    citation_verified: bool
    response_grounded: bool
    response_requires_human: bool
    qa_strategy: str
    escalation_recommended: bool
    escalation_reason: Optional[str]
    sla_hours: float
    tokens_input: int
    tokens_output: int
    cost_usd: float
    latency_seconds: float
    approval_required: bool
    degradation_level: str
    degradation_reasons: List[str]
    dependency_events: List[Dict[str, Any]]
    fallbacks_used: List[str]
    decision_records: List[Dict[str, Any]]
    workflow_path: List[str]
    errors: List[str]


# --- Node Wrappers ---
async def _run_node(
    node: str,
    handler: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    state: AgentState,
) -> Dict[str, Any]:
    started = time.perf_counter()
    status = "success"
    scope_token = begin_resilience_scope()
    scope_finished = False
    events: list[DependencyEvent] = []
    try:
        result = await handler(state)
        events = finish_resilience_scope(scope_token)
        scope_finished = True
        result = {
            **result,
            "workflow_path": [*state.get("workflow_path", []), node],
        }
        result = _apply_resilience_events(result, events)
        if len(result.get("errors", [])) > len(state.get("errors", [])):
            status = "error"
        return result
    except BaseException:
        if not scope_finished:
            finish_resilience_scope(scope_token)
        status = "error"
        raise
    finally:
        try:
            AGENT_NODE_EXECUTIONS_TOTAL.add(1, {"node": node, "status": status})
            AGENT_NODE_DURATION_SECONDS.record(
                time.perf_counter() - started, {"node": node}
            )
        except Exception:
            logger.debug("Unable to record metrics for Agent node %s", node)


def _record_decision_strategy(node: str, strategy: str) -> None:
    """记录 Analyzer/QA 路由比例，观测失败不影响主流程。"""
    try:
        AGENT_DECISION_STRATEGY_TOTAL.add(
            1, {"node": node, "strategy": strategy or "unknown"}
        )
    except Exception:
        logger.debug("Unable to record decision strategy for node %s", node)


def _apply_resilience_events(
    state: Dict[str, Any], events: list[DependencyEvent]
) -> Dict[str, Any]:
    """将 Node 内的 Retry/Fallback/失败事件合并到 AgentState。"""
    if not events:
        return state
    serialized = [event.as_dict() for event in events]
    existing_level = str(state.get("degradation_level", "none"))
    degradation_level = max(
        [existing_level, *(item.degradation_level.value for item in events)],
        key=lambda item: DEGRADATION_RANK.get(item, 0),
    )
    reasons = [
        f"{event.component}.{event.operation}:{event.status}"
        for event in events
        if event.degradation_level.value != "none"
    ]
    fallbacks = [event.fallback_used for event in events if event.fallback_used]
    return {
        **state,
        "degradation_level": degradation_level,
        "degradation_reasons": _unique_values(
            state.get("degradation_reasons", []), reasons
        ),
        "dependency_events": _unique_values(
            state.get("dependency_events", []), serialized
        ),
        "fallbacks_used": _unique_values(state.get("fallbacks_used", []), fallbacks),
    }


async def analyze_node(state: AgentState) -> Dict[str, Any]:
    with observed_span(
        tracer, "agent.analyzer", _trace_attrs(state, node="analyzer")
    ) as span:
        result = await _run_node(
            "ticket_analyzer", ticket_analyzer_agent.analyze, state
        )
        set_span_attributes(
            span,
            {
                "analyzer.strategy": result.get("analyzer_strategy", "unknown"),
                "request.scope": result.get("request_scope", "support"),
                "request.scope_reason": result.get("scope_reason"),
                "request.scope_strategy": result.get("scope_strategy", "not_run"),
                **_latest_decision_trace_attrs(result),
            },
        )
        _record_decision_strategy(
            "analyzer", result.get("analyzer_strategy", "unknown")
        )
        logger.info(
            "analyzer completed",
            extra={
                "ticket_id": result.get("ticket_id"),
                "intent": result.get("intent"),
                "priority": result.get("priority"),
                "strategy": result.get("analyzer_strategy"),
                "risk_level": result.get("risk_level"),
                "risk_score": result.get("risk_score"),
            },
        )
        return result


async def skill_selector_node(state: AgentState) -> Dict[str, Any]:
    """在 Analyzer 后选择版本化 Skill，不调用 LLM。"""
    with observed_span(
        tracer, "agent.skill_selector", _trace_attrs(state, node="skill_selector")
    ) as span:
        result = await _run_node("skill_selector", skill_selector_agent.select, state)
        attrs = {
            "skill.name": result.get("skill_name", "unknown"),
            "skill.version": result.get("skill_version", "unknown"),
            "skill.selection_strategy": result.get("selection_strategy", "unknown"),
            "skill.registry_id": result.get("skill_registry_id", "unknown"),
        }
        set_span_attributes(span, attrs)
        try:
            AGENT_SKILL_SELECTIONS_TOTAL.add(
                1,
                {
                    "skill": attrs["skill.name"],
                    "version": attrs["skill.version"],
                    "strategy": attrs["skill.selection_strategy"],
                },
            )
        except Exception:
            logger.debug("Unable to record Skill selection metric")
        return result


async def retrieve_node(state: AgentState) -> Dict[str, Any]:
    with observed_span(
        tracer, "agent.retriever", _trace_attrs(state, node="retriever")
    ):
        result = await _run_node("retriever", knowledge_retriever_agent.retrieve, state)
        logger.info(
            "retriever completed",
            extra={
                "ticket_id": result.get("ticket_id"),
                "citations": len(result.get("context_citations", [])),
                "risk_level": result.get("risk_level"),
                "risk_score": result.get("risk_score"),
            },
        )
        return result


async def tooling_node(state: AgentState) -> Dict[str, Any]:
    with observed_span(tracer, "agent.tooling", _trace_attrs(state, node="tooling")):
        result = await _run_node("tool_call", tooling_agent.enrich, state)
        tool_calls = result.get("tool_calls", [])
        logger.info(
            "tool completed",
            extra={
                "ticket_id": result.get("ticket_id"),
                "tool": ",".join(
                    call.get("tool_name", "unknown") for call in tool_calls
                ),
                "tool_count": len(tool_calls),
                "risk_level": result.get("risk_level"),
                "risk_score": result.get("risk_score"),
            },
        )
        return result


def _unique_values(*groups: List[Any]) -> List[Any]:
    """合并并行分支的列表结果并保留顺序。"""
    output = []
    seen = set()
    for group in groups:
        for value in group:
            marker = repr(value)
            if marker not in seen:
                seen.add(marker)
                output.append(value)
    return output


def _merge_context_results(
    state: AgentState,
    tool_result: Dict[str, Any],
    retrieval_result: Dict[str, Any],
) -> Dict[str, Any]:
    """安全合并 Tool 与 RAG 并行结果，风险信号只升不降。"""
    label_rank = {"not_run": 0, "safe": 1, "controversial": 2, "unsafe": 3}
    labels = [
        str(tool_result.get("semantic_guard_label", "not_run")),
        str(retrieval_result.get("semantic_guard_label", "not_run")),
    ]
    semantic_label = max(labels, key=lambda item: label_rank.get(item.lower(), 0))
    merged = {
        **state,
        "tool_context": tool_result.get("tool_context", {}),
        "tool_calls": tool_result.get("tool_calls", []),
        "context_citations": retrieval_result.get("context_citations", []),
        "errors": _unique_values(
            state.get("errors", []),
            tool_result.get("errors", []),
            retrieval_result.get("errors", []),
        ),
        "security_threat_detected": bool(
            tool_result.get("security_threat_detected")
            or retrieval_result.get("security_threat_detected")
        ),
        "security_risk_score": max(
            float(tool_result.get("security_risk_score", 0.0) or 0.0),
            float(retrieval_result.get("security_risk_score", 0.0) or 0.0),
        ),
        "security_source": tool_result.get("security_source")
        or retrieval_result.get("security_source"),
        "security_findings": _unique_values(
            tool_result.get("security_findings", []),
            retrieval_result.get("security_findings", []),
        ),
        "semantic_guard_label": semantic_label,
        "semantic_guard_categories": _unique_values(
            tool_result.get("semantic_guard_categories", []),
            retrieval_result.get("semantic_guard_categories", []),
        ),
        "semantic_guard_checks": _unique_values(
            tool_result.get("semantic_guard_checks", []),
            retrieval_result.get("semantic_guard_checks", []),
        ),
        "semantic_guard_degraded": bool(
            tool_result.get("semantic_guard_degraded")
            or retrieval_result.get("semantic_guard_degraded")
        ),
        "semantic_guard_model": tool_result.get("semantic_guard_model")
        or retrieval_result.get("semantic_guard_model"),
        "risk_score": max(
            float(tool_result.get("risk_score", 0.0) or 0.0),
            float(retrieval_result.get("risk_score", 0.0) or 0.0),
        ),
        "risk_reasons": _unique_values(
            tool_result.get("risk_reasons", []),
            retrieval_result.get("risk_reasons", []),
        ),
        "risk_requires_human": bool(
            tool_result.get("risk_requires_human")
            or retrieval_result.get("risk_requires_human")
        ),
        "risk_block_automation": bool(
            tool_result.get("risk_block_automation")
            or retrieval_result.get("risk_block_automation")
        ),
        "degradation_level": max(
            [
                str(state.get("degradation_level", "none")),
                str(tool_result.get("degradation_level", "none")),
                str(retrieval_result.get("degradation_level", "none")),
            ],
            key=lambda item: DEGRADATION_RANK.get(item, 0),
        ),
        "degradation_reasons": _unique_values(
            state.get("degradation_reasons", []),
            tool_result.get("degradation_reasons", []),
            retrieval_result.get("degradation_reasons", []),
        ),
        "dependency_events": _unique_values(
            state.get("dependency_events", []),
            tool_result.get("dependency_events", []),
            retrieval_result.get("dependency_events", []),
        ),
        "fallbacks_used": _unique_values(
            state.get("fallbacks_used", []),
            tool_result.get("fallbacks_used", []),
            retrieval_result.get("fallbacks_used", []),
        ),
        "tokens_input": (
            state.get("tokens_input", 0)
            + max(
                tool_result.get("tokens_input", 0) - state.get("tokens_input", 0),
                0,
            )
            + max(
                retrieval_result.get("tokens_input", 0) - state.get("tokens_input", 0),
                0,
            )
        ),
        "tokens_output": (
            state.get("tokens_output", 0)
            + max(
                tool_result.get("tokens_output", 0) - state.get("tokens_output", 0),
                0,
            )
            + max(
                retrieval_result.get("tokens_output", 0)
                - state.get("tokens_output", 0),
                0,
            )
        ),
        "workflow_path": [
            *state.get("workflow_path", []),
            "tool_call",
            "retriever",
        ],
    }
    assessment = risk_engine.assess(merged, stage="input")
    merged = {**merged, **assessment.state_updates()}
    if merged.get("risk_block_automation"):
        merged["tool_context"] = {}
        merged["context_citations"] = []
    return merged


async def context_enrichment_node(state: AgentState) -> Dict[str, Any]:
    """并行执行 Tool Calling 和 RAG Retrieval。"""
    with observed_span(
        tracer,
        "agent.context_enrichment",
        _trace_attrs(state, node="context_enrichment"),
    ):
        tool_result, retrieval_result = await asyncio.gather(
            tooling_node(state), retrieve_node(state)
        )
        return _merge_context_results(state, tool_result, retrieval_result)


async def resolve_node(state: AgentState) -> Dict[str, Any]:
    with observed_span(
        tracer, "agent.resolver", _trace_attrs(state, node="resolver")
    ) as span:
        result = await _run_node("llm_generation", resolution_agent.resolve, state)
        set_span_attributes(
            span, {"response.kind": result.get("response_kind", "business_answer")}
        )
        logger.info(
            "generation completed",
            extra={
                "ticket_id": result.get("ticket_id"),
                "generated": bool(result.get("suggested_response")),
            },
        )
        return result


async def qa_node(state: AgentState) -> Dict[str, Any]:
    with observed_span(tracer, "agent.qa", _trace_attrs(state, node="qa")) as span:
        result = await _run_node("qa", quality_assurance_agent.verify, state)
        set_span_attributes(
            span,
            {
                "qa.strategy": result.get("qa_strategy", "unknown"),
                **_latest_decision_trace_attrs(result),
            },
        )
        _record_decision_strategy("qa", result.get("qa_strategy", "unknown"))
        logger.info(
            "qa completed",
            extra={
                "ticket_id": result.get("ticket_id"),
                "score": result.get("qa_score"),
                "hallucination_detected": result.get("hallucination_detected", False),
                "strategy": result.get("qa_strategy"),
                "risk_level": result.get("risk_level"),
                "risk_score": result.get("risk_score"),
            },
        )
        return result


async def escalate_node(state: AgentState) -> Dict[str, Any]:
    with observed_span(
        tracer, "agent.escalation", _trace_attrs(state, node="escalation")
    ):
        result = await _run_node("escalation", escalation_agent.evaluate, state)
        result["approval_required"] = _requires_approval(result)
        logger.info(
            "escalation decided",
            extra={
                "ticket_id": result.get("ticket_id"),
                "required": result.get("escalation_recommended", False),
                "risk_level": result.get("risk_level"),
                "risk_score": result.get("risk_score"),
            },
        )
        return result


def _requires_approval(state: Dict[str, Any]) -> bool:
    """使审批规则在暂停前确定，不依赖 LLM 自行决策。"""
    return bool(
        state.get("escalation_recommended")
        or float(state.get("qa_score", 1.0)) < settings.RISK_QA_SCORE_THRESHOLD
        or state.get("risk_requires_human", False)
    )


async def approval_gate_node(state: AgentState) -> Dict[str, Any]:
    """高风险回复在此暂停，人工决策后从同一 Checkpoint 续跑。"""
    approval_required = _requires_approval(state)
    if not approval_required or not state.get("durable_execution_enabled", False):
        return {
            **state,
            "approval_required": approval_required,
            "execution_status": "completed",
            "workflow_path": state.get("workflow_path", []),
        }

    decision = interrupt(
        {
            "type": "response_approval",
            "ticket_id": state.get("ticket_id"),
            "risk_level": state.get("risk_level", "low"),
            "risk_score": state.get("risk_score", 0.0),
            "qa_score": state.get("qa_score", 0.0),
        }
    )
    if not isinstance(decision, dict):
        raise ValueError("Approval resume payload must be an object.")
    status_value = str(decision.get("status", ""))
    if status_value not in {"approved", "modified", "rejected"}:
        raise ValueError("Approval resume status is invalid.")
    final_response = str(decision.get("final_response") or "")
    suggested_response = state.get("suggested_response", "")
    if status_value in {"approved", "modified"} and final_response:
        suggested_response = final_response
    return {
        **state,
        "suggested_response": suggested_response,
        "approval_required": True,
        "approval_status": status_value,
        "human_decision": status_value,
        "execution_status": "completed",
        "workflow_path": [*state.get("workflow_path", []), "human_approval"],
    }


def _trace_attrs(state: Dict[str, Any], node: str) -> Dict[str, Any]:
    return {
        **langsmith_span_attributes("chain"),
        "agent.node": node,
        "request.scope": state.get("request_scope", "support"),
        "request.scope_reason": state.get("scope_reason"),
        "request.scope_strategy": state.get("scope_strategy", "not_run"),
        "response.kind": state.get("response_kind", "business_answer"),
        "request.id": state.get("request_id"),
        "ticket.id": state.get("ticket_id"),
        "kb.version": state.get("kb_version"),
        "ticket.department": state.get("department"),
        "ticket.priority": state.get("priority"),
        "operator.role": state.get("operator_role"),
        "skill.name": state.get("skill_name"),
        "skill.version": state.get("skill_version"),
        "skill.selection_strategy": state.get("selection_strategy"),
        "skill.registry_id": state.get("skill_registry_id"),
        "memory.source": state.get("memory_source", "empty"),
        "memory.version": state.get("memory_version", 0),
        "memory.message_count": len(state.get("memory_recent_turns", [])),
        "memory.filtered_count": state.get("memory_filtered_messages", 0),
        "risk.level": state.get("risk_level"),
        "risk.score": state.get("risk_score"),
        "risk.requires_human": state.get("risk_requires_human"),
        "security.threat_detected": state.get("security_threat_detected"),
        "guardrail.semantic_label": state.get("semantic_guard_label", "not_run"),
        "guardrail.semantic_degraded": state.get("semantic_guard_degraded", False),
        "guardrail.semantic_check_count": len(state.get("semantic_guard_checks", [])),
        "resilience.degradation_level": state.get("degradation_level", "none"),
        "resilience.event_count": len(state.get("dependency_events", [])),
        "resilience.fallback_count": len(state.get("fallbacks_used", [])),
        "decision.record_count": len(state.get("decision_records", [])),
    }


def _latest_decision_trace_attrs(state: Dict[str, Any]) -> Dict[str, Any]:
    """将最近决策的版本和回退原因关联到 Agent 节点。"""
    records = state.get("decision_records", [])
    if not records or not isinstance(records[-1], dict):
        return {}
    record = records[-1]
    return {
        "decision.provider": record.get("provider"),
        "decision.model": record.get("model"),
        "decision.question_set_version": record.get("question_set_version"),
        "decision.accepted": record.get("accepted"),
        "decision.fallback_reason": record.get("fallback_reason"),
    }


def _configured_llm_model_name() -> str:
    """返回当前 Workflow 实际使用的模型或 Azure deployment 名。"""
    provider = settings.LLM_PROVIDER.lower()
    if provider == "openai":
        return settings.LLM_MODEL_NAME or provider
    if provider == "azure":
        return settings.AZURE_OPENAI_DEPLOYMENT or provider
    return provider


def _is_automation_blocked(state: AgentState) -> bool:
    """对安全威胁使用统一的自动化阻断条件。"""
    return (
        bool(state.get("risk_block_automation"))
        or bool(state.get("security_threat_detected"))
        or "Security threat" in "".join(state.get("errors", []))
    )


def route_after_analyzer(state: AgentState) -> str:
    """输入安全检查失败时直接进入人工升级。"""
    if _is_automation_blocked(state):
        return "escalation"
    if is_scope_boundary_request(state):
        return "resolver"
    return "skill_selector"


def route_after_context_enrichment(state: AgentState) -> str:
    """Tool 或 RAG 任一分支高风险时跳过生成。"""
    if _is_automation_blocked(state):
        return "escalation"
    return "resolver"


def create_agent_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """Build and compile the LangGraph workflow."""
    workflow = StateGraph(AgentState)

    # Register Nodes
    workflow.add_node("analyzer", analyze_node)
    workflow.add_node("skill_selector", skill_selector_node)
    workflow.add_node("context_enrichment", context_enrichment_node)
    workflow.add_node("resolver", resolve_node)
    workflow.add_node("qa", qa_node)
    workflow.add_node("escalation", escalate_node)
    workflow.add_node("approval_gate", approval_gate_node)

    # Establish Transitions
    workflow.set_entry_point("analyzer")
    workflow.add_conditional_edges(
        "analyzer",
        route_after_analyzer,
        {
            "skill_selector": "skill_selector",
            "resolver": "resolver",
            "escalation": "escalation",
        },
    )
    workflow.add_edge("skill_selector", "context_enrichment")
    workflow.add_conditional_edges(
        "context_enrichment",
        route_after_context_enrichment,
        {
            "resolver": "resolver",
            "escalation": "escalation",
        },
    )
    workflow.add_edge("resolver", "qa")
    workflow.add_edge("qa", "escalation")
    workflow.add_edge("escalation", "approval_gate")
    workflow.add_edge("approval_gate", END)

    return workflow.compile(checkpointer=checkpointer)


compiled_graph = create_agent_graph(checkpoint_manager.saver)


async def initialize_agent_checkpointing() -> None:
    """在 FastAPI 启动时切换到持久化 Checkpointer。"""
    global compiled_graph
    saver = await checkpoint_manager.start()
    compiled_graph = create_agent_graph(saver)


async def shutdown_agent_checkpointing() -> None:
    """在应用退出时释放 Checkpointer 连接。"""
    global compiled_graph
    await checkpoint_manager.stop()
    compiled_graph = create_agent_graph(checkpoint_manager.saver)


def build_ticket_state(initial_state: Dict[str, Any]) -> AgentState:
    """根据业务输入构造可直接交给 LangGraph 的完整 Ticket State。"""
    return {
        "request_id": initial_state.get("request_id")
        or get_request_id()
        or "background",
        "prompt_bundle_id": active_bundle().bundle_id,
        "prompt_version": active_bundle().version,
        "checkpoint_thread_id": initial_state.get("checkpoint_thread_id")
        or str(uuid.uuid4()),
        "checkpoint_namespace": initial_state.get("checkpoint_namespace")
        or settings.LANGGRAPH_CHECKPOINT_NAMESPACE,
        "durable_execution_enabled": bool(
            initial_state.get("durable_execution_enabled", False)
        ),
        "execution_status": "running",
        "approval_status": None,
        "human_decision": None,
        "ticket_id": initial_state.get("ticket_id", 0),
        "customer_id": initial_state.get("customer_id", ""),
        "session_id": initial_state.get("session_id", ""),
        "subject": initial_state.get("subject", ""),
        "description": initial_state.get("description", ""),
        "kb_version": initial_state.get("kb_version", "v1"),
        "sentiment": "neutral",
        "priority": "medium",
        "intent": DEFAULT_INTENT,
        "department": "general",
        "analyzer_confidence": 1.0,
        "analyzer_strategy": "not_run",
        "request_scope": "support",
        "scope_reason": None,
        "scope_strategy": "not_run",
        "response_kind": "business_answer",
        "skill_name": "unselected",
        "skill_version": "unselected",
        "selection_strategy": "not_run",
        "skill_registry_id": "unselected",
        "skill_required_slots": [],
        "skill_missing_slots": [],
        "skill_allowed_tools": [],
        "skill_forbidden_tools": [],
        "skill_rag_categories": [],
        "memory_recent_turns": list(initial_state.get("memory_recent_turns", [])),
        "memory_summary": initial_state.get("memory_summary", ""),
        "memory_active_entities": dict(initial_state.get("memory_active_entities", {})),
        "memory_resolved_slots": dict(initial_state.get("memory_resolved_slots", {})),
        "memory_last_intent": initial_state.get("memory_last_intent"),
        "memory_last_department": initial_state.get("memory_last_department"),
        "memory_version": int(initial_state.get("memory_version", 0)),
        "memory_source": initial_state.get("memory_source", "empty"),
        "memory_filtered_messages": int(
            initial_state.get("memory_filtered_messages", 0)
        ),
        "memory_prompt_context": initial_state.get("memory_prompt_context", ""),
        "memory_retrieval_context": initial_state.get("memory_retrieval_context", ""),
        "security_threat_detected": False,
        "security_risk_score": 0.0,
        "security_source": None,
        "security_findings": [],
        "semantic_guard_label": "not_run",
        "semantic_guard_categories": [],
        "semantic_guard_checks": [],
        "semantic_guard_degraded": False,
        "semantic_guard_model": None,
        "risk_level": "low",
        "risk_score": 0.0,
        "risk_reasons": [],
        "risk_requires_human": False,
        "risk_block_automation": False,
        "operator_role": initial_state.get("operator_role", "agent"),
        "tool_context": {},
        "tool_calls": [],
        "context_citations": [],
        "suggested_response": "",
        "qa_score": 1.0,
        "hallucination_detected": False,
        "citation_verified": False,
        "response_grounded": False,
        "response_requires_human": False,
        "qa_strategy": "not_run",
        "escalation_recommended": False,
        "escalation_reason": None,
        "sla_hours": 24.0,
        "tokens_input": 0,
        "tokens_output": 0,
        "cost_usd": 0.0,
        "latency_seconds": 0.0,
        "approval_required": False,
        "degradation_level": "none",
        "degradation_reasons": [],
        "dependency_events": [],
        "fallbacks_used": [],
        "decision_records": [],
        "workflow_path": [],
        "errors": [],
    }


async def run_agent_workflow(initial_state: Dict[str, Any]) -> Dict[str, Any]:
    """固定本次请求的 Prompt 快照，并行节点继承同一上下文。"""
    with prompt_scope():
        return await _run_agent_workflow_pinned(initial_state)


async def _run_agent_workflow_pinned(initial_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes the Agent workflow with parallel context enrichment using LangGraph.
    Estimates latency, total tokens, and USD costs.
    """
    start_time = time.time()
    state_input = build_ticket_state(initial_state)
    graph_config = _graph_config(state_input)

    logger.info(f"Invoking LangGraph flow for ticket ID {state_input['ticket_id']}")
    try:
        with langsmith_agent_trace_context():
            with observed_span(
                tracer,
                "supportgpt.langgraph.workflow",
                {
                    **_trace_attrs(state_input, node="workflow"),
                    "prompt.bundle_id": state_input["prompt_bundle_id"],
                    "prompt.version": state_input["prompt_version"],
                    **langsmith_span_attributes(
                        "chain",
                        trace_name="SupportGPT Agent Workflow",
                        force=True,
                    ),
                },
                root=True,
            ) as span:
                final_output = await compiled_graph.ainvoke(
                    state_input, config=graph_config
                )
                final_output = await _annotate_checkpoint_state(
                    final_output, graph_config
                )
                final_output["trace_id"] = get_current_trace_id()
                set_span_attributes(span, {
                    "request.scope": final_output.get("request_scope", "support"),
                    "request.scope_reason": final_output.get("scope_reason"),
                    "request.scope_strategy": final_output.get("scope_strategy", "not_run"),
                    "response.kind": final_output.get(
                        "response_kind", "business_answer"
                    ),
                    "risk.level": final_output.get("risk_level", "low"),
                    "risk.requires_human": final_output.get("risk_requires_human", False),
                    "agent.approval_required": final_output.get("approval_required", False),
                    "agent.execution_status": final_output.get(
                        "execution_status", "completed"
                    ),
                })
                set_agent_trace_id(final_output["trace_id"])
        try:
            request_status = (
                "interrupted" if final_output.get("workflow_interrupted") else "success"
            )
            AGENT_REQUESTS_TOTAL.add(1, {"status": request_status})
        except Exception:
            logger.debug("Unable to record successful Agent request metric")
    except BaseException:
        try:
            AGENT_REQUESTS_TOTAL.add(1, {"status": "error"})
        except Exception:
            logger.debug("Unable to record failed Agent request metric")
        raise

    # Compute execution costs
    tokens_in = final_output.get("tokens_input", 0)
    tokens_out = final_output.get("tokens_output", 0)
    model_name = _configured_llm_model_name()
    cost = calculate_llm_cost(model_name, tokens_in, tokens_out)

    final_output["cost_usd"] = cost
    final_output["latency_seconds"] = round(time.time() - start_time, 4)
    span_attrs = {
        "llm.provider": settings.LLM_PROVIDER,
        "llm.model": model_name,
        "llm.tokens_input": tokens_in,
        "llm.tokens_output": tokens_out,
        "llm.cost_usd": cost,
        "agent.latency_seconds": final_output["latency_seconds"],
        "agent.approval_required": final_output.get("approval_required", False),
        "agent.escalation_recommended": final_output.get(
            "escalation_recommended", False
        ),
        "skill.name": final_output.get("skill_name", "unselected"),
        "skill.version": final_output.get("skill_version", "unselected"),
        "skill.selection_strategy": final_output.get("selection_strategy", "not_run"),
        "skill.registry_id": final_output.get("skill_registry_id", "unselected"),
        "risk.level": final_output.get("risk_level", "low"),
        "risk.score": final_output.get("risk_score", 0.0),
        "risk.requires_human": final_output.get("risk_requires_human", False),
        "risk.block_automation": final_output.get("risk_block_automation", False),
        "security.threat_detected": final_output.get("security_threat_detected", False),
        "security.source": final_output.get("security_source"),
        "resilience.degradation_level": final_output.get("degradation_level", "none"),
        "resilience.event_count": len(final_output.get("dependency_events", [])),
        "resilience.fallback_count": len(final_output.get("fallbacks_used", [])),
    }

    # Determine if human approval is required
    # Escalation needed or low QA score triggers approval
    if (
        final_output.get("escalation_recommended")
        or final_output.get("qa_score", 1.0) < settings.RISK_QA_SCORE_THRESHOLD
        or final_output.get("risk_requires_human", False)
    ):
        final_output["approval_required"] = True
        span_attrs["agent.approval_required"] = True

    with observed_span(tracer, "agent.workflow.summary", span_attrs):
        pass

    # Record OpenTelemetry usage metrics.
    try:
        LLM_COST_TOTAL.add(cost, {"model": model_name})
        if final_output.get("degradation_level", "none") != "none":
            DEGRADED_AGENT_REQUESTS_TOTAL.add(
                1,
                {"level": final_output.get("degradation_level", "unknown")},
            )
    except Exception:
        logger.debug("Unable to record LLM usage metrics")

    logger.info(
        f"LangGraph completed in {final_output['latency_seconds']}s. Cost: ${final_output['cost_usd']}."
    )
    if final_output.get("workflow_interrupted"):
        try:
            AGENT_WORKFLOW_INTERRUPTS_TOTAL.add(1, {"type": "response_approval"})
        except Exception:
            logger.debug("Unable to record workflow interrupt metric")
    return final_output


def _graph_config(state: Dict[str, Any]) -> Dict[str, Any]:
    """生成稳定 Thread 配置，恢复时必须使用完全相同的值。"""
    return {
        "configurable": {
            "thread_id": state["checkpoint_thread_id"],
            # 根 Graph 的 checkpoint_ns 必须为空；业务版本另存于 State。
            "checkpoint_ns": "",
        }
    }


async def _annotate_checkpoint_state(
    output: Dict[str, Any], graph_config: Dict[str, Any]
) -> Dict[str, Any]:
    """从 StateSnapshot 识别 interrupt，并返回可持久化的恢复元数据。"""
    snapshot = await compiled_graph.aget_state(graph_config)
    configurable = (snapshot.config or graph_config).get("configurable", {})
    interrupt_payload = None
    for task in snapshot.tasks:
        if task.interrupts:
            interrupt_payload = task.interrupts[0].value
            break
    interrupted = bool(snapshot.next)
    return {
        **output,
        "workflow_interrupted": interrupted,
        "execution_status": "interrupted" if interrupted else "completed",
        "checkpoint_id": configurable.get("checkpoint_id"),
        "checkpoint_next_nodes": list(snapshot.next),
        "interrupt_payload": interrupt_payload,
    }


async def resume_agent_workflow(
    *,
    checkpoint_thread_id: str,
    checkpoint_namespace: str,
    decision: Dict[str, Any],
) -> Dict[str, Any]:
    """使用人工决策恢复已暂停的 Workflow，不重跑已完成节点。"""
    if not checkpoint_namespace:
        raise ValueError("Checkpoint namespace is required for workflow resume.")
    started = time.time()
    graph_config = {
        "configurable": {
            "thread_id": checkpoint_thread_id,
            "checkpoint_ns": "",
        }
    }
    status = "success"
    try:
        with langsmith_agent_trace_context():
            with observed_span(
                tracer,
                "supportgpt.langgraph.resume",
                {
                    **langsmith_span_attributes(
                        "chain",
                        trace_name="SupportGPT Agent Workflow Resume",
                        force=True,
                    ),
                    "checkpoint.thread_id": checkpoint_thread_id,
                    "workflow.namespace": checkpoint_namespace,
                },
                root=True,
            ):
                output = await compiled_graph.ainvoke(
                    Command(resume=decision), config=graph_config
                )
                output = await _annotate_checkpoint_state(output, graph_config)
                output["resume_trace_id"] = get_current_trace_id()
                set_agent_trace_id(output["resume_trace_id"])
    except BaseException:
        status = "error"
        raise
    finally:
        duration = time.time() - started
        try:
            AGENT_WORKFLOW_RESUMES_TOTAL.add(1, {"status": status})
            AGENT_WORKFLOW_RESUME_DURATION_SECONDS.record(duration)
        except Exception:
            logger.debug("Unable to record workflow resume metrics")
    if output.get("workflow_interrupted"):
        raise RuntimeError("Workflow remained interrupted after approval resume.")
    output["resume_latency_seconds"] = round(time.time() - started, 4)
    output["cost_usd"] = calculate_llm_cost(
        _configured_llm_model_name(),
        int(output.get("tokens_input", 0)),
        int(output.get("tokens_output", 0)),
    )
    logger.info(
        "LangGraph workflow resumed",
        extra={
            "ticket_id": output.get("ticket_id"),
            "checkpoint_thread_id": checkpoint_thread_id,
            "decision": output.get("human_decision"),
        },
    )
    return output
