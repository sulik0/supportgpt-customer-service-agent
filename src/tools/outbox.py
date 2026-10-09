"""Transactional Outbox、异步投递、Retry Queue 与 DLQ。"""

import asyncio
import datetime
import logging
import uuid
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import settings
from src.database import AsyncSessionLocal
from src.models.db_models import ToolAction, ToolOutboxEvent
from src.observability.metrics import TOOL_OUTBOX_EVENTS_TOTAL
from src.observability.sanitization import sanitize_value
from src.observability.tracing import (
    bind_request_id,
    get_current_trace_id,
    get_request_id,
    get_tracer,
    observed_span,
    reset_request_id,
)


logger = logging.getLogger("supportgpt.tools.outbox")
tracer = get_tracer(__name__)


class OutboxEventType:
    """限定 Worker 可以分发的治理事件类型。"""

    EXECUTE = "execute"
    RECONCILE = "reconcile"
    COMPENSATE = "compensate"


class OutboxStatus:
    """同一张表同时表达待投递、Retry Queue 和 DLQ。"""

    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class OutboxLeaseLost(RuntimeError):
    """旧 Worker 已失去执行资格，不允许覆盖新 Worker 或人工结果。"""


async def ensure_outbox_lease(db: AsyncSession, event: ToolOutboxEvent) -> None:
    """用领取版本作为 fencing token，并在状态提交期间锁住租约记录。"""
    token = event.version
    owner = event.lease_owner
    current = (
        await db.execute(
            select(ToolOutboxEvent.id)
            .where(
                ToolOutboxEvent.id == event.id,
                ToolOutboxEvent.status == OutboxStatus.PROCESSING,
                ToolOutboxEvent.lease_owner == owner,
                ToolOutboxEvent.version == token,
                ToolOutboxEvent.lease_expires_at > datetime.datetime.utcnow(),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if current is None:
        raise OutboxLeaseLost("Outbox lease expired or was replaced.")


class ToolOutboxService:
    """在业务事务内写入无敏感参数的 Outbox 事件。"""

    def enqueue(
        self,
        db: AsyncSession,
        *,
        action: ToolAction,
        event_type: str,
        actor_user_id: Optional[int],
        actor_role: str,
        dedupe_key: str,
        payload: Optional[dict[str, Any]] = None,
        delay_seconds: float = 0.0,
    ) -> ToolOutboxEvent:
        now = datetime.datetime.utcnow()
        event = ToolOutboxEvent(
            id=str(uuid.uuid4()),
            tool_action_id=action.id,
            event_type=event_type,
            status=OutboxStatus.PENDING,
            dedupe_key=dedupe_key,
            payload=sanitize_value(payload or {}),
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            request_id=get_request_id() or action.request_id,
            trace_id=get_current_trace_id() or action.trace_id,
            attempts=0,
            max_attempts=settings.TOOL_OUTBOX_MAX_ATTEMPTS,
            version=1,
            available_at=now + datetime.timedelta(seconds=max(delay_seconds, 0.0)),
            created_at=now,
            updated_at=now,
        )
        db.add(event)
        return event

    async def list_events(
        self,
        db: AsyncSession,
        *,
        limit: int,
        offset: int,
        event_status: Optional[str] = None,
        action_id: Optional[str] = None,
    ) -> tuple[list[ToolOutboxEvent], int]:
        filters = []
        if event_status:
            filters.append(ToolOutboxEvent.status == event_status)
        if action_id:
            filters.append(ToolOutboxEvent.tool_action_id == action_id)
        total = int(
            (
                await db.execute(select(func.count(ToolOutboxEvent.id)).where(*filters))
            ).scalar_one()
        )
        query = (
            select(ToolOutboxEvent)
            .where(*filters)
            .order_by(ToolOutboxEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await db.execute(query)).scalars().all()), total

    async def retry_dead_letter(
        self, db: AsyncSession, *, event_id: str, actor: Any
    ) -> ToolOutboxEvent:
        """显式重放 DLQ；写操作仍受 Action 状态与 unknown 对账门禁保护。"""
        query = (
            select(ToolOutboxEvent)
            .where(ToolOutboxEvent.id == event_id)
            .with_for_update()
        )
        event = (await db.execute(query)).scalar_one_or_none()
        if not event:
            raise HTTPException(status_code=404, detail="Outbox event not found.")
        if event.status != OutboxStatus.DEAD_LETTER:
            raise HTTPException(
                status_code=409, detail="Only DLQ events can be retried."
            )
        action = await db.get(ToolAction, event.tool_action_id)
        if action and action.status in {
            "succeeded",
            "failed",
            "rejected",
            "compensated",
            "compensation_failed",
        }:
            raise HTTPException(
                status_code=409, detail="A terminal Action cannot be replayed from DLQ."
            )
        event.status = OutboxStatus.RETRY
        event.attempts = 0
        event.version += 1
        event.available_at = datetime.datetime.utcnow()
        event.lease_owner = None
        event.lease_expires_at = None
        event.completed_at = None
        event.last_error_type = None
        event.last_error_message = None
        from src.tools.governance import tool_governance_service

        await tool_governance_service.record_outbox_retry(db, event, actor)
        await db.flush()
        return event


class ToolOutboxWorker:
    """使用数据库租约抢占事件，多实例之间以乐观更新避免重复消费。"""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self.worker_id = f"outbox-{uuid.uuid4().hex[:12]}"

    async def start(self, *, force: bool = False) -> None:
        if not force and (
            not settings.TOOL_OUTBOX_WORKER_ENABLED or settings.APP_ENV == "testing"
        ):
            return
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_forever())
        logger.info("tool outbox worker started", extra={"worker_id": self.worker_id})

    async def stop(self) -> None:
        if not self._task:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def run_once(
        self,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    ) -> int:
        event_ids = await self._claim_batch(session_factory)
        # 每个已领取事件立即开始续租，避免批次尾部任务等待到租约过期。
        await asyncio.gather(
            *(self._process_one(session_factory, event_id) for event_id in event_ids)
        )
        return len(event_ids)

    async def _run_forever(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                count = await self.run_once()
            except Exception:
                logger.exception("tool outbox worker iteration failed")
                count = 0
            delay = 0.05 if count else settings.TOOL_OUTBOX_POLL_INTERVAL_SECONDS
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _claim_batch(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> list[str]:
        now = datetime.datetime.utcnow()
        lease_expires = now + datetime.timedelta(
            seconds=settings.TOOL_OUTBOX_LEASE_SECONDS
        )
        claimed: list[str] = []
        exhausted_ids: list[str] = []
        async with session_factory() as db:
            query = (
                select(ToolOutboxEvent)
                .where(
                    ToolOutboxEvent.available_at <= now,
                    or_(
                        ToolOutboxEvent.status.in_(
                            [OutboxStatus.PENDING, OutboxStatus.RETRY]
                        ),
                        (
                            (ToolOutboxEvent.status == OutboxStatus.PROCESSING)
                            & (ToolOutboxEvent.lease_expires_at < now)
                        ),
                    ),
                )
                .order_by(ToolOutboxEvent.created_at)
                .limit(settings.TOOL_OUTBOX_BATCH_SIZE)
            )
            candidates = list((await db.execute(query)).scalars().all())
            # 与人工确认使用相同锁顺序，批次选择仍按创建时间保证公平。
            for event in sorted(candidates, key=lambda item: item.id):
                if event.attempts >= event.max_attempts:
                    exhausted = await db.execute(
                        update(ToolOutboxEvent)
                        .where(
                            ToolOutboxEvent.id == event.id,
                            ToolOutboxEvent.version == event.version,
                            ToolOutboxEvent.status == event.status,
                            or_(
                                ToolOutboxEvent.status.in_(
                                    [OutboxStatus.PENDING, OutboxStatus.RETRY]
                                ),
                                ToolOutboxEvent.lease_expires_at < now,
                            ),
                        )
                        .values(
                            status=OutboxStatus.DEAD_LETTER,
                            version=event.version + 1,
                            lease_owner=None,
                            lease_expires_at=None,
                            last_error_type="lease_expired",
                            last_error_message=(
                                "Worker lease expired too many times; manual review required."
                            ),
                            completed_at=now,
                            updated_at=now,
                        )
                    )
                    if exhausted.rowcount == 1:
                        exhausted_ids.append(event.id)
                    continue
                result = await db.execute(
                    update(ToolOutboxEvent)
                    .where(
                        ToolOutboxEvent.id == event.id,
                        ToolOutboxEvent.version == event.version,
                        ToolOutboxEvent.status == event.status,
                        or_(
                            ToolOutboxEvent.status.in_(
                                [OutboxStatus.PENDING, OutboxStatus.RETRY]
                            ),
                            ToolOutboxEvent.lease_expires_at < now,
                        ),
                    )
                    .values(
                        status=OutboxStatus.PROCESSING,
                        attempts=event.attempts + 1,
                        version=event.version + 1,
                        lease_owner=self.worker_id,
                        lease_expires_at=lease_expires,
                        updated_at=now,
                    )
                )
                if result.rowcount == 1:
                    claimed.append(event.id)
            if exhausted_ids:
                await db.flush()
                from src.tools.governance import tool_governance_service

                for event_id in exhausted_ids:
                    exhausted_event = await db.get(
                        ToolOutboxEvent, event_id, populate_existing=True
                    )
                    if exhausted_event:
                        await tool_governance_service.record_dead_letter(
                            db, exhausted_event
                        )
            await db.commit()
        for event_id in exhausted_ids:
            async with session_factory() as db:
                event = await db.get(ToolOutboxEvent, event_id)
                if event:
                    self._metric(event, OutboxStatus.DEAD_LETTER)
        return claimed

    async def _process_one(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_id: str,
    ) -> None:
        async with session_factory() as db:
            event = await db.get(ToolOutboxEvent, event_id)
            if (
                not event
                or event.lease_owner != self.worker_id
                or event.status != OutboxStatus.PROCESSING
            ):
                return
            token = event.version
            # 固定本次领取凭证，事务回滚不能把它刷新为新 Worker 的版本。
            claimed_event = SimpleNamespace(
                **{
                    name: getattr(event, name)
                    for name in (
                        "id",
                        "tool_action_id",
                        "event_type",
                        "actor_user_id",
                        "actor_role",
                        "request_id",
                        "trace_id",
                        "attempts",
                        "version",
                        "lease_owner",
                    )
                }
            )
            heartbeat = asyncio.create_task(
                self._heartbeat(session_factory, event_id, token)
            )
            request_token = bind_request_id(event.request_id)
            try:
                # 延迟导入打破 Governance 与 Outbox 的模块依赖环。
                from src.tools.governance import tool_governance_service

                with observed_span(
                    tracer,
                    f"supportgpt.tool_outbox.{event.event_type}",
                    {
                        "tool.outbox_id": event.id,
                        "tool.action_id": event.tool_action_id,
                        "tool.outbox_attempt": event.attempts,
                        "tool.origin_trace_id": event.trace_id,
                    },
                ):
                    await tool_governance_service.dispatch_outbox_event(
                        db, claimed_event
                    )
                await self._mark_succeeded(db, event_id, token)
            except OutboxLeaseLost:
                await db.rollback()
                logger.warning("tool outbox lease lost", extra={"outbox_id": event_id})
            except Exception as exc:
                await db.rollback()
                await self._mark_failed(session_factory, event_id, exc, token)
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
                reset_request_id(request_token)

    async def _heartbeat(self, session_factory, event_id, token):
        """只续当前且未过期的租约，不能复活已经失效的执行资格。"""
        while True:
            await asyncio.sleep(max(settings.TOOL_OUTBOX_LEASE_SECONDS / 3, 0.1))
            try:
                if not await self.renew_lease(session_factory, event_id, token):
                    return
            except Exception:
                logger.warning(
                    "tool outbox heartbeat failed", extra={"outbox_id": event_id}
                )
                return

    async def renew_lease(self, session_factory, event_id, token) -> bool:
        async with session_factory() as db:
            now = datetime.datetime.utcnow()
            result = await db.execute(
                update(ToolOutboxEvent)
                .where(
                    ToolOutboxEvent.id == event_id,
                    ToolOutboxEvent.version == token,
                    ToolOutboxEvent.lease_owner == self.worker_id,
                    ToolOutboxEvent.status == OutboxStatus.PROCESSING,
                    ToolOutboxEvent.lease_expires_at > now,
                )
                .values(
                    lease_expires_at=now
                    + datetime.timedelta(seconds=settings.TOOL_OUTBOX_LEASE_SECONDS),
                    updated_at=now,
                )
            )
            await db.commit()
            return result.rowcount == 1

    async def _owned_event(self, db, event_id, token):
        """完成、重试和死信写回都必须持有相同且未过期的租约。"""
        return (
            await db.execute(
                select(ToolOutboxEvent)
                .where(
                    ToolOutboxEvent.id == event_id,
                    ToolOutboxEvent.version == token,
                    ToolOutboxEvent.lease_owner == self.worker_id,
                    ToolOutboxEvent.status == OutboxStatus.PROCESSING,
                    ToolOutboxEvent.lease_expires_at > datetime.datetime.utcnow(),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _mark_succeeded(
        self, db: AsyncSession, event_id: str, token: int
    ) -> None:
        event = await self._owned_event(db, event_id, token)
        if not event:
            return
        event.status = OutboxStatus.SUCCEEDED
        event.version += 1
        event.lease_owner = None
        event.lease_expires_at = None
        event.completed_at = datetime.datetime.utcnow()
        await db.commit()
        self._metric(event, OutboxStatus.SUCCEEDED)
        logger.info(
            "tool outbox delivered",
            extra={
                "outbox_id": event.id,
                "action_id": event.tool_action_id,
                "event_type": event.event_type,
                "attempts": event.attempts,
            },
        )

    async def _mark_failed(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_id: str,
        exc: Exception,
        token: int,
    ) -> None:
        async with session_factory() as db:
            event = await self._owned_event(db, event_id, token)
            if not event:
                return
            exhausted = event.attempts >= event.max_attempts
            event.status = OutboxStatus.DEAD_LETTER if exhausted else OutboxStatus.RETRY
            event.version += 1
            event.lease_owner = None
            event.lease_expires_at = None
            event.last_error_type = exc.__class__.__name__[:80]
            event.last_error_message = "Outbox delivery failed; inspect linked Trace."
            if exhausted:
                event.completed_at = datetime.datetime.utcnow()
            else:
                backoff = settings.TOOL_OUTBOX_RETRY_BASE_SECONDS * (
                    2 ** max(event.attempts - 1, 0)
                )
                event.available_at = datetime.datetime.utcnow() + datetime.timedelta(
                    seconds=min(backoff, settings.TOOL_OUTBOX_RETRY_MAX_SECONDS)
                )
            if exhausted:
                from src.tools.governance import tool_governance_service

                await tool_governance_service.record_dead_letter(db, event)
            await db.commit()
            self._metric(event, event.status)
            logger.warning(
                "tool outbox delivery failed",
                extra={
                    "outbox_id": event.id,
                    "action_id": event.tool_action_id,
                    "event_type": event.event_type,
                    "status": event.status,
                    "attempts": event.attempts,
                },
            )

    @staticmethod
    def _metric(event: ToolOutboxEvent, outcome: str) -> None:
        try:
            TOOL_OUTBOX_EVENTS_TOTAL.add(
                1, {"event_type": event.event_type, "outcome": outcome}
            )
        except Exception:
            pass


tool_outbox_service = ToolOutboxService()
tool_outbox_worker = ToolOutboxWorker()
