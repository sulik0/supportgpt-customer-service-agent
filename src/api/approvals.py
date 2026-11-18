"""回复审批和 Agent 执行恢复。"""

import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.agents.durable_execution import durable_execution_service
from src.api import runtime
from src.approval.workflows import human_it_loop_service
from src.auth.rbac import require_agent, require_manager
from src.database import get_db
from src.models.db_models import ResponseApproval, User
from src.models.schemas import (
    AgentExecutionResponse,
    ResponseApprovalRequest,
    ResponseApprovalResponse,
)
from src.tickets.test_marks import test_ticket_ids

router = APIRouter()


# --- HUMAN IN THE LOOP APPROVAL APIS ---
@router.get("/approvals/pending", response_model=list[ResponseApprovalResponse])
async def list_pending_approvals(
    include_test: bool = Query(default=False),
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """Secure endpoint: list all response drafts needing validation."""
    records = await human_it_loop_service.get_pending_approvals(db)
    if not include_test:
        # 测试工单的待审批不进入员工的审批列表。
        marked = set((await db.execute(test_ticket_ids())).scalars().all())
        records = [record for record in records if record.ticket_id not in marked]

    # Map to schema
    output = []
    for r in records:
        output.append(
            ResponseApprovalResponse(
                id=r.id,
                ticket_id=r.ticket_id,
                status=r.status,
                final_response=r.drafted_response,
                latency_seconds=r.latency_seconds or 0.0,
                approved_at=r.created_at,
            )
        )
    return output


@router.post("/approvals/{approval_id}", response_model=ResponseApprovalResponse)
async def process_approval(
    approval_id: int,
    req: ResponseApprovalRequest,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """Secure endpoint: approve, reject, or edit a response draft."""
    record = await human_it_loop_service.process_agent_approval(
        db=db, approval_id=approval_id, agent_id=current_user.id, req=req
    )
    execution = await runtime.resume_approval_execution(db, record)

    return ResponseApprovalResponse(
        id=record.id,
        ticket_id=record.ticket_id,
        status=record.status,
        final_response=record.modified_response or record.drafted_response,
        latency_seconds=record.latency_seconds or 0.0,
        approved_at=datetime.datetime.utcnow(),
        workflow_execution_status=execution.status if execution else None,
    )


@router.get("/agent-executions/{execution_id}", response_model=AgentExecutionResponse)
async def get_agent_execution(
    execution_id: str,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """供客服查看 Workflow 暂停、恢复和 Trace 关联状态。"""
    execution = await durable_execution_service.get(db, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Agent execution not found.")
    return execution


@router.post(
    "/agent-executions/{execution_id}/resume",
    response_model=AgentExecutionResponse,
)
async def retry_agent_execution_resume(
    execution_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """供主管重试因进程退出或短暂故障未完成的续跑。"""
    execution = await durable_execution_service.get(db, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Agent execution not found.")
    if execution.approval_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Execution has no persisted approval decision to resume.",
        )
    approval_result = await db.execute(
        select(ResponseApproval).where(ResponseApproval.id == execution.approval_id)
    )
    approval = approval_result.scalars().first()
    if approval is None or approval.status == "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Human approval must be completed before workflow resume.",
        )
    resumed = await runtime.resume_approval_execution(db, approval)
    if resumed is None:
        raise HTTPException(status_code=404, detail="Agent execution not found.")
    return resumed
