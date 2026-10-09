from dataclasses import replace
from types import SimpleNamespace
import asyncio
import datetime
import time

import pytest
from sqlalchemy import func, select

from src.config import settings
from src.models.db_models import (
    Ticket,
    ToolAction,
    ToolActionControl,
    ToolInvocationAudit,
    ToolOutboxEvent,
    ToolActionReview,
    ToolBusinessRequest,
)
from src.tools.outbox import (
    OutboxStatus,
    OutboxLeaseLost,
    ToolOutboxWorker,
    tool_outbox_worker,
)
from src.tools.governance import tool_governance_service
from src.tools.payload_security import tool_payload_security
from src.tools.refund_gateway import refund_gateway
from src.tools.registry import tool_registry


@pytest.fixture(autouse=True)
async def isolated_tool_circuits():
    """故障注入产生的熔断状态不能影响下一条独立测试。"""
    from src.resilience.circuit_breaker import circuit_breakers

    await circuit_breakers.clear()
    yield
    await circuit_breakers.clear()


async def _register_and_login(client, username: str, role: str) -> dict[str, str]:
    register = await client.post(
        "/auth/register",
        json={"username": username, "password": "test-password", "role": role},
    )
    assert register.status_code == 201
    login = await client.post(
        "/auth/token",
        json={"username": username, "password": "test-password"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _ticket(db_session, customer_id: str = "cust_101") -> Ticket:
    ticket = Ticket(
        customer_id=customer_id,
        subject="Refund request",
        description="Please refund order ORD-7001.",
        status="open",
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)
    return ticket


@pytest.mark.asyncio
async def test_high_risk_action_persists_approval_execution_and_audit(
    client, db_session, agent_headers, outbox_session_factory
):
    manager_headers = await _register_and_login(client, "governance_manager", "manager")
    ticket = await _ticket(db_session)
    payload = {
        "customer_id": "cust_101",
        "order_id": "ORD-7001",
        "reason": "Duplicate charge",
    }

    proposed = await client.post(
        "/tool-actions",
        headers=agent_headers,
        json={
            "ticket_id": ticket.id,
            "tool_name": "orders.create_refund_request",
            "payload": payload,
            "intent": "billing_dispute",
        },
    )
    assert proposed.status_code == 201, proposed.text
    proposed_body = proposed.json()
    assert proposed_body["status"] == "pending_approval"
    assert proposed_body["version"] == 2
    assert proposed_body["idempotency_key"].startswith("supportgpt:")
    assert len(proposed_body["policy_hash"]) == 64
    assert len(proposed_body["events"]) == 2
    assert proposed_body["payload_summary"]["customer_id"] == "[FILTERED]"
    assert proposed_body["payload_summary"]["order_id"] == "[FILTERED]"
    assert proposed_body["payload_summary"]["reason"] == "[FILTERED]"

    stored = await db_session.get(ToolAction, proposed_body["id"])
    assert stored is not None
    assert "cust_101" not in stored.payload_encrypted
    assert "ORD-7001" not in stored.payload_encrypted
    assert tool_payload_security.decrypt(stored.payload_encrypted) == payload

    approved = await client.post(
        f"/tool-actions/{stored.id}/decision",
        headers=manager_headers,
        json={
            "decision": "approved",
            "expected_version": 2,
            "comment": "Policy checked",
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["version"] == 3

    executed = await client.post(
        f"/tool-actions/{stored.id}/execute",
        headers=manager_headers,
        json={"expected_version": 3},
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "queued"
    assert executed.json()["version"] == 4
    assert await tool_outbox_worker.run_once(outbox_session_factory) == 1

    completed = await client.get(f"/tool-actions/{stored.id}", headers=manager_headers)
    executed_body = completed.json()
    assert executed_body["status"] == "succeeded"
    assert executed_body["version"] == 6
    assert len(executed_body["events"]) == 6
    assert all(event["request_id"] for event in executed_body["events"])
    assert executed_body["result_summary"]["status"] == "submitted"
    assert executed_body["result_summary"]["message"] == "[FILTERED]"

    audits = await client.get(
        "/tool-audits", headers=manager_headers, params={"action_id": stored.id}
    )
    assert audits.status_code == 200
    audit_body = audits.json()
    assert audit_body["total"] == 1
    assert audit_body["items"][0]["status"] == "success"
    assert audit_body["items"][0]["tool_action_id"] == stored.id
    assert audit_body["items"][0]["policy_version"] == "tool-policy-v2.2-test"
    assert "payload_hash" not in audit_body["items"][0]


@pytest.mark.asyncio
async def test_action_blocks_self_approval_stale_version_and_cross_customer(
    client, db_session
):
    manager_headers = await _register_and_login(client, "self_manager", "manager")
    ticket = await _ticket(db_session)
    request = {
        "ticket_id": ticket.id,
        "tool_name": "orders.create_refund_request",
        "payload": {
            "customer_id": "cust_101",
            "order_id": "ORD-7001",
            "reason": "Duplicate charge",
        },
        "intent": "billing_dispute",
    }
    proposed = await client.post("/tool-actions", headers=manager_headers, json=request)
    assert proposed.status_code == 201
    action_id = proposed.json()["id"]

    self_approval = await client.post(
        f"/tool-actions/{action_id}/decision",
        headers=manager_headers,
        json={"decision": "approved", "expected_version": 2},
    )
    assert self_approval.status_code == 409

    stale = await client.post(
        f"/tool-actions/{action_id}/decision",
        headers=manager_headers,
        json={"decision": "rejected", "expected_version": 1},
    )
    assert stale.status_code == 409

    request["payload"]["customer_id"] = "cust_999"
    cross_customer = await client.post(
        "/tool-actions", headers=manager_headers, json=request
    )
    assert cross_customer.status_code == 403


@pytest.mark.asyncio
async def test_pending_action_and_direct_registry_call_cannot_execute(
    client, db_session, agent_headers
):
    manager_headers = await _register_and_login(client, "execution_manager", "manager")
    ticket = await _ticket(db_session)
    proposed = await client.post(
        "/tool-actions",
        headers=agent_headers,
        json={
            "ticket_id": ticket.id,
            "tool_name": "orders.create_refund_request",
            "payload": {
                "customer_id": "cust_101",
                "order_id": "ORD-7001",
                "reason": "Duplicate charge",
            },
            "intent": "billing_dispute",
        },
    )
    action_id = proposed.json()["id"]
    premature = await client.post(
        f"/tool-actions/{action_id}/execute",
        headers=manager_headers,
        json={"expected_version": 2},
    )
    assert premature.status_code == 409

    direct = await tool_registry.call_tool(
        "orders.create_refund_request",
        {
            "customer_id": "cust_101",
            "order_id": "ORD-7001",
            "reason": "Duplicate charge",
        },
        role="manager",
        ticket_id=ticket.id,
        intent="billing_dispute",
        request_risk_level="high",
    )
    assert direct["allowed"] is False
    assert direct["status"] == "approval_required"


@pytest.mark.asyncio
async def test_agent_cannot_list_governance_records(client, db_session, agent_headers):
    assert (await client.get("/tool-actions", headers=agent_headers)).status_code == 403
    assert (await client.get("/tool-audits", headers=agent_headers)).status_code == 403
    assert (await client.get("/tool-outbox", headers=agent_headers)).status_code == 403

    count = await db_session.execute(select(func.count(ToolInvocationAudit.id)))
    assert count.scalar_one() == 0


@pytest.mark.asyncio
async def test_ticket_workflow_batch_persists_parallel_tool_audits(
    client, db_session, agent_headers
):
    response = await client.post(
        "/tickets",
        headers=agent_headers,
        json={
            "customer_id": "cust_101",
            "subject": "Order status",
            "description": "Where is order ORD-7001?",
            "kb_version": "v1",
        },
    )
    assert response.status_code == 201, response.text

    records = list(
        (
            await db_session.execute(
                select(ToolInvocationAudit).order_by(ToolInvocationAudit.tool_name)
            )
        )
        .scalars()
        .all()
    )
    assert {record.tool_name for record in records} == {
        "crm.get_customer_profile",
        "orders.get_order_history",
        "tickets.get_past_tickets",
        "shipping.get_shipments",
    }
    assert all(record.request_id != "unbound" for record in records)
    assert all(record.payload_keys == ["customer_id"] for record in records)


@pytest.mark.asyncio
async def test_uncertain_write_failure_enters_unknown_without_retry(
    client, db_session, agent_headers, monkeypatch, outbox_session_factory
):
    manager_headers = await _register_and_login(client, "unknown_manager", "manager")
    ticket = await _ticket(db_session)
    proposed = await client.post(
        "/tool-actions",
        headers=agent_headers,
        json={
            "ticket_id": ticket.id,
            "tool_name": "orders.create_refund_request",
            "payload": {
                "customer_id": "cust_101",
                "order_id": "ORD-7001",
                "reason": "Duplicate charge",
            },
            "intent": "billing_dispute",
        },
    )
    action_id = proposed.json()["id"]
    approved = await client.post(
        f"/tool-actions/{action_id}/decision",
        headers=manager_headers,
        json={"decision": "approved", "expected_version": 2},
    )
    assert approved.status_code == 200

    calls = 0

    def uncertain_handler(**kwargs):
        nonlocal calls
        calls += 1
        # 模拟 OMS 已落账，但客户端在收到响应前超时。
        refund_gateway.create_refund_request(**kwargs)
        raise TimeoutError("mock downstream timeout")

    definition = tool_registry.get_definition("orders.create_refund_request")
    assert definition is not None
    monkeypatch.setitem(
        tool_registry._tools,
        definition.name,
        replace(definition, handler=uncertain_handler),
    )
    executed = await client.post(
        f"/tool-actions/{action_id}/execute",
        headers=manager_headers,
        json={"expected_version": 3},
    )

    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "queued"
    assert await tool_outbox_worker.run_once(outbox_session_factory) == 1
    unknown = await client.get(f"/tool-actions/{action_id}", headers=manager_headers)
    assert unknown.json()["status"] == "unknown"
    assert unknown.json()["error_type"] == "timeout"
    assert calls == 1
    # 对账只查询相同幂等键的权威结果，不会再次调用退款写 Handler。
    assert await tool_outbox_worker.run_once(outbox_session_factory) == 1
    reconciled = await client.get(f"/tool-actions/{action_id}", headers=manager_headers)
    assert reconciled.json()["status"] == "succeeded"
    assert calls == 1
    audit = (
        await db_session.execute(
            select(ToolInvocationAudit).where(
                ToolInvocationAudit.tool_action_id == action_id
            )
        )
    ).scalar_one()
    assert audit.status == "timeout"
    assert audit.attempts == 1


@pytest.mark.asyncio
async def test_reconciliation_exhaustion_enters_dlq_and_requires_manual_review(
    client,
    db_session,
    agent_headers,
    monkeypatch,
    outbox_session_factory,
):
    manager_headers = await _register_and_login(client, "dlq_manager", "manager")
    ticket = await _ticket(db_session)
    proposed = await client.post(
        "/tool-actions",
        headers=agent_headers,
        json={
            "ticket_id": ticket.id,
            "tool_name": "orders.create_refund_request",
            "payload": {
                "customer_id": "cust_101",
                "order_id": "ORD-7001",
                "reason": "Duplicate charge",
            },
            "intent": "billing_dispute",
        },
    )
    action_id = proposed.json()["id"]
    await client.post(
        f"/tool-actions/{action_id}/decision",
        headers=manager_headers,
        json={"decision": "approved", "expected_version": 2},
    )
    calls = 0

    def timeout_without_side_effect(**_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("OMS did not persist this request")

    definition = tool_registry.get_definition("orders.create_refund_request")
    assert definition is not None
    monkeypatch.setitem(
        tool_registry._tools,
        definition.name,
        replace(definition, handler=timeout_without_side_effect),
    )
    monkeypatch.setattr(settings, "TOOL_OUTBOX_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "TOOL_OUTBOX_RETRY_BASE_SECONDS", 0.0)
    monkeypatch.setattr(settings, "TOOL_OUTBOX_RETRY_MAX_SECONDS", 0.0)
    await client.post(
        f"/tool-actions/{action_id}/execute",
        headers=manager_headers,
        json={"expected_version": 3},
    )

    await tool_outbox_worker.run_once(outbox_session_factory)
    await tool_outbox_worker.run_once(outbox_session_factory)
    await tool_outbox_worker.run_once(outbox_session_factory)

    dlq = await client.get(
        "/tool-outbox",
        headers=manager_headers,
        params={"status": OutboxStatus.DEAD_LETTER, "action_id": action_id},
    )
    assert dlq.status_code == 200
    assert dlq.json()["total"] == 1
    assert dlq.json()["items"][0]["event_type"] == "reconcile"
    action = await client.get(f"/tool-actions/{action_id}", headers=manager_headers)
    assert action.json()["status"] == "unknown"
    assert action.json()["events"][-1]["action"] == "dead_letter"
    assert calls == 1
    replayed = await client.post(
        f"/tool-outbox/{dlq.json()['items'][0]['id']}/retry",
        headers=manager_headers,
    )
    assert replayed.status_code == 200
    assert replayed.json()["status"] == OutboxStatus.RETRY
    retried_action = await client.get(
        f"/tool-actions/{action_id}", headers=manager_headers
    )
    assert retried_action.json()["events"][-1]["action"] == "retry_dead_letter"


@pytest.mark.asyncio
async def test_successful_action_can_be_compensated_asynchronously(
    client, db_session, agent_headers, outbox_session_factory
):
    manager_headers = await _register_and_login(
        client, "compensation_manager", "manager"
    )
    ticket = await _ticket(db_session)
    proposed = await client.post(
        "/tool-actions",
        headers=agent_headers,
        json={
            "ticket_id": ticket.id,
            "tool_name": "orders.create_refund_request",
            "payload": {
                "customer_id": "cust_101",
                "order_id": "ORD-7001",
                "reason": "Duplicate charge",
            },
            "intent": "billing_dispute",
        },
    )
    action_id = proposed.json()["id"]
    await client.post(
        f"/tool-actions/{action_id}/decision",
        headers=manager_headers,
        json={"decision": "approved", "expected_version": 2},
    )
    await client.post(
        f"/tool-actions/{action_id}/execute",
        headers=manager_headers,
        json={"expected_version": 3},
    )
    await tool_outbox_worker.run_once(outbox_session_factory)

    requested = await client.post(
        f"/tool-actions/{action_id}/compensate",
        headers=manager_headers,
        json={"expected_version": 6, "reason": "Order dispute was withdrawn"},
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["status"] == "compensation_pending"
    await tool_outbox_worker.run_once(outbox_session_factory)
    compensated = await client.get(
        f"/tool-actions/{action_id}", headers=manager_headers
    )
    assert compensated.json()["status"] == "compensated"
    assert compensated.json()["version"] == 9


@pytest.mark.asyncio
async def test_policy_snapshot_replay_detects_tampering(
    client, db_session, agent_headers
):
    manager_headers = await _register_and_login(client, "replay_manager", "manager")
    ticket = await _ticket(db_session)
    proposed = await client.post(
        "/tool-actions",
        headers=agent_headers,
        json={
            "ticket_id": ticket.id,
            "tool_name": "orders.create_refund_request",
            "payload": {
                "customer_id": "cust_101",
                "order_id": "ORD-7001",
                "reason": "Duplicate charge",
            },
            "intent": "billing_dispute",
        },
    )
    action_id = proposed.json()["id"]
    replay = await client.get(
        f"/tool-actions/{action_id}/policy-replay", headers=manager_headers
    )
    assert replay.status_code == 200
    assert replay.json()["passed"] is True

    control = await db_session.get(ToolActionControl, action_id)
    control.policy_hash = "0" * 64
    await db_session.commit()
    tampered = await client.get(
        f"/tool-actions/{action_id}/policy-replay", headers=manager_headers
    )
    assert tampered.json()["passed"] is False
    assert "policy_hash_valid" in tampered.json()["violations"]


@pytest.mark.asyncio
async def test_outbox_lease_allows_only_one_worker_to_claim_event(
    client, db_session, agent_headers, outbox_session_factory
):
    manager_headers = await _register_and_login(client, "lease_manager", "manager")
    ticket = await _ticket(db_session)
    proposed = await client.post(
        "/tool-actions",
        headers=agent_headers,
        json={
            "ticket_id": ticket.id,
            "tool_name": "orders.create_refund_request",
            "payload": {
                "customer_id": "cust_101",
                "order_id": "ORD-7001",
                "reason": "Duplicate charge",
            },
            "intent": "billing_dispute",
        },
    )
    action_id = proposed.json()["id"]
    await client.post(
        f"/tool-actions/{action_id}/decision",
        headers=manager_headers,
        json={"decision": "approved", "expected_version": 2},
    )
    await client.post(
        f"/tool-actions/{action_id}/execute",
        headers=manager_headers,
        json={"expected_version": 3},
    )
    worker_a = ToolOutboxWorker()
    worker_b = ToolOutboxWorker()

    claimed_a = await worker_a._claim_batch(outbox_session_factory)
    claimed_b = await worker_b._claim_batch(outbox_session_factory)

    assert len(claimed_a) == 1
    assert claimed_b == []
    await worker_a._process_one(outbox_session_factory, claimed_a[0])
    completed = await client.get(f"/tool-actions/{action_id}", headers=manager_headers)
    assert completed.json()["status"] == "succeeded"


def test_tool_payload_integrity_rejects_ciphertext_tampering():
    encrypted = tool_payload_security.encrypt({"customer_id": "cust_101"})
    tampered = encrypted[:-2] + ("AA" if encrypted[-2:] != "AA" else "BB")

    with pytest.raises(ValueError):
        tool_payload_security.decrypt(tampered)


def test_mock_oms_returns_same_result_for_same_business_idempotency_key():
    payload = {
        "customer_id": "cust_101",
        "order_id": "ORD-7001",
        "reason": "Duplicate charge",
        "idempotency_key": "refund-action-idempotency-001",
    }

    first = refund_gateway.create_refund_request(**payload)
    second = refund_gateway.create_refund_request(**payload)

    assert second == first
    assert (
        refund_gateway.reconcile(idempotency_key=payload["idempotency_key"])["result"]
        == first
    )


async def _queued_action(client, db_session, agent_headers, name):
    manager = await _register_and_login(client, name, "manager")
    ticket = await _ticket(db_session)
    request = {
        "ticket_id": ticket.id,
        "tool_name": "orders.create_refund_request",
        "intent": "billing_dispute",
        "payload": {
            "customer_id": ticket.customer_id,
            "order_id": "ORD-7001",
            "reason": "Duplicate charge",
        },
    }
    proposed = await client.post("/tool-actions", headers=agent_headers, json=request)
    assert proposed.status_code == 201, proposed.text
    action_id = proposed.json()["id"]
    assert (
        await client.post(
            f"/tool-actions/{action_id}/decision",
            headers=manager,
            json={"decision": "approved", "expected_version": 2},
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/tool-actions/{action_id}/execute",
            headers=manager,
            json={"expected_version": 3},
        )
    ).status_code == 200
    return ticket, action_id, manager, request


@pytest.mark.asyncio
async def test_business_request_deduplicates_across_tickets_and_blocks_parameter_change(
    client, db_session, agent_headers
):
    ticket, action_id, manager, request = await _queued_action(
        client, db_session, agent_headers, "dedupe_manager"
    )
    another = await _ticket(db_session)
    request["ticket_id"] = another.id
    repeated = await client.post("/tool-actions", headers=agent_headers, json=request)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == action_id
    assert repeated.json()["ticket_id"] == ticket.id
    assert (
        await db_session.execute(select(func.count(ToolAction.id)))
    ).scalar_one() == 1
    assert (
        await db_session.execute(select(func.count(ToolBusinessRequest.business_key)))
    ).scalar_one() == 1
    request["payload"]["reason"] = "Different request"
    conflict = await client.post("/tool-actions", headers=agent_headers, json=request)
    assert conflict.status_code == 409
    request["payload"]["order_id"] = " ord-7001 "
    assert (
        await client.post("/tool-actions", headers=agent_headers, json=request)
    ).status_code == 409
    assert (
        await db_session.execute(select(func.count(ToolAction.id)))
    ).scalar_one() == 1


@pytest.mark.asyncio
async def test_existing_legacy_action_is_not_duplicated(
    client, db_session, agent_headers
):
    _, action_id, _, request = await _queued_action(
        client, db_session, agent_headers, "legacy_manager"
    )
    from sqlalchemy import delete

    await db_session.execute(delete(ToolBusinessRequest))
    await db_session.commit()
    repeated = await client.post("/tool-actions", headers=agent_headers, json=request)
    assert repeated.json()["id"] == action_id
    assert (
        await db_session.execute(select(func.count(ToolAction.id)))
    ).scalar_one() == 1


@pytest.mark.asyncio
async def test_expired_worker_cannot_execute_or_overwrite_new_lease(
    client, db_session, agent_headers, outbox_session_factory
):
    _, action_id, _, _ = await _queued_action(
        client, db_session, agent_headers, "fence_manager"
    )
    first, second = ToolOutboxWorker(), ToolOutboxWorker()
    event_id = (await first._claim_batch(outbox_session_factory))[0]
    event = await db_session.get(ToolOutboxEvent, event_id, populate_existing=True)
    old_token = event.version
    snapshot = SimpleNamespace(
        id=event.id, version=old_token, lease_owner=event.lease_owner
    )
    event.lease_expires_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
    await db_session.commit()
    assert await second._claim_batch(outbox_session_factory) == [event_id]
    async with outbox_session_factory() as db:
        with pytest.raises(OutboxLeaseLost):
            await tool_governance_service._dispatch_execution(db, snapshot)
    async with outbox_session_factory() as db:
        await first._mark_succeeded(db, event_id, old_token)
    await first._mark_failed(
        outbox_session_factory, event_id, RuntimeError(), old_token
    )
    assert not await first.renew_lease(outbox_session_factory, event_id, old_token)
    await db_session.refresh(event)
    assert event.status == OutboxStatus.PROCESSING
    assert event.lease_owner == second.worker_id
    await second._process_one(outbox_session_factory, event_id)
    assert (
        await client.get(f"/tool-actions/{action_id}", headers=agent_headers)
    ).json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_heartbeat_keeps_slow_handler_owned(monkeypatch, tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from src.database import Base
    from src.models.db_models import User

    # 独立文件数据库允许多个真实连接；不能用共享单连接的内存 SQLite 演练续租。
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'heartbeat.sqlite'}")
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        proposer = User(username="proposer", hashed_password="unused", role="agent")
        manager = User(username="manager", hashed_password="unused", role="manager")
        ticket = Ticket(
            customer_id="cust_101",
            subject="Refund",
            description="Refund",
            status="open",
            priority="high",
        )
        db.add_all([proposer, manager, ticket])
        await db.flush()
        action = await tool_governance_service.propose(
            db,
            ticket_id=ticket.id,
            tool_name="orders.create_refund_request",
            intent="billing_dispute",
            payload={
                "customer_id": "cust_101",
                "order_id": "ORD-7001",
                "reason": "Duplicate charge",
            },
            proposer=proposer,
        )
        await tool_governance_service.decide(
            db,
            action_id=action.id,
            decision="approved",
            expected_version=2,
            reviewer=manager,
        )
        await tool_governance_service.execute(
            db, action_id=action.id, expected_version=3, executor=manager
        )
        action_id = action.id
        await db.commit()
    definition = tool_registry.get_definition("orders.create_refund_request")

    def slow_handler(**kwargs):
        time.sleep(0.7)
        return refund_gateway.create_refund_request(**kwargs)

    monkeypatch.setitem(
        tool_registry._tools, definition.name, replace(definition, handler=slow_handler)
    )
    monkeypatch.setattr(settings, "TOOL_OUTBOX_LEASE_SECONDS", 0.3)
    first, second = ToolOutboxWorker(), ToolOutboxWorker()
    try:
        task = asyncio.create_task(first.run_once(factory))
        await asyncio.sleep(0.5)
        assert await second._claim_batch(factory) == []
        await task
        async with factory() as db:
            assert (await db.get(ToolAction, action_id)).status == "succeeded"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["succeeded", "failed"])
async def test_unknown_manual_confirmation_links_ticket_and_never_rewrites(
    client, db_session, agent_headers, outbox_session_factory, monkeypatch, outcome
):
    ticket, action_id, manager, _ = await _queued_action(
        client, db_session, agent_headers, "confirmation_manager"
    )
    definition = tool_registry.get_definition("orders.create_refund_request")
    calls = 0

    def uncertain(**kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError()

    monkeypatch.setitem(
        tool_registry._tools, definition.name, replace(definition, handler=uncertain)
    )
    await tool_outbox_worker.run_once(outbox_session_factory)
    action = (await client.get(f"/tool-actions/{action_id}", headers=manager)).json()
    queue = (await client.get("/staff/review-queue", headers=agent_headers)).json()
    assert any(row["id"] == ticket.id and row["requires_tool_review"] for row in queue)
    reviews = await client.get(
        f"/tickets/{ticket.id}/tool-reviews", headers=agent_headers
    )
    assert reviews.json()[0]["reason"] == "unknown"
    payload = {
        "expected_version": action["version"],
        "outcome": outcome,
        "evidence_reference": "OMS-VERIFICATION-1",
        "note": "Confirmed in the order system",
    }
    invalid = {**payload, "evidence_reference": "   "}
    assert (
        await client.post(
            f"/tool-actions/{action_id}/resolve", headers=manager, json=invalid
        )
    ).status_code == 422
    stale = {**payload, "expected_version": 1}
    assert (
        await client.post(
            f"/tool-actions/{action_id}/resolve", headers=manager, json=stale
        )
    ).status_code == 409
    assert (
        await client.post(
            f"/tool-actions/{action_id}/resolve", headers=agent_headers, json=payload
        )
    ).status_code == 403
    resolved = await client.post(
        f"/tool-actions/{action_id}/resolve", headers=manager, json=payload
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == outcome
    assert resolved.json()["events"][-1]["action"].startswith("manual_")
    assert await tool_outbox_worker.run_once(outbox_session_factory) == 0
    assert calls == 1
    assert not any(
        row["id"] == ticket.id
        for row in (
            await client.get("/staff/review-queue", headers=agent_headers)
        ).json()
    )
    review = await db_session.get(ToolActionReview, action_id, populate_existing=True)
    assert review.status == "resolved"
    assert "OMS-VERIFICATION-1" not in review.evidence_encrypted
    assert "OMS-VERIFICATION-1" not in str(review.evidence_summary)
    assert (
        await client.post(
            f"/tool-actions/{action_id}/resolve", headers=manager, json=payload
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_manual_confirmation_cannot_override_active_worker(
    client, db_session, agent_headers, outbox_session_factory
):
    _, action_id, manager, _ = await _queued_action(
        client, db_session, agent_headers, "active_confirmation_manager"
    )
    await tool_outbox_worker._claim_batch(outbox_session_factory)
    result = await client.post(
        f"/tool-actions/{action_id}/resolve",
        headers=manager,
        json={
            "expected_version": 4,
            "outcome": "succeeded",
            "evidence_reference": "OMS-1",
            "note": "External result verified",
        },
    )
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_dlq_confirmation_preserves_independent_response_approval(
    client, db_session, agent_headers, outbox_session_factory, monkeypatch
):
    from src.approval.workflows import human_it_loop_service
    from src.models.db_models import ResponseApproval

    ticket, action_id, manager, _ = await _queued_action(
        client, db_session, agent_headers, "dlq_confirmation_manager"
    )
    approval = await human_it_loop_service.create_pending_approval(
        db_session, ticket.id, "Draft awaiting human approval"
    )
    approval_id = approval.id
    definition = tool_registry.get_definition("orders.create_refund_request")

    def timeout(**_kwargs):
        raise TimeoutError()

    monkeypatch.setitem(
        tool_registry._tools, definition.name, replace(definition, handler=timeout)
    )
    monkeypatch.setattr(settings, "TOOL_OUTBOX_MAX_ATTEMPTS", 1)
    await tool_outbox_worker.run_once(outbox_session_factory)
    await tool_outbox_worker.run_once(outbox_session_factory)
    action = (await client.get(f"/tool-actions/{action_id}", headers=manager)).json()
    reviews = (
        await client.get(f"/tickets/{ticket.id}/tool-reviews", headers=manager)
    ).json()
    assert reviews[0]["reason"] == "dead_letter"
    confirmed = await client.post(
        f"/tool-actions/{action_id}/resolve",
        headers=manager,
        json={
            "expected_version": action["version"],
            "outcome": "failed",
            "evidence_reference": "OMS-CONFIRMED-FAILURE",
            "note": "Failure confirmed by the order system",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    ticket = await db_session.get(Ticket, ticket.id, populate_existing=True)
    response_approval = await db_session.get(
        ResponseApproval, approval_id, populate_existing=True
    )
    assert ticket.status == "pending_approval"
    assert response_approval.status == "pending"
    queue = (await client.get("/staff/review-queue", headers=manager)).json()
    assert any(
        row["id"] == ticket.id and not row["requires_tool_review"] for row in queue
    )
    assert await tool_outbox_worker.run_once(outbox_session_factory) == 0


@pytest.mark.asyncio
async def test_startup_backfills_legacy_unknown_review_without_changing_ticket(
    client, db_session, agent_headers, monkeypatch, outbox_session_factory
):
    from sqlalchemy import delete

    ticket, action_id, _, _ = await _queued_action(
        client, db_session, agent_headers, "backfill_manager"
    )
    definition = tool_registry.get_definition("orders.create_refund_request")

    def timeout(**_kwargs):
        raise TimeoutError()

    monkeypatch.setitem(
        tool_registry._tools, definition.name, replace(definition, handler=timeout)
    )
    await tool_outbox_worker.run_once(outbox_session_factory)
    await db_session.execute(delete(ToolActionReview))
    await db_session.commit()
    await tool_governance_service.backfill_reviews(db_session)
    await db_session.commit()
    await tool_governance_service.backfill_reviews(db_session)
    await db_session.commit()
    assert (
        await db_session.execute(select(func.count(ToolActionReview.tool_action_id)))
    ).scalar_one() == 1
    assert (
        await db_session.get(ToolAction, action_id, populate_existing=True)
    ).status == "unknown"
    ticket = await db_session.get(Ticket, ticket.id, populate_existing=True)
    assert ticket.status == "open"


@pytest.mark.asyncio
async def test_interrupted_compensation_is_not_replayed_and_can_be_confirmed(
    client, db_session, agent_headers, outbox_session_factory, monkeypatch
):
    from src.tools.action_state_machine import (
        ToolActionCommand,
        tool_action_state_machine,
    )

    _, action_id, manager, _ = await _queued_action(
        client, db_session, agent_headers, "compensation_recovery_manager"
    )
    await tool_outbox_worker.run_once(outbox_session_factory)
    assert (
        await client.post(
            f"/tool-actions/{action_id}/compensate",
            headers=manager,
            json={"expected_version": 6, "reason": "External refund request withdrawn"},
        )
    ).status_code == 200
    interrupted = ToolOutboxWorker()
    event_id = (await interrupted._claim_batch(outbox_session_factory))[0]
    action = await tool_governance_service.get(db_session, action_id)
    tool_action_state_machine.transition(action, ToolActionCommand.START_COMPENSATION)
    event = await db_session.get(ToolOutboxEvent, event_id)
    event.lease_expires_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
    await db_session.commit()
    definition = tool_registry.get_definition(action.tool_name)

    def forbidden_compensation(**_kwargs):
        raise AssertionError("Uncertain compensation must not be executed twice")

    monkeypatch.setitem(
        tool_registry._tools,
        definition.name,
        replace(definition, compensation_handler=forbidden_compensation),
    )
    assert await ToolOutboxWorker().run_once(outbox_session_factory) == 1
    current = (await client.get(f"/tool-actions/{action_id}", headers=manager)).json()
    assert current["status"] == "compensation_unknown"
    resolved = await client.post(
        f"/tool-actions/{action_id}/resolve",
        headers=manager,
        json={
            "expected_version": current["version"],
            "outcome": "compensated",
            "evidence_reference": "OMS-COMPENSATION-1",
            "note": "Compensation confirmed in the external system",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "compensated"
