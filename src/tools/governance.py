"""高风险 Tool Action 的提议、审批、执行与查询服务。"""

import asyncio
import datetime
import uuid
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.models.db_models import (
    Ticket,
    ToolAction,
    ToolActionControl,
    ToolActionEvent,
    ToolActionReview,
    ToolBusinessRequest,
    ToolOutboxEvent,
    User,
)
from src.models.intents import normalize_intent
from src.observability.metrics import (
    TOOL_ACTION_TRANSITIONS_TOTAL,
    TOOL_RECONCILIATIONS_TOTAL,
)
from src.observability.sanitization import redact_text, sanitize_value
from src.observability.tracing import (
    get_current_trace_id,
    get_request_id,
    get_tracer,
    observed_span,
    set_span_attributes,
)
from src.resilience.models import OperationType
from src.tools.action_state_machine import (
    ToolActionCommand,
    ToolActionStatus,
    tool_action_state_machine,
)
from src.tools.contracts import ApprovedToolExecution
from src.tools.outbox import (
    OutboxEventType,
    OutboxStatus,
    OutboxLeaseLost,
    ensure_outbox_lease,
    tool_outbox_service,
)
from src.tools.payload_security import tool_payload_security
from src.tools.policy import tool_policy_service
from src.tools.registry import ROLE_RANK, ToolDefinition, tool_registry


tracer = get_tracer(__name__)
TRANSIENT_EXECUTION_ERRORS = {
    "timeout",
    "rate_limit",
    "connection",
    "server_error",
    "circuit_open",
    "unknown",
}


class ReconciliationPending(RuntimeError):
    """通知 Outbox 将尚无权威结果的对账任务放入 Retry Queue。"""


class ToolGovernanceService:
    """强制高风险写操作经过可追溯的职责分离审批。"""

    async def propose(
        self,
        db: AsyncSession,
        *,
        ticket_id: int,
        tool_name: str,
        payload: dict[str, Any],
        intent: str,
        proposer: User,
    ) -> ToolAction:
        definition = self._high_risk_write_definition(tool_name)
        ticket = await db.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found.")

        normalized_payload = self._validate_payload(definition, payload)
        if (
            not str(normalized_payload.get("order_id", "")).strip()
            or len(str(normalized_payload.get("reason", "")).strip()) < 2
        ):
            raise HTTPException(
                status_code=422, detail="Order ID and refund reason cannot be blank."
            )
        normalized_intent = normalize_intent(intent)
        if (
            definition.allowed_intents is not None
            and normalized_intent not in definition.allowed_intents
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tool is not allowed for the requested intent.",
            )
        if normalized_payload.get("customer_id") != ticket.customer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tool payload does not belong to the ticket customer.",
            )

        # 当前退款工具只表示整单退款申请，同一订单不能新建第二个 Action。
        if definition.name != "orders.create_refund_request":
            raise HTTPException(
                status_code=422,
                detail="Business deduplication contract is not configured for this Tool.",
            )
        business_key = tool_payload_security.payload_hash(
            {
                "tool": definition.name,
                "customer_id": normalized_payload["customer_id"],
                "order_id": normalized_payload["order_id"].strip().casefold(),
            }
        )
        payload_hash = tool_payload_security.payload_hash(normalized_payload)
        existing = await self._existing_business_action(
            db, business_key, definition.name, normalized_payload
        )
        if existing:
            return self._check_duplicate(existing, payload_hash)

        now = datetime.datetime.utcnow()
        action_id = str(uuid.uuid4())
        policy_snapshot = tool_policy_service.snapshot(definition)
        action = ToolAction(
            id=action_id,
            ticket_id=ticket.id,
            tool_name=definition.name,
            tool_version=definition.version,
            intent=str(normalized_intent),
            risk_level=definition.risk_level,
            operation_type=definition.operation_type.value,
            status=ToolActionStatus.PROPOSED,
            version=1,
            policy_version=settings.TOOL_POLICY_VERSION,
            payload_encrypted=tool_payload_security.encrypt(normalized_payload),
            payload_hash=tool_payload_security.payload_hash(normalized_payload),
            payload_summary=tool_payload_security.summary(normalized_payload),
            request_id=get_request_id() or uuid.uuid4().hex,
            trace_id=get_current_trace_id(),
            proposed_by_user_id=proposer.id,
            proposed_by_role=proposer.role,
            created_at=now,
            updated_at=now,
        )
        action.control = ToolActionControl(
            tool_action_id=action_id,
            idempotency_key=f"supportgpt:{definition.name}:{uuid.uuid4().hex}",
            policy_snapshot=policy_snapshot,
            policy_hash=tool_policy_service.digest(policy_snapshot),
            created_at=now,
            updated_at=now,
        )
        self._append_event(
            action,
            command="propose",
            source=None,
            target=ToolActionStatus.PROPOSED,
            actor=proposer,
        )
        transition = tool_action_state_machine.transition(
            action, ToolActionCommand.REQUEST_APPROVAL
        )
        self._append_transition(action, transition, proposer)
        try:
            async with db.begin_nested():
                db.add(action)
                await db.flush()
                db.add(
                    ToolBusinessRequest(
                        business_key=business_key,
                        tool_action_id=action.id,
                        payload_hash=payload_hash,
                    )
                )
                await db.flush()
        except IntegrityError:
            # 唯一键解决多进程同时创建；输掉竞争的 Action 随保存点回滚。
            existing = await self._existing_business_action(
                db, business_key, definition.name, normalized_payload
            )
            if existing is None:
                raise
            return self._check_duplicate(existing, payload_hash)
        await db.refresh(action, attribute_names=["events"])
        return action

    async def _existing_business_action(self, db, business_key, tool_name, payload):
        """兼容升级前已存在的 Action，避免新表为空时再次提交退款。"""
        request = await db.get(ToolBusinessRequest, business_key)
        if request:
            return await self.get(db, request.tool_action_id)
        candidates = (
            (
                await db.execute(
                    select(ToolAction)
                    .join(Ticket, Ticket.id == ToolAction.ticket_id)
                    .where(
                        ToolAction.tool_name == tool_name,
                        Ticket.customer_id == payload["customer_id"],
                    )
                    .order_by(ToolAction.created_at, ToolAction.id)
                )
            )
            .scalars()
            .all()
        )
        for candidate in candidates:
            try:
                original = tool_payload_security.decrypt(candidate.payload_encrypted)
            except ValueError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="Existing business Action cannot be verified; manual review is required.",
                ) from exc
            if (
                str(original.get("order_id", "")).strip().casefold()
                == payload["order_id"].strip().casefold()
            ):
                return await self.get(db, candidate.id)
        return None

    @staticmethod
    def _check_duplicate(action, payload_hash):
        if action.payload_hash != payload_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "This order already has a refund Action with different parameters.",
                    "action_id": action.id,
                },
            )
        return action

    async def decide(
        self,
        db: AsyncSession,
        *,
        action_id: str,
        decision: str,
        expected_version: int,
        reviewer: User,
        comment: Optional[str] = None,
    ) -> ToolAction:
        action = await self._load_for_update(db, action_id)
        self._check_version(action, expected_version)
        if ROLE_RANK.get(reviewer.role, 0) < ROLE_RANK["manager"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Reviewer must have manager or admin role.",
            )
        if decision not in {"approved", "rejected"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Decision must be approved or rejected.",
            )
        if decision == "approved" and action.proposed_by_user_id == reviewer.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Proposer cannot approve their own high-risk Tool Action.",
            )

        command = (
            ToolActionCommand.APPROVE
            if decision == "approved"
            else ToolActionCommand.REJECT
        )
        transition = tool_action_state_machine.transition(action, command)
        action.reviewed_by_user_id = reviewer.id
        action.reviewed_by_role = reviewer.role
        action.review_comment = redact_text(comment or "")[:1000] or None
        action.reviewed_at = datetime.datetime.utcnow()
        self._append_transition(action, transition, reviewer)
        await db.flush()
        return action

    async def execute(
        self,
        db: AsyncSession,
        *,
        action_id: str,
        expected_version: int,
        executor: User,
    ) -> ToolAction:
        """在同一事务中冻结执行资格并写入 Outbox，不同步触发副作用。"""
        action = await self._load_for_update(db, action_id)
        self._check_version(action, expected_version)
        definition = self._high_risk_write_definition(action.tool_name)
        if (
            action.tool_version != definition.version
            or action.policy_version != settings.TOOL_POLICY_VERSION
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tool or policy version changed; create a new Action.",
            )
        if ROLE_RANK.get(executor.role, 0) < ROLE_RANK.get(definition.min_role, 999):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Executor role is not permitted for this Tool.",
            )
        payload = self._verified_payload(action, definition)
        ticket = await db.get(Ticket, action.ticket_id)
        if not ticket or payload.get("customer_id") != ticket.customer_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tool Action no longer matches its ticket customer.",
            )
        replay = tool_policy_service.replay(action)
        if not replay["passed"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Tool Policy replay failed.", **replay},
            )
        transition = tool_action_state_machine.transition(
            action, ToolActionCommand.ENQUEUE
        )
        action.executed_by_user_id = executor.id
        self._append_transition(action, transition, executor)
        tool_outbox_service.enqueue(
            db,
            action=action,
            event_type=OutboxEventType.EXECUTE,
            actor_user_id=executor.id,
            actor_role=executor.role,
            dedupe_key=f"{action.id}:execute",
        )
        await db.flush()
        return action

    async def request_compensation(
        self,
        db: AsyncSession,
        *,
        action_id: str,
        expected_version: int,
        requester: User,
        reason: str,
    ) -> ToolAction:
        """仅为已成功且具备补偿契约的 Action 创建异步补偿任务。"""
        action = await self._load_for_update(db, action_id)
        self._check_version(action, expected_version)
        definition = self._high_risk_write_definition(action.tool_name)
        if definition.compensation_handler is None:
            raise HTTPException(
                status_code=422, detail="Tool does not support compensation."
            )
        if ROLE_RANK.get(requester.role, 0) < ROLE_RANK["manager"]:
            raise HTTPException(status_code=403, detail="Manager role is required.")
        transition = tool_action_state_machine.transition(
            action, ToolActionCommand.REQUEST_COMPENSATION
        )
        action.control.compensation_key = (
            f"{action.control.idempotency_key}:compensation"
        )
        action.control.compensation_reason = tool_payload_security.summary(
            {"reason": reason}
        )["reason"]
        self._append_transition(action, transition, requester)
        tool_outbox_service.enqueue(
            db,
            action=action,
            event_type=OutboxEventType.COMPENSATE,
            actor_user_id=requester.id,
            actor_role=requester.role,
            dedupe_key=f"{action.id}:compensate",
            payload={"reason_supplied": True},
        )
        await db.flush()
        return action

    async def policy_replay(self, db: AsyncSession, action_id: str) -> dict[str, Any]:
        action = await self.get(db, action_id)
        return tool_policy_service.replay(action)

    async def _open_review(self, db, action, reason):
        """独立于回复审批保存业务核实任务，工单队列按此记录联动。"""
        if action.status in {
            ToolActionStatus.SUCCEEDED,
            ToolActionStatus.FAILED,
            ToolActionStatus.REJECTED,
            ToolActionStatus.COMPENSATED,
            ToolActionStatus.COMPENSATION_FAILED,
        }:
            return
        review = await db.get(ToolActionReview, action.id)
        if review is None:
            db.add(
                ToolActionReview(
                    tool_action_id=action.id,
                    ticket_id=action.ticket_id,
                    status="pending",
                    reason=reason,
                )
            )
        elif review.status == "pending":
            review.reason = reason

    async def backfill_reviews(self, db):
        """升级时补齐旧 unknown / DLQ 的工单关联，不修改执行或回复审批状态。"""
        dead_actions = select(ToolOutboxEvent.tool_action_id).where(
            ToolOutboxEvent.status == OutboxStatus.DEAD_LETTER
        )
        actions = (
            (
                await db.execute(
                    select(ToolAction)
                    .where(
                        ToolAction.status.in_(
                            [
                                ToolActionStatus.UNKNOWN,
                                ToolActionStatus.COMPENSATION_UNKNOWN,
                            ]
                        )
                        | ToolAction.id.in_(dead_actions)
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for action in actions:
            await self._open_review(
                db,
                action,
                (
                    "compensation_unknown"
                    if action.status == ToolActionStatus.COMPENSATION_UNKNOWN
                    else (
                        "unknown"
                        if action.status == ToolActionStatus.UNKNOWN
                        else "dead_letter"
                    )
                ),
            )
        await db.flush()

    async def _close_review(self, db, action, outcome, actor):
        review = await db.get(ToolActionReview, action.id)
        if review and review.status == "pending":
            review.status = "resolved"
            review.outcome = outcome
            review.resolved_at = datetime.datetime.utcnow()
            review.resolved_by_user_id = actor.id if actor else None

    async def list_ticket_reviews(self, db, ticket_id):
        """客服可读核实原因和状态，不能读取外部凭证原文。"""
        if not await db.get(Ticket, ticket_id):
            raise HTTPException(status_code=404, detail="Ticket not found.")
        reviews = (
            (
                await db.execute(
                    select(ToolActionReview)
                    .where(ToolActionReview.ticket_id == ticket_id)
                    .order_by(ToolActionReview.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        result = []
        for review in reviews:
            result.append(
                {
                    name: getattr(review, name)
                    for name in (
                        "tool_action_id",
                        "ticket_id",
                        "status",
                        "reason",
                        "outcome",
                        "evidence_summary",
                        "resolved_by_user_id",
                        "created_at",
                        "resolved_at",
                    )
                }
                | {"action": await self.get(db, review.tool_action_id)}
            )
        return result

    async def resolve_manually(
        self,
        db,
        *,
        action_id,
        expected_version,
        outcome,
        evidence_reference,
        note,
        reviewer,
    ):
        """只确认人工查到的外部结果，禁止重发写操作或覆盖正在运行的 Worker。"""
        if ROLE_RANK.get(reviewer.role, 0) < ROLE_RANK["manager"]:
            raise HTTPException(status_code=403, detail="Manager role is required.")
        if not evidence_reference.strip() or len(note.strip()) < 5:
            raise HTTPException(
                status_code=422,
                detail="External evidence and a verification note are required.",
            )
        # 与 Worker 一样先锁租约行再锁 Action，避免锁顺序反转。
        events = (
            (
                await db.execute(
                    select(ToolOutboxEvent)
                    .where(ToolOutboxEvent.tool_action_id == action_id)
                    .order_by(ToolOutboxEvent.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        now = datetime.datetime.utcnow()
        if any(
            event.status == OutboxStatus.PROCESSING
            and event.lease_expires_at
            and event.lease_expires_at > now
            for event in events
        ):
            raise HTTPException(
                status_code=409,
                detail="An active Worker still owns this Action; wait for completion or lease expiry.",
            )
        action = await self._load_for_update(db, action_id)
        self._check_version(action, expected_version)
        if reviewer.id == action.proposed_by_user_id:
            raise HTTPException(
                status_code=409,
                detail="Proposer cannot confirm their own high-risk Action.",
            )
        review = await db.get(ToolActionReview, action_id)
        if review is None or review.status != "pending":
            raise HTTPException(
                status_code=409,
                detail="Action has no pending manual verification task.",
            )
        if action.status == ToolActionStatus.EXECUTING:
            transition = tool_action_state_machine.transition(
                action, ToolActionCommand.MARK_UNKNOWN
            )
            self._append_transition(action, transition, reviewer)
        if action.status == ToolActionStatus.COMPENSATING:
            transition = tool_action_state_machine.transition(
                action, ToolActionCommand.MARK_COMPENSATION_UNKNOWN
            )
            self._append_transition(action, transition, reviewer)
        commands = {
            "succeeded": ToolActionCommand.MANUAL_SUCCESS,
            "failed": ToolActionCommand.MANUAL_FAILURE,
            "compensated": ToolActionCommand.MANUAL_COMPENSATED,
            "compensation_failed": ToolActionCommand.MANUAL_COMPENSATION_FAILURE,
        }
        command = commands.get(outcome)
        if not command:
            raise HTTPException(
                status_code=422, detail="Unsupported confirmation outcome."
            )
        transition = tool_action_state_machine.transition(action, command)
        review.evidence_encrypted = tool_payload_security.encrypt(
            {"reference": evidence_reference.strip(), "note": note.strip()}
        )
        review.evidence_summary = {
            "reference_hash": tool_payload_security.payload_hash(
                {"reference": evidence_reference.strip()}
            ),
            "note": redact_text(note.strip())[:1000],
        }
        action.error_type = (
            None
            if outcome in {"succeeded", "compensated"}
            else "manually_confirmed_failure"
        )
        action.failure_reason = (
            None
            if action.error_type is None
            else "External failure confirmed by a manager."
        )
        action.completed_at = now
        action.result_summary = {
            "status": outcome,
            "confirmation_source": "manual",
            "evidence_hash": review.evidence_summary["reference_hash"],
        }
        self._append_transition(
            action,
            transition,
            reviewer,
            details={
                "confirmation_source": "manual",
                "evidence_hash": review.evidence_summary["reference_hash"],
            },
        )
        await self._close_review(db, action, outcome, reviewer)
        for event in events:
            if event.status not in {OutboxStatus.SUCCEEDED, OutboxStatus.CANCELLED}:
                event.status = OutboxStatus.CANCELLED
                event.version += 1
                event.lease_owner = None
                event.lease_expires_at = None
                event.completed_at = now
        await db.flush()
        return action

    async def dispatch_outbox_event(
        self, db: AsyncSession, event: ToolOutboxEvent
    ) -> None:
        """由 Worker 分发已持久化事件，禁止 API 绕过 Outbox 直接调用。"""
        if event.event_type == OutboxEventType.EXECUTE:
            await self._dispatch_execution(db, event)
        elif event.event_type == OutboxEventType.RECONCILE:
            await self._dispatch_reconciliation(db, event)
        elif event.event_type == OutboxEventType.COMPENSATE:
            await self._dispatch_compensation(db, event)
        else:
            raise ValueError(f"Unsupported Tool Outbox event: {event.event_type}")

    async def _dispatch_execution(
        self, db: AsyncSession, event: ToolOutboxEvent
    ) -> None:
        await ensure_outbox_lease(db, event)
        action = await self._load_for_update(db, event.tool_action_id)
        actor = self._event_actor(event)
        if action.status in {
            ToolActionStatus.SUCCEEDED,
            ToolActionStatus.FAILED,
            ToolActionStatus.UNKNOWN,
            ToolActionStatus.RECONCILING,
            ToolActionStatus.COMPENSATION_PENDING,
            ToolActionStatus.COMPENSATING,
            ToolActionStatus.COMPENSATED,
            ToolActionStatus.COMPENSATION_FAILED,
            ToolActionStatus.COMPENSATION_UNKNOWN,
        }:
            return
        if action.status == ToolActionStatus.EXECUTING:
            # 租约过期表示上次写入结果不确定，只允许转对账，禁止再次写入。
            await self._enter_unknown(
                db, action, actor, error_type="worker_interrupted"
            )
            return
        if action.status != ToolActionStatus.QUEUED:
            raise RuntimeError(f"Action cannot execute from status {action.status}.")

        definition = self._high_risk_write_definition(action.tool_name)
        transition = tool_action_state_machine.transition(
            action, ToolActionCommand.START_EXECUTION
        )
        action.execution_started_at = datetime.datetime.utcnow()
        self._append_transition(action, transition, actor)
        # 外部调用前先提交 executing；进程崩溃后只能走对账。
        await db.commit()

        action = await self._load_for_update(db, event.tool_action_id)
        payload = self._verified_payload(action, definition)
        grant = ApprovedToolExecution(
            action_id=action.id,
            tool_name=action.tool_name,
            payload_hash=action.payload_hash,
            approved_by_user_id=int(action.reviewed_by_user_id or 0),
            idempotency_key=action.control.idempotency_key,
            policy_version=action.policy_version,
        )
        # 外部调用期间不占数据库锁，独立连接可以正常续租。
        await db.commit()
        with observed_span(
            tracer,
            "supportgpt.tool_action.execute",
            {
                "tool.action_id": action.id,
                "gen_ai.tool.name": action.tool_name,
                "ticket.id": action.ticket_id,
                "tool.outbox_id": event.id,
            },
        ) as span:
            try:
                result = await tool_registry.call_tool(
                    action.tool_name,
                    payload,
                    role=event.actor_role,
                    ticket_id=action.ticket_id,
                    intent=action.intent,
                    request_risk_level=action.risk_level,
                    actor_user_id=event.actor_user_id,
                    audit_db=db,
                    action_id=action.id,
                    execution_grant=grant,
                    idempotency_key=action.control.idempotency_key,
                )
                await ensure_outbox_lease(db, event)
                action = await self._load_for_update(db, event.tool_action_id)
                if result.get("status") == "success":
                    transition = tool_action_state_machine.transition(
                        action, ToolActionCommand.SUCCEED
                    )
                    action.result_summary = tool_payload_security.result_summary(
                        result.get("result")
                    )
                    raw_result = result.get("result") or {}
                    action.control.external_reference = raw_result.get(
                        "refund_request_id"
                    )
                    action.error_type = None
                    action.failure_reason = None
                    action.completed_at = datetime.datetime.utcnow()
                    self._append_transition(
                        action,
                        transition,
                        actor,
                        details={"audit_id": result.get("audit_id")},
                    )
                elif result.get("error_type") in TRANSIENT_EXECUTION_ERRORS:
                    await self._enter_unknown(
                        db,
                        action,
                        actor,
                        error_type=result.get("error_type") or "unknown",
                        audit_id=result.get("audit_id"),
                        commit=False,
                    )
                else:
                    transition = tool_action_state_machine.transition(
                        action, ToolActionCommand.FAIL
                    )
                    action.error_type = result.get("error_type") or str(
                        result.get("status") or "failed"
                    )
                    action.failure_reason = (
                        "Tool execution was rejected or failed deterministically."
                    )
                    action.completed_at = datetime.datetime.utcnow()
                    self._append_transition(
                        action,
                        transition,
                        actor,
                        details={"audit_id": result.get("audit_id")},
                    )
                set_span_attributes(
                    span,
                    {
                        "tool.action.status": action.status,
                        "tool.audit_id": result.get("audit_id"),
                    },
                )
                await db.commit()
            except OutboxLeaseLost:
                raise
            except Exception as exc:
                await db.rollback()
                await ensure_outbox_lease(db, event)
                action = await self._load_for_update(db, event.tool_action_id)
                if action.status == ToolActionStatus.EXECUTING:
                    await self._enter_unknown(
                        db,
                        action,
                        actor,
                        error_type=exc.__class__.__name__,
                    )
                else:
                    raise

    async def _enter_unknown(
        self,
        db: AsyncSession,
        action: ToolAction,
        actor: Any,
        *,
        error_type: str,
        audit_id: Optional[str] = None,
        commit: bool = True,
    ) -> None:
        transition = tool_action_state_machine.transition(
            action, ToolActionCommand.MARK_UNKNOWN
        )
        action.error_type = error_type[:80]
        action.failure_reason = "External write outcome requires reconciliation."
        action.completed_at = None
        self._append_transition(
            action, transition, actor, details={"audit_id": audit_id}
        )
        tool_outbox_service.enqueue(
            db,
            action=action,
            event_type=OutboxEventType.RECONCILE,
            actor_user_id=getattr(actor, "id", None),
            actor_role=getattr(actor, "role", "system"),
            dedupe_key=f"{action.id}:reconcile",
            delay_seconds=settings.TOOL_RECONCILIATION_DELAY_SECONDS,
        )
        await self._open_review(db, action, "unknown")
        if commit:
            await db.commit()

    async def _dispatch_reconciliation(
        self, db: AsyncSession, event: ToolOutboxEvent
    ) -> None:
        await ensure_outbox_lease(db, event)
        action = await self._load_for_update(db, event.tool_action_id)
        actor = self._event_actor(event, role="system")
        if action.status in {
            ToolActionStatus.SUCCEEDED,
            ToolActionStatus.FAILED,
            ToolActionStatus.COMPENSATION_PENDING,
            ToolActionStatus.COMPENSATING,
            ToolActionStatus.COMPENSATED,
            ToolActionStatus.COMPENSATION_FAILED,
            ToolActionStatus.COMPENSATION_UNKNOWN,
        }:
            return
        if action.status == ToolActionStatus.UNKNOWN:
            transition = tool_action_state_machine.transition(
                action, ToolActionCommand.START_RECONCILIATION
            )
            action.control.reconciliation_attempts += 1
            self._append_transition(action, transition, actor)
            await db.commit()
            action = await self._load_for_update(db, event.tool_action_id)
        if action.status != ToolActionStatus.RECONCILING:
            raise RuntimeError(f"Action cannot reconcile from status {action.status}.")

        definition = self._high_risk_write_definition(action.tool_name)
        if definition.reconciliation_handler is None:
            await self._finish_reconciliation_pending(
                db, action, actor, error_type="handler_missing"
            )
            raise ReconciliationPending("Reconciliation handler is not configured.")
        idempotency_key = action.control.idempotency_key
        await db.commit()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    definition.reconciliation_handler,
                    idempotency_key=idempotency_key,
                ),
                timeout=definition.timeout_seconds,
            )
        except Exception as exc:
            await ensure_outbox_lease(db, event)
            action = await self._load_for_update(db, event.tool_action_id)
            await self._finish_reconciliation_pending(
                db, action, actor, error_type=exc.__class__.__name__
            )
            self._reconciliation_metric("retry")
            raise

        await ensure_outbox_lease(db, event)
        action = await self._load_for_update(db, event.tool_action_id)
        outcome = str(result.get("status") or "pending").lower()
        if outcome == "succeeded":
            transition = tool_action_state_machine.transition(
                action, ToolActionCommand.RECONCILE_SUCCESS
            )
            actual = result.get("result") or {}
            action.result_summary = tool_payload_security.result_summary(actual)
            action.control.external_reference = actual.get("refund_request_id")
            action.error_type = None
            action.failure_reason = None
            action.completed_at = datetime.datetime.utcnow()
            self._append_transition(
                action, transition, actor, details={"outcome": outcome}
            )
            await self._close_review(db, action, outcome, actor=None)
            await db.commit()
            self._reconciliation_metric("succeeded")
            return
        if outcome == "failed":
            transition = tool_action_state_machine.transition(
                action, ToolActionCommand.RECONCILE_FAILURE
            )
            action.error_type = "reconciled_failure"
            action.failure_reason = "External system confirmed the write failed."
            action.completed_at = datetime.datetime.utcnow()
            self._append_transition(
                action, transition, actor, details={"outcome": outcome}
            )
            await self._close_review(db, action, outcome, actor=None)
            await db.commit()
            self._reconciliation_metric("failed")
            return

        await self._finish_reconciliation_pending(
            db, action, actor, error_type="result_pending"
        )
        self._reconciliation_metric("retry")
        raise ReconciliationPending("External system has no final result yet.")

    async def _finish_reconciliation_pending(
        self,
        db: AsyncSession,
        action: ToolAction,
        actor: Any,
        *,
        error_type: str,
    ) -> None:
        transition = tool_action_state_machine.transition(
            action, ToolActionCommand.RECONCILE_PENDING
        )
        action.error_type = error_type[:80]
        action.failure_reason = "Reconciliation is pending; retry is scheduled."
        self._append_transition(action, transition, actor)
        await db.commit()

    async def _dispatch_compensation(
        self, db: AsyncSession, event: ToolOutboxEvent
    ) -> None:
        await ensure_outbox_lease(db, event)
        action = await self._load_for_update(db, event.tool_action_id)
        actor = self._event_actor(event)
        if action.status in {
            ToolActionStatus.COMPENSATED,
            ToolActionStatus.COMPENSATION_FAILED,
            ToolActionStatus.COMPENSATION_UNKNOWN,
        }:
            return
        if action.status == ToolActionStatus.COMPENSATING:
            # 旧 Worker 中断后补偿结果同样不确定，不能重新执行副作用。
            transition = tool_action_state_machine.transition(
                action, ToolActionCommand.MARK_COMPENSATION_UNKNOWN
            )
            action.error_type = "worker_interrupted"
            action.failure_reason = "Compensation outcome requires manual verification."
            self._append_transition(action, transition, actor)
            await self._open_review(db, action, "compensation_unknown")
            await db.commit()
            return
        if action.status == ToolActionStatus.COMPENSATION_PENDING:
            transition = tool_action_state_machine.transition(
                action, ToolActionCommand.START_COMPENSATION
            )
            self._append_transition(action, transition, actor)
            await db.commit()
            action = await self._load_for_update(db, event.tool_action_id)
        definition = self._high_risk_write_definition(action.tool_name)
        if action.status != ToolActionStatus.COMPENSATING:
            raise RuntimeError(f"Action cannot compensate from status {action.status}.")
        if definition.compensation_handler is None:
            raise RuntimeError("Compensation handler is not configured.")
        idempotency_key = action.control.idempotency_key
        compensation_key = action.control.compensation_key
        await db.commit()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    definition.compensation_handler,
                    idempotency_key=idempotency_key,
                    compensation_key=compensation_key,
                ),
                timeout=definition.timeout_seconds,
            )
            await ensure_outbox_lease(db, event)
            action = await self._load_for_update(db, event.tool_action_id)
            command = (
                ToolActionCommand.COMPENSATE_SUCCESS
                if result.get("status") == "compensated"
                else ToolActionCommand.COMPENSATE_FAILURE
            )
            transition = tool_action_state_machine.transition(action, command)
            action.result_summary = tool_payload_security.result_summary(result)
            if command == ToolActionCommand.COMPENSATE_SUCCESS:
                action.error_type = None
                action.failure_reason = None
            else:
                action.error_type = "compensation_failed"
                action.failure_reason = "External system rejected the compensation."
            action.completed_at = datetime.datetime.utcnow()
            self._append_transition(action, transition, actor)
            await self._close_review(db, action, action.status, actor=None)
            await db.commit()
        except OutboxLeaseLost:
            raise
        except Exception as exc:
            await db.rollback()
            await ensure_outbox_lease(db, event)
            action = await self._load_for_update(db, event.tool_action_id)
            if action.status == ToolActionStatus.COMPENSATING:
                transition = tool_action_state_machine.transition(
                    action, ToolActionCommand.MARK_COMPENSATION_UNKNOWN
                )
                action.error_type = exc.__class__.__name__[:80]
                action.failure_reason = (
                    "Compensation outcome is unknown and requires manual review."
                )
                self._append_transition(action, transition, actor)
                await self._open_review(db, action, "compensation_unknown")
                await db.commit()

    @staticmethod
    def _event_actor(event: ToolOutboxEvent, role: Optional[str] = None) -> Any:
        return SimpleNamespace(
            id=None if role == "system" else event.actor_user_id,
            role=role or event.actor_role,
        )

    @staticmethod
    def _reconciliation_metric(outcome: str) -> None:
        try:
            TOOL_RECONCILIATIONS_TOTAL.add(1, {"outcome": outcome})
        except Exception:
            pass

    async def record_dead_letter(
        self, db: AsyncSession, event: ToolOutboxEvent
    ) -> None:
        """Retry 耗尽时补写同状态审计事件并明确转人工处理。"""
        action = await self._load_for_update(db, event.tool_action_id)
        if action.status not in {
            ToolActionStatus.SUCCEEDED,
            ToolActionStatus.FAILED,
            ToolActionStatus.REJECTED,
            ToolActionStatus.COMPENSATED,
            ToolActionStatus.COMPENSATION_FAILED,
        }:
            action.failure_reason = (
                "Outbox delivery exhausted; manual review is required."
            )
        await self._open_review(db, action, "dead_letter")
        self._append_event(
            action,
            command="dead_letter",
            source=action.status,
            target=action.status,
            actor=self._event_actor(event, role="system"),
            details={
                "outbox_id": event.id,
                "event_type": event.event_type,
                "attempts": event.attempts,
            },
        )

    async def record_outbox_retry(
        self, db: AsyncSession, event: ToolOutboxEvent, actor: User
    ) -> None:
        """记录主管对 DLQ 的显式重放，保留完整运维审计链。"""
        action = await self._load_for_update(db, event.tool_action_id)
        self._append_event(
            action,
            command="retry_dead_letter",
            source=action.status,
            target=action.status,
            actor=actor,
            details={"outbox_id": event.id, "event_type": event.event_type},
        )

    async def get(self, db: AsyncSession, action_id: str) -> ToolAction:
        query = (
            select(ToolAction)
            .options(selectinload(ToolAction.events), selectinload(ToolAction.control))
            .where(ToolAction.id == action_id)
            .execution_options(populate_existing=True)
        )
        action = (await db.execute(query)).scalar_one_or_none()
        if not action:
            raise HTTPException(status_code=404, detail="Tool Action not found.")
        return action

    async def list_actions(
        self,
        db: AsyncSession,
        *,
        limit: int,
        offset: int,
        action_status: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> tuple[list[ToolAction], int]:
        filters = []
        if action_status:
            filters.append(ToolAction.status == action_status)
        if tool_name:
            filters.append(ToolAction.tool_name == tool_name)
        total = int(
            (
                await db.execute(select(func.count(ToolAction.id)).where(*filters))
            ).scalar_one()
        )
        query = (
            select(ToolAction)
            .options(selectinload(ToolAction.events), selectinload(ToolAction.control))
            .where(*filters)
            .order_by(ToolAction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await db.execute(query)).scalars().all()), total

    async def _load_for_update(self, db: AsyncSession, action_id: str) -> ToolAction:
        query = (
            select(ToolAction)
            .options(selectinload(ToolAction.events), selectinload(ToolAction.control))
            .where(ToolAction.id == action_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        action = (await db.execute(query)).scalar_one_or_none()
        if not action:
            raise HTTPException(status_code=404, detail="Tool Action not found.")
        return action

    @staticmethod
    def _check_version(action: ToolAction, expected_version: int) -> None:
        if action.version != expected_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Stale Tool Action version.",
                    "expected_version": expected_version,
                    "current_version": action.version,
                },
            )

    @staticmethod
    def _high_risk_write_definition(tool_name: str) -> ToolDefinition:
        definition = tool_registry.get_definition(tool_name)
        if not definition:
            raise HTTPException(status_code=404, detail="Tool is not registered.")
        if not (
            definition.risk_level == "high"
            and definition.operation_type is OperationType.WRITE
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only high-risk write Tools use the Action approval workflow.",
            )
        return definition

    @staticmethod
    def _validate_payload(
        definition: ToolDefinition, payload: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return definition.input_schema(**payload).model_dump()
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Tool payload failed schema validation.",
            ) from exc

    @staticmethod
    def _verified_payload(
        action: ToolAction, definition: ToolDefinition
    ) -> dict[str, Any]:
        try:
            payload = tool_payload_security.decrypt(action.payload_encrypted)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tool Action payload failed integrity verification.",
            ) from exc
        if tool_payload_security.payload_hash(payload) != action.payload_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tool Action payload hash does not match.",
            )
        return ToolGovernanceService._validate_payload(definition, payload)

    def _append_transition(
        self,
        action: ToolAction,
        transition: Any,
        actor: Any,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        self._append_event(
            action,
            command=transition.command,
            source=transition.source,
            target=transition.target,
            actor=actor,
            details=details,
        )
        try:
            TOOL_ACTION_TRANSITIONS_TOTAL.add(
                1,
                {
                    "command": transition.command,
                    "from_status": transition.source,
                    "to_status": transition.target,
                },
            )
        except Exception:
            pass

    @staticmethod
    def _append_event(
        action: ToolAction,
        *,
        command: str,
        source: Optional[str],
        target: str,
        actor: Any,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        action.events.append(
            ToolActionEvent(
                id=str(uuid.uuid4()),
                sequence=len(action.events) + 1,
                action=command,
                from_status=source,
                to_status=target,
                actor_user_id=actor.id,
                actor_role=actor.role,
                request_id=get_request_id() or action.request_id,
                trace_id=get_current_trace_id() or action.trace_id,
                details=sanitize_value(details or {}),
            )
        )


tool_governance_service = ToolGovernanceService()
