"""独立参考 OMS HTTP 服务，使用 uvicorn src.oms.app:create_app --factory 启动。"""

import secrets
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.oms.contracts import (
    CompensationReceipt,
    KeyInput,
    ReconciliationReceipt,
    RefundInput,
    RefundReceipt,
)
from src.oms.settings import ReferenceOMSSettings
from src.oms.store import OMSBase, OMSConflict, OMSStore


def create_oms_app(config: ReferenceOMSSettings, engine=None) -> FastAPI:
    """允许隔离测试注入数据库；运行时创建专用 Engine。"""
    owns_engine = engine is None
    engine = engine or create_async_engine(config.database_url, pool_pre_ping=True)
    store = OMSStore(
        async_sessionmaker(engine, expire_on_commit=False),
        config.encryption_key.get_secret_value(),
    )

    @asynccontextmanager
    async def lifespan(app):
        try:
            async with engine.begin() as connection:
                await connection.run_sync(OMSBase.metadata.create_all)
            yield
        finally:
            if owns_engine:
                await engine.dispose()

    app = FastAPI(
        title="SupportGPT OMS 参考服务",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store
    bearer = HTTPBearer(auto_error=False)

    async def authenticate(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ):
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode(), config.api_key.get_secret_value().encode()
        ):
            raise HTTPException(status_code=401, detail="Invalid OMS credentials")

    @app.exception_handler(OMSConflict)
    async def conflict_handler(request, error):
        return JSONResponse(
            status_code=error.status_code,
            content={"code": error.code, "execution_rejected": True},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, error):
        return JSONResponse(
            status_code=422,
            content={"code": "invalid_request", "execution_rejected": True},
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_handler(request, error):
        # 不暴露连接串、SQL 或输入参数，Gateway 按结果不确定处理。
        return JSONResponse(status_code=503, content={"code": "oms_unavailable"})

    @app.get("/health")
    async def health():
        return {"status": "ok", "reference_implementation": True, "funds_moved": False}

    dependencies = [Depends(authenticate)]
    key_header = Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=160,
            pattern=r"^[A-Za-z0-9_.:\-]+$",
        ),
    ]

    @app.post(
        "/v1/refund-requests", dependencies=dependencies, response_model=RefundReceipt
    )
    async def create_refund(payload: RefundInput, idempotency_key: key_header):
        return await store.create(payload, idempotency_key)

    @app.post(
        "/v1/refund-requests/reconcile",
        dependencies=dependencies,
        response_model=ReconciliationReceipt,
    )
    async def reconcile(payload: KeyInput):
        return await store.reconcile(payload.idempotency_key)

    @app.post(
        "/v1/refund-requests/compensate",
        dependencies=dependencies,
        response_model=CompensationReceipt,
    )
    async def compensate(payload: KeyInput, idempotency_key: key_header):
        return await store.compensate(payload.idempotency_key, idempotency_key)

    return app


def create_app() -> FastAPI:
    """运行入口只读取 REFERENCE_OMS_*，不加载客服或 LLM 配置。"""
    return create_oms_app(ReferenceOMSSettings())
