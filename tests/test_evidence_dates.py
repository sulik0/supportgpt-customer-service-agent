"""证据里的日期由代码计算：模型不知道今天，不能轻信用户说的天数。"""

import datetime
import json

from src.agents import evidence
from src.agents.evidence import build_resolution_evidence, compact_tool_context

_TODAY = datetime.date(2026, 11, 17)
_CALLED = {"orders.get_order_history", "crm.get_customer_profile"}


def _freeze_today(monkeypatch):
    monkeypatch.setattr(evidence, "_today", lambda: _TODAY)


def _orders(order_date):
    return {
        "recent_orders": [
            {"order_id": "ORD-12002", "status": "delivered", "order_date": order_date}
        ]
    }


def test_evidence_adds_today_and_days_since_order(monkeypatch):
    _freeze_today(monkeypatch)
    payload = json.loads(
        compact_tool_context(
            _orders(datetime.datetime(2026, 8, 19, 9, 30)), called_tools=_CALLED
        )
    )

    assert payload["today"] == "2026-11-17"
    assert "follow the records" in payload["date_note"]
    order = payload["recent_orders"][0]
    assert order["order_date"] == "2026-08-19"
    assert order["days_since_order"] == 90


def test_evidence_accepts_iso_string_dates(monkeypatch):
    _freeze_today(monkeypatch)
    payload = json.loads(
        compact_tool_context(_orders("2026-10-28T02:00:00Z"), called_tools=_CALLED)
    )

    assert payload["recent_orders"][0]["days_since_order"] == 20


def test_evidence_skips_unparseable_or_future_dates(monkeypatch):
    _freeze_today(monkeypatch)
    for value in ("not-a-date", "2026-12-01", None):
        payload = json.loads(compact_tool_context(_orders(value), called_tools=_CALLED))

        assert "days_since_order" not in payload["recent_orders"][0]
        assert "today" not in payload


def test_evidence_has_no_date_note_when_orders_were_not_queried(monkeypatch):
    _freeze_today(monkeypatch)
    payload = json.loads(
        compact_tool_context(
            _orders(datetime.datetime(2026, 8, 19)),
            called_tools={"crm.get_customer_profile"},
        )
    )

    assert "recent_orders" not in payload
    assert "today" not in payload


def test_date_note_is_dropped_with_orders_under_tight_budget(monkeypatch):
    _freeze_today(monkeypatch)
    context = {
        **_orders(datetime.datetime(2026, 8, 19)),
        "service_query": {
            "tool_name": "billing.get_payment_invoices",
            "status": "success",
            "data": {"records": [{"order_id": "ORD-12002", "note": "x" * 600}]},
        },
    }
    payload = json.loads(compact_tool_context(context, 700, called_tools=_CALLED))

    assert "recent_orders" not in payload
    assert "today" not in payload


def test_resolution_evidence_shared_with_qa_carries_dates(monkeypatch):
    _freeze_today(monkeypatch)
    texts = build_resolution_evidence(
        {
            "tool_context": _orders(datetime.datetime(2026, 8, 19)),
            "tool_calls": [
                {"tool_name": "orders.get_order_history", "status": "success"}
            ],
            "context_citations": [],
        }
    )

    tool = json.loads(texts[0][len("[TOOL] ") :])
    assert tool["today"] == "2026-11-17"
    assert tool["recent_orders"][0]["days_since_order"] == 90
