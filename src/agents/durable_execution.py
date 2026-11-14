import datetime
import uuid
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.db_models import AgentExecution, ResponseApproval


class AgentExecutionStatus:
    """集中定义 Durable Execution 的合法状态。"""

    RUNNING = "running"
    INTERRUPTED = "interrupted"
    RESUME_PENDING = "resume_pending"
    RESUMING = "resuming"
    COMPLETED = "completed"
    FAILED = "failed"
    # 提交人撤回工单：暂停的执行不再恢复。
    CANCELLED = "cancelled"


class DurableExecutionService:
    """管理 Checkpoint Thread 与工单、审批、Agent Run 的持久化关联。

    恢复前使用数据库租约抢占执行权，防止多 Worker 重复恢复。
    """

    async def create(
        self,
        db: AsyncSession,
        *,
        ticket_id: int,
        request_id: str,
        checkpoint_backend: str,
    ) -> AgentExecution:
        execution = AgentExecution(
            id=str(uuid.uuid4()),
            ticket_id=ticket_id,
            request_id=request_id,
            checkpoint_namespace=settings.LANGGRAPH_CHECKPOINT_NAMESPACE,
            checkpoint_backend=checkpoint_backend,
            workflow_version=settings.AGENT_WORKFLOW_VERSION,
            status=AgentExecutionStatus.RUNNING,
        )
        db.add(execution)
        await db.flush()
        return execution

    async def get(self, db: AsyncSession, execution_id: str) -> AgentExecution | None:
        result = await db.execute(
            select(AgentExecution).where(AgentExecution.id == execution_id)
        )
        return result.scalars().first()

    async def get_by_approval(
        self, db: AsyncSession, approval_id: int
    ) -> AgentExecution | None:
        result = await db.execute(
            select(AgentExecution).where(AgentExecution.approval_id == approval_id)
        )
        return result.scalars().first()

    async def cancel_for_approval(self, db: AsyncSession, approval_id: int) -> bool:
        """撤回时把等待审批的执行标为已取消，启动扫描和恢复租约都不会再选中它。"""
        execution = await self.get_by_approval(db, approval_id)
        if execution is None or execution.status not in {
            AgentExecutionStatus.INTERRUPTED,
            AgentExecutionStatus.RESUME_PENDING,
        }:
            return False
        execution.status = AgentExecutionStatus.CANCELLED
        return True

    async def mark_interrupted(
        self,
        db: AsyncSession,
        *,
        execution: AgentExecution,
        approval_id: int,
        checkpoint_id: str | None,
        interrupt_payload: dict[str, Any] | None,
        trace_id: str | None,
    ) -> None:
        now = datetime.datetime.utcnow()
        execution.status = AgentExecutionStatus.INTERRUPTED
        execution.approval_id = approval_id
        execution.checkpoint_id = checkpoint_id
        execution.interrupt_type = str(
            (interrupt_payload or {}).get("type", "response_approval")
        )[:80]
        execution.interrupt_payload = interrupt_payload or {}
        execution.initial_trace_id = trace_id
        execution.interrupted_at = now
        execution.updated_at = now
        execution.lock_version += 1
        await db.flush()

    async def mark_initial_completed(
        self,
        db: AsyncSession,
        *,
        execution: AgentExecution,
        checkpoint_id: str | None,
        trace_id: str | None,
    ) -> None:
        now = datetime.datetime.utcnow()
        execution.status = AgentExecutionStatus.COMPLETED
        execution.checkpoint_id = checkpoint_id
        execution.initial_trace_id = trace_id
        execution.completed_at = now
        execution.updated_at = now
        execution.lock_version += 1
        await db.flush()

    async def attach_agent_run(
        self, db: AsyncSession, execution: AgentExecution, agent_run_id: str
    ) -> None:
        execution.agent_run_id = agent_run_id
        execution.updated_at = datetime.datetime.utcnow()
        await db.flush()

    async def mark_failed(
        self, db: AsyncSession, execution: AgentExecution, exc: BaseException
    ) -> None:
        execution.status = AgentExecutionStatus.FAILED
        execution.last_error_type = exc.__class__.__name__[:100]
        execution.last_error_message = "Agent workflow execution failed."
        execution.lease_owner = None
        execution.lease_expires_at = None
        execution.updated_at = datetime.datetime.utcnow()
        execution.lock_version += 1
        await db.flush()

    async def queue_resume(
        self, db: AsyncSession, execution: AgentExecution
    ) -> AgentExecution:
        if execution.status == AgentExecutionStatus.COMPLETED:
            return execution
        if execution.status not in {
            AgentExecutionStatus.INTERRUPTED,
            AgentExecutionStatus.RESUME_PENDING,
            AgentExecutionStatus.RESUMING,
        }:
            raise ValueError(
                f"Execution {execution.id} cannot resume from {execution.status}."
            )
        if execution.status == AgentExecutionStatus.INTERRUPTED:
            execution.status = AgentExecutionStatus.RESUME_PENDING
            execution.updated_at = datetime.datetime.utcnow()
            execution.lock_version += 1
            await db.flush()
        return execution

    async def acquire_resume_lease(
        self, db: AsyncSession, execution_id: str
    ) -> str | None:
        """原子抢占恢复租约；被其他 Worker 持有时返回 None。"""
        now = datetime.datetime.utcnow()
        owner = str(uuid.uuid4())
        expires_at = now + datetime.timedelta(
            seconds=settings.LANGGRAPH_RESUME_LEASE_SECONDS
        )
        result = await db.execute(
            update(AgentExecution)
            .where(
                AgentExecution.id == execution_id,
                or_(
                    AgentExecution.status == AgentExecutionStatus.INTERRUPTED,
                    AgentExecution.status == AgentExecutionStatus.RESUME_PENDING,
                    and_(
                        AgentExecution.status == AgentExecutionStatus.RESUMING,
                        AgentExecution.lease_expires_at.is_not(None),
                        AgentExecution.lease_expires_at < now,
                    ),
                ),
            )
            .values(
                status=AgentExecutionStatus.RESUMING,
                lease_owner=owner,
                lease_expires_at=expires_at,
                resume_attempts=AgentExecution.resume_attempts + 1,
                lock_version=AgentExecution.lock_version + 1,
                updated_at=now,
            )
        )
        await db.commit()
        return owner if result.rowcount == 1 else None

    async def mark_resume_completed(
        self,
        db: AsyncSession,
        *,
        execution_id: str,
        lease_owner: str,
        checkpoint_id: str | None,
        resume_trace_id: str | None,
    ) -> bool:
        now = datetime.datetime.utcnow()
        result = await db.execute(
            update(AgentExecution)
            .where(
                AgentExecution.id == execution_id,
                AgentExecution.status == AgentExecutionStatus.RESUMING,
                AgentExecution.lease_owner == lease_owner,
            )
            .values(
                status=AgentExecutionStatus.COMPLETED,
                checkpoint_id=checkpoint_id,
                resume_trace_id=resume_trace_id,
                lease_owner=None,
                lease_expires_at=None,
                last_error_type=None,
                last_error_message=None,
                resumed_at=now,
                completed_at=now,
                updated_at=now,
                lock_version=AgentExecution.lock_version + 1,
            )
        )
        await db.commit()
        return result.rowcount == 1

    async def mark_resume_pending(
        self,
        db: AsyncSession,
        *,
        execution_id: str,
        lease_owner: str,
        exc: BaseException,
    ) -> None:
        await db.execute(
            update(AgentExecution)
            .where(
                AgentExecution.id == execution_id,
                AgentExecution.lease_owner == lease_owner,
            )
            .values(
                status=AgentExecutionStatus.RESUME_PENDING,
                lease_owner=None,
                lease_expires_at=None,
                last_error_type=exc.__class__.__name__[:100],
                last_error_message="Agent workflow resume failed and remains retryable.",
                updated_at=datetime.datetime.utcnow(),
                lock_version=AgentExecution.lock_version + 1,
            )
        )
        await db.commit()

    async def list_recoverable(
        self, db: AsyncSession, *, limit: int = 100
    ) -> list[tuple[AgentExecution, ResponseApproval]]:
        """查询已完成人工决策但 Graph 尚未续跑的执行。"""
        now = datetime.datetime.utcnow()
        result = await db.execute(
            select(AgentExecution, ResponseApproval)
            .join(ResponseApproval, ResponseApproval.id == AgentExecution.approval_id)
            .where(
                ResponseApproval.status != "pending",
                or_(
                    AgentExecution.status == AgentExecutionStatus.INTERRUPTED,
                    AgentExecution.status == AgentExecutionStatus.RESUME_PENDING,
                    and_(
                        AgentExecution.status == AgentExecutionStatus.RESUMING,
                        AgentExecution.lease_expires_at.is_not(None),
                        AgentExecution.lease_expires_at < now,
                    ),
                ),
            )
            .order_by(AgentExecution.updated_at.asc())
            .limit(limit)
        )
        return list(result.all())


durable_execution_service = DurableExecutionService()
