"""漏调诊断：题集约束和判定逻辑。"""

import importlib.util
import re
from pathlib import Path

from src.evaluation.offline_rag import load_evaluation_dataset

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "tool_coverage" / "supportgpt_tool_coverage.json"
BASELINE = ROOT / "evaluation" / "baseline" / "supportgpt_baseline_100.json"


def _script():
    spec = importlib.util.spec_from_file_location(
        "run_tool_coverage_eval", ROOT / "scripts" / "run_tool_coverage_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cases():
    return load_evaluation_dataset(DATASET, validate_agent=False, validate_security=False)


def test_ids_unique_and_not_in_baseline():
    base = {c.id for c in load_evaluation_dataset(BASELINE, validate_agent=False, validate_security=False)}
    ids = [c.id for c in _cases()]
    assert len(ids) == len(set(ids)) and not base & set(ids)


def test_every_case_has_valid_fact_patterns():
    for case in _cases():
        groups = case.agent_expectations["reply_facts"]
        assert groups and all(group for group in groups), case.id
        for group in groups:
            for pattern in group:
                re.compile(pattern)


def test_order_data_cases_use_real_mock_orders():
    """需要订单数据的题，必须点名一个属于该客户的真实模拟订单。"""
    owners = {"ORD-7001": "cust_101", "ORD-8002": "cust_102", "ORD-9003": "cust_103"}
    for case in _cases():
        if case.agent_expectations["need_order_data"]:
            order = re.search(r"ORD-\d+", case.query).group(0)
            assert owners[order] == case.customer_id, case.id


def test_check_facts_any_of_group_and_all_groups():
    script = _script()
    assert script.check_facts("fee is 5% ($7.50)", [["5%"], ["7\\.50"]]) == []
    assert script.check_facts("fee is 5%", [["5%"], ["7\\.50"]]) == [["7\\.50"]]


def test_verdict_attributes_cause():
    script = _script()

    def row(history, ok, failed=False):
        return {"called_history": history, "facts_ok": ok, "generation_failed": failed}

    assert script.verdict(row(True, True), row(True, True)) == "无漏调"
    assert "过严" in script.verdict(row(False, True), row(True, True))
    assert "模型问题" in script.verdict(row(False, False), row(True, True))
    assert "非漏调" in script.verdict(row(False, False), row(True, False))
    assert "不判断" in script.verdict(row(False, False, True), row(True, True))
