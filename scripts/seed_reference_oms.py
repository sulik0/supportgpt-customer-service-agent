"""显式导入演示订单；不覆盖已有订单，不接触客服或资金数据库。"""

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def seed():
    """读取现有 Mock 订单，将金额转换为最小货币单位后写入参考 OMS。"""
    from decimal import Decimal
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from src.oms.settings import ReferenceOMSSettings
    from src.oms.store import OMSBase, OMSOrder
    from src.tools.order_mgmt import order_mgmt_tool

    config = ReferenceOMSSettings()
    engine = create_async_engine(config.database_url)
    count = 0
    try:
        async with engine.begin() as connection:
            await connection.run_sync(OMSBase.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db, db.begin():
            for customer, orders in order_mgmt_tool.orders.items():
                for order in orders:
                    if await db.get(OMSOrder, order["order_id"]):
                        continue
                    db.add(
                        OMSOrder(
                            order_id=order["order_id"],
                            customer_id=customer,
                            amount_minor=int(Decimal(str(order["total_amount"])) * 100),
                            currency=order.get("currency", "USD"),
                            refundable=True,
                        )
                    )
                    count += 1
        print(f"参考 OMS 新增 {count} 条虚构订单；未执行真实资金操作。")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-reference-data", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(seed())
