"""验证参考 OMS 的外部契约、重启持久性与不确定结果处理。"""

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.oms.app import create_oms_app
from src.oms.settings import ReferenceOMSSettings
from src.oms.store import OMSOrder, OMSRefund
from src.resilience.error_classifier import classify_dependency_error
from src.tools.oms_gateway import HTTPRefundGateway, OMSRejectedError


API_KEY = "isolated-oms-test-api-key-0123456789"
PAYLOAD = {"customer_id": "cust_101", "order_id": "ORD-7001", "reason": "申请退款"}
HEADERS = {
    "Authorization": "Bearer " + API_KEY,
    "Idempotency-Key": "refund-original-001",
}


@pytest.fixture
async def oms(tmp_path):
    config = ReferenceOMSSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/oms.sqlite",
        api_key=API_KEY,
        encryption_key=Fernet.generate_key().decode(),
        environment="testing",
    )
    app = create_oms_app(config)
    async with app.router.lifespan_context(app):
        async with app.state.store.sessions() as db:
            db.add_all(
                [
                    OMSOrder(
                        order_id="ORD-7001",
                        customer_id="cust_101",
                        amount_minor=15000,
                        currency="USD",
                    ),
                    OMSOrder(
                        order_id="ORD-8002",
                        customer_id="cust_102",
                        amount_minor=2500,
                        currency="USD",
                    ),
                ]
            )
            await db.commit()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://oms.test"
        ) as client:
            yield config, app, client


@pytest.mark.asyncio
async def test_receipt_survives_restart_and_conflicting_parameters_are_rejected(oms):
    config, app, client = oms
    first = await client.post("/v1/refund-requests", json=PAYLOAD, headers=HEADERS)
    assert first.status_code == 200
    receipt = first.json()
    assert receipt["amount_minor"] == 15000 and receipt["funds_moved"] is False
    assert (
        await client.post("/v1/refund-requests", json=PAYLOAD, headers=HEADERS)
    ).json() == receipt
    conflict = await client.post(
        "/v1/refund-requests",
        json={**PAYLOAD, "reason": "更换退款原因"},
        headers=HEADERS,
    )
    assert (
        conflict.status_code == 409
        and conflict.json()["code"] == "idempotency_parameter_conflict"
    )
    other_order = await client.post(
        "/v1/refund-requests",
        json={"customer_id": "cust_102", "order_id": "ORD-8002", "reason": "申请退款"},
        headers=HEADERS,
    )
    assert other_order.status_code == 409
    duplicate = await client.post(
        "/v1/refund-requests",
        json=PAYLOAD,
        headers={**HEADERS, "Idempotency-Key": "different-action-001"},
    )
    assert (
        duplicate.status_code == 409
        and duplicate.json()["code"] == "order_refund_already_requested"
    )
    # 新应用、新连接池仍能读出首次回执；不是进程内缓存。
    restarted = create_oms_app(config)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(restarted), base_url="http://oms.test"
        ) as reader:
            result = await reader.post(
                "/v1/refund-requests/reconcile",
                json={"idempotency_key": HEADERS["Idempotency-Key"]},
                headers=HEADERS,
            )
            assert result.json()["authoritative"] is True
            assert result.json()["result"] == receipt
            assert (
                await reader.post("/v1/refund-requests", json=PAYLOAD, headers=HEADERS)
            ).json() == receipt
    async with app.state.store.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(OMSRefund)) == 1
        row = await db.scalar(select(OMSRefund))
        assert PAYLOAD["reason"] not in row.encrypted_payload
        assert row.key_digest != HEADERS["Idempotency-Key"]


@pytest.mark.asyncio
async def test_auth_ownership_server_amount_and_missing_reconciliation(oms):
    _, _, client = oms
    assert (await client.post("/v1/refund-requests", json=PAYLOAD)).status_code == 401
    assert (
        await client.post(
            "/v1/refund-requests",
            json=PAYLOAD,
            headers={**HEADERS, "Authorization": "Bearer bad"},
        )
    ).status_code == 401
    wrong_owner = await client.post(
        "/v1/refund-requests",
        json={**PAYLOAD, "customer_id": "cust_102"},
        headers=HEADERS,
    )
    assert wrong_owner.status_code == 422
    extra = await client.post(
        "/v1/refund-requests", json={**PAYLOAD, "amount_minor": 999}, headers=HEADERS
    )
    assert extra.status_code == 422 and PAYLOAD["reason"] not in extra.text
    missing = await client.post(
        "/v1/refund-requests/reconcile",
        json={"idempotency_key": "not-found-001"},
        headers=HEADERS,
    )
    assert missing.json() == {
        "status": "pending",
        "found": False,
        "authoritative": True,
        "result": None,
    }


@pytest.mark.asyncio
async def test_compensation_is_persistent_idempotent_and_checks_conflict(oms):
    config, _, client = oms
    created = (
        await client.post("/v1/refund-requests", json=PAYLOAD, headers=HEADERS)
    ).json()
    headers = {**HEADERS, "Idempotency-Key": "compensation-001"}
    payload = {"idempotency_key": HEADERS["Idempotency-Key"]}
    first = await client.post(
        "/v1/refund-requests/compensate", json=payload, headers=headers
    )
    assert (
        first.status_code == 200
        and first.json()["refund_request_id"] == created["refund_request_id"]
    )
    assert (
        await client.post(
            "/v1/refund-requests/compensate", json=payload, headers=headers
        )
    ).json() == first.json()
    assert (
        await client.post(
            "/v1/refund-requests/compensate",
            json={"idempotency_key": "another-original-001"},
            headers=headers,
        )
    ).status_code == 409
    assert (
        await client.post(
            "/v1/refund-requests/compensate",
            json=payload,
            headers={**headers, "Idempotency-Key": "another-compensation-001"},
        )
    ).status_code == 409
    restarted = create_oms_app(config)
    async with restarted.router.lifespan_context(restarted):
        assert (
            await restarted.state.store.compensate(
                payload["idempotency_key"], "compensation-001"
            )
            == first.json()
        )
        assert (await restarted.state.store.reconcile(payload["idempotency_key"]))[
            "result"
        ]["status"] == "compensated"


@pytest.mark.parametrize(
    "mode", ["timeout", "malformed", "server_error", "unconfirmed_conflict", "redirect"]
)
def test_uncertain_http_write_never_becomes_deterministic_failure(mode):
    calls = []

    def responder(request):
        calls.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("sensitive server response", request=request)
        if mode == "malformed":
            return httpx.Response(200, json={"status": "submitted"})
        return httpx.Response(
            {"server_error": 503, "unconfirmed_conflict": 409, "redirect": 307}[mode],
            json={"secret": API_KEY},
        )

    gateway = HTTPRefundGateway(
        "http://oms.test", API_KEY, transport=httpx.MockTransport(responder)
    )
    with pytest.raises((ConnectionError, TimeoutError)) as error:
        gateway.create_refund_request(
            **PAYLOAD, idempotency_key=HEADERS["Idempotency-Key"]
        )
    assert classify_dependency_error(error.value).value in {"connection", "timeout"}
    assert len(calls) == 1 and API_KEY not in str(error.value)


def test_confirmed_parameter_conflict_is_a_deterministic_rejection():
    gateway = HTTPRefundGateway(
        "http://oms.test",
        API_KEY,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(409, json={"execution_rejected": True})
        ),
    )
    with pytest.raises(OMSRejectedError) as error:
        gateway.create_refund_request(
            **PAYLOAD, idempotency_key=HEADERS["Idempotency-Key"]
        )
    assert classify_dependency_error(error.value).value == "validation_error"


@pytest.mark.parametrize(
    "response",
    [
        {"status": "succeeded", "found": True, "result": {}},
        {"status": "failed", "found": False, "authoritative": True},
        {"status": "pending", "found": True, "authoritative": True},
    ],
)
def test_reconciliation_requires_a_consistent_authoritative_receipt(response):
    gateway = HTTPRefundGateway(
        "http://oms.test",
        API_KEY,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response)
        ),
    )
    with pytest.raises(ConnectionError):
        gateway.reconcile(idempotency_key="refund-original-001")


def test_reference_service_requires_separate_keys_and_postgres():
    key = Fernet.generate_key().decode()
    with pytest.raises(ValueError):
        ReferenceOMSSettings(
            database_url="sqlite+aiosqlite:///demo.db",
            api_key=API_KEY,
            encryption_key=key,
        )
    with pytest.raises(ValueError):
        ReferenceOMSSettings(
            database_url="postgresql+asyncpg://localhost/oms",
            api_key="short",
            encryption_key=key,
        )


def test_gateway_configuration_is_explicit_and_validates_credentials():
    from src.config import Settings

    assert Settings(_env_file=None, OMS_PROVIDER="mock").OMS_PROVIDER == "mock"
    valid = Settings(
        _env_file=None,
        OMS_PROVIDER="reference_http",
        OMS_BASE_URL="http://127.0.0.1:8010",
        OMS_API_KEY=API_KEY,
    )
    assert valid.OMS_TIMEOUT_SECONDS < 2.0
    for values in [
        {"OMS_API_KEY": "short"},
        {"OMS_BASE_URL": "http://user:password@localhost:8010"},
        {"OMS_BASE_URL": "http://localhost:8010?api_key=secret"},
        {"OMS_TIMEOUT_SECONDS": 3.0},
    ]:
        with pytest.raises(ValueError):
            Settings(
                _env_file=None,
                OMS_PROVIDER="reference_http",
                OMS_BASE_URL=values.get("OMS_BASE_URL", "http://localhost:8010"),
                OMS_API_KEY=values.get("OMS_API_KEY", API_KEY),
                OMS_TIMEOUT_SECONDS=values.get("OMS_TIMEOUT_SECONDS", 1.5),
            )


@pytest.mark.asyncio
async def test_http_gateway_worker_recovers_committed_request_without_second_write(
    oms, client, db_session, agent_headers, outbox_session_factory, monkeypatch
):
    """故障注入覆盖审批、HTTP Gateway、持久化 OMS、unknown 及自动对账。"""
    import asyncio
    from dataclasses import replace
    from src.models.db_models import Ticket
    from src.oms.contracts import RefundInput
    from src.oms.store import OMSStore
    from src.resilience.circuit_breaker import circuit_breakers
    from src.tools.outbox import tool_outbox_worker
    from src.tools.registry import tool_registry

    config, app, _ = oms
    calls = []

    async def downstream(request):
        # 各 HTTP 请求建立新连接池，确认结果不依赖进程内 Gateway 状态。
        import json

        engine = create_async_engine(config.database_url)
        store = OMSStore(
            async_sessionmaker(engine, expire_on_commit=False),
            config.encryption_key.get_secret_value(),
        )
        body = json.loads(request.content)
        try:
            if request.url.path == "/v1/refund-requests":
                await store.create(
                    RefundInput(**body), request.headers["Idempotency-Key"]
                )
                raise httpx.ReadTimeout("Response lost after commit", request=request)
            return httpx.Response(
                200, json=await store.reconcile(body["idempotency_key"])
            )
        finally:
            await engine.dispose()

    def transport(request):
        calls.append(request.url.path)
        return asyncio.run(downstream(request))

    gateway = HTTPRefundGateway(
        "http://oms.test", API_KEY, transport=httpx.MockTransport(transport)
    )
    definition = tool_registry.get_definition("orders.create_refund_request")
    monkeypatch.setitem(
        tool_registry._tools,
        definition.name,
        replace(
            definition,
            handler=gateway.create_refund_request,
            reconciliation_handler=gateway.reconcile,
            compensation_handler=gateway.compensate,
        ),
    )
    await circuit_breakers.clear()
    register = await client.post(
        "/auth/register",
        json={
            "username": "oms_manager",
            "password": "test-password",
            "role": "manager",
        },
    )
    assert register.status_code == 201
    token = (
        await client.post(
            "/auth/token", json={"username": "oms_manager", "password": "test-password"}
        )
    ).json()["access_token"]
    manager = {"Authorization": "Bearer " + token}
    ticket = Ticket(
        customer_id="cust_101",
        subject="Refund",
        description="Refund ORD-7001",
        status="open",
    )
    db_session.add(ticket)
    await db_session.commit()
    proposed = await client.post(
        "/tool-actions",
        headers=agent_headers,
        json={
            "ticket_id": ticket.id,
            "tool_name": definition.name,
            "payload": PAYLOAD,
            "intent": "billing_dispute",
        },
    )
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()
    approved = await client.post(
        f"/tool-actions/{action['id']}/decision",
        headers=manager,
        json={"decision": "approved", "expected_version": action["version"]},
    )
    assert approved.status_code == 200
    queued = await client.post(
        f"/tool-actions/{action['id']}/execute",
        headers=manager,
        json={"expected_version": approved.json()["version"]},
    )
    assert queued.status_code == 200
    assert await tool_outbox_worker.run_once(outbox_session_factory) == 1
    unknown = (
        await client.get(f"/tool-actions/{action['id']}", headers=manager)
    ).json()
    assert unknown["status"] == "unknown" and unknown["error_type"] == "timeout"
    assert await tool_outbox_worker.run_once(outbox_session_factory) == 1
    final = (await client.get(f"/tool-actions/{action['id']}", headers=manager)).json()
    assert final["status"] == "succeeded"
    assert calls == ["/v1/refund-requests", "/v1/refund-requests/reconcile"]
    assert (await app.state.store.reconcile(action["idempotency_key"]))["found"] is True
    async with app.state.store.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(OMSRefund)) == 1
    await circuit_breakers.clear()
