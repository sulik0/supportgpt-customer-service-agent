import asyncio
import json
import logging
import time
from typing import Any, Dict

from src.agents.tool_planner import tool_planner
from src.config import settings
from src.guardrails.prompt_injection import analyze_prompt_injection
from src.guardrails.qwen3_guard import merge_qwen3_guard_result, qwen3_guard
from src.guardrails.security_policy import build_security_block
from src.models.intents import IntentType, normalize_intent
from src.observability.metrics import AGENT_EXECUTION_DURATION_SECONDS
from src.observability.sanitization import sanitize_value
from src.risk.engine import risk_engine
from src.skills import skill_registry
from src.skills.definitions import COMMON_READ_TOOLS
from src.tools.registry import tool_registry

logger = logging.getLogger("supportgpt.agents.tooling")

ORDER_INTENTS = frozenset(
    {
        IntentType.BILLING_DISPUTE,
        IntentType.ORDER_CANCELLATION,
        IntentType.ORDER_STATUS,
    }
)
SERVICE_TOOL_BY_INTENT = {
    IntentType.ORDER_STATUS: "shipping.get_shipments",
    IntentType.ORDER_CANCELLATION: "shipping.get_shipments",
    IntentType.BILLING_DISPUTE: "billing.get_payment_invoices",
    IntentType.WARRANTY_CLAIM: "warranty.get_entitlements",
    IntentType.OUTAGE_REPORT: "services.get_status",
}
SERVICE_TOOLS = frozenset(SERVICE_TOOL_BY_INTENT.values())


def rule_tool_names(intent: IntentType) -> list[str]:
    """确定性路由：通用画像和历史工单，再按意图追加订单与售后查询。"""
    names = ["crm.get_customer_profile", "tickets.get_past_tickets"]
    if intent in ORDER_INTENTS:
        names.append("orders.get_order_history")
    service_tool = SERVICE_TOOL_BY_INTENT.get(intent)
    if service_tool:
        names.append(service_tool)
    return names


class ToolingAgent:
    """负责调用受 ToolRegistry 管理的工具补全业务上下文。

    客户、订单、工单和新增售后查询均为本地 Mock Adapter。
    """

    async def enrich(self, state: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        customer_id = state.get("customer_id", "")
        ticket_id = state.get("ticket_id")
        operator_role = state.get("operator_role", "agent")
        department = state.get("department", "general")
        intent = normalize_intent(state.get("intent"))
        skill_selection = (
            skill_registry.select(state)
            if state.get("skill_name") in {None, "", "unselected"}
            else None
        )
        skill_name = (
            skill_selection.definition.name
            if skill_selection
            else str(state.get("skill_name"))
        )
        skill_version = (
            skill_selection.definition.version
            if skill_selection
            else str(state.get("skill_version"))
        )

        if "Security threat" in "".join(state.get("errors", [])):
            return state

        logger.info(
            "Tooling node started for customer=%s department=%s intent=%s",
            customer_id,
            department,
            intent,
        )

        try:
            # Tool Routing 仅依赖归一化意图，不让错误 department 继续传播。
            automated_forbidden_tools = {"orders.check_refund_eligibility"}
            if intent not in ORDER_INTENTS:
                automated_forbidden_tools.add("orders.get_order_history")
            policy_kwargs = {
                "intent": intent,
                "request_risk_level": state.get("risk_level", "low"),
                "forbidden_tools": automated_forbidden_tools,
                "skill_name": skill_name,
                "skill_version": skill_version,
            }

            async def invoke(name: str) -> Dict[str, Any]:
                # customer_id 始终来自工单归属，规划模型无法指定查询对象。
                return await tool_registry.call_tool(
                    name,
                    {"customer_id": customer_id},
                    role=operator_role,
                    ticket_id=ticket_id,
                    **policy_kwargs,
                )

            planner_summary: Dict[str, Any] = {"mode": "rules"}
            planner_tokens = (0, 0)
            planner_fallbacks: list[str] = []
            tool_calls: list[Dict[str, Any]] | None = None
            if settings.TOOL_SELECTION_MODE == "react":
                skill = skill_registry.get(skill_name)
                # 画像和历史工单决定 SLA 与风险评估，属于强制上下文，不交给模型裁量。
                mandatory = [
                    name for name in rule_tool_names(intent) if name in COMMON_READ_TOOLS
                ]
                candidates = tool_registry.planner_candidates(
                    (skill.allowed_tools if skill else frozenset()) - COMMON_READ_TOOLS,
                    role=operator_role,
                    intent=intent,
                )
                candidate_names = [item.name for item in candidates]
                mandatory_task = asyncio.gather(*(invoke(name) for name in mandatory))
                try:
                    plan = await tool_planner.run(
                        state,
                        skill_name=skill_name,
                        candidates=candidates,
                        invoke=invoke,
                    )
                    tool_calls = [*await mandatory_task, *plan.tool_calls]
                    planner_summary = {
                        **plan.summary(candidate_names),
                        "mandatory_tools": mandatory,
                    }
                    planner_tokens = (plan.tokens_input, plan.tokens_output)
                except Exception as exc:
                    # 规划失败不影响业务事实补全，强制上下文复用，其余按确定性路由补齐。
                    logger.warning("Tool planner failed, using rules: %s", exc)
                    fetched = list(await mandatory_task)
                    remaining = [
                        name for name in rule_tool_names(intent) if name not in mandatory
                    ]
                    tool_calls = [
                        *fetched,
                        *await asyncio.gather(*(invoke(name) for name in remaining)),
                    ]
                    planner_summary = {
                        "mode": "react",
                        "candidate_tools": candidate_names,
                        "mandatory_tools": mandatory,
                        "stop_reason": "planner_failed",
                        "fallback": "rules",
                        "error_type": exc.__class__.__name__,
                    }
                    planner_fallbacks.append("tool_planner.rules")
            if tool_calls is None:
                tool_calls = list(
                    await asyncio.gather(*(invoke(name) for name in rule_tool_names(intent)))
                )
                if planner_summary["mode"] == "rules":
                    planner_summary["selected_tools"] = rule_tool_names(intent)

            calls_by_name = {call["tool_name"]: call for call in tool_calls}
            profile_call = calls_by_name.get("crm.get_customer_profile") or {}
            ticket_call = calls_by_name.get("tickets.get_past_tickets") or {}
            order_call = calls_by_name.get("orders.get_order_history")
            service_tool = next(
                (call["tool_name"] for call in tool_calls if call["tool_name"] in SERVICE_TOOLS),
                None,
            )
            service_call = calls_by_name.get(service_tool) if service_tool else None

            profile = profile_call.get("result") or {}
            past_tickets = ticket_call.get("result") or []
            orders = order_call.get("result") if order_call else []
            order_id = (state.get("memory_active_entities") or {}).get("order_id")
            service_data = service_call.get("result") if service_call else None
            if order_id and service_data and service_tool != "services.get_status":
                # 用户指定订单时只提供该订单的记录，不用别的订单代替。
                records = [
                    item for item in service_data.get("records", [])
                    if str(item.get("order_id", "")).upper() == str(order_id).upper()
                ]
                service_data = {
                    **service_data, "records": records,
                    "status": "found" if records else "not_found",
                    "requested_order_id": order_id,
                }
            if order_id and orders:
                # Memory 只调整展示优先级，Tool 仍以实时 OMS 结果为准。
                orders = sorted(
                    orders,
                    key=lambda item: str(item.get("order_id", "")).upper()
                    != str(order_id).upper(),
                )

            public_tool_calls = [
                {key: value for key, value in call.items() if key != "result"}
                for call in tool_calls
            ]

            # 工具返回属于不可信外部数据，入上下文前扫描间接注入。
            tool_payload = json.dumps(
                [call.get("result") for call in tool_calls],
                ensure_ascii=False,
                default=str,
            )
            injection = analyze_prompt_injection(tool_payload, source="tool_result")
            if injection.detected:
                logger.warning(
                    "Indirect prompt injection detected in Tool result",
                    extra={
                        "ticket_id": ticket_id,
                        "risk_score": injection.risk_score,
                        "security_source": injection.source,
                    },
                )
                blocked = build_security_block(
                    state,
                    threat_type="Indirect prompt injection",
                    source=injection.source,
                    risk_score=injection.risk_score,
                    findings=[*injection.layers, *injection.signals],
                )
                return {**blocked, "tool_calls": public_tool_calls, "tool_planner": planner_summary}

            semantic_payload = json.dumps(
                sanitize_value([call.get("result") for call in tool_calls]),
                ensure_ascii=False,
                default=str,
            )
            semantic_result = await qwen3_guard.classify(
                semantic_payload, source="tool_result"
            )
            guarded_state = merge_qwen3_guard_result(state, semantic_result)
            assessment = risk_engine.assess(guarded_state, stage="input")
            guarded_state = {**guarded_state, **assessment.state_updates()}
            if semantic_result.block_recommended:
                blocked = build_security_block(
                    guarded_state,
                    threat_type="Qwen3Guard semantic safety violation",
                    source=semantic_result.source,
                    risk_score=semantic_result.policy_score,
                    findings=[
                        f"semantic_severity:{semantic_result.severity}",
                        *(
                            f"semantic_category:{item}"
                            for item in semantic_result.categories
                        ),
                    ],
                )
                return {**blocked, "tool_calls": public_tool_calls, "tool_planner": planner_summary}
            if semantic_result.degraded:
                return {
                    **guarded_state,
                    "tool_context": {},
                    "tool_calls": public_tool_calls,
                    "tool_planner": planner_summary,
                    "errors": list(guarded_state.get("errors", []))
                    + ["Semantic guard unavailable; Tool context isolated."],
                }

            tool_context = {
                "service_query": {
                    "tool": service_tool,
                    "status": service_call.get("status"),
                    "data": service_data,
                } if service_call else {},
                "customer_profile": {
                    "customer_id": profile.get("customer_id"),
                    "tier": profile.get("tier"),
                    "open_tickets_count": profile.get("open_tickets_count"),
                },
                "recent_orders": orders[:3],
                "past_tickets": past_tickets[:3],
                "resolved_request_entities": dict(
                    state.get("memory_active_entities") or {}
                ),
                "mocked": True,
                "tool_policy": {
                    "operator_role": operator_role,
                    "schema_validated": True,
                    "permission_checked": True,
                    "risk_checked": True,
                    "forbidden_tool_checked": True,
                    "skill_checked": True,
                    "selection_mode": planner_summary.get("mode", "rules"),
                    "skill_name": skill_name,
                    "skill_version": skill_version,
                    "audit_enabled": True,
                },
                "mock_note": "All business queries use local demo adapters, not live CRM, OMS, logistics or billing systems.",
            }

            duration = time.time() - start_time
            AGENT_EXECUTION_DURATION_SECONDS.record(
                duration, {"agent_name": "tooling_agent"}
            )

            return {
                **guarded_state,
                "tool_context": tool_context,
                "tool_calls": public_tool_calls,
                "tool_planner": planner_summary,
                "tokens_input": guarded_state.get("tokens_input", 0) + planner_tokens[0],
                "tokens_output": guarded_state.get("tokens_output", 0) + planner_tokens[1],
                "fallbacks_used": [
                    *guarded_state.get("fallbacks_used", []),
                    *planner_fallbacks,
                ],
            }
        except Exception as exc:
            logger.error("Tooling agent failed: %s", exc)
            return {
                **state,
                "tool_context": {},
                "tool_calls": [],
                "errors": state.get("errors", [])
                + [f"Tooling agent error: {str(exc)}"],
            }


tooling_agent = ToolingAgent()
