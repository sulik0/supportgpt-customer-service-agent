import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.intents import IntentType, normalize_intent
from src.tools.audit import tool_audit_repository
from src.tools.contracts import ApprovedToolExecution
from src.tools.crm import crm_tool
from src.tools.order_mgmt import order_mgmt_tool
from src.tools.payload_security import tool_payload_security
from src.tools.refund_gateway import refund_gateway
from src.tools.ticketing import ticketing_tool
from src.tools.service_queries import service_query_adapter
from src.observability.tracing import (
    get_tracer,
    langsmith_span_attributes,
    observed_span,
    set_span_attributes,
)
from src.observability.metrics import TOOL_CALLS_TOTAL, TOOL_CALL_DURATION_SECONDS
from src.resilience.executor import resilience_executor
from src.resilience.models import OperationType
from src.resilience.policies import tool_policy
from src.skills import skill_registry


ROLE_RANK = {
    "agent": 1,
    "manager": 2,
    "admin": 3,
}

tracer = get_tracer(__name__)


class CustomerToolInput(BaseModel):
    """定义只需要客户标识的 Tool 输入参数。"""

    customer_id: str = Field(..., min_length=1)


class RefundEligibilityInput(BaseModel):
    """定义退款资格查询所需的客户和订单标识。"""

    customer_id: str = Field(..., min_length=1)
    order_id: str = Field(..., min_length=1)


class RefundRequestInput(BaseModel):
    """定义创建退款请求所需的最小参数。"""

    customer_id: str = Field(..., min_length=1)
    order_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=2, max_length=500)


@dataclass(frozen=True)
class ToolDefinition:
    """描述 Tool 的协议、权限、风险、适用意图和执行入口。"""

    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: Dict[str, Any]
    min_role: str
    timeout_seconds: float
    mocked: bool
    handler: Callable[..., Any]
    risk_level: str = "low"
    allowed_intents: Optional[frozenset[IntentType]] = None
    operation_type: OperationType = OperationType.READ
    version: str = "v1"
    reconciliation_handler: Optional[Callable[..., Any]] = None
    compensation_handler: Optional[Callable[..., Any]] = None


class ToolRegistry:
    """统一注册和治理 Agent 可调用的业务工具。

    每次调用执行 Schema、RBAC、策略门禁并持久化脱敏审计。
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._enabled_overrides: Dict[str, bool] = {}

    def register(self, definition: ToolDefinition) -> None:
        self._tools[definition.name] = definition

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema.model_json_schema(),
                "output_schema": tool.output_schema,
                "min_role": tool.min_role,
                "timeout_seconds": tool.timeout_seconds,
                "mocked": tool.mocked,
                "risk_level": tool.risk_level,
                "operation_type": tool.operation_type.value,
                "allowed_intents": (
                    sorted(str(intent) for intent in tool.allowed_intents)
                    if tool.allowed_intents is not None
                    else None
                ),
                "version": tool.version,
                "enabled": self.is_enabled(tool.name),
            }
            for tool in self._tools.values()
        ]

    def is_enabled(self, name: str) -> bool:
        """返回 Tool 的运行时启用状态。"""
        return self._enabled_overrides.get(name, True)

    def set_enabled(self, name: str, enabled: bool) -> None:
        """应用已持久化的运行时开关，不修改 Tool 定义。"""
        if name not in self._tools:
            raise KeyError(name)
        self._enabled_overrides[name] = enabled

    def replace_runtime_settings(self, values: Dict[str, bool]) -> None:
        """以数据库快照原子替换当前进程内开关。"""
        self._enabled_overrides = {
            name: enabled for name, enabled in values.items() if name in self._tools
        }

    def get_definition(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    async def call_tool(
        self,
        name: str,
        payload: Dict[str, Any],
        role: str = "agent",
        ticket_id: Optional[int] = None,
        intent: Any = None,
        request_risk_level: Optional[str] = None,
        forbidden_tools: Optional[set[str] | frozenset[str]] = None,
        actor_user_id: Optional[int] = None,
        audit_db: Optional[AsyncSession] = None,
        action_id: Optional[str] = None,
        execution_grant: Optional[ApprovedToolExecution] = None,
        idempotency_key: Optional[str] = None,
        skill_name: Optional[str] = None,
        skill_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        trace_tool_name = name if self.get_definition(name) else "unregistered_tool"
        with observed_span(
            tracer,
            f"tool.{trace_tool_name}",
            {
                **langsmith_span_attributes("tool", trace_name=trace_tool_name),
                "gen_ai.tool.name": name,
                "tool.name": name,
                "tool.role": role,
                "tool.policy.intent": str(intent) if intent is not None else None,
                "tool.policy.request_risk_level": request_risk_level,
                "tool.policy.forbidden": name in (forbidden_tools or set()),
                "skill.name": skill_name,
                "skill.version": skill_version,
                "ticket.id": ticket_id,
                "tool.payload_keys": sorted(payload.keys()),
            },
        ) as span:
            result = await self._call_tool_impl(
                name,
                payload,
                role,
                ticket_id,
                intent=intent,
                request_risk_level=request_risk_level,
                forbidden_tools=forbidden_tools or frozenset(),
                action_id=action_id,
                execution_grant=execution_grant,
                idempotency_key=idempotency_key,
                skill_name=skill_name,
                skill_version=skill_version,
            )
            definition = self.get_definition(name)
            audit_record = tool_audit_repository.build_record(
                tool_name=name,
                tool_version=definition.version if definition else "unknown",
                operation_type=(
                    definition.operation_type.value if definition else "unknown"
                ),
                risk_level=definition.risk_level if definition else "unknown",
                payload=payload,
                role=role,
                allowed=bool(result.get("allowed")),
                status=str(result.get("status") or "unknown"),
                attempts=int(result.get("attempts") or 0),
                latency_ms=float(result.get("latency_ms") or 0.0),
                mocked=bool(result.get("mocked")),
                ticket_id=ticket_id,
                actor_user_id=actor_user_id,
                action_id=action_id,
                error_type=result.get("error_type"),
                result=result.get("result"),
                policy_version=(
                    execution_grant.policy_version if execution_grant else None
                ),
            )
            await tool_audit_repository.record(audit_record, db=audit_db)
            result["audit_id"] = audit_record["id"]
            set_span_attributes(
                span,
                {
                    "tool.allowed": result.get("allowed"),
                    "tool.status": result.get("status"),
                    "tool.mocked": result.get("mocked"),
                    "tool.latency_ms": result.get("latency_ms"),
                    "tool.has_error": bool(result.get("error")),
                    "tool.audit_id": audit_record["id"],
                    "tool.action_id": action_id,
                    "tool.attempts": result.get("attempts", 0),
                    "tool.retry_count": max(int(result.get("attempts") or 0) - 1, 0),
                    "tool.degradation_level": result.get("degradation_level", "none"),
                    "tool.error_type": result.get("error_type"),
                },
            )
            # 业务层捕获的失败也标红；名称直接展示重试或降级结果。
            try:
                from opentelemetry.trace import Status, StatusCode

                status = str(result.get("status") or "unknown")
                retries = max(int(result.get("attempts") or 0) - 1, 0)
                markers = []
                if status != "success":
                    markers.append(status)
                    span.set_status(Status(StatusCode.ERROR, status))
                if retries:
                    markers.append(f"retry {retries}")
                if result.get("degradation_level", "none") != "none":
                    markers.append("degraded")
                label = trace_tool_name + (
                    f" [{', '.join(markers)}]" if markers else ""
                )
                span.update_name(f"tool.{label}")
                set_span_attributes(
                    span, langsmith_span_attributes("tool", trace_name=label)
                )
            except Exception:
                pass
            return result

    async def _call_tool_impl(
        self,
        name: str,
        payload: Dict[str, Any],
        role: str,
        ticket_id: Optional[int],
        *,
        intent: Any,
        request_risk_level: Optional[str],
        forbidden_tools: set[str] | frozenset[str],
        action_id: Optional[str],
        execution_grant: Optional[ApprovedToolExecution],
        idempotency_key: Optional[str],
        skill_name: Optional[str],
        skill_version: Optional[str],
    ) -> Dict[str, Any]:
        started = time.time()
        definition = self._tools.get(name)
        if not definition:
            return self._record_call(
                name=name,
                role=role,
                ticket_id=ticket_id,
                allowed=False,
                status="not_found",
                started=started,
                mocked=False,
                error=f"Tool '{name}' is not registered.",
            )

        if not self.is_enabled(name):
            return self._record_call(
                name=name,
                role=role,
                ticket_id=ticket_id,
                allowed=False,
                status="disabled",
                started=started,
                mocked=definition.mocked,
                error=f"Tool '{name}' is disabled by an administrator.",
            )

        if not self._is_allowed(role, definition.min_role):
            return self._record_call(
                name=name,
                role=role,
                ticket_id=ticket_id,
                allowed=False,
                status="permission_denied",
                started=started,
                mocked=definition.mocked,
                error=(
                    f"Role '{role}' cannot call tool '{name}'. "
                    f"Required role: {definition.min_role}."
                ),
            )

        policy_error = self._policy_error(
            definition,
            payload=payload,
            intent=intent,
            request_risk_level=request_risk_level,
            forbidden_tools=forbidden_tools,
            action_id=action_id,
            execution_grant=execution_grant,
            idempotency_key=idempotency_key,
            skill_name=skill_name,
            skill_version=skill_version,
        )
        if policy_error:
            policy_status, policy_message = policy_error
            return self._record_call(
                name=name,
                role=role,
                ticket_id=ticket_id,
                allowed=False,
                status=policy_status,
                started=started,
                mocked=definition.mocked,
                error=policy_message,
            )

        try:
            args = definition.input_schema(**payload)
        except ValidationError as exc:
            return self._record_call(
                name=name,
                role=role,
                ticket_id=ticket_id,
                allowed=True,
                status="validation_error",
                started=started,
                mocked=definition.mocked,
                error=str(exc),
            )

        handler_args = args.model_dump()
        if definition.operation_type is OperationType.WRITE:
            handler_args["idempotency_key"] = idempotency_key
        resilient_result = await resilience_executor.execute(
            component="tool",
            operation=name,
            call=lambda: asyncio.to_thread(definition.handler, **handler_args),
            policy=tool_policy(
                timeout_seconds=definition.timeout_seconds,
                operation_type=definition.operation_type,
                high_risk=definition.risk_level == "high",
            ),
            circuit_key=f"tool:{name}",
        )
        event = resilient_result.event
        if resilient_result.success:
            return self._record_call(
                name=name,
                role=role,
                ticket_id=ticket_id,
                allowed=True,
                status="success",
                started=started,
                mocked=definition.mocked,
                result=resilient_result.value,
                attempts=event.attempts,
                degradation_level=event.degradation_level.value,
            )
        error_type = event.error_type.value if event.error_type else "unknown"
        status = "timeout" if error_type == "timeout" else event.status
        return self._record_call(
            name=name,
            role=role,
            ticket_id=ticket_id,
            allowed=True,
            status=status,
            started=started,
            mocked=definition.mocked,
            error=f"Tool dependency failed ({error_type}).",
            attempts=event.attempts,
            error_type=error_type,
            degradation_level=event.degradation_level.value,
        )

    def _is_allowed(self, role: str, min_role: str) -> bool:
        return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(min_role, 999)

    @staticmethod
    def _policy_error(
        definition: ToolDefinition,
        *,
        payload: Dict[str, Any],
        intent: Any,
        request_risk_level: Optional[str],
        forbidden_tools: set[str] | frozenset[str],
        action_id: Optional[str],
        execution_grant: Optional[ApprovedToolExecution],
        idempotency_key: Optional[str],
        skill_name: Optional[str],
        skill_version: Optional[str],
    ) -> Optional[tuple[str, str]]:
        """在 Handler 之前独立校验 forbidden tool、意图边界和高风险语义。"""
        if skill_name is not None:
            skill_error = skill_registry.tool_policy_error(
                skill_name, skill_version, definition.name
            )
            if skill_error:
                return "skill_denied", skill_error
        if definition.name in forbidden_tools:
            return (
                "policy_denied",
                f"Tool '{definition.name}' is forbidden by the current request policy.",
            )
        if intent is not None and definition.allowed_intents is not None:
            normalized_intent = normalize_intent(intent)
            if normalized_intent not in definition.allowed_intents:
                return (
                    "policy_denied",
                    f"Intent '{normalized_intent}' cannot call tool "
                    f"'{definition.name}'.",
                )
        if definition.risk_level == "high":
            if str(request_risk_level or "").lower() not in {"high", "critical"}:
                return (
                    "policy_denied",
                    f"High-risk tool '{definition.name}' requires a high-risk "
                    "business request.",
                )
            if definition.operation_type is OperationType.WRITE:
                expected_hash = tool_payload_security.payload_hash(payload)
                if (
                    execution_grant is None
                    or action_id is None
                    or execution_grant.action_id != action_id
                    or execution_grant.tool_name != definition.name
                    or execution_grant.payload_hash != expected_hash
                    or not execution_grant.approved_by_user_id
                    or execution_grant.idempotency_key != idempotency_key
                    or not execution_grant.policy_version
                ):
                    return (
                        "approval_required",
                        f"High-risk write tool '{definition.name}' requires an "
                        "approved Tool Action execution grant.",
                    )
        if definition.operation_type is OperationType.WRITE and not idempotency_key:
            return (
                "policy_denied",
                f"Write tool '{definition.name}' requires a business idempotency key.",
            )
        return None

    def _record_call(
        self,
        name: str,
        role: str,
        ticket_id: Optional[int],
        allowed: bool,
        status: str,
        started: float,
        mocked: bool,
        result: Any = None,
        error: Optional[str] = None,
        attempts: int = 0,
        error_type: Optional[str] = None,
        degradation_level: str = "none",
    ) -> Dict[str, Any]:
        record = {
            "tool_name": name,
            "role": role,
            "ticket_id": ticket_id,
            "allowed": allowed,
            "status": status,
            "latency_ms": round((time.time() - started) * 1000, 2),
            "mocked": mocked,
            "error": error,
            "attempts": attempts,
            "error_type": error_type,
            "degradation_level": degradation_level,
        }
        try:
            TOOL_CALLS_TOTAL.add(1, {"tool_name": name, "status": status})
            TOOL_CALL_DURATION_SECONDS.record(
                record["latency_ms"] / 1000.0, {"tool_name": name}
            )
        except Exception:
            pass
        return {**record, "result": result}


tool_registry = ToolRegistry()

tool_registry.register(
    ToolDefinition(
        name="crm.get_customer_profile",
        description="查询客户画像、客户等级和当前未关闭工单数量。",
        input_schema=CustomerToolInput,
        output_schema={
            "customer_id": "str",
            "name": "str",
            "tier": "str",
            "open_tickets_count": "int",
            "email": "str",
        },
        min_role="agent",
        timeout_seconds=1.0,
        mocked=True,
        handler=crm_tool.get_customer_profile,
        allowed_intents=frozenset(IntentType),
    )
)

tool_registry.register(
    ToolDefinition(
        name="orders.create_refund_request",
        description="创建退款处理请求；必须经过独立人工审批，不允许 Agent 自动执行。",
        input_schema=RefundRequestInput,
        output_schema={
            "refund_request_id": "str",
            "status": "str",
            "message": "str",
        },
        min_role="manager",
        timeout_seconds=2.0,
        mocked=True,
        handler=refund_gateway.create_refund_request,
        risk_level="high",
        allowed_intents=frozenset({IntentType.BILLING_DISPUTE}),
        operation_type=OperationType.WRITE,
        version="v2.2",
        reconciliation_handler=refund_gateway.reconcile,
        compensation_handler=refund_gateway.compensate,
    )
)

tool_registry.register(
    ToolDefinition(
        name="orders.get_order_history",
        description="查询客户近期订单、订单状态和付款金额。",
        input_schema=CustomerToolInput,
        output_schema={
            "orders": "list[dict]",
        },
        min_role="agent",
        timeout_seconds=1.0,
        mocked=True,
        handler=order_mgmt_tool.get_order_history,
        allowed_intents=frozenset(
            {
                IntentType.BILLING_DISPUTE,
                IntentType.ORDER_CANCELLATION,
                IntentType.ORDER_STATUS,
            }
        ),
    )
)

tool_registry.register(
    ToolDefinition(
        name="tickets.get_past_tickets",
        description="查询客户历史工单、处理状态和历史解决方案。",
        input_schema=CustomerToolInput,
        output_schema={
            "tickets": "list[dict]",
        },
        min_role="agent",
        timeout_seconds=1.0,
        mocked=True,
        handler=ticketing_tool.get_past_tickets,
        allowed_intents=frozenset(IntentType),
    )
)


def mock_refund_eligibility_check(customer_id: str, order_id: str) -> Dict[str, Any]:
    orders = order_mgmt_tool.get_order_history(customer_id)
    matched_order = next(
        (order for order in orders if order.get("order_id") == order_id), None
    )
    if not matched_order:
        return {
            "customer_id": customer_id,
            "order_id": order_id,
            "eligible": False,
            "reason": "Order not found in mock OMS data.",
        }

    return {
        "customer_id": customer_id,
        "order_id": order_id,
        "eligible": matched_order.get("status") == "delivered",
        "reason": "Delivered orders are eligible for manager review in the mock policy.",
    }


tool_registry.register(
    ToolDefinition(
        name="orders.check_refund_eligibility",
        description="校验订单是否满足退款初筛条件；该工具代表高风险业务动作，要求 manager 及以上角色。",
        input_schema=RefundEligibilityInput,
        output_schema={
            "customer_id": "str",
            "order_id": "str",
            "eligible": "bool",
            "reason": "str",
        },
        min_role="manager",
        timeout_seconds=1.0,
        mocked=True,
        handler=mock_refund_eligibility_check,
        risk_level="high",
        allowed_intents=frozenset({IntentType.BILLING_DISPUTE}),
    )
)

for name, description, handler, intents in (
    ("shipping.get_shipments", "查询客户配送进度、物流异常和跟进状态（演示数据）。", service_query_adapter.get_shipments, {IntentType.ORDER_STATUS, IntentType.ORDER_CANCELLATION}),
    ("warranty.get_entitlements", "查询客户保修或服务支持权益，不执行维修或赔付（演示数据）。", service_query_adapter.get_warranties, {IntentType.WARRANTY_CLAIM}),
    ("billing.get_payment_invoices", "查询客户订单支付和发票状态，不执行资金操作（演示数据）。", service_query_adapter.get_billing, {IntentType.BILLING_DISPUTE}),
    ("services.get_status", "查询演示 API 服务健康状态和公开故障信息。", service_query_adapter.get_service_status, {IntentType.OUTAGE_REPORT}),
):
    tool_registry.register(ToolDefinition(
        name=name, description=description, input_schema=CustomerToolInput,
        output_schema={"status": "str", "records": "list[dict]", "source": "str", "mocked": "bool"},
        min_role="agent", timeout_seconds=1.0, mocked=True, handler=handler,
        allowed_intents=frozenset(intents), version="v1.1",
    ))

# 启动期确保 Skill Policy 没有引用未注册 Tool。
skill_registry.validate_tool_catalog(
    item["name"] for item in tool_registry.list_tools()
)
