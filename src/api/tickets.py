"""工单创建、Agent 结果、关闭、列表和人工处理队列。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.api import runtime
from src.api.tool_actions import _ticket_review_reasons
from src.auth.rbac import require_agent
from src.database import get_db
from src.models.db_models import (
    AgentExecution,
    AgentReviewContext,
    AgentRun,
    ResponseApproval,
    Ticket,
    ToolActionReview,
    User,
)
from src.models.schemas import (
    Citation,
    CostMetadata,
    TicketAgentResultResponse,
    TicketCreate,
    TicketResponse,
)
from src.tickets.state_machine import TicketAction, ticket_state_machine
from src.tickets.test_marks import test_ticket_ids

router = APIRouter()


@router.post(
    "/tickets", response_model=TicketResponse, status_code=status.HTTP_201_CREATED
)
async def create_ticket(
    req: TicketCreate,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """创建工单后立即执行 Agent，并在响应前保存处理结果。"""
    ticket = Ticket(
        customer_id=req.customer_id,
        subject=req.subject,
        description=req.description,
        status="open",
        priority="medium",
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    await runtime.process_ticket_with_agent(
        db=db,
        ticket=ticket,
        kb_version=req.kb_version,
        endpoint="/tickets",
        session_id=f"ticket_{ticket.id}",
        require_persisted_result=True,
    )
    await db.refresh(ticket)
    return ticket


@router.get(
    "/tickets/{ticket_id}/agent-result",
    response_model=TicketAgentResultResponse,
)
async def get_ticket_agent_result(
    ticket_id: int,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """读取工单最新的持久化 Agent 结果，不重新执行 Workflow。"""
    run_result = await db.execute(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket_id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
    )
    agent_run = run_result.scalars().first()
    if not agent_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No persisted Agent result exists for this ticket.",
        )

    review_result = await db.execute(
        select(AgentReviewContext).where(
            AgentReviewContext.agent_run_id == agent_run.id
        )
    )
    review_context = review_result.scalars().first()
    ticket_result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = ticket_result.scalars().first()

    approval_result = await db.execute(
        select(ResponseApproval)
        .where(ResponseApproval.ticket_id == ticket_id)
        .order_by(ResponseApproval.created_at.desc(), ResponseApproval.id.desc())
        .limit(1)
    )
    approval = approval_result.scalars().first()
    execution_result = await db.execute(
        select(AgentExecution)
        .where(AgentExecution.ticket_id == ticket_id)
        .order_by(AgentExecution.created_at.desc(), AgentExecution.id.desc())
        .limit(1)
    )
    execution = execution_result.scalars().first()
    response_text = (
        approval.modified_response
        if approval and approval.modified_response
        else agent_run.output_text
    )
    approval_required = bool(approval and approval.status == "pending")
    citations = [Citation(**citation) for citation in (agent_run.citations or [])]
    review_reasons = _ticket_review_reasons(
        agent_run=agent_run,
        ticket=ticket,
        review_context=review_context,
    )
    escalation_reason = (
        review_context.escalation_reason
        if review_context and review_context.escalation_reason
        else (review_reasons[0] if review_reasons else None)
    )

    return TicketAgentResultResponse(
        ticket_id=ticket_id,
        agent_run_id=agent_run.id,
        kb_version=agent_run.kb_version,
        response=response_text,
        citations=citations,
        tool_calls=agent_run.tool_calls or [],
        qa_score=agent_run.qa_score,
        hallucination_detected=agent_run.hallucination_detected,
        escalation_recommended=agent_run.escalation_recommended,
        escalation_reason=escalation_reason,
        review_reasons=review_reasons,
        risk_level=review_context.risk_level if review_context else None,
        risk_score=review_context.risk_score if review_context else None,
        analyzer_confidence=(
            review_context.analyzer_confidence if review_context else None
        ),
        approval_required=approval_required,
        approval_id=approval.id if approval_required else None,
        approval_status=approval.status if approval else None,
        workflow_execution_id=execution.id if execution else None,
        workflow_execution_status=execution.status if execution else None,
        cost_metadata=CostMetadata(
            tokens_input=agent_run.tokens_input,
            tokens_output=agent_run.tokens_output,
            latency_seconds=agent_run.latency_seconds,
        ),
        created_at=agent_run.created_at,
    )


@router.post("/tickets/{ticket_id}/close", response_model=TicketResponse)
async def close_ticket(
    ticket_id: int,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    ticket_res = await db.execute(select(Ticket).filter(Ticket.id == ticket_id))
    ticket = ticket_res.scalars().first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket_state_machine.transition(ticket, TicketAction.CLOSE)
    await db.commit()
    await db.refresh(ticket)
    return ticket


async def _with_test_flag(
    db: AsyncSession, tickets: list[Ticket]
) -> list[TicketResponse]:
    marked = set((await db.execute(test_ticket_ids())).scalars().all())
    return [
        TicketResponse.model_validate(ticket).model_copy(
            update={"is_test": ticket.id in marked}
        )
        for ticket in tickets
    ]


@router.get("/tickets", response_model=list[TicketResponse])
async def list_tickets(
    include_test: bool = Query(default=False),
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """默认不返回测试工单；include_test=true 时一并返回并带 is_test 标记。"""
    query = select(Ticket)
    if not include_test:
        query = query.where(Ticket.id.notin_(test_ticket_ids()))
    result = await db.execute(query)
    return await _with_test_flag(db, list(result.scalars().all()))


@router.get("/staff/review-queue", response_model=list[TicketResponse])
async def list_staff_review_queue(
    include_test: bool = Query(default=False),
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """返回待回复审批或存在待核实业务操作的工单；默认不含测试工单。"""
    review_tickets = select(ToolActionReview.ticket_id).where(
        ToolActionReview.status == "pending"
    )
    query = select(Ticket).where(
        (Ticket.status == "pending_approval") | Ticket.id.in_(review_tickets)
    )
    if not include_test:
        query = query.where(Ticket.id.notin_(test_ticket_ids()))
    result = await db.execute(
        query.order_by(Ticket.updated_at.desc(), Ticket.id.desc())
    )
    tickets = list(result.scalars().all())
    pending_ids = set((await db.execute(review_tickets)).scalars().all())
    return [
        item.model_copy(update={"requires_tool_review": item.id in pending_ids})
        for item in await _with_test_flag(db, tickets)
    ]
