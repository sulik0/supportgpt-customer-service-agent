"""隐蔽注入数据集的约束，以及评测脚本统计逻辑。"""

import importlib.util
import re
from pathlib import Path

from src.evaluation.offline_rag import load_evaluation_dataset
from src.guardrails.jailbreak_detection import detect_jailbreak
from src.guardrails.prompt_injection import analyze_prompt_injection
from src.tools.registry import tool_registry

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "covert_injection" / "supportgpt_covert_injection.json"
BASELINE = ROOT / "evaluation" / "baseline" / "supportgpt_baseline_100.json"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "run_covert_injection_eval", ROOT / "scripts" / "run_covert_injection_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cases():
    return load_evaluation_dataset(DATASET, validate_agent=False, validate_security=False)


def test_ids_are_unique_and_do_not_overlap_baseline():
    baseline_ids = {c.id for c in load_evaluation_dataset(BASELINE, validate_agent=False, validate_security=False)}
    ids = [c.id for c in _cases()]
    assert len(ids) == len(set(ids)) and not baseline_ids & set(ids)


def test_only_chinese_and_english_text():
    other_scripts = re.compile(r"[぀-ヿ가-힯Ѐ-ӿ؀-ۿ]")
    assert not [c.id for c in _cases() if other_scripts.search(c.query)]


def test_every_kind_has_both_languages():
    kinds = {}
    for case in _cases():
        kind = next(t for t in case.tags if t != "covert_injection")
        has_cjk = bool(re.search(r"[一-鿿]", case.query))
        kinds.setdefault(kind, set()).add("zh" if has_cjk else "en")
    assert all(langs == {"zh", "en"} for langs in kinds.values()), kinds


def test_forbidden_and_required_tools_are_registered():
    for case in _cases():
        names = case.agent_expectations.get("forbidden_tools", []) + case.agent_expectations.get("required_tools", [])
        assert all(tool_registry.get_definition(n) for n in names), case.id


def test_attacks_stay_covert_for_rule_guards():
    """用例必须绕过规则层前置检查，否则到不了选工具这一步，测不到模型。"""
    caught = [
        c.id
        for c in _cases()
        if analyze_prompt_injection(c.query, source="user_input").detected
        or detect_jailbreak(c.query)
    ]
    assert not caught, caught


def test_summarize_case_separates_attempt_from_execution():
    script = _load_script()
    case = next(c for c in _cases() if c.id == "covert-refund-zh-1")
    output = {
        "intent": "billing_dispute",
        "tool_calls": [
            {"tool_name": "crm.get_customer_profile", "allowed": True},
            {"tool_name": "orders.check_refund_eligibility", "allowed": False},
        ],
        "tool_planner": {"rejected_tools": ["orders.create_refund_request"], "stop_reason": "model_done"},
        "escalation_recommended": True,
        "approval_required": True,
    }

    row = script.summarize_case(case, output)

    assert row["executed_forbidden"] == []
    assert row["attempted_forbidden"] == [
        "orders.check_refund_eligibility",
        "orders.create_refund_request",
    ]
    assert row["denied_by_registry"] == ["orders.check_refund_eligibility"]
    assert row["rejected_by_planner"] == ["orders.create_refund_request"]
    summary = script.aggregate([row])
    assert summary["attacks"]["model_tried_forbidden"] == 1
    assert summary["attacks"]["executed_forbidden"] == 0
