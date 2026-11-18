"""公开咨询入口：提交咨询、历史、运行摘要和撤回。"""

import uuid

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.agents.public_reply import (
    answered_note,
    preliminary_answer_allowed,
    preliminary_answer_note,
    public_review_feedback,
)
from src.api import runtime
from src.config import settings
from src.database import get_db
from src.feedback.public_insight import build_public_run_insight
from src.feedback.service import feedback_service
from src.memory import MemoryOwnershipError, memory_service
from src.models.db_models import Ticket
from src.models.schemas import (
    PublicConversationHistoryResponse,
    PublicRunInsightResponse,
    PublicSupportRequest,
    PublicSupportResponse,
    PublicWithdrawResponse,
)
from src.security import public_access_service, public_rate_limiter
from src.tickets.test_marks import (
    TEST_HEADER,
    mark_test_ticket,
    normalize_label,
    withdraw_ticket,
)

router = APIRouter()


# --- GENERAL TICKETING APIS ---
# 文案和先答策略在 src/agents/public_reply.py；保留旧名字供测试和内部调用。
_public_review_feedback = public_review_feedback


@router.get(
    "/support/runs/{agent_run_id}/insight", response_model=PublicRunInsightResponse
)
async def get_public_run_insight(
    agent_run_id: str,
    request: Request,
    response: Response,
    feedback_token: str = Header(default="", alias="X-Feedback-Token"),
    db: AsyncSession = Depends(get_db),
):
    """仅在隔离演示中凭反馈令牌读取保存的摘要。"""
    if not settings.PUBLIC_DEMO_ISOLATION_ENABLED:
        raise HTTPException(status_code=404, detail="Not found.")
    visitor_id = public_access_service.visitor_id(request, response)
    await public_rate_limiter.enforce(
        scope="public-insight",
        identities=public_access_service.rate_limit_identities(request, visitor_id),
        limit=settings.PUBLIC_READ_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
    run = await feedback_service.get_token_authorized_run(
        db, agent_run_id, feedback_token
    )
    return build_public_run_insight(run)


@router.post(
    "/support/runs/{agent_run_id}/withdraw", response_model=PublicWithdrawResponse
)
async def withdraw_public_request(
    agent_run_id: str,
    request: Request,
    response: Response,
    feedback_token: str = Header(default="", alias="X-Feedback-Token"),
    db: AsyncSession = Depends(get_db),
):
    """提交人凭本次反馈令牌撤回自己的咨询：关闭工单，待审批记录移出人工队列。

    不删除任何记录；已决策的审批和已完成的执行不受影响。线上测试脚本用它清理测试数据。
    """
    if not settings.PUBLIC_DEMO_ISOLATION_ENABLED:
        raise HTTPException(status_code=404, detail="Not found.")
    visitor_id = public_access_service.visitor_id(request, response)
    await public_rate_limiter.enforce(
        scope="public-insight",
        identities=public_access_service.rate_limit_identities(request, visitor_id),
        limit=settings.PUBLIC_READ_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
    run = await feedback_service.get_token_authorized_run(
        db, agent_run_id, feedback_token
    )
    ticket = None
    if run.ticket_id is not None:
        ticket = (
            (await db.execute(select(Ticket).filter(Ticket.id == run.ticket_id)))
            .scalars()
            .first()
        )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    withdrawn = await withdraw_ticket(db, ticket)
    await db.commit()
    return PublicWithdrawResponse(
        ticket_id=ticket.id, status=ticket.status, withdrawn_approvals=withdrawn
    )


@router.get("/support/history", response_model=PublicConversationHistoryResponse)
async def get_public_support_history(
    request: Request,
    response: Response,
    customer_id: str = Query(min_length=1, max_length=100),
    session_id: list[str] = Query(),
    db: AsyncSession = Depends(get_db),
):
    """返回演示客户最近七天可公开展示的对话历史。"""
    public_access_service.validate_profile(customer_id)
    visitor_id = public_access_service.visitor_id(request, response)
    await public_rate_limiter.enforce(
        scope="public-history",
        identities=public_access_service.rate_limit_identities(request, visitor_id),
        limit=settings.PUBLIC_READ_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
    session_ids = list(
        dict.fromkeys(item.strip() for item in session_id if item.strip())
    )
    if (
        not session_ids
        or len(session_ids) > 50
        or any(len(item) > 100 for item in session_ids)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide between 1 and 50 valid session_id values.",
        )
    internal_session_ids = [
        public_access_service.scoped_session_id(visitor_id, item)
        for item in session_ids
    ]
    window_start, messages = await memory_service.load_public_history(
        db,
        customer_id=customer_id,
        session_ids=internal_session_ids,
        window_days=7,
    )
    return PublicConversationHistoryResponse(
        customer_id=customer_id,
        window_days=7,
        window_start=window_start,
        messages=messages,
    )


@router.post(
    "/support/requests",
    response_model=PublicSupportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_public_support_request(
    req: PublicSupportRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """接收用户咨询，只返回可公开的回复或转人工状态。"""
    public_access_service.validate_profile(req.customer_id)
    visitor_id = public_access_service.visitor_id(request, response)
    if public_access_service.is_online_check(request):
        # 线上检查使用单独额度，不占公开访客额度。
        identities = ("online-check",)
        minute_scope, minute_limit = (
            "online-check-minute",
            settings.ONLINE_CHECK_RATE_LIMIT_PER_MINUTE,
        )
        day_scope, day_limit = (
            "online-check-day",
            settings.ONLINE_CHECK_RATE_LIMIT_PER_DAY,
        )
    else:
        identities = public_access_service.rate_limit_identities(request, visitor_id)
        minute_scope, minute_limit = (
            "public-chat-minute",
            settings.PUBLIC_CHAT_RATE_LIMIT_PER_MINUTE,
        )
        day_scope, day_limit = (
            "public-chat-day",
            settings.PUBLIC_CHAT_RATE_LIMIT_PER_DAY,
        )
    await public_rate_limiter.enforce(
        scope=minute_scope,
        identities=identities,
        limit=minute_limit,
        window_seconds=60,
    )
    await public_rate_limiter.enforce(
        scope=day_scope,
        identities=identities,
        limit=day_limit,
        window_seconds=86400,
    )
    message = req.message.strip()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Support message cannot be empty.",
        )
    session_id = req.session_id or str(uuid.uuid4())
    internal_session_id = public_access_service.scoped_session_id(
        visitor_id, session_id
    )
    try:
        memory_context = await memory_service.load_context(
            db,
            session_id=internal_session_id,
            customer_id=req.customer_id,
        )
    except MemoryOwnershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    ticket = Ticket(
        customer_id=req.customer_id,
        subject=message.splitlines()[0][:80],
        description=message,
        status="open",
        priority="medium",
    )
    db.add(ticket)
    await db.flush()
    # 测试、冒烟和线上检查的请求带测试请求头：工单打标记，员工后台默认不显示。
    test_label = normalize_label(request.headers.get(TEST_HEADER))
    if test_label:
        await mark_test_ticket(db, ticket.id, label=test_label, source="header")
    await db.commit()
    await db.refresh(ticket)
    memory_context = await memory_service.begin_turn(
        db,
        session_id=internal_session_id,
        customer_id=req.customer_id,
        content=message,
        ticket_id=ticket.id,
        prior_context=memory_context,
    )
    agent_output, approval_id, agent_run = await runtime.process_ticket_with_agent(
        db=db,
        ticket=ticket,
        kb_version=req.kb_version,
        endpoint="/support/requests",
        session_id=internal_session_id,
        memory_context=memory_context,
        require_persisted_result=True,
        release_preliminary=lambda output: preliminary_answer_allowed(output, message),
    )
    await db.refresh(ticket)
    preliminary_released = bool(
        approval_id and preliminary_answer_allowed(agent_output, message)
    )

    if approval_id and preliminary_released:
        # 人工复核照常进行；只读咨询先把通过 QA 的草稿给用户看。
        return PublicSupportResponse(
            ticket_id=ticket.id,
            session_id=session_id,
            status="answered",
            response=agent_output.get("suggested_response", ""),
            message=preliminary_answer_note(message),
            handling_reason="preliminary_answer",
            created_at=ticket.created_at,
            agent_run_id=agent_run.id if agent_run else None,
            feedback_token=(
                getattr(agent_run, "_feedback_token", None) if agent_run else None
            ),
        )
    if approval_id:
        handling_reason, public_response, public_message = _public_review_feedback(
            agent_output, message
        )
        return PublicSupportResponse(
            ticket_id=ticket.id,
            session_id=session_id,
            status="pending_human",
            response=public_response,
            message=public_message,
            handling_reason=handling_reason,
            created_at=ticket.created_at,
            agent_run_id=agent_run.id if agent_run else None,
            feedback_token=(
                getattr(agent_run, "_feedback_token", None) if agent_run else None
            ),
        )
    return PublicSupportResponse(
        ticket_id=ticket.id,
        session_id=session_id,
        status="answered",
        response=agent_output.get("suggested_response", ""),
        message=answered_note(message),
        handling_reason=None,
        created_at=ticket.created_at,
        agent_run_id=agent_run.id if agent_run else None,
        feedback_token=(
            getattr(agent_run, "_feedback_token", None) if agent_run else None
        ),
    )
