import datetime
import json

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from src.config import Settings, settings
from src.models.db_models import ConversationMessage, ConversationSession
from src.security import public_access_service, public_rate_limiter


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["manager", "admin"])
async def test_staff_self_registration_can_be_disabled(
    client, monkeypatch, role
):
    monkeypatch.setattr(settings, "STAFF_SELF_REGISTRATION_ENABLED", False)

    response = await client.post(
        "/auth/register",
        json={
            "username": f"public_{role}",
            "password": "password",
            "role": role,
        },
    )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/chat",
            {
                "session_id": "unauthorized-session",
                "customer_id": "cust_101",
                "message": "Run the internal workflow",
            },
        ),
        (
            "/evaluate-response",
            {"query": "q", "context": ["c"], "response": "a"},
        ),
    ],
)
async def test_internal_llm_endpoints_require_staff_authentication(
    client, path, payload
):
    response = await client.post(path, json=payload)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_agent_cannot_call_manager_api(client, agent_headers):
    response = await client.get("/observability/runs", headers=agent_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cors_only_allows_configured_frontend(client):
    allowed = await client.options(
        "/support/requests",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    denied = await client.options(
        "/support/requests",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


@pytest.mark.asyncio
async def test_sensitive_responses_disable_caching(client):
    response = await client.post(
        "/auth/token",
        json={"username": "unknown", "password": "not-the-password"},
    )

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.asyncio
async def test_public_history_is_isolated_by_signed_visitor(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "PUBLIC_DEMO_ISOLATION_ENABLED", True)
    monkeypatch.setattr(settings, "PUBLIC_VISITOR_SECRET", "v" * 48)
    visitor_id = "visitor_identity_1234567890"
    session_id = "shared-browser-session"
    internal_session_id = public_access_service.scoped_session_id(
        visitor_id, session_id
    )
    db_session.add(
        ConversationSession(
            session_id=internal_session_id,
            customer_id="cust_101",
        )
    )
    db_session.add(
        ConversationMessage(
            id="isolated-message",
            session_id=internal_session_id,
            role="user",
            content="只属于访客 A 的对话",
            status="final",
            created_at=datetime.datetime.utcnow(),
        )
    )
    await db_session.commit()
    client.cookies.set(
        settings.PUBLIC_VISITOR_COOKIE_NAME,
        public_access_service._signed(visitor_id),
    )

    owner = await client.get(
        "/support/history",
        params={"customer_id": "cust_101", "session_id": session_id},
    )
    assert owner.status_code == 200
    assert [item["id"] for item in owner.json()["messages"]] == [
        "isolated-message"
    ]

    client.cookies.clear()
    other_visitor = await client.get(
        "/support/history",
        params={"customer_id": "cust_101", "session_id": session_id},
    )
    assert other_visitor.status_code == 200
    assert other_visitor.json()["messages"] == []
    assert settings.PUBLIC_VISITOR_COOKIE_NAME in other_visitor.cookies


@pytest.mark.asyncio
async def test_tampered_visitor_cookie_cannot_read_original_session(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "PUBLIC_DEMO_ISOLATION_ENABLED", True)
    monkeypatch.setattr(settings, "PUBLIC_VISITOR_SECRET", "s" * 48)
    visitor_id = "signed_visitor_identity_123456"
    session_id = "private-session"
    internal_session_id = public_access_service.scoped_session_id(
        visitor_id, session_id
    )
    db_session.add(
        ConversationSession(
            session_id=internal_session_id,
            customer_id="cust_101",
        )
    )
    db_session.add(
        ConversationMessage(
            id="private-message",
            session_id=internal_session_id,
            role="user",
            content="visitor A private content",
            status="final",
            created_at=datetime.datetime.utcnow(),
        )
    )
    await db_session.commit()
    signed = public_access_service._signed(visitor_id)
    replacement = "0" if signed[-1] != "0" else "1"
    client.cookies.set(settings.PUBLIC_VISITOR_COOKIE_NAME, signed[:-1] + replacement)

    response = await client.get(
        "/support/history",
        params={"customer_id": "cust_101", "session_id": session_id},
    )

    assert response.status_code == 200
    assert response.json()["messages"] == []
    assert response.cookies[settings.PUBLIC_VISITOR_COOKIE_NAME] != signed


@pytest.mark.asyncio
async def test_public_demo_rejects_unknown_customer_profile(client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_DEMO_ISOLATION_ENABLED", True)

    response = await client.get(
        "/support/history",
        params={"customer_id": "real_customer_42", "session_id": "session-a"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_configured_limit(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "REDIS_URL", None)
    await public_rate_limiter.reset_for_tests()

    await public_rate_limiter.enforce(
        scope="security-test",
        identities=("visitor-a",),
        limit=1,
        window_seconds=60,
    )
    with pytest.raises(HTTPException) as error:
        await public_rate_limiter.enforce(
            scope="security-test",
            identities=("visitor-a",),
            limit=1,
            window_seconds=60,
        )

    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"]


@pytest.mark.asyncio
async def test_public_support_endpoint_returns_429_when_flooded(client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "PUBLIC_CHAT_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "PUBLIC_CHAT_RATE_LIMIT_PER_DAY", 100)
    monkeypatch.setattr(settings, "REDIS_URL", None)
    await public_rate_limiter.reset_for_tests()

    first = await client.post(
        "/support/requests",
        json={"customer_id": "cust_101", "message": "How do I update settings?"},
    )
    second = await client.post(
        "/support/requests",
        json={"customer_id": "cust_101", "message": "One more request"},
    )

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.headers["retry-after"]


@pytest.mark.asyncio
async def test_redis_failure_uses_in_memory_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:1/0")
    await public_rate_limiter.close()
    public_rate_limiter._redis_failed = False
    await public_rate_limiter.reset_for_tests()

    await public_rate_limiter.enforce(
        scope="redis-shutdown-test",
        identities=("same-client",),
        limit=1,
        window_seconds=60,
    )
    with pytest.raises(HTTPException) as error:
        await public_rate_limiter.enforce(
            scope="redis-shutdown-test",
            identities=("same-client",),
            limit=1,
            window_seconds=60,
        )

    assert public_rate_limiter._redis_failed is True
    assert error.value.status_code == 429
    public_rate_limiter._redis_failed = False


@pytest.mark.asyncio
async def test_oversized_public_request_is_rejected_before_agent(
    client, monkeypatch
):
    agent_called = False

    async def should_not_run(**_kwargs):
        nonlocal agent_called
        agent_called = True
        raise AssertionError("Agent must not run for oversized bodies")

    monkeypatch.setattr("src.main._process_ticket_with_agent", should_not_run)
    body = json.dumps(
        {"customer_id": "cust_101", "message": "x" * 70000}
    ).encode("utf-8")

    response = await client.post(
        "/support/requests",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert agent_called is False


@pytest.mark.asyncio
async def test_llm_timeout_returns_safe_public_degradation(
    client, monkeypatch
):
    from src.llm.provider import llm_provider

    async def timeout(**_kwargs):
        raise TimeoutError("private-upstream.internal secret diagnostic")

    monkeypatch.setattr(llm_provider, "generate_resolution", timeout)

    response = await client.post(
        "/support/requests",
        json={
            "customer_id": "cust_101",
            "message": "请说明如何修改账户偏好设置。",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "pending_human"
    assert payload["handling_reason"] == "processing_exception"
    assert "暂时不可用" in payload["response"]
    assert "private-upstream" not in response.text
    assert "Traceback" not in response.text


def test_production_rejects_default_jwt_secret():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(
            APP_ENV="production",
            DEBUG=False,
            JWT_SECRET="super-secret-jwt-key-change-in-production-123456",
            TOOL_ACTION_ENCRYPTION_KEY=Fernet.generate_key().decode("ascii"),
            PUBLIC_VISITOR_SECRET="p" * 48,
            CORS_ALLOWED_ORIGINS="https://support.example.com",
            STAFF_SELF_REGISTRATION_ENABLED=False,
            PUBLIC_DEMO_ISOLATION_ENABLED=True,
            PUBLIC_RATE_LIMIT_ENABLED=True,
        )


def test_production_accepts_explicit_security_configuration():
    configured = Settings(
        APP_ENV="production",
        DEBUG=False,
        JWT_SECRET="j" * 48,
        TOOL_ACTION_ENCRYPTION_KEY=Fernet.generate_key().decode("ascii"),
        PUBLIC_VISITOR_SECRET="p" * 48,
        CORS_ALLOWED_ORIGINS="https://support.example.com",
        STAFF_SELF_REGISTRATION_ENABLED=False,
        PUBLIC_DEMO_ISOLATION_ENABLED=True,
        PUBLIC_RATE_LIMIT_ENABLED=True,
    )

    assert configured.cors_allowed_origins == ["https://support.example.com"]


def _request_with(headers: dict[str, str], client_host: str = "100.64.0.9"):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (client_host, 1234),
        }
    )


def test_client_ip_ignores_proxy_headers_when_untrusted(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    request = _request_with({"X-Real-IP": "203.0.113.5", "X-Forwarded-For": "1.1.1.1"})

    assert public_access_service.client_ip(request) == "100.64.0.9"


def test_client_ip_prefers_real_ip_header_behind_proxy(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    request = _request_with(
        {"X-Real-IP": "203.0.113.5", "X-Forwarded-For": "6.6.6.6, 203.0.113.5"}
    )

    assert public_access_service.client_ip(request) == "203.0.113.5"


def test_client_ip_uses_rightmost_forwarded_hop_not_client_supplied(monkeypatch):
    # 最左一段是客户端自带的，伪造它不能换一个限流身份。
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    request = _request_with({"X-Forwarded-For": "6.6.6.6, 203.0.113.5"})

    assert public_access_service.client_ip(request) == "203.0.113.5"


def test_rate_limit_ip_identity_is_stable_across_proxy_nodes(monkeypatch):
    # 复现线上问题：代理节点地址每次不同，但同一客户端的限流身份必须相同。
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    first = _request_with({"X-Real-IP": "203.0.113.5"}, client_host="100.64.0.4")
    second = _request_with({"X-Real-IP": "203.0.113.5"}, client_host="100.64.0.22")

    assert (
        public_access_service.rate_limit_identities(first, "visitor-a")[0]
        == public_access_service.rate_limit_identities(second, "visitor-b")[0]
    )
