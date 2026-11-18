"""员工侧 Agent 接口：对话、客户上下文、回复评估、反馈和运行记录。"""


from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api import runtime
from src.auth.rbac import require_agent, require_manager
from src.config import settings
from src.database import get_db
from src.evaluation.framework import run_deeval_evaluation
from src.feedback.service import feedback_service
from src.memory import MemoryOwnershipError, memory_service
from src.models.db_models import Ticket, User
from src.models.schemas import (
    AgentRunPageResponse,
    AgentRunResponse,
    ChatRequest,
    ChatResponse,
    Citation,
    CostMetadata,
    CustomerContextRequest,
    CustomerContextResponse,
    EvaluateResponseRequest,
    EvaluateResponseResponse,
    FeedbackEventResponse,
    OrderInfo,
    UserFeedbackRequest,
)
from src.security import public_access_service, public_rate_limiter
from src.tickets.test_marks import test_ticket_ids
from src.tools.crm import crm_tool
from src.tools.order_mgmt import order_mgmt_tool
from src.tools.ticketing import ticketing_tool

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat_session(
    req: ChatRequest,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a conversational message. Runs the LangGraph multi-agent flow.
    Appends conversation exchanges to historical memory.
    """
    # 1. 创建工单前先校验会话归属，避免越权请求留下孤立工单。
    try:
        memory_context = await memory_service.load_context(
            db,
            session_id=req.session_id,
            customer_id=req.customer_id,
        )
    except MemoryOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    # 2. Retrieve or create ticket log record
    ticket = Ticket(
        customer_id=req.customer_id,
        subject="Active Chat Conversation",
        description=req.message,
        status="open",
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)

    # 3. 持久化 User 消息，Workflow 使用写入前的历史上下文。
    memory_context = await memory_service.begin_turn(
        db,
        session_id=req.session_id,
        customer_id=req.customer_id,
        content=req.message,
        ticket_id=ticket.id,
        prior_context=memory_context,
    )

    # 4. 执行 Workflow，并持久化工单分析、审批与 Agent Run。
    agent_output, approval_id, agent_run = await runtime.process_ticket_with_agent(
        db=db,
        ticket=ticket,
        kb_version=req.kb_version,
        endpoint="/chat",
        session_id=req.session_id,
        memory_context=memory_context,
    )

    # Build schema output
    citations = [
        Citation(source=c.source, text=c.text, score=c.score, version=c.version)
        for c in agent_output.get("context_citations", [])
    ]

    cost_meta = CostMetadata(
        tokens_input=agent_output.get("tokens_input", 0),
        tokens_output=agent_output.get("tokens_output", 0),
        cost_usd=agent_output.get("cost_usd", 0.0),
        latency_seconds=agent_output.get("latency_seconds", 0.0),
    )

    return ChatResponse(
        session_id=req.session_id,
        response=agent_output.get("suggested_response", ""),
        sentiment=agent_output.get("sentiment", "neutral"),
        priority=agent_output.get("priority", "medium"),
        tool_context=agent_output.get("tool_context", {}),
        tool_calls=agent_output.get("tool_calls", []),
        citations=citations,
        escalation_recommended=agent_output.get("escalation_recommended", False),
        escalation_reason=agent_output.get("escalation_reason"),
        analyzer_confidence=agent_output.get("analyzer_confidence", 1.0),
        risk_level=agent_output.get("risk_level", "low"),
        risk_score=agent_output.get("risk_score", 0.0),
        risk_reasons=agent_output.get("risk_reasons", []),
        cost_metadata=cost_meta,
        approval_required=agent_output.get("approval_required", False),
        approval_id=approval_id,
        agent_run_id=agent_run.id if agent_run else None,
        feedback_token=(
            getattr(agent_run, "_feedback_token", None) if agent_run else None
        ),
    )


@router.post("/customer-context", response_model=CustomerContextResponse)
async def get_customer_context(
    req: CustomerContextRequest, current_user: User = Depends(require_agent)
):
    """Retrieve full customer profile summary across CRM and Ticketing tools."""
    profile = crm_tool.get_customer_profile(req.customer_id)
    history = ticketing_tool.get_past_tickets(req.customer_id)
    orders = order_mgmt_tool.get_order_history(req.customer_id)

    order_schemas = [
        OrderInfo(
            order_id=o["order_id"],
            status=o["status"],
            items=o["items"],
            total_amount=o["total_amount"],
            currency=o.get("currency"),
            order_date=o["order_date"],
        )
        for o in orders
    ]

    return CustomerContextResponse(
        customer_id=req.customer_id,
        name=profile["name"],
        tier=profile["tier"],
        open_tickets_count=profile["open_tickets_count"],
        recent_orders=order_schemas,
        # 取最近一张历史工单的创建时间；没有历史工单时不编造。
        last_interaction=max(
            (t["created_at"] for t in history if t.get("created_at")), default=None
        ),
    )


@router.post("/evaluate-response", response_model=EvaluateResponseResponse)
async def evaluate_response(
    req: EvaluateResponseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_agent),
):
    """Run evaluation scores comparing drafted answers against context."""
    if req.agent_run_id and current_user.role not in {"admin", "manager"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager role is required to link evaluation feedback.",
        )
    results = await run_deeval_evaluation(
        query=req.query, context=req.context, response=req.response
    )
    if req.agent_run_id:
        await db.commit()
        await runtime.record_evaluation_fail_open(
            db,
            agent_run_id=req.agent_run_id,
            metrics={
                "faithfulness": results.faithfulness_score,
                "context_precision": results.context_precision,
                "context_recall": results.context_recall,
                "hallucination_rate": results.hallucination_rate,
                "answer_relevance": results.answer_relevance,
                "overall_quality_score": results.overall_quality_score,
            },
            passed=results.passed_evaluation,
            external_ref=req.external_ref,
        )
    return results


# --- FEEDBACK PIPELINE APIS ---
@router.post(
    "/feedback/user",
    response_model=FeedbackEventResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user_feedback(
    req: UserFeedbackRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """采集用户评分，并关联对应 Agent Run 与 OpenTelemetry Trace。"""
    visitor_id = public_access_service.visitor_id(request, response)
    await public_rate_limiter.enforce(
        scope="public-feedback",
        identities=public_access_service.rate_limit_identities(request, visitor_id),
        limit=settings.PUBLIC_FEEDBACK_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
    event = await feedback_service.record_user_feedback(
        db,
        agent_run_id=req.agent_run_id,
        feedback_token=req.feedback_token,
        rating=req.rating,
        comment=req.comment,
        idempotency_key=req.idempotency_key,
    )
    await db.commit()
    await db.refresh(event)
    return event


@router.get("/feedback/runs/{agent_run_id}", response_model=AgentRunResponse)
async def get_feedback_run(
    agent_run_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """供主管按 Agent Run 查看 Trace、版本快照和全部反馈。"""
    return await feedback_service.get_agent_run(db, agent_run_id)


@router.get("/observability/runs", response_model=AgentRunPageResponse)
async def list_observability_runs(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ticket_id: int | None = Query(default=None, ge=1),
    include_test: bool = Query(default=False),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """供主管按工单筛选并分页查看可关联 Trace 的 Agent Run；默认不含测试工单。"""
    runs, total = await feedback_service.list_agent_runs(
        db,
        limit=limit,
        offset=offset,
        ticket_id=ticket_id,
        # 按工单号精确查询时不过滤，方便排查某张测试工单。
        exclude_ticket_ids=(
            None if include_test or ticket_id is not None else test_ticket_ids()
        ),
    )
    return AgentRunPageResponse(
        items=runs,
        total=total,
        limit=limit,
        offset=offset,
    )
