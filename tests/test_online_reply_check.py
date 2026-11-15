"""线上回复质量检查脚本的判定逻辑和用例文件。"""

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "run_online_reply_check", ROOT / "scripts" / "run_online_reply_check.py"
)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def test_good_reply_passes_all_checks():
    case = {"status": ["answered"], "intent": ["billing_dispute"], "tools": ["get_payment_invoices"],
            "language": "zh", "must_include_any": ["INV-"], "must_not_match": ["保修.{0,8}\\d+\\s*个月"]}
    data = {"status": "answered", "handling_reason": None, "response": "发票已开具，发票号 INV-DEMO-1。"}
    insight = {"intent": "billing_dispute", "tool_calls": [{"tool_name": "billing.get_payment_invoices"}]}
    assert check.evaluate_case(case, data, insight) == []


def test_each_kind_of_problem_is_reported():
    case = {"status": ["answered"], "handling_reason": ["preliminary_answer"], "intent": ["billing_dispute"],
            "tools": ["get_payment_invoices"], "language": "zh", "must_include_any": ["INV-"],
            "must_not_match": ["保修.{0,8}\\d+\\s*个月"], "max_chars": 20}
    data = {"status": "pending_human", "handling_reason": "quality_review",
            "response": "Your gateway 保修期为 12 个月 [S1], thanks for waiting so long."}
    failures = check.evaluate_case(case, data, {"intent": "information_request", "tool_calls": []})
    text = " ".join(failures)
    for expected in ("status=", "handling_reason=", "intent=", "没有调用 Tool", "关键内容", "禁止内容", "超过", "引用标记"):
        assert expected in text


def test_language_check_distinguishes_chinese_and_english():
    data = {"status": "answered", "response": "我们主要处理订单问题。"}
    assert "回复不是英文" in check.evaluate_case({"language": "en"}, data, {})
    assert check.evaluate_case({"language": "zh"}, data, {}) == []


def test_case_file_only_has_chinese_or_english_and_valid_patterns():
    dataset = json.loads((ROOT / "evaluation/online_checks/reply_checks.json").read_text(encoding="utf-8"))
    ids = [case["id"] for case in dataset["cases"]]
    assert len(ids) == len(set(ids))
    for case in dataset["cases"]:
        # 只保留中文和英文：不允许其他语言的字母。
        assert not re.search(r"[¿¡áéíóúñüàèçâêôöäßЀ-ӿ぀-ヿ가-힯]", case["message"])
        for pattern in case.get("must_not_match", []):
            re.compile(pattern)


def test_status_note_language_is_checked_too():
    data = {"status": "answered", "response": "We handle orders.", "message": "智能客服已完成处理。"}
    assert "状态说明不是英文" in check.evaluate_case({"language": "en"}, data, {})
    data["message"] = "The assistant has answered your question."
    assert check.evaluate_case({"language": "en"}, data, {}) == []
