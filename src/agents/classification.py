"""每条消息只判一次：分类结论和处理结果。

依据 docs/10_HANDLING_PRINCIPLES.md（第二版）。Jev 回答事实问题，重要情况由 LLM
复核；规则不参与语义判断。处理结果由本模块的处理表决定，下游只读这份结论。

答案字典（Jev 和 LLM 同一格式，None 表示没把握）：

- support_scope: support / out_of_scope / unclear
- request: action / eligibility / record_lookup / information / unclear
- complaint、account_security、urgent_outage、negative_emotion、
  answerable_question: True / False
- intent: IntentType 的值，只决定 Skill 和查询工具
"""

from __future__ import annotations

from typing import Any, Mapping

from src.models.intents import IntentType, intent_defaults, normalize_intent


AUTO_ANSWER = "auto_answer"
ANSWER_THEN_REVIEW = "answer_then_review"
HUMAN = "human"
CAPABILITY = "capability"

ANSWER_KEYS = (
    "support_scope",
    "request",
    "complaint",
    "account_security",
    "urgent_outage",
    "negative_emotion",
    "answerable_question",
    "intent",
)
SCOPES = frozenset({"support", "out_of_scope", "unclear"})
REQUESTS = frozenset(
    {"action", "eligibility", "record_lookup", "information", "unclear"}
)
FLAG_KEYS = (
    "complaint",
    "account_security",
    "urgent_outage",
    "negative_emotion",
    "answerable_question",
)

# 严格程度：安全拦截在分类之前处理，这里从能力说明到转人工。
_STRICTNESS = {CAPABILITY: 0, AUTO_ANSWER: 1, ANSWER_THEN_REVIEW: 2, HUMAN: 3}

# 涉及钱、订单、账户、设备的类别需要 LLM 复核（第五节）。
REVIEW_CATEGORIES = frozenset(
    {
        IntentType.BILLING_DISPUTE,
        IntentType.ORDER_CANCELLATION,
        IntentType.ACCOUNT_SUPPORT,
        IntentType.WARRANTY_CLAIM,
    }
)

# 查自己的记录时，这条查询必须成功才能回答（第四节）。
RECORD_TOOLS = {
    IntentType.BILLING_DISPUTE: "billing.get_payment_invoices",
    IntentType.WARRANTY_CLAIM: "warranty.get_entitlements",
    IntentType.ORDER_STATUS: "shipping.get_shipments",
    IntentType.ORDER_CANCELLATION: "shipping.get_shipments",
}


def empty_answers() -> dict[str, Any]:
    return {key: None for key in ANSWER_KEYS}


def normalize_answers(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """把模型答案收敛到允许的取值，其余一律记为没把握。"""
    raw = raw or {}
    answers = empty_answers()
    scope = raw.get("support_scope")
    answers["support_scope"] = scope if scope in SCOPES else None
    request = raw.get("request")
    answers["request"] = request if request in REQUESTS else None
    for key in FLAG_KEYS:
        value = raw.get(key)
        answers[key] = value if isinstance(value, bool) else None
    intent = raw.get("intent")
    known = isinstance(intent, IntentType) or str(intent or "") in IntentType.values()
    answers["intent"] = normalize_intent(intent).value if known else None
    return answers


def fill_unsure(primary: Mapping[str, Any], other: Mapping[str, Any]) -> dict[str, Any]:
    """primary 没把握的项用 other 的答案（第五节合并第 1 步）。"""
    return {
        key: primary.get(key) if primary.get(key) is not None else other.get(key)
        for key in ANSWER_KEYS
    }


def decide_handling(a: Mapping[str, Any]) -> tuple[str, int]:
    """处理表（第四节），返回（处理结果, 命中的行号）。"""
    if a.get("support_scope") == "out_of_scope":
        return CAPABILITY, 1
    if a.get("complaint") or a.get("account_security") or a.get("urgent_outage"):
        return HUMAN, 2
    request = a.get("request")
    if request == "action":
        return (ANSWER_THEN_REVIEW if a.get("answerable_question") else HUMAN), 3
    if request == "eligibility":
        return ANSWER_THEN_REVIEW, 4
    if a.get("negative_emotion"):
        return ANSWER_THEN_REVIEW, 5
    if request is None:
        return ANSWER_THEN_REVIEW, 6
    return AUTO_ANSWER, 7


def needs_review(jev: Mapping[str, Any]) -> bool:
    """Jev 的结论没有直接转人工，且涉及敏感类别、范围外或有项没把握（第五节）。"""
    handling, _ = decide_handling(jev)
    if handling == HUMAN:
        return False
    category = jev.get("intent")
    return bool(
        (category is not None and normalize_intent(category) in REVIEW_CATEGORIES)
        or jev.get("support_scope") == "out_of_scope"
        or any(jev.get(key) is None for key in ANSWER_KEYS)
    )


def build_classification(
    *,
    jev: Mapping[str, Any] | None,
    llm: Mapping[str, Any] | None,
    jev_status: str,
    review_status: str,
) -> dict[str, Any]:
    """合并 Jev 和 LLM 的答案并查表。两者都为 None 时调用方应走 fallback。

    jev_status: accepted / unavailable；review_status: not_needed / done /
    unavailable / primary（Jev 不可用时 LLM 是主判断）。
    """
    if jev is not None and llm is not None:
        jev_side = fill_unsure(jev, llm)
        llm_side = fill_unsure(llm, jev)
        jev_result = decide_handling(jev_side)
        llm_result = decide_handling(llm_side)
        # 取更严格的结果；相同时以 Jev 为准。
        if _STRICTNESS[llm_result[0]] > _STRICTNESS[jev_result[0]]:
            answers, (handling, row), source = llm_side, llm_result, "llm"
        else:
            answers, (handling, row), source = jev_side, jev_result, "jev"
        disagreement = sorted(
            key
            for key in ANSWER_KEYS
            if jev.get(key) is not None
            and llm.get(key) is not None
            and jev.get(key) != llm.get(key)
        )
    else:
        answers = dict(jev if jev is not None else llm or empty_answers())
        handling, row = decide_handling(answers)
        source = "jev" if jev is not None else "llm"
        disagreement = []
    return _classification(
        answers,
        handling=handling,
        row=row,
        source=source,
        jev_status=jev_status,
        review_status=review_status,
        jev=jev,
        llm=llm,
        disagreement=disagreement,
    )


def fallback_classification(out_of_scope_reason: str | None) -> dict[str, Any]:
    """Jev 和 LLM 都不可用：规则认出的明显范围外给能力说明，其余先答后审（第七节）。"""
    answers = empty_answers()
    if out_of_scope_reason:
        answers["support_scope"] = "out_of_scope"
        handling, row = CAPABILITY, 1
    else:
        handling, row = ANSWER_THEN_REVIEW, 0
    classification = _classification(
        answers,
        handling=handling,
        row=row,
        source="rule",
        jev_status="unavailable",
        review_status="unavailable",
        jev=None,
        llm=None,
        disagreement=[],
    )
    if out_of_scope_reason:
        classification["scope_reason"] = out_of_scope_reason
    return classification


def _classification(
    answers: Mapping[str, Any],
    *,
    handling: str,
    row: int,
    source: str,
    jev_status: str,
    review_status: str,
    jev: Mapping[str, Any] | None,
    llm: Mapping[str, Any] | None,
    disagreement: list[str],
) -> dict[str, Any]:
    out_of_scope = handling == CAPABILITY
    intent = (
        IntentType.INFORMATION_REQUEST
        if out_of_scope or answers.get("intent") is None
        else normalize_intent(answers.get("intent"))
    )
    request = answers.get("request")
    classification = {
        "category": intent.value,
        "category_source": source,
        "mode": request or "unsure",
        "mode_source": source,
        "scope": "out_of_scope" if out_of_scope else "support",
        "scope_reason": "no_support_capability" if out_of_scope else None,
        "scope_source": source if out_of_scope else None,
        "record_tool": (
            RECORD_TOOLS.get(intent) if request == "record_lookup" else None
        ),
        "complaint": bool(answers.get("complaint")),
        "negative": bool(answers.get("negative_emotion")),
        "account_compromised": bool(answers.get("account_security")),
        "wide_impact": bool(answers.get("urgent_outage")),
        "mixed_request": bool(answers.get("answerable_question")),
        "jev_status": jev_status,
        "review_status": review_status,
        "disagreement": disagreement,
        "answers": {"jev": dict(jev) if jev else None, "llm": dict(llm) if llm else None},
        "handling": handling,
        "principle": row,
    }
    classification["priority"] = handling_priority(classification)
    return classification


def handling_priority(c: Mapping[str, Any]) -> str:
    """优先级只决定人工处理时限（第四节）。"""
    if c.get("handling") == CAPABILITY:
        return "low"
    if c.get("wide_impact"):
        return "urgent"
    if c.get("complaint") or c.get("account_compromised") or c.get("mode") == "action":
        return "high"
    intent = normalize_intent(c.get("category"))
    # 普通故障不是紧急；账务默认值偏高，非办理类按中等。
    if intent == IntentType.OUTAGE_REPORT:
        return "high"
    if intent == IntentType.BILLING_DISPUTE:
        return "medium"
    return intent_defaults(intent).priority


def requires_human(c: Mapping[str, Any] | None) -> bool:
    return bool(c) and c.get("handling") in {HUMAN, ANSWER_THEN_REVIEW}


def sentiment_label(c: Mapping[str, Any]) -> str:
    """情绪只用于观测指标，不参与转人工判断。"""
    if c.get("negative") or c.get("complaint"):
        return "negative"
    if normalize_intent(c.get("category")) == IntentType.FEEDBACK:
        return "positive"
    return "neutral"
