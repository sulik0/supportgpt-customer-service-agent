"""在专用 PostgreSQL 测试库中运行多进程 Tool Governance 故障演练。"""

import argparse
import asyncio
import datetime
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def configure(url):
    """演练不继承线上模型、Worker 或观测配置，不发送真实业务请求。"""
    os.environ.update(
        {
            "APP_ENV": "testing",
            "DATABASE_URL": url,
            "LLM_PROVIDER": "mock",
            "OMS_PROVIDER": "mock",
            "DECISION_PROVIDER": "disabled",
            "JEV_API_KEY": "",
            "OTEL_ENABLED": "false",
            "QWEN3_GUARD_ENABLED": "false",
            "TOOL_OUTBOX_WORKER_ENABLED": "false",
            "TOOL_RECONCILIATION_DELAY_SECONDS": "0",
            "TOOL_OUTBOX_LEASE_SECONDS": "5",
            "JWT_SECRET": "isolated-drill-secret-not-for-production-0123456789",
            "TOOL_ACTION_ENCRYPTION_KEY": "",
        }
    )


def database(url, schema):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    engine = create_async_engine(
        url,
        connect_args={"server_settings": {"search_path": schema}},
        pool_size=4,
        max_overflow=2,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def gateway(url, schema, queue=None, mode="normal"):
    """用独立数据库账本模拟 OMS，使外部结果在 Worker 被杀后仍然存在。"""
    import psycopg
    from sqlalchemy.engine import make_url
    from dataclasses import replace
    from src.tools.registry import tool_registry

    sync_url = (
        make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )

    def create(**payload):
        key = payload["idempotency_key"]
        reference = "DRILL-" + uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:12]
        with psycopg.connect(
            sync_url, options=f"-c search_path={schema}"
        ) as connection:
            connection.execute(
                "INSERT INTO drill_calls (idempotency_key) VALUES (%s)", (key,)
            )
            connection.execute(
                "INSERT INTO drill_ledger (idempotency_key, reference) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (key, reference),
            )
        if queue is not None:
            queue.put({"stage": "external_write_committed", "key": key})
        if mode == "slow":
            time.sleep(10)
        elif mode == "crash":
            time.sleep(30)
        return {"refund_request_id": reference, "status": "submitted"}

    def reconcile(*, idempotency_key):
        with psycopg.connect(
            sync_url, options=f"-c search_path={schema}"
        ) as connection:
            row = connection.execute(
                "SELECT reference FROM drill_ledger WHERE idempotency_key=%s",
                (idempotency_key,),
            ).fetchone()
        return (
            {
                "status": "succeeded",
                "result": {"refund_request_id": row[0], "status": "submitted"},
            }
            if row
            else {"status": "pending"}
        )

    definition = tool_registry.get_definition("orders.create_refund_request")
    tool_registry._tools[definition.name] = replace(
        definition, handler=create, reconciliation_handler=reconcile, timeout_seconds=12
    )


def child(url, schema, mode, queue, order_id=None, start_event=None):
    """子进程各自建立连接和 Worker，不共享父进程的内存锁。"""
    configure(url)

    async def run():
        engine, factory = database(url, schema)
        try:
            if start_event is not None:
                await asyncio.to_thread(start_event.wait, 15)
            if mode == "propose":
                from src.models.db_models import User
                from src.tools.governance import tool_governance_service

                async with factory() as db:
                    action = await tool_governance_service.propose(
                        db,
                        ticket_id=1,
                        tool_name="orders.create_refund_request",
                        intent="billing_dispute",
                        payload={
                            "customer_id": "drill_customer",
                            "order_id": order_id,
                            "reason": "Drill refund request",
                        },
                        proposer=await db.get(User, 1),
                    )
                    await db.commit()
                    queue.put({"stage": "proposed", "action_id": action.id})
            else:
                from src.tools.outbox import ToolOutboxWorker

                gateway(url, schema, queue, mode)
                count = await ToolOutboxWorker().run_once(factory)
                queue.put({"stage": "worker_done", "count": count})
        finally:
            await engine.dispose()

    try:
        asyncio.run(run())
    except Exception as exc:
        queue.put({"stage": "error", "type": exc.__class__.__name__})
        raise


async def next_message(queue, expected, timeout=25):
    message = await asyncio.to_thread(queue.get, True, timeout)
    assert message["stage"] == expected, message
    return message


async def join(process, timeout=25):
    await asyncio.to_thread(process.join, timeout)
    assert not process.is_alive(), "Drill child did not finish in time."
    assert process.exitcode == 0, f"Drill child failed: {process.exitcode}"


async def drill(url):
    from sqlalchemy import text, select
    from sqlalchemy.ext.asyncio import create_async_engine
    from src.database import Base
    from src.models.db_models import Ticket, User, ToolAction, ToolOutboxEvent
    from src.tools.governance import tool_governance_service
    from src.tools.outbox import ToolOutboxWorker, OutboxLeaseLost

    schema = "tool_drill_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    engine, factory = database(url, schema)
    context = multiprocessing.get_context("spawn")
    processes = []
    results = []
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "CREATE TABLE drill_ledger (idempotency_key TEXT PRIMARY KEY, reference TEXT NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE drill_calls (id BIGSERIAL PRIMARY KEY, idempotency_key TEXT NOT NULL)"
                )
            )
        async with factory() as db:
            db.add_all(
                [
                    User(
                        id=1,
                        username="drill_agent",
                        hashed_password="unused",
                        role="agent",
                    ),
                    User(
                        id=2,
                        username="drill_manager",
                        hashed_password="unused",
                        role="manager",
                    ),
                    Ticket(
                        id=1,
                        customer_id="drill_customer",
                        subject="Drill",
                        description="Drill",
                        priority="high",
                        status="open",
                    ),
                ]
            )
            await db.commit()

        async def queued(order):
            async with factory() as db:
                action = await tool_governance_service.propose(
                    db,
                    ticket_id=1,
                    tool_name="orders.create_refund_request",
                    intent="billing_dispute",
                    payload={
                        "customer_id": "drill_customer",
                        "order_id": order,
                        "reason": "Drill refund request",
                    },
                    proposer=await db.get(User, 1),
                )
                manager = await db.get(User, 2)
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
                await db.commit()
                return action.id

        # 1. 同时创建同一业务申请，数据库只保留一个 Action。
        print("drill: concurrent_business_deduplication", flush=True)
        queue, barrier = context.Queue(), context.Event()
        for _ in range(2):
            process = context.Process(
                target=child, args=(url, schema, "propose", queue, "DEDUPE-1", barrier)
            )
            processes.append(process)
            process.start()
        barrier.set()
        ids = [(await next_message(queue, "proposed"))["action_id"] for _ in range(2)]
        for process in processes:
            await join(process)
        assert ids[0] == ids[1]
        async with factory() as db:
            assert len((await db.execute(select(ToolAction))).scalars().all()) == 1
        results.append({"case": "concurrent_business_deduplication", "passed": True})

        # 2. 写调用长于租约期限，另一进程仍不能接管已续租事件。
        print("drill: cross_process_heartbeat", flush=True)
        slow_id = await queued("HEARTBEAT-1")
        slow_queue = context.Queue()
        slow = context.Process(target=child, args=(url, schema, "slow", slow_queue))
        processes.append(slow)
        slow.start()
        await next_message(slow_queue, "external_write_committed")
        await asyncio.sleep(5.5)
        rival_queue = context.Queue()
        rival = context.Process(target=child, args=(url, schema, "normal", rival_queue))
        processes.append(rival)
        rival.start()
        assert (await next_message(rival_queue, "worker_done"))["count"] == 0
        await join(rival)
        await next_message(slow_queue, "worker_done")
        await join(slow)
        async with factory() as db:
            assert (await db.get(ToolAction, slow_id)).status == "succeeded"
        results.append({"case": "cross_process_heartbeat", "passed": True})

        # 3. 外部写入已提交后强杀进程，新进程仅对账，不能再写。
        print("drill: kill_after_external_commit", flush=True)
        crash_id = await queued("CRASH-1")
        crash_queue = context.Queue()
        crashed = context.Process(
            target=child, args=(url, schema, "crash", crash_queue)
        )
        processes.append(crashed)
        crashed.start()
        await next_message(crash_queue, "external_write_committed")
        crashed.kill()
        await asyncio.to_thread(crashed.join, 10)
        async with factory() as db:
            event = (
                await db.execute(
                    select(ToolOutboxEvent).where(
                        ToolOutboxEvent.tool_action_id == crash_id
                    )
                )
            ).scalar_one()
            event.lease_expires_at = datetime.datetime.utcnow() - datetime.timedelta(
                seconds=1
            )
            await db.commit()
        for _ in range(2):
            recovery_queue = context.Queue()
            recovery = context.Process(
                target=child, args=(url, schema, "normal", recovery_queue)
            )
            processes.append(recovery)
            recovery.start()
            assert (await next_message(recovery_queue, "worker_done"))["count"] == 1
            await join(recovery)
        async with factory() as db:
            action = await tool_governance_service.get(db, crash_id)
            assert action.status == "succeeded"
            calls = (
                await db.execute(
                    text("SELECT COUNT(*) FROM drill_calls WHERE idempotency_key=:key"),
                    {"key": action.control.idempotency_key},
                )
            ).scalar_one()
            assert calls == 1
        results.append(
            {
                "case": "kill_after_external_commit_reconcile_without_rewrite",
                "passed": True,
            }
        )

        # 4. 旧领取版本不得修改新租约，也不得调用外部 Handler。
        print("drill: stale_worker_fencing", flush=True)
        fence_id = await queued("FENCE-1")
        old, new = ToolOutboxWorker(), ToolOutboxWorker()
        event_id = (await old._claim_batch(factory))[0]
        async with factory() as db:
            event = await db.get(ToolOutboxEvent, event_id)
            token = event.version
            claim = SimpleNamespace(
                id=event.id, version=token, lease_owner=event.lease_owner
            )
            event.lease_expires_at = datetime.datetime.utcnow() - datetime.timedelta(
                seconds=1
            )
            await db.commit()
        assert await new._claim_batch(factory) == [event_id]
        async with factory() as db:
            try:
                await tool_governance_service._dispatch_execution(db, claim)
            except OutboxLeaseLost:
                await db.rollback()
            else:
                raise AssertionError("Stale worker was not fenced.")
            await old._mark_succeeded(db, event_id, token)
        await old._mark_failed(factory, event_id, RuntimeError("old worker"), token)
        assert not await old.renew_lease(factory, event_id, token)
        async with factory() as db:
            event = await db.get(ToolOutboxEvent, event_id)
            assert event.status == "processing" and event.lease_owner == new.worker_id
        gateway(url, schema)
        await new._process_one(factory, event_id)
        async with factory() as db:
            assert (await db.get(ToolAction, fence_id)).status == "succeeded"
        results.append({"case": "stale_worker_fencing", "passed": True})
        return {
            "database": "PostgreSQL",
            "gateway": "persistent simulated OMS, not a real refund system",
            "cases": results,
            "passed": True,
        }
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 5)
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-isolated-database", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    url = os.environ.get("TOOL_DRILL_DATABASE_URL", "")
    from sqlalchemy.engine import make_url

    if not args.confirm_isolated_database or not url:
        parser.error(
            "Set TOOL_DRILL_DATABASE_URL and explicitly confirm an isolated test database."
        )
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg" or not (
        parsed.database or ""
    ).endswith(("_test", "_drill")):
        parser.error(
            "Only postgresql+asyncpg databases ending in _test or _drill are accepted; never use production."
        )
    configure(url)
    try:
        report = asyncio.run(drill(url))
    except Exception as exc:
        # 失败也输出可归档报告；不把数据库 URL 或凭据写入报告。
        report = {
            "database": "PostgreSQL",
            "passed": False,
            "error_type": exc.__class__.__name__,
        }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
