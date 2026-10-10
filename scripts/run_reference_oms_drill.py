"""使用隔离 PostgreSQL Schema 验证 OMS 多进程幂等和提交后崩溃。"""

import argparse
import asyncio
import json
import multiprocessing
import os
import socket
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def resources(url, schema, key):
    """每个进程创建自己的连接池，不共享客户端锁或内存账本。"""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from src.oms.store import OMSStore

    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}}
    )
    return engine, OMSStore(async_sessionmaker(engine, expire_on_commit=False), key)


def submit(url, schema, key, idem, order, reason, start, queue, crash=False):
    """可在提交已完成后强制退出，模拟 HTTP 回执未到达调用方。"""

    async def run():
        from src.oms.contracts import RefundInput
        from src.oms.store import OMSConflict

        engine, store = resources(url, schema, key)
        try:
            result = await store.create(
                RefundInput(
                    customer_id="drill-customer", order_id=order, reason=reason
                ),
                idem,
            )
            if crash:
                os._exit(23)
            queue.put({"status": "ok", "result": result})
        except OMSConflict as error:
            queue.put({"status": "conflict", "code": error.code})
        except Exception as error:
            queue.put({"status": "error", "type": type(error).__name__})
        finally:
            await engine.dispose()

    start.wait(20)
    asyncio.run(run())


def serve(url, schema, key, api_key, port):
    """在独立进程启动真实 HTTP 服务，数据库仍限制在本轮 Schema。"""
    import uvicorn
    from src.oms.app import create_oms_app
    from src.oms.settings import ReferenceOMSSettings

    async def run():
        engine, _ = resources(url, schema, key)
        config = ReferenceOMSSettings(
            database_url=url, api_key=api_key, encryption_key=key
        )
        server = uvicorn.Server(
            uvicorn.Config(
                create_oms_app(config, engine),
                host="127.0.0.1",
                port=port,
                log_level="error",
                access_log=False,
            )
        )
        try:
            await server.serve()
        finally:
            await engine.dispose()

    asyncio.run(run())


async def run_drill(url):
    """只删除本轮随机 Schema；不读写既有业务表。"""
    from cryptography.fernet import Fernet
    from sqlalchemy import func, select, text
    from sqlalchemy.ext.asyncio import create_async_engine
    from src.oms.store import OMSBase, OMSOrder, OMSRefund
    from src.oms.contracts import RefundInput

    schema = "oms_drill_" + uuid.uuid4().hex
    key = Fernet.generate_key().decode()
    admin = create_async_engine(url)
    engine = None
    checks = []
    ctx = multiprocessing.get_context("spawn")
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine, store = resources(url, schema, key)
        async with engine.begin() as connection:
            await connection.run_sync(OMSBase.metadata.create_all)
        async with store.sessions() as db, db.begin():
            db.add_all(
                [
                    OMSOrder(
                        order_id=order,
                        customer_id="drill-customer",
                        amount_minor=9900,
                        currency="CNY",
                    )
                    for order in ["ORDER-A", "ORDER-B", "ORDER-C", "ORDER-D"]
                ]
            )

        async def race(specifications):
            event, queue = ctx.Event(), ctx.Queue()
            workers = [
                ctx.Process(
                    target=submit,
                    args=(url, schema, key, idem, order, reason, event, queue),
                )
                for idem, order, reason in specifications
            ]
            for worker in workers:
                worker.start()
            event.set()
            for worker in workers:
                await asyncio.to_thread(worker.join, 30)
                if worker.is_alive():
                    worker.kill()
                    worker.join()
                    raise RuntimeError("OMS drill worker exceeded timeout")
                assert worker.exitcode == 0
            return [queue.get(timeout=5) for _ in workers]

        same = await race([("same-key-001", "ORDER-A", "申请退款")] * 4)
        assert all(item["status"] == "ok" for item in same), same
        assert len({item["result"]["refund_request_id"] for item in same}) == 1
        checks.append({"name": "multi_process_same_key_single_receipt", "passed": True})

        conflict = await race(
            [
                ("conflict-key-001", "ORDER-B", "申请退款"),
                ("conflict-key-001", "ORDER-C", "不同原因"),
            ]
        )
        assert sorted(item["status"] for item in conflict) == [
            "conflict",
            "ok",
        ], conflict
        assert (
            next(item for item in conflict if item["status"] == "conflict")["code"]
            == "idempotency_parameter_conflict"
        )
        checks.append({"name": "multi_process_parameter_conflict", "passed": True})

        duplicate = await race([("different-action-001", "ORDER-A", "申请退款")])
        assert duplicate[0]["code"] == "order_refund_already_requested"
        checks.append({"name": "cross_action_order_dedup", "passed": True})

        event, queue = ctx.Event(), ctx.Queue()
        crashed = ctx.Process(
            target=submit,
            args=(
                url,
                schema,
                key,
                "crash-key-001",
                "ORDER-D",
                "申请退款",
                event,
                queue,
                True,
            ),
        )
        crashed.start()
        event.set()
        await asyncio.to_thread(crashed.join, 30)
        if crashed.is_alive():
            crashed.kill()
            crashed.join()
            raise RuntimeError("OMS crash drill exceeded timeout")
        assert crashed.exitcode == 23
        await engine.dispose()
        engine, store = resources(url, schema, key)
        recovered = await store.reconcile("crash-key-001")
        assert recovered["authoritative"] and recovered["status"] == "succeeded"
        repeated = await store.create(
            RefundInput(
                customer_id="drill-customer", order_id="ORDER-D", reason="申请退款"
            ),
            "crash-key-001",
        )
        assert repeated == recovered["result"]
        async with store.sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(OMSRefund)
                    .where(OMSRefund.order_id == "ORDER-D")
                )
                == 1
            )
        checks.append(
            {
                "name": "commit_then_process_crash_authoritative_reconciliation",
                "passed": True,
            }
        )
        compensation = await store.compensate("crash-key-001", "compensation-key-001")
        await engine.dispose()
        engine, store = resources(url, schema, key)
        assert (
            await store.compensate("crash-key-001", "compensation-key-001")
            == compensation
        )
        checks.append({"name": "compensation_survives_restart", "passed": True})

        import httpx
        from src.tools.oms_gateway import HTTPRefundGateway, OMSRejectedError

        api_key = "isolated-reference-oms-drill-0123456789"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        address = f"http://127.0.0.1:{port}"
        gateway = HTTPRefundGateway(address, api_key)
        expected = same[0]["result"]
        # 连续启动两个服务进程，HTTP Gateway 应始终查询到同一持久化回执。
        for _ in range(2):
            server = ctx.Process(target=serve, args=(url, schema, key, api_key, port))
            server.start()
            try:
                async with httpx.AsyncClient(timeout=0.2, trust_env=False) as client:
                    for attempt in range(200):
                        if server.exitcode is not None:
                            raise RuntimeError(
                                "Reference OMS HTTP process failed to start"
                            )
                        try:
                            if (
                                await client.get(address + "/health")
                            ).status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        await asyncio.sleep(0.05)
                    else:
                        raise RuntimeError("Reference OMS HTTP startup timed out")
                    denied = await client.post(
                        address + "/v1/refund-requests/reconcile",
                        json={"idempotency_key": "same-key-001"},
                    )
                    assert denied.status_code == 401
                replay = await asyncio.to_thread(
                    gateway.create_refund_request,
                    customer_id="drill-customer",
                    order_id="ORDER-A",
                    reason="申请退款",
                    idempotency_key="same-key-001",
                )
                assert replay == expected
                authoritative = await asyncio.to_thread(
                    gateway.reconcile, idempotency_key="same-key-001"
                )
                assert (
                    authoritative["authoritative"]
                    and authoritative["result"] == expected
                )
                try:
                    await asyncio.to_thread(
                        gateway.create_refund_request,
                        customer_id="drill-customer",
                        order_id="ORDER-A",
                        reason="不同参数",
                        idempotency_key="same-key-001",
                    )
                except OMSRejectedError as error:
                    assert error.status_code == 409
                else:
                    raise AssertionError("HTTP parameter conflict was not rejected")
            finally:
                server.terminate()
                await asyncio.to_thread(server.join, 10)
                if server.is_alive():
                    server.kill()
                    server.join()
        checks.append(
            {
                "name": "http_gateway_auth_conflict_and_restart_reconciliation",
                "passed": True,
            }
        )
        return {
            "backend": "PostgreSQL",
            "reference_implementation": True,
            "funds_moved": False,
            "checks": checks,
        }
    finally:
        if engine:
            await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


if __name__ == "__main__":
    from sqlalchemy.engine import make_url

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-isolated-database", action="store_true", required=True
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    url = os.environ.get("TOOL_DRILL_DATABASE_URL", "")
    database = make_url(url)
    if database.drivername != "postgresql+asyncpg" or not any(
        tag in (database.database or "") for tag in ("_test", "_drill")
    ):
        parser.error("A dedicated PostgreSQL *_test or *_drill database is required")
    report = asyncio.run(run_drill(url))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
