import datetime
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.future import select

from src.config import settings
from src.observability.logging_config import configure_logging

configure_logging(settings.LOG_LEVEL)

from src.agents.checkpointing import checkpoint_manager
from src.agents.durable_execution import (
    AgentExecutionStatus,
    durable_execution_service,
)
from src.agents.graph import (
    initialize_agent_checkpointing,
    resume_agent_workflow,
    run_agent_workflow,
    shutdown_agent_checkpointing,
)
from src.approval.workflows import human_it_loop_service
from src.auth.jwt import create_access_token, get_password_hash, verify_password
from src.auth.rbac import (
    get_current_user,
    require_admin,
    require_agent,
    require_manager,
)
from src.database import AsyncSessionLocal, engine, get_db, init_db
from src.decision import close_decision_provider
from src.evaluation.framework import run_deeval_evaluation
from src.feedback.service import feedback_service
from src.memory import MemoryContext, MemoryOwnershipError, memory_service
from src.models.db_models import (
    AgentExecution,
    AgentReviewContext,
    AgentRun,
    ToolRuntimeSetting,
    ToolActionReview,
    ResponseApproval,
    Ticket,
    User,
)
from src.models.schemas import (
    AdminKnowledgeDocumentRequest,
    AdminKnowledgeDocumentResponse,
    AdminPromptBundleRequest,
    AdminPromptBundleResponse,
    AdminPromptRegistryResponse,
    AdminRagReindexResponse,
    AdminToolResponse,
    AdminToolUpdateRequest,
    AgentExecutionResponse,
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
    LoginRequest,
    OrderInfo,
    PublicConversationHistoryResponse,
    PublicSupportRequest,
    PublicSupportResponse,
    ResponseApprovalRequest,
    ResponseApprovalResponse,
    SuggestResponseRequest,
    SuggestResponseResponse,
    TicketCreate,
    TicketAgentResultResponse,
    TicketEscalationResponse,
    TicketResponse,
    TicketSentimentResponse,
    TicketSummaryResponse,
    Token,
    ToolActionCreateRequest,
    ToolActionCompensationRequest,
    ToolActionDecisionRequest,
    ToolActionExecuteRequest,
    ToolActionPageResponse,
    ToolActionResponse,
    ToolActionResolutionRequest,
    ToolActionReviewResponse,
    ToolInvocationAuditPageResponse,
    ToolOutboxEventResponse,
    ToolOutboxPageResponse,
    ToolPolicyReplayResponse,
    UserCreate,
    UserFeedbackRequest,
    UserResponse,
)
from src.observability.instrumentation import instrument_dependencies
from src.observability.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL
from src.observability.tracing import (
    bind_agent_trace_id,
    bind_request_id,
    get_agent_trace_id,
    get_current_trace_id,
    get_request_id,
    get_tracer,
    init_tracing,
    observed_span,
    reset_agent_trace_id,
    reset_request_id,
    set_span_attributes,
)
from src.security import public_access_service, public_rate_limiter
from src.promptops.registry import PromptRegistry
from src.rag.kb_versioning import kb_versioning_service
from src.tickets.state_machine import TicketAction, ticket_state_machine
from src.tools.crm import crm_tool
from src.tools.audit import tool_audit_repository
from src.tools.audit_context import begin_tool_audit_scope, finish_tool_audit_scope
from src.tools.governance import tool_governance_service
from src.tools.outbox import tool_outbox_service, tool_outbox_worker
from src.tools.order_mgmt import order_mgmt_tool
from src.tools.ticketing import ticketing_tool
from src.tools.registry import tool_registry

tracer = get_tracer(__name__)
logger = logging.getLogger("supportgpt.main")


async def _load_tool_runtime_settings() -> None:
    """启动时将持久化 Tool 开关恢复到当前进程。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ToolRuntimeSetting))
        tool_registry.replace_runtime_settings(
            {item.tool_name: item.enabled for item in result.scalars().all()}
        )


async def _run_workflow_with_tool_audit(db: AsyncSession, initial_state: dict) -> dict:
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


async def _record_agent_run_fail_open(
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


async def _link_approval_fail_open(
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


async def _record_ticket_result_required(
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


async def _process_ticket_with_agent(
    *,
    db: AsyncSession,
    ticket: Ticket,
    kb_version: str,
    endpoint: str,
    session_id: str | None = None,
    memory_context: MemoryContext | None = None,
    require_persisted_result: bool = False,
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
        agent_output = await _run_workflow_with_tool_audit(
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
        )
    await db.commit()

    if require_persisted_result:
        agent_run = await _record_ticket_result_required(
            db=db,
            agent_output=agent_output,
            input_text=ticket.description,
            endpoint=endpoint,
            session_id=session_id,
            approval_id=approval_id,
        )
    else:
        agent_run = await _record_agent_run_fail_open(
            primary_db=db,
            agent_output=agent_output,
            input_text=ticket.description,
            endpoint=endpoint,
            session_id=session_id,
        )
        if agent_run and approval_id:
            await _link_approval_fail_open(db, agent_run.id, approval_id)
    if agent_run:
        await durable_execution_service.attach_agent_run(db, execution, agent_run.id)
        await db.commit()
    return agent_output, approval_id, agent_run


async def _resume_approval_execution(
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


async def _recover_resumable_workflows() -> None:
    """应用重启后自动续跑已决策但未完成的审批 Thread。"""
    async with AsyncSessionLocal() as db:
        recoverable = await durable_execution_service.list_recoverable(db)
        for _, approval in recoverable:
            await _resume_approval_execution(db, approval)


async def _record_evaluation_fail_open(
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


def _record_http_metrics(
    method: str, endpoint: str, status_code: str, duration: float
) -> None:
    try:
        HTTP_REQUESTS_TOTAL.add(
            1, {"method": method, "endpoint": endpoint, "status": status_code}
        )
        HTTP_REQUEST_DURATION_SECONDS.record(
            duration, {"method": method, "endpoint": endpoint}
        )
    except Exception:
        return


def _route_template(request: Request) -> str:
    """Return the matched route template without exporting raw path identifiers."""
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


# --- FastAPI Lifespan Handler ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 确保 Uvicorn 初始化后业务日志仍使用应用配置。
    configure_logging(settings.LOG_LEVEL)
    # 初始化统一 OpenTelemetry Trace 与 Metrics。
    init_tracing()
    # Create DB schemas (SQLite or PostgreSQL)
    await init_db()
    async with AsyncSessionLocal() as db:
        await tool_governance_service.backfill_reviews(db)
        await db.commit()
    await _load_tool_runtime_settings()
    await initialize_agent_checkpointing()
    await _recover_resumable_workflows()
    await tool_outbox_worker.start()
    try:
        yield
    finally:
        await tool_outbox_worker.stop()
        await public_rate_limiter.close()
        await close_decision_provider()
        await shutdown_agent_checkpointing()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Enterprise Customer Support AI Copilot Platform",
    lifespan=lifespan,
    docs_url=None if settings.APP_ENV.lower() in {"production", "prod"} else "/docs",
    redoc_url=None if settings.APP_ENV.lower() in {"production", "prod"} else "/redoc",
    openapi_url=(
        None if settings.APP_ENV.lower() in {"production", "prod"} else "/openapi.json"
    ),
)

# Configure CORS for Frontend connectivity
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """为公网响应增加基础浏览器安全策略。"""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={"detail": "Request body is too large."},
                )
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid Content-Length header."},
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith(("/auth", "/support", "/feedback")):
        response.headers["Cache-Control"] = "no-store"
    if settings.APP_ENV.lower() in {"production", "prod"}:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


# --- Observability Request Latency Middleware ---
@app.middleware("http")
async def track_http_telemetry(request: Request, call_next):
    start_time = time.time()
    endpoint = request.url.path
    method = request.method

    # Avoid polluting application metrics with health probes.
    if endpoint == "/health":
        return await call_next(request)

    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request_token = bind_request_id(request_id)
    agent_trace_token = bind_agent_trace_id()
    logger.info(
        "http request started",
        extra={"request_id": request_id, "method": method},
    )
    try:
        with observed_span(tracer, f"api.{method}") as span:
            set_span_attributes(
                span,
                {
                    "http.method": method,
                    "request.id": request_id,
                },
            )
            try:
                response = await call_next(request)
                status_code = str(response.status_code)

                duration = time.time() - start_time
                route_template = _route_template(request)
                span.update_name(f"api.{method} {route_template}")
                _record_http_metrics(method, route_template, status_code, duration)
                set_span_attributes(
                    span,
                    {
                        "http.route": route_template,
                        "http.status_code": response.status_code,
                        "http.duration_seconds": round(duration, 4),
                    },
                )
                response.headers["X-Request-ID"] = request_id
                trace_id = get_agent_trace_id() or get_current_trace_id()
                if trace_id:
                    response.headers["X-Trace-ID"] = trace_id
                logger.info(
                    "http request completed",
                    extra={
                        "request_id": request_id,
                        "trace_id": trace_id,
                        "method": method,
                        "route": route_template,
                        "status_code": response.status_code,
                        "duration_ms": round(duration * 1000, 2),
                    },
                )
                return response
            except Exception as e:
                route_template = _route_template(request)
                duration = time.time() - start_time
                _record_http_metrics(method, route_template, "500", duration)
                set_span_attributes(
                    span,
                    {"http.status_code": 500, "error.type": e.__class__.__name__},
                )
                logger.exception(
                    "http request failed",
                    extra={
                        "request_id": request_id,
                        "method": method,
                        "route": route_template,
                        "status_code": 500,
                        "duration_ms": round(duration * 1000, 2),
                        "error_type": e.__class__.__name__,
                    },
                )
                raise
    finally:
        reset_agent_trace_id(agent_trace_token)
        reset_request_id(request_token)


# Auto-instrument infrastructure once; each integration fails open when unavailable.
instrument_dependencies(app, engine)


# --- AUTH ENDPOINTS ---
async def _create_staff_user(user_in: UserCreate, db: AsyncSession) -> User:
    """集中创建员工账号，避免公开注册与管理接口逻辑漂移。"""
    if (
        settings.APP_ENV.lower() in {"production", "prod"}
        and len(user_in.password) < 12
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Production staff passwords must contain at least 12 characters.",
        )
    existing = await db.execute(select(User).filter(User.username == user_in.username))
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    new_user = User(
        username=user_in.username,
        hashed_password=get_password_hash(user_in.password),
        role=user_in.role,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


@app.post(
    "/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register_user(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    if not settings.STAFF_SELF_REGISTRATION_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return await _create_staff_user(user_in, db)


@app.post(
    "/auth/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def create_staff_user(
    user_in: UserCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """只允许管理员创建 agent、manager 或 admin 账号。"""
    return await _create_staff_user(user_in, db)


@app.post("/auth/token", response_model=Token)
async def login_for_access_token(
    form_data: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await public_rate_limiter.enforce(
        scope="staff-login",
        identities=public_access_service.login_identity(request, form_data.username),
        limit=settings.AUTH_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
    result = await db.execute(select(User).filter(User.username == form_data.username))
    user = result.scalars().first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user.username, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer", "role": user.role}


@app.get("/auth/users/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user


# --- CORE COPILOT APIS ---
@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}


# --- ADMIN RESOURCE MANAGEMENT ---
async def _admin_tool_catalog(db: AsyncSession) -> list[AdminToolResponse]:
    """合并代码注册表与数据库运行开关。"""
    result = await db.execute(select(ToolRuntimeSetting))
    settings_by_name = {item.tool_name: item for item in result.scalars().all()}
    catalog: list[AdminToolResponse] = []
    for item in tool_registry.list_tools():
        runtime = settings_by_name.get(item["name"])
        catalog.append(
            AdminToolResponse(
                **item,
                disabled_reason=(
                    runtime.reason if runtime and not runtime.enabled else None
                ),
                updated_by=runtime.updated_by if runtime else None,
                updated_at=runtime.updated_at if runtime else None,
            )
        )
    return catalog


@app.get("/admin/resources/tools", response_model=list[AdminToolResponse])
async def list_admin_tools(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """仅管理员可查看 Tool 定义和当前启停状态。"""
    return await _admin_tool_catalog(db)


@app.put("/admin/resources/tools/{tool_name}", response_model=AdminToolResponse)
async def update_admin_tool(
    tool_name: str,
    req: AdminToolUpdateRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """持久化 Tool 开关，所有 Schema、RBAC 和高风险门禁保持不变。"""
    if tool_registry.get_definition(tool_name) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )
    reason = (req.reason or "").strip() or None
    if not req.enabled and reason is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Disabling a Tool requires a reason.",
        )
    runtime = await db.get(ToolRuntimeSetting, tool_name)
    if runtime is None:
        runtime = ToolRuntimeSetting(
            tool_name=tool_name, updated_by=current_user.username
        )
        db.add(runtime)
    runtime.enabled = req.enabled
    runtime.reason = reason
    runtime.updated_by = current_user.username
    runtime.updated_at = datetime.datetime.utcnow()
    await db.commit()
    tool_registry.set_enabled(tool_name, req.enabled)
    catalog = await _admin_tool_catalog(db)
    return next(item for item in catalog if item.name == tool_name)


def _prompt_bundle_response(bundle) -> AdminPromptBundleResponse:
    """将 Prompt Bundle 转换为管理视图。"""
    return AdminPromptBundleResponse(
        **bundle.metadata(),
        payload=bundle.payload(),
    )


@app.get("/admin/resources/prompts", response_model=AdminPromptRegistryResponse)
async def list_admin_prompts(current_user: User = Depends(require_admin)):
    """查看全部不可变 Bundle 及生产/预发布指针。"""
    registry = PromptRegistry(Path(settings.PROMPT_REGISTRY_DIR))
    return AdminPromptRegistryResponse(
        state=registry.state(),
        effective={
            environment: _prompt_bundle_response(registry.resolve(environment))
            for environment in ("staging", "production")
        },
        bundles=[AdminPromptBundleResponse(**item) for item in registry.list_bundles()],
    )


@app.post(
    "/admin/resources/prompts",
    response_model=AdminPromptBundleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_prompt_candidate(
    req: AdminPromptBundleRequest,
    current_user: User = Depends(require_admin),
):
    """只创建候选快照；生产晋级仍必须通过 EvalOps 质量门禁。"""
    registry = PromptRegistry(Path(settings.PROMPT_REGISTRY_DIR))
    try:
        bundle = registry.register(req.model_dump())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _prompt_bundle_response(bundle)


def _knowledge_document_response(document) -> AdminKnowledgeDocumentResponse:
    """将 KnowledgeDoc ORM 对象转换为对外模型。"""
    return AdminKnowledgeDocumentResponse(
        id=document.id,
        title=document.title,
        content=document.content,
        version=document.version,
        category=document.category,
        metadata=document.metadata_json or {},
        created_at=document.created_at,
    )


@app.get(
    "/admin/resources/rag-documents",
    response_model=list[AdminKnowledgeDocumentResponse],
)
async def list_admin_rag_documents(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """列出 SQL 中的全部权威知识文档。"""
    documents = await kb_versioning_service.list_documents(db)
    return [_knowledge_document_response(item) for item in documents]


@app.post(
    "/admin/resources/rag-documents",
    response_model=AdminKnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_rag_document(
    req: AdminKnowledgeDocumentRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """新建或幂等更新文档，同步重建它的向量 Chunk。"""
    document = await kb_versioning_service.register_document(
        db,
        doc_id=req.id,
        title=req.title,
        content=req.content,
        category=req.category,
        version=req.version,
        metadata=req.metadata,
    )
    return _knowledge_document_response(document)


@app.put(
    "/admin/resources/rag-documents/{doc_id}",
    response_model=AdminKnowledgeDocumentResponse,
)
async def update_admin_rag_document(
    doc_id: str,
    req: AdminKnowledgeDocumentRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新文档时严格保持 Path 与 Payload 标识一致。"""
    if doc_id != req.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document ID in path and payload must match.",
        )
    if await kb_versioning_service.get_document(db, doc_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge document not found.",
        )
    document = await kb_versioning_service.register_document(
        db,
        doc_id=req.id,
        title=req.title,
        content=req.content,
        category=req.category,
        version=req.version,
        metadata=req.metadata,
    )
    return _knowledge_document_response(document)


@app.delete("/admin/resources/rag-documents/{doc_id}", status_code=204)
async def delete_admin_rag_document(
    doc_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """删除文档及对应向量索引。"""
    deleted = await kb_versioning_service.delete_document(db, doc_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge document not found.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/admin/resources/rag-documents/reindex",
    response_model=AdminRagReindexResponse,
)
async def reindex_admin_rag_documents(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """从 SQL 权威文档全量刷新 ChromaDB 索引。"""
    count = await kb_versioning_service.reindex_all(db)
    return AdminRagReindexResponse(indexed_documents=count)


@app.post("/chat", response_model=ChatResponse)
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
    agent_output, approval_id, agent_run = await _process_ticket_with_agent(
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


@app.post("/summarize-ticket", response_model=TicketSummaryResponse)
async def summarize_ticket(
    req: SuggestResponseRequest,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """Analyze a ticket description and summarize key issues."""
    ticket_res = await db.execute(select(Ticket).filter(Ticket.id == req.ticket_id))
    ticket = ticket_res.scalars().first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    initial_state = {
        "ticket_id": ticket.id,
        "customer_id": ticket.customer_id,
        "subject": ticket.subject,
        "description": ticket.description,
        "kb_version": req.kb_version,
    }
    agent_output = await _run_workflow_with_tool_audit(db, initial_state)

    summary = f"The customer is reporting an issue regarding '{ticket.subject}'. Category: {agent_output.get('department')}."
    key_issues = [ticket.subject, f"Detected Intent: {agent_output.get('intent')}"]

    return TicketSummaryResponse(
        ticket_id=ticket.id,
        summary=summary,
        key_issues=key_issues,
        sentiment=agent_output.get("sentiment", "neutral"),
        priority=agent_output.get("priority", "medium"),
        urgency_score=0.9 if agent_output.get("priority") == "urgent" else 0.5,
    )


@app.post("/suggest-response", response_model=SuggestResponseResponse)
async def suggest_response(
    req: SuggestResponseRequest,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """Provide a response suggestion with citations and QA verification details."""
    ticket_res = await db.execute(select(Ticket).filter(Ticket.id == req.ticket_id))
    ticket = ticket_res.scalars().first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    initial_state = {
        "ticket_id": ticket.id,
        "customer_id": ticket.customer_id,
        "subject": ticket.subject,
        "description": ticket.description,
        "kb_version": req.kb_version,
    }
    agent_output = await _run_workflow_with_tool_audit(db, initial_state)

    # 先结束读取事务，再使用隔离事务写入反馈域。
    await db.commit()
    agent_run = await _record_agent_run_fail_open(
        primary_db=db,
        agent_output=agent_output,
        input_text=ticket.description,
        endpoint="/suggest-response",
    )

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

    return SuggestResponseResponse(
        ticket_id=ticket.id,
        suggested_response=agent_output.get("suggested_response", ""),
        tool_context=agent_output.get("tool_context", {}),
        tool_calls=agent_output.get("tool_calls", []),
        citations=citations,
        qa_score=agent_output.get("qa_score", 1.0),
        hallucination_detected=agent_output.get("hallucination_detected", False),
        analyzer_confidence=agent_output.get("analyzer_confidence", 1.0),
        risk_level=agent_output.get("risk_level", "low"),
        risk_score=agent_output.get("risk_score", 0.0),
        risk_reasons=agent_output.get("risk_reasons", []),
        cost_metadata=cost_meta,
        agent_run_id=agent_run.id if agent_run else None,
        feedback_token=(
            getattr(agent_run, "_feedback_token", None) if agent_run else None
        ),
    )


@app.post("/analyze-sentiment", response_model=TicketSentimentResponse)
async def analyze_sentiment(
    req: SuggestResponseRequest,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """Evaluate customer ticket tone and urgency levels."""
    ticket_res = await db.execute(select(Ticket).filter(Ticket.id == req.ticket_id))
    ticket = ticket_res.scalars().first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    initial_state = {
        "ticket_id": ticket.id,
        "customer_id": ticket.customer_id,
        "subject": ticket.subject,
        "description": ticket.description,
        "kb_version": req.kb_version,
    }
    agent_output = await _run_workflow_with_tool_audit(db, initial_state)

    return TicketSentimentResponse(
        ticket_id=ticket.id,
        sentiment=agent_output.get("sentiment", "neutral"),
        confidence_score=0.95,
        detected_emotions=[agent_output.get("sentiment", "neutral")],
        priority=agent_output.get("priority", "medium"),
    )


@app.post("/recommend-escalation", response_model=TicketEscalationResponse)
async def recommend_escalation(
    req: SuggestResponseRequest,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """SLA routing prediction."""
    ticket_res = await db.execute(select(Ticket).filter(Ticket.id == req.ticket_id))
    ticket = ticket_res.scalars().first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    initial_state = {
        "ticket_id": ticket.id,
        "customer_id": ticket.customer_id,
        "subject": ticket.subject,
        "description": ticket.description,
        "kb_version": req.kb_version,
    }
    agent_output = await _run_workflow_with_tool_audit(db, initial_state)

    return TicketEscalationResponse(
        ticket_id=ticket.id,
        escalation_recommended=agent_output.get("escalation_recommended", False),
        escalation_reason=agent_output.get("escalation_reason") or "Standard flow",
        suggested_department=agent_output.get("department", "general"),
        sla_hours=agent_output.get("sla_hours", 24.0),
    )


@app.post("/customer-context", response_model=CustomerContextResponse)
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
        last_interaction=datetime.datetime.utcnow() - datetime.timedelta(days=2),
    )


@app.post("/evaluate-response", response_model=EvaluateResponseResponse)
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
        await _record_evaluation_fail_open(
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
@app.post(
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


@app.get("/feedback/runs/{agent_run_id}", response_model=AgentRunResponse)
async def get_feedback_run(
    agent_run_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """供主管按 Agent Run 查看 Trace、版本快照和全部反馈。"""
    return await feedback_service.get_agent_run(db, agent_run_id)


@app.get("/observability/runs", response_model=AgentRunPageResponse)
async def list_observability_runs(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ticket_id: int | None = Query(default=None, ge=1),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """供主管按工单筛选并分页查看可关联 Trace 的 Agent Run。"""
    runs, total = await feedback_service.list_agent_runs(
        db,
        limit=limit,
        offset=offset,
        ticket_id=ticket_id,
    )
    return AgentRunPageResponse(
        items=runs,
        total=total,
        limit=limit,
        offset=offset,
    )


# --- HUMAN IN THE LOOP APPROVAL APIS ---
@app.get("/approvals/pending", response_model=list[ResponseApprovalResponse])
async def list_pending_approvals(
    current_user: User = Depends(require_agent), db: AsyncSession = Depends(get_db)
):
    """Secure endpoint: list all response drafts needing validation."""
    records = await human_it_loop_service.get_pending_approvals(db)

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


@app.post("/approvals/{approval_id}", response_model=ResponseApprovalResponse)
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
    execution = await _resume_approval_execution(db, record)

    return ResponseApprovalResponse(
        id=record.id,
        ticket_id=record.ticket_id,
        status=record.status,
        final_response=record.modified_response or record.drafted_response,
        latency_seconds=record.latency_seconds or 0.0,
        approved_at=datetime.datetime.utcnow(),
        workflow_execution_status=execution.status if execution else None,
    )


@app.get("/agent-executions/{execution_id}", response_model=AgentExecutionResponse)
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


@app.post(
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
    resumed = await _resume_approval_execution(db, approval)
    if resumed is None:
        raise HTTPException(status_code=404, detail="Agent execution not found.")
    return resumed


# --- HIGH-RISK TOOL GOVERNANCE APIS ---
@app.post(
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


@app.get("/tool-actions", response_model=ToolActionPageResponse)
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


@app.get("/tool-actions/{action_id}", response_model=ToolActionResponse)
async def get_tool_action(
    action_id: str,
    current_user: User = Depends(require_agent),
    db: AsyncSession = Depends(get_db),
):
    """读取 Action 脱敏视图与 Append-only 事件。"""
    return await tool_governance_service.get(db, action_id)


@app.post("/tool-actions/{action_id}/decision", response_model=ToolActionResponse)
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


@app.post("/tool-actions/{action_id}/execute", response_model=ToolActionResponse)
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


@app.post("/tool-actions/{action_id}/compensate", response_model=ToolActionResponse)
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


@app.post("/tool-actions/{action_id}/resolve", response_model=ToolActionResponse)
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


@app.get(
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
    if ticket and ticket.priority == "urgent":
        add("urgent_priority")
    if ticket and ticket.priority == "high" and ticket.sentiment == "negative":
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


@app.get(
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


@app.get("/tool-audits", response_model=ToolInvocationAuditPageResponse)
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


@app.get("/tool-outbox", response_model=ToolOutboxPageResponse)
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


@app.post("/tool-outbox/{event_id}/retry", response_model=ToolOutboxEventResponse)
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


# --- GENERAL TICKETING APIS ---
def _public_review_feedback(agent_output: dict) -> tuple[str, str, str]:
    """将内部风险原因转换为可安全展示给用户的处理说明。"""
    degradation = str(agent_output.get("degradation_level", "none")).lower()
    if agent_output.get("errors") or degradation in {"human_required", "failed"}:
        return (
            "processing_exception",
            "我已经收到您的问题，但部分处理服务暂时不可用。工单已保留并转交人工客服，您无需重复提交。",
            "系统会保留当前工单，由人工客服核对业务信息后继续处理。",
        )
    if (
        agent_output.get("security_threat_detected")
        or agent_output.get("risk_requires_human")
        or str(agent_output.get("risk_level", "low")).lower() in {"high", "critical"}
    ):
        return (
            "risk_review",
            "我已经收到您的请求。由于该问题涉及敏感或高风险操作，自动处理已暂停并转交人工客服进行安全核验。",
            "为保护您的账户和业务权益，客服人员确认后会继续处理。",
        )
    if (
        agent_output.get("hallucination_detected")
        or agent_output.get("response_requires_human")
        or float(agent_output.get("qa_score", 1.0)) < settings.RISK_QA_SCORE_THRESHOLD
    ):
        return (
            "quality_review",
            "我已完成初步查询，但当前结果的依据或置信度不足。为避免提供不准确的信息，工单已转交人工客服复核。",
            "人工客服会结合业务记录和服务政策确认后回复。",
        )
    return (
        "manual_review",
        "我已经收到您的问题。该请求需要人工客服进一步确认，工单已进入处理队列。",
        "客服人员会核对 AI 草稿和相关业务信息后继续处理。",
    )


@app.get("/support/history", response_model=PublicConversationHistoryResponse)
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


@app.post(
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
    identities = public_access_service.rate_limit_identities(request, visitor_id)
    await public_rate_limiter.enforce(
        scope="public-chat-minute",
        identities=identities,
        limit=settings.PUBLIC_CHAT_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
    await public_rate_limiter.enforce(
        scope="public-chat-day",
        identities=identities,
        limit=settings.PUBLIC_CHAT_RATE_LIMIT_PER_DAY,
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
    agent_output, approval_id, agent_run = await _process_ticket_with_agent(
        db=db,
        ticket=ticket,
        kb_version=req.kb_version,
        endpoint="/support/requests",
        session_id=internal_session_id,
        memory_context=memory_context,
        require_persisted_result=True,
    )
    await db.refresh(ticket)

    if approval_id:
        handling_reason, public_response, public_message = _public_review_feedback(
            agent_output
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
        message="智能客服已完成处理。",
        handling_reason=None,
        created_at=ticket.created_at,
        agent_run_id=agent_run.id if agent_run else None,
        feedback_token=(
            getattr(agent_run, "_feedback_token", None) if agent_run else None
        ),
    )


@app.post(
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
    await _process_ticket_with_agent(
        db=db,
        ticket=ticket,
        kb_version=req.kb_version,
        endpoint="/tickets",
        session_id=f"ticket_{ticket.id}",
        require_persisted_result=True,
    )
    await db.refresh(ticket)
    return ticket


@app.get(
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


@app.post("/tickets/{ticket_id}/close", response_model=TicketResponse)
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


@app.get("/tickets", response_model=list[TicketResponse])
async def list_tickets(
    current_user: User = Depends(require_agent), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Ticket))
    return result.scalars().all()


@app.get("/staff/review-queue", response_model=list[TicketResponse])
async def list_staff_review_queue(
    current_user: User = Depends(require_agent), db: AsyncSession = Depends(get_db)
):
    """返回待回复审批或存在待核实业务操作的工单。"""
    review_tickets = select(ToolActionReview.ticket_id).where(
        ToolActionReview.status == "pending"
    )
    result = await db.execute(
        select(Ticket)
        .where((Ticket.status == "pending_approval") | Ticket.id.in_(review_tickets))
        .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
    )
    tickets = result.scalars().all()
    pending_ids = set((await db.execute(review_tickets)).scalars().all())
    return [
        TicketResponse.model_validate(ticket).model_copy(
            update={"requires_tool_review": ticket.id in pending_ids}
        )
        for ticket in tickets
    ]
