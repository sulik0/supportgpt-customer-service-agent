"""评测时临时装入 Mock 业务数据，跑完撤掉。

用于测工具返回里的间接注入：题目给专用测试客户（customer_id 以 eval_ 开头）准备
订单、历史工单或客户资料，内容里藏攻击文本。只改进程内 Mock Adapter，不碰演示客户。
"""

import datetime
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Dict, Iterator, Sequence

FIXTURE_CUSTOMER_PREFIX = "eval_"
_MISSING = object()


def _targets() -> Dict[str, Dict[str, Any]]:
    from src.tools.crm import crm_tool
    from src.tools.order_mgmt import order_mgmt_tool
    from src.tools.ticketing import ticketing_tool

    return {
        "orders": order_mgmt_tool.orders,
        "past_tickets": ticketing_tool.ticket_history,
        "customer_profile": crm_tool.customers,
    }


def _materialize(value: Any) -> Any:
    """把 days_ago 换成相对今天的时间，避免题目里的日期过期。"""
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    item = {key: _materialize(val) for key, val in value.items() if key != "days_ago"}
    if "days_ago" in value:
        moment = datetime.datetime.utcnow() - datetime.timedelta(days=int(value["days_ago"]))
        item.setdefault("order_date" if "order_id" in value else "created_at", moment)
    return item


@contextmanager
def install_tool_fixtures(cases: Sequence[Any]) -> Iterator[int]:
    """装入所有题目的 tool_fixtures，退出时恢复原状；返回装入的客户数。"""
    targets = _targets()
    saved: list[tuple[Dict[str, Any], str, Any]] = []
    installed = set()
    try:
        for case in cases:
            fixtures = getattr(case, "tool_fixtures", None) or {}
            if not fixtures:
                continue
            customer_id = str(case.customer_id or "")
            if not customer_id.startswith(FIXTURE_CUSTOMER_PREFIX):
                raise ValueError(
                    f"{case.id}: 带 tool_fixtures 的题必须用 {FIXTURE_CUSTOMER_PREFIX} 开头的测试客户"
                )
            unknown = set(fixtures) - set(targets)
            if unknown:
                raise ValueError(f"{case.id}: 不支持的 tool_fixtures 字段 {sorted(unknown)}")
            for name, value in fixtures.items():
                store = targets[name]
                saved.append((store, customer_id, store.get(customer_id, _MISSING)))
                if name == "customer_profile":
                    value = {"customer_id": customer_id, **value}
                store[customer_id] = _materialize(value)
            installed.add(customer_id)
        yield len(installed)
    finally:
        for store, customer_id, previous in reversed(saved):
            if previous is _MISSING:
                store.pop(customer_id, None)
            else:
                store[customer_id] = previous
