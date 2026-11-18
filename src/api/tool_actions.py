"""受治理写 Tool：提议、审批、执行、补偿、对账、审计和 Outbox。"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.rbac import require_agent, require_manager
from src.config import settings
from src.database import get_db
from src.models.db_models import AgentReviewContext, AgentRun, Ticket, User
from src.models.schemas import (
    ToolActionCompensationRequest,
    ToolActionCreateRequest,
    ToolActionDecisionRequest,
    ToolActionExecuteRequest,
    ToolActionPageResponse,
    ToolActionResolutionRequest,
    ToolActionResponse,
    ToolActionReviewResponse,
    ToolInvocationAuditPageResponse,
    ToolOutboxEventResponse,
    ToolOutboxPageResponse,
    ToolPolicyReplayResponse,
)
from src.tools.audit import tool_audit_repository
from src.tools.governance import tool_governance_service
from src.tools.outbox import tool_outbox_service

router = APIRouter()


# --- HIGH-RISK TOOL GOVERNANCE APIS ---
@router.post(
    "/tool-actions",
    response_model=ToolActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tool_action(
    req: ToolActionCreateRequest,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """只创建待审批 Action，此接口绝不执行高风险 Tool。"""
    action = await tool_governance_service.propose(
        db,
        ticket_id=req.ticket_id,
        tool_name=req.tool_name,
        payload=req.payload,
        intent=req.intent,
        proposer=current_user,
    )
    await db.commit()
    return await tool_governance_service.get(db, action.id)


@router.get("/tool-actions", response_model=ToolActionPageResponse)
async def list_tool_actions(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    action_status: str | None = Query(default=None, alias="status"),
    tool_name: str | None = Query(default=None),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """供主管查询高风险动作队列与完整状态。"""
    actions, total = await tool_governance_service.list_actions(
        db,
        limit=limit,
        offset=offset,
        action_status=action_status,
        tool_name=tool_name,
    )
    return ToolActionPageResponse(
        items=actions, total=total, limit=limit, offset=offset
    )


@router.get("/tool-actions/{action_id}", response_model=ToolActionResponse)
async def get_tool_action(
    action_id: str,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """读取 Action 脱敏视图与 Append-only 事件。"""
    return await tool_governance_service.get(db, action_id)


@router.post("/tool-actions/{action_id}/decision", response_model=ToolActionResponse)
async def decide_tool_action(
    action_id: str,
    req: ToolActionDecisionRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """审批人必须为 manager/admin，且不能批准自己的提议。"""
    action = await tool_governance_service.decide(
        db,
        action_id=action_id,
        decision=req.decision,
        expected_version=req.expected_version,
        reviewer=current_user,
        comment=req.comment,
    )
    await db.commit()
    return await tool_governance_service.get(db, action.id)


@router.post("/tool-actions/{action_id}/execute", response_model=ToolActionResponse)
async def execute_tool_action(
    action_id: str,
    req: ToolActionExecuteRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """仅将已批准 Action 与执行事件原子入队，由 Worker 异步投递。"""
    action = await tool_governance_service.execute(
        db,
        action_id=action_id,
        expected_version=req.expected_version,
        executor=current_user,
    )
    await db.commit()
    return await tool_governance_service.get(db, action.id)


@router.post("/tool-actions/{action_id}/compensate", response_model=ToolActionResponse)
async def compensate_tool_action(
    action_id: str,
    req: ToolActionCompensationRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """为已成功写操作创建幂等补偿 Outbox 事件。"""
    action = await tool_governance_service.request_compensation(
        db,
        action_id=action_id,
        expected_version=req.expected_version,
        requester=current_user,
        reason=req.reason,
    )
    await db.commit()
    return await tool_governance_service.get(db, action.id)


@router.post("/tool-actions/{action_id}/resolve", response_model=ToolActionResponse)
async def resolve_tool_action(
    action_id: str,
    req: ToolActionResolutionRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """保存主管核实的外部结果，不调用业务写工具。"""
    action = await tool_governance_service.resolve_manually(
        db, action_id=action_id, reviewer=current_user, **req.model_dump()
    )
    await db.commit()
    return await tool_governance_service.get(db, action.id)


@router.get(
    "/tickets/{ticket_id}/tool-reviews", response_model=list[ToolActionReviewResponse]
)
async def ticket_tool_reviews(
    ticket_id: int,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """在工单详情显示业务核实任务，与回复审批分开处理。"""
    return await tool_governance_service.list_ticket_reviews(db, ticket_id)


def _ticket_review_reasons(
    *,
    agent_run: AgentRun,
    ticket: Ticket | None,
    review_context: AgentReviewContext | None,
) -> list[str]:
    """聚合快照中的具体审批信号，并兼容上线前的历史工单。"""
    reasons: list[str] = []

    def add(value: str | None) -> None:
        normalized = str(value or "").strip()
        if normalized and normalized not in reasons:
            reasons.append(normalized)

    if review_context:
        add(review_context.escalation_reason)
        for reason in review_context.risk_reasons or []:
            add(str(reason))
        if review_context.response_requires_human:
            add("authoritative_answer_unavailable")
        for reason in review_context.degradation_reasons or []:
            add(str(reason))

    # 旧 AgentRun 没有审批快照时，仍用已持久化字段给出可说明的原因。
    # 新工单的转人工由处理原则决定，优先级不再是原因。
    if not review_context and ticket and ticket.priority == "urgent":
        add("urgent_priority")
    if (
        not review_context
        and ticket
        and ticket.priority == "high"
        and ticket.sentiment == "negative"
    ):
        add("negative_high_priority")
    if agent_run.hallucination_detected:
        add("hallucination_detected")
    if (
        agent_run.qa_score is not None
        and agent_run.qa_score < settings.RISK_QA_SCORE_THRESHOLD
    ):
        add("qa_score_below_threshold")
    if agent_run.workflow_errors:
        add("workflow_error")
    if agent_run.escalation_recommended and not reasons:
        add("manual_review_policy")
    return reasons


@router.get(
    "/tool-actions/{action_id}/policy-replay",
    response_model=ToolPolicyReplayResponse,
)
async def replay_tool_policy(
    action_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """按 Action 创建时的 Policy 快照执行纯离线审计回放。"""
    return await tool_governance_service.policy_replay(db, action_id)


@router.get("/tool-audits", response_model=ToolInvocationAuditPageResponse)
async def list_tool_audits(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tool_name: str | None = Query(default=None),
    audit_status: str | None = Query(default=None, alias="status"),
    action_id: str | None = Query(default=None),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """供主管按 Tool、状态或 Action 查询持久化审计。"""
    records, total = await tool_audit_repository.list_records(
        db,
        limit=limit,
        offset=offset,
        tool_name=tool_name,
        status=audit_status,
        action_id=action_id,
    )
    return ToolInvocationAuditPageResponse(
        items=records, total=total, limit=limit, offset=offset
    )


@router.get("/tool-outbox", response_model=ToolOutboxPageResponse)
async def list_tool_outbox(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    event_status: str | None = Query(default=None, alias="status"),
    action_id: str | None = Query(default=None),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """供主管查看 Outbox、Retry Queue 和 DLQ，不返回业务参数。"""
    events, total = await tool_outbox_service.list_events(
        db,
        limit=limit,
        offset=offset,
        event_status=event_status,
        action_id=action_id,
    )
    return ToolOutboxPageResponse(items=events, total=total, limit=limit, offset=offset)


@router.post("/tool-outbox/{event_id}/retry", response_model=ToolOutboxEventResponse)
async def retry_tool_outbox_event(
    event_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """允许主管显式重放 DLQ，写操作仍先对账而非盲目重试。"""
    event = await tool_outbox_service.retry_dead_letter(
        db, event_id=event_id, actor=current_user
    )
    await db.commit()
    return event
