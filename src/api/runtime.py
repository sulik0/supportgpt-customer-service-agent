"""Workflow 运行、AgentRun 记录和审批恢复，供各路由和启动期共用。"""

import logging
from typing import Callable

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.future import select

from src.agents.checkpointing import checkpoint_manager
from src.agents.durable_execution import AgentExecutionStatus, durable_execution_service
from src.agents.graph import resume_agent_workflow, run_agent_workflow
from src.approval.workflows import human_it_loop_service
from src.database import AsyncSessionLocal
from src.feedback.service import feedback_service
from src.memory import MemoryContext, memory_service
from src.models.db_models import (
    AgentExecution,
    AgentRun,
    ResponseApproval,
    Ticket,
    ToolRuntimeSetting,
)
from src.observability.tracing import get_current_trace_id, get_request_id
from src.tools.audit import tool_audit_repository
from src.tools.audit_context import begin_tool_audit_scope, finish_tool_audit_scope
from src.tools.registry import tool_registry

logger = logging.getLogger("supportgpt.main")


async def load_tool_runtime_settings() -> None:
    """启动时将持久化 Tool 开关恢复到当前进程。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ToolRuntimeSetting))
        tool_registry.replace_runtime_settings(
            {item.tool_name: item.enabled for item in result.scalars().all()}
        )


async def run_workflow_with_tool_audit(db: AsyncSession, initial_state: dict) -> dict:
    """并行 Tool 只收集审计，由主请求会话统一持久化。"""
    token = begin_tool_audit_scope()
    workflow_error: Exception | None = None
    output: dict = {}
    try:
        output = await run_agent_workflow(initial_state)
    except Exception as exc:
        workflow_error = exc
    finally:
        audit_records = finish_tool_audit_scope(token)

    try:
        await tool_audit_repository.persist_many(db, audit_records)
        if audit_records:
            await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("Unable to persist required Tool audit: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tool audit could not be persisted.",
        ) from exc
    if workflow_error:
        raise workflow_error
    return output


async def record_agent_run_fail_open(
    *,
    primary_db: AsyncSession,
    agent_output: dict,
    input_text: str,
    endpoint: str,
    session_id: str | None = None,
) -> AgentRun | None:
    """使用独立事务保存 Agent Run，失败时不影响客服主流程。"""
    try:
        feedback_sessions = async_sessionmaker(
            bind=primary_db.bind,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with feedback_sessions() as feedback_db:
            agent_run = await feedback_service.record_agent_run(
                feedback_db,
                agent_output=agent_output,
                input_text=input_text,
                endpoint=endpoint,
                session_id=session_id,
                trace_id=agent_output.get("trace_id") or get_current_trace_id(),
            )
            await feedback_db.commit()
            return agent_run
    except Exception as exc:
        logger.warning("Unable to persist Agent Run for feedback: %s", exc)
        return None


async def link_approval_fail_open(
    primary_db: AsyncSession, agent_run_id: str, approval_id: int
) -> None:
    """使用独立事务关联审批记录，关联失败只写日志。"""
    try:
        feedback_sessions = async_sessionmaker(
            bind=primary_db.bind,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with feedback_sessions() as feedback_db:
            await feedback_service.link_entity(
                feedback_db,
                agent_run_id=agent_run_id,
                entity_type="approval",
                entity_id=approval_id,
            )
            await feedback_db.commit()
    except Exception as exc:
        logger.warning("Unable to link approval to Agent Run: %s", exc)


async def record_ticket_result_required(
    *,
    db: AsyncSession,
    agent_output: dict,
    input_text: str,
    endpoint: str,
    session_id: str | None,
    approval_id: int | None,
) -> AgentRun:
    """可靠保存工作台处理结果；写入失败时不返回虚假的创建成功。"""
    try:
        agent_run = await feedback_service.record_agent_run(
            db,
            agent_output=agent_output,
            input_text=input_text,
            endpoint=endpoint,
            session_id=session_id,
            trace_id=agent_output.get("trace_id") or get_current_trace_id(),
        )
        if approval_id:
            await feedback_service.link_entity(
                db,
                agent_run_id=agent_run.id,
                entity_type="approval",
                entity_id=approval_id,
            )
        await db.commit()
        return agent_run
    except Exception as exc:
        await db.rollback()
        logger.exception("Unable to persist required ticket Agent result: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ticket was received but its Agent result could not be persisted.",
        ) from exc


async def process_ticket_with_agent(
    *,
    db: AsyncSession,
    ticket: Ticket,
    kb_version: str,
    endpoint: str,
    session_id: str | None = None,
    memory_context: MemoryContext | None = None,
    require_persisted_result: bool = False,
    release_preliminary: Callable[[dict], bool] | None = None,
) -> tuple[dict, int | None, AgentRun | None]:
    """执行并持久化工单 Workflow，供创建工单和对话入口复用。"""
    execution = await durable_execution_service.create(
        db,
        ticket_id=ticket.id,
        request_id=get_request_id() or "background",
        checkpoint_backend=checkpoint_manager.backend,
    )
    await db.commit()
    try:
        agent_output = await run_workflow_with_tool_audit(
            db,
            {
                **(memory_context.state_updates() if memory_context else {}),
                "ticket_id": ticket.id,
                "customer_id": ticket.customer_id,
                "subject": ticket.subject,
                "description": ticket.description,
                "kb_version": kb_version,
                "checkpoint_thread_id": execution.id,
                "checkpoint_namespace": execution.checkpoint_namespace,
                "durable_execution_enabled": True,
            },
        )
    except Exception as exc:
        await durable_execution_service.mark_failed(db, execution, exc)
        await db.commit()
        raise
    ticket.sentiment = agent_output.get("sentiment")
    ticket.priority = agent_output.get("priority")
    ticket.department = agent_output.get("department")
    ticket.sla_hours = agent_output.get("sla_hours")

    approval_id = None
    if agent_output.get("approval_required"):
        approval = await human_it_loop_service.create_pending_approval(
            db=db,
            ticket_id=ticket.id,
            drafted_response=agent_output.get("suggested_response", ""),
            commit=False,
        )
        approval_id = approval.id
    if agent_output.get("workflow_interrupted") and approval_id is None:
        exc = RuntimeError("Interrupted workflow did not create an approval record.")
        await durable_execution_service.mark_failed(db, execution, exc)
        await db.commit()
        raise exc
    if agent_output.get("workflow_interrupted") and approval_id:
        await durable_execution_service.mark_interrupted(
            db,
            execution=execution,
            approval_id=approval_id,
            checkpoint_id=agent_output.get("checkpoint_id"),
            interrupt_payload=agent_output.get("interrupt_payload"),
            trace_id=agent_output.get("trace_id"),
        )
    else:
        await durable_execution_service.mark_initial_completed(
            db,
            execution=execution,
            checkpoint_id=agent_output.get("checkpoint_id"),
            trace_id=agent_output.get("trace_id"),
        )
    if memory_context is not None:
        await memory_service.record_assistant_result(
            db,
            context=memory_context,
            content=agent_output.get("suggested_response", ""),
            ticket_id=ticket.id,
            approval_id=approval_id,
            intent=agent_output.get("intent"),
            department=agent_output.get("department"),
            preliminary=bool(
                approval_id
                and release_preliminary is not None
                and release_preliminary(agent_output)
            ),
        )
    await db.commit()

    if require_persisted_result:
        agent_run = await record_ticket_result_required(
            db=db,
            agent_output=agent_output,
            input_text=ticket.description,
            endpoint=endpoint,
            session_id=session_id,
            approval_id=approval_id,
        )
    else:
        agent_run = await record_agent_run_fail_open(
            primary_db=db,
            agent_output=agent_output,
            input_text=ticket.description,
            endpoint=endpoint,
            session_id=session_id,
        )
        if agent_run and approval_id:
            await link_approval_fail_open(db, agent_run.id, approval_id)
    if agent_run:
        await durable_execution_service.attach_agent_run(db, execution, agent_run.id)
        await db.commit()
    return agent_output, approval_id, agent_run


async def resume_approval_execution(
    db: AsyncSession, approval: ResponseApproval
) -> AgentExecution | None:
    """将已持久化的人工决策送回原 LangGraph Thread。"""
    execution = await durable_execution_service.get_by_approval(db, approval.id)
    if execution is None or execution.status == AgentExecutionStatus.COMPLETED:
        return execution
    await durable_execution_service.queue_resume(db, execution)
    await db.commit()
    lease_owner = await durable_execution_service.acquire_resume_lease(db, execution.id)
    if lease_owner is None:
        await db.refresh(execution)
        return execution

    try:
        await memory_service.finalize_approval(
            db,
            approval_id=approval.id,
            status=approval.status,
            final_response=approval.modified_response or approval.drafted_response,
        )
        final_response = approval.modified_response or approval.drafted_response
        output = await resume_agent_workflow(
            checkpoint_thread_id=execution.id,
            checkpoint_namespace=execution.checkpoint_namespace,
            decision={
                "status": approval.status,
                "final_response": final_response,
                "approval_id": approval.id,
            },
        )
        if execution.agent_run_id:
            run_result = await db.execute(
                select(AgentRun).where(AgentRun.id == execution.agent_run_id)
            )
            agent_run = run_result.scalars().first()
            if agent_run:
                # AgentRun 保留模型原始草稿，人工最终版本由 Approval/Feedback 保存。
                agent_run.workflow_path = output.get(
                    "workflow_path", agent_run.workflow_path
                )
        completed = await durable_execution_service.mark_resume_completed(
            db,
            execution_id=execution.id,
            lease_owner=lease_owner,
            checkpoint_id=output.get("checkpoint_id"),
            resume_trace_id=output.get("resume_trace_id"),
        )
        if not completed:
            raise RuntimeError("Durable execution lease was lost during resume.")
    except Exception as exc:
        await durable_execution_service.mark_resume_pending(
            db,
            execution_id=execution.id,
            lease_owner=lease_owner,
            exc=exc,
        )
        logger.exception(
            "Unable to resume durable Agent workflow",
            extra={"execution_id": execution.id, "approval_id": approval.id},
        )
    await db.refresh(execution)
    return execution


async def recover_resumable_workflows() -> None:
    """应用重启后自动续跑已决策但未完成的审批 Thread。"""
    async with AsyncSessionLocal() as db:
        recoverable = await durable_execution_service.list_recoverable(db)
        for _, approval in recoverable:
            await resume_approval_execution(db, approval)


async def record_evaluation_fail_open(
    primary_db: AsyncSession,
    *,
    agent_run_id: str,
    metrics: dict,
    passed: bool,
    external_ref: str | None,
) -> None:
    """使用独立事务回写可信评测，写入失败不影响评测结果。"""
    try:
        feedback_sessions = async_sessionmaker(
            bind=primary_db.bind,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with feedback_sessions() as feedback_db:
            await feedback_service.record_evaluation(
                feedback_db,
                agent_run_id=agent_run_id,
                metrics=metrics,
                passed=passed,
                external_ref=external_ref,
            )
            await feedback_db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Unable to persist evaluation feedback: %s", exc)
