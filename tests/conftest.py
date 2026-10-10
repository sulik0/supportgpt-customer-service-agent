import os
import pytest
import asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from httpx import AsyncClient, ASGITransport

# Set environment variables for testing context
os.environ["APP_ENV"] = "testing"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["OMS_PROVIDER"] = "mock"
# 测试环境不得继承开发机的小模型端点与凭据。
os.environ["LLM_FAST_MODEL_NAME"] = ""
os.environ["LLM_FAST_BASE_URL"] = ""
os.environ["LLM_FAST_API_KEY"] = ""
os.environ["LLM_ANALYZER_MODEL_NAME"] = ""
os.environ["LLM_QA_MODEL_NAME"] = ""
os.environ["DECISION_PROVIDER"] = "disabled"
os.environ["JEV_API_KEY"] = ""
os.environ["TOOL_OUTBOX_WORKER_ENABLED"] = "false"
os.environ["TOOL_RECONCILIATION_DELAY_SECONDS"] = "0"
os.environ["TOOL_POLICY_VERSION"] = "tool-policy-v2.2-test"
os.environ["OTEL_ENABLED"] = "false"
os.environ["STAFF_SELF_REGISTRATION_ENABLED"] = "true"
os.environ["PUBLIC_DEMO_ISOLATION_ENABLED"] = "false"
os.environ["PUBLIC_RATE_LIMIT_ENABLED"] = "false"

from src.database import Base, get_db
from src.main import app
from src.rag.vector_store import vector_store
from src.tools.refund_gateway import refund_gateway
from src.tools.registry import tool_registry

# Configure isolated testing engine
test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}
)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest.fixture(scope="session")
def event_loop():
    """Create session-scoped event loop for async fixtures."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
async def init_test_db():
    """Create a fresh database structure for each test run."""
    tool_registry.replace_runtime_settings({})
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    # Clear memory vector store collection
    vector_store.clear_database()
    refund_gateway.reset()
    tool_registry.replace_runtime_settings({})


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session wrapper."""
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
def outbox_session_factory():
    """允许测试显式推进异步 Outbox Worker。"""
    return TestSessionLocal


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Provide an HTTPX AsyncClient bound to the FastAPI application with mock DB dependency overrides."""

    async def override_get_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise
        finally:
            await db_session.close()

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def agent_headers(client: AsyncClient) -> dict[str, str]:
    """为内部工单接口提供隔离的客服身份。"""
    register = await client.post(
        "/auth/register",
        json={"username": "test_agent", "password": "test-password", "role": "agent"},
    )
    assert register.status_code == 201
    login = await client.post(
        "/auth/token",
        json={"username": "test_agent", "password": "test-password"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}
