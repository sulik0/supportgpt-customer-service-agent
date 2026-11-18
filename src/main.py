"""FastAPI 应用：生命周期、中间件和路由挂载。路由实现见 src/api/。"""

import datetime
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import settings
from src.observability.logging_config import configure_logging

configure_logging(settings.LOG_LEVEL)

from src.agents.graph import (
    initialize_agent_checkpointing,
    shutdown_agent_checkpointing,
)
from src.api import admin, agent, approvals, auth, public, tickets, tool_actions
from src.api.runtime import load_tool_runtime_settings, recover_resumable_workflows
from src.database import AsyncSessionLocal, engine, init_db
from src.decision import close_decision_provider
from src.observability.instrumentation import instrument_dependencies
from src.observability.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL
from src.observability.tracing import (
    bind_agent_trace_id,
    bind_request_id,
    get_agent_trace_id,
    get_current_trace_id,
    get_tracer,
    init_tracing,
    observed_span,
    reset_agent_trace_id,
    reset_request_id,
    set_span_attributes,
)
from src.security import public_rate_limiter
from src.tickets.test_marks import cleanup_legacy_test_tickets
from src.tools.governance import tool_governance_service
from src.tools.outbox import tool_outbox_worker

tracer = get_tracer(__name__)


logger = logging.getLogger("supportgpt.main")


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
    # 初始化统一 OpenTelemetry Trace 与 Metrics。
    init_tracing()
    # Create DB schemas (SQLite or PostgreSQL)
    await init_db()
    async with AsyncSessionLocal() as db:
        await tool_governance_service.backfill_reviews(db)
        await db.commit()
    # 一次性处理测试标记上线前留下的线上测试工单；幂等，失败不影响启动。
    try:
        async with AsyncSessionLocal() as db:
            await cleanup_legacy_test_tickets(db)
            await db.commit()
    except Exception:
        logger.exception("Legacy test ticket cleanup failed; startup continues.")
    await load_tool_runtime_settings()
    await initialize_agent_checkpointing()
    await recover_resumable_workflows()
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
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Feedback-Token"],
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
    # CORS 预检不产生业务 Span 和指标。
    if method == "OPTIONS":
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


# --- CORE COPILOT APIS ---
@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}


# 路由按原注册顺序挂载。
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(agent.router)
app.include_router(approvals.router)
app.include_router(tool_actions.router)
app.include_router(public.router)
app.include_router(tickets.router)
