"""公开评测集改编层：范围期望、回复标记串检查和评测时装入的 Mock 数据。"""

from pathlib import Path

import pytest

from src.evaluation.baseline_evaluation import (
    BaselineExpectations,
    evaluate_baseline_behavior,
)
from src.evaluation.manifest import load_manifest, validate_manifest
from src.evaluation.offline_rag import EvaluationCase, load_evaluation_dataset
from src.evaluation.tool_fixtures import install_tool_fixtures
from src.tools.crm import crm_tool
from src.tools.order_mgmt import order_mgmt_tool
from src.tools.ticketing import ticketing_tool

ROOT = Path(__file__).resolve().parents[1]


def _case(customer_id, fixtures):
    return EvaluationCase(
        id="t1",
        query="q",
        reference_answer="r",
        expected_sources=[],
        category="security",
        risk_level="high",
        customer_id=customer_id,
        tool_fixtures=fixtures,
    )


def test_scope_expectation_checked_only_when_set():
    output = {"intent": "information_request", "request_scope": "support"}
    plain = evaluate_baseline_behavior(output, BaselineExpectations.from_mapping({}))
    assert plain.passed and "scope" not in plain.expected

    expect = BaselineExpectations.from_mapping({"expected_scope": "out_of_scope"})
    result = evaluate_baseline_behavior(output, expect)
    assert not result.passed
    assert result.failures == ("scope expected=out_of_scope actual=support",)

    ok = evaluate_baseline_behavior({**output, "request_scope": "out_of_scope"}, expect)
    assert ok.passed


def test_forbidden_reply_markers_case_insensitive():
    expect = BaselineExpectations.from_mapping(
        {"forbidden_reply_markers": ["ATK-COMP-7781"]}
    )
    leaked = evaluate_baseline_behavior(
        {"suggested_response": "您的赔偿码 atk-comp-7781 已生效"}, expect
    )
    assert not leaked.passed
    assert leaked.actual["leaked_reply_markers"] == ["ATK-COMP-7781"]

    clean = evaluate_baseline_behavior({"suggested_response": "订单已发货"}, expect)
    assert clean.passed


def test_tool_fixtures_installed_then_removed():
    fixtures = {
        "orders": [{"order_id": "ORD-EVAL-1", "status": "shipped", "items": ["x"], "days_ago": 3}],
        "past_tickets": [{"ticket_id": 1, "subject": "s", "days_ago": 10}],
        "customer_profile": {"tier": "Standard"},
    }
    with install_tool_fixtures([_case("eval_t1", fixtures)]) as count:
        assert count == 1
        order = order_mgmt_tool.get_order_history("eval_t1")[0]
        assert "days_ago" not in order and order["order_date"] is not None
        assert ticketing_tool.get_past_tickets("eval_t1")[0]["created_at"] is not None
        assert crm_tool.get_customer_profile("eval_t1")["customer_id"] == "eval_t1"
    assert "eval_t1" not in order_mgmt_tool.orders
    assert "eval_t1" not in ticketing_tool.ticket_history
    assert "eval_t1" not in crm_tool.customers


def test_tool_fixtures_refuse_demo_customers():
    before = order_mgmt_tool.get_order_history("cust_101")
    with pytest.raises(ValueError):
        with install_tool_fixtures([_case("cust_101", {"orders": []})]):
            pass
    assert order_mgmt_tool.get_order_history("cust_101") == before


@pytest.mark.parametrize("layer", ["public_scope", "public_tool_injection"])
def test_public_layers_load(layer):
    assert validate_manifest(only=[layer]) == []
    path = ROOT / load_manifest()["layers"][layer]["file"]
    cases = load_evaluation_dataset(path, validate_agent=False, validate_security=False)
    assert cases
    if layer == "public_tool_injection":
        assert all(case.customer_id.startswith("eval_") for case in cases)
        assert all(case.tool_fixtures for case in cases)
