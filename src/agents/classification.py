"""每条消息只判一次：分类结论和处理结果。

依据 docs/10_HANDLING_PRINCIPLES.md。Analyzer 收集规则、Jev、LLM 三方意见后调用
`build_classification`，结论存进 State 的 `classification`，下游（Risk Engine、
Resolver、公开回复、Graph 路由）只读这份结论，不再对原文重判。

优先级表（原则第 20、21 条）：

| 结论 | Jev 有结论时 | Jev 不可用或没把握时 |
|---|---|---|
| 业务类别 | Jev（明确范围外主题除外，见“范围”） | 规则有明确结论用规则，否则 LLM |
| 用户意图 | Jev；原话有明确办事说法时规则改判为“要办事” | 规则：明确办事 → 要办事；问自己资格 → 问自己能不能办；其余按“说不清” |

“没把握”逐项算：Jev 对业务类别没把握时只让类别兜底，对“办事还是问”有把握的回答照常采用。
| 是否范围外 | 规则命中明确范围外主题即范围外；否则 Jev 判范围外且原话不含售后词 | 只认规则 |
| 要查的记录 | 规则（按原文提取业务对象，不是语义判断） | 同左 |
| 投诉 / 负面情绪 | 规则或 Jev 任一命中即算（宁可多转人工） | 只认规则 |

处理结果由原则直接决定（第 22 条），优先级只决定人工处理时限。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from src.agents.inquiry import (
    ACTION_OR_DISPUTE,
    ELIGIBILITY_REQUEST,
    WIDE_IMPACT,
)
from src.models.intents import IntentType, intent_defaults, normalize_intent


AUTO_ANSWER = "auto_answer"
ANSWER_THEN_REVIEW = "answer_then_review"
HUMAN = "human"
CAPABILITY = "capability"

# 这三类在 Jev 不可用或没把握时先答后审（第 21 条）。
_FALLBACK_REVIEW_CATEGORIES = frozenset(
    {
        IntentType.BILLING_DISPUTE,
        IntentType.ORDER_CANCELLATION,
        IntentType.OUTAGE_REPORT,
    }
)
# “要办事”在这些类别里是实际办理（第 5、11、13 条）；其他类别的“办事”是查询或排查。
_WRITE_CATEGORIES = frozenset(
    {
        IntentType.BILLING_DISPUTE,
        IntentType.ORDER_CANCELLATION,
        IntentType.WARRANTY_CLAIM,
    }
)
# “问自己能不能办”先答后审的类别（第 7 条）；保修状态查询按第 13 条自动回答。
_ELIGIBILITY_CATEGORIES = frozenset(
    {IntentType.BILLING_DISPUTE, IntentType.ORDER_CANCELLATION}
)

# 正在影响业务的说法（第 12 条），与 WIDE_IMPACT 一起判断紧急故障。
_BUSINESS_IMPACT = re.compile(
    r"影响(?:了)?(?:我们|我司|公司)?的?(?:业务|生产|线上|营业|客户)|"
    r"业务(?:停摆|中断|瘫痪|受影响|停了)|生产环境.{0,6}(?:挂|宕|不可用|瘫痪)|"
    r"\bproduction\b.{0,30}\b(?:down|outage|unavailable)\b|"
    r"\b(?:affecting|impacting|blocking) (?:our|my) (?:business|customers|production|operations)\b|"
    r"\bbusiness (?:is )?(?:down|impacted|blocked|stopped)\b",
    re.IGNORECASE,
)
# 账户被盗（第 14 条）。
_ACCOUNT_COMPROMISED = re.compile(
    r"被盗|盗号|盗用|被人登录|被别人登录|异地登录|不是我本人|陌生设备登录|被黑|"
    r"\b(?:hacked|compromised|stolen account|account (?:was |has been )?stolen)\b|"
    r"\bunauthori[sz]ed (?:login|access|sign[- ]?in)\b|"
    r"\bsomeone (?:else )?(?:logged|signed|got) (?:in|into)\b",
    re.IGNORECASE,
)
# 规则能认出的负面情绪；投诉单独判断。
_NEGATIVE_EMOTION = re.compile(
    r"\b(?:angry|upset|furious|unacceptable|terrible|awful|disappointed|ridiculous|"
    r"frustrat\w*|so slow|still (?:haven't|have not|not))\b|"
    r"不满|生气|愤怒|糟糕|离谱|坑人|欺骗|迟迟|这么慢|太慢|怎么还没|到底(?:多久|什么时候|要)|"
    r"烦死|受不了|气死",
    re.IGNORECASE,
)


def rule_signals(text: str) -> dict[str, bool]:
    """对原文只跑一次的确定性信号。"""
    normalized = " ".join(str(text or "").split())
    return {
        "explicit_action": bool(ACTION_OR_DISPUTE.search(normalized)),
        "eligibility": bool(ELIGIBILITY_REQUEST.search(normalized)),
        "wide_impact": bool(
            WIDE_IMPACT.search(normalized) or _BUSINESS_IMPACT.search(normalized)
        ),
        "account_compromised": bool(_ACCOUNT_COMPROMISED.search(normalized)),
        "negative_emotion": bool(_NEGATIVE_EMOTION.search(normalized)),
    }


def build_classification(
    *,
    text: str,
    category: Any,
    category_source: str,
    jev_status: str,
    jev_analysis: Mapping[str, Any] | None,
    complaint_rule: bool,
    record_tool: str | None,
    out_of_scope: bool = False,
    scope_reason: str | None = None,
    scope_source: str | None = None,
) -> dict[str, Any]:
    """按优先级表合并三方意见，并按原则得出处理结果。"""
    intent = normalize_intent(category)
    signals = rule_signals(text)
    # Jev 的各项回答逐项采用：没把握的项在 DecisionService 里已置为 None。
    jev = dict(jev_analysis or {}) if jev_status in {"accepted", "unsure"} else {}

    # 用户意图：Jev 为主，规则只能改判为“要办事”。
    jev_mode = jev.get("operation_mode")
    if signals["explicit_action"]:
        mode, mode_source = "action", "rule"
    elif jev_mode:
        mode, mode_source = str(jev_mode), "jev"
    elif signals["eligibility"]:
        mode, mode_source = "eligibility", "rule"
    else:
        mode, mode_source = "unclear", "fallback"

    tone = jev.get("customer_tone")
    complaint = bool(complaint_rule or tone == "complaint")
    negative = bool(
        complaint or signals["negative_emotion"] or tone == "negative_emotion"
    )
    classification = {
        "category": intent.value,
        "category_source": category_source,
        "mode": mode,
        "mode_source": mode_source,
        "scope": "out_of_scope" if out_of_scope else "support",
        "scope_reason": scope_reason if out_of_scope else None,
        "scope_source": scope_source if out_of_scope else None,
        "record_tool": None if out_of_scope else record_tool,
        "complaint": complaint,
        "negative": negative,
        "mixed_request": bool(jev.get("mixed_request")),
        "wide_impact": signals["wide_impact"],
        "account_compromised": signals["account_compromised"],
        "jev_status": jev_status,
    }
    handling, principle = decide_handling(classification)
    classification["handling"] = handling
    classification["principle"] = principle
    classification["priority"] = handling_priority(classification)
    return classification


def decide_handling(c: Mapping[str, Any]) -> tuple[str, int]:
    """返回（处理结果, 依据的原则编号）。安全拦截在 Analyzer 更早处理（第 1 条）。"""
    intent = normalize_intent(c.get("category"))
    mode = c.get("mode")
    # Jev 不可用或对“办事还是问”没把握，且规则也没有结论（第 21 条）。
    uncertain = c.get("mode_source") == "fallback"

    if c.get("scope") == "out_of_scope":
        return CAPABILITY, 3
    if c.get("complaint"):
        return HUMAN, 16
    if c.get("account_compromised"):
        return HUMAN, 14
    if intent == IntentType.OUTAGE_REPORT and c.get("wide_impact"):
        return HUMAN, 12
    if mode == "action" and intent in _WRITE_CATEGORIES:
        # 既问又办：能答的先答，办事部分转人工。
        if c.get("mixed_request"):
            return ANSWER_THEN_REVIEW, 8
        return HUMAN, {
            IntentType.ORDER_CANCELLATION: 11,
            IntentType.WARRANTY_CLAIM: 13,
        }.get(intent, 5)
    if mode == "eligibility" and intent in _ELIGIBILITY_CATEGORIES:
        return ANSWER_THEN_REVIEW, 7
    if c.get("negative"):
        return ANSWER_THEN_REVIEW, 17
    if uncertain and intent in _FALLBACK_REVIEW_CATEGORIES:
        return ANSWER_THEN_REVIEW, 21
    if intent == IntentType.ORDER_CANCELLATION and mode != "information":
        return HUMAN, 11
    if intent == IntentType.OUTAGE_REPORT:
        return AUTO_ANSWER, 12
    if mode == "unclear":
        return AUTO_ANSWER, 9
    if c.get("record_tool"):
        return AUTO_ANSWER, 10
    return AUTO_ANSWER, 6


def handling_priority(c: Mapping[str, Any]) -> str:
    """优先级只决定人工处理时限（第 22 条）。"""
    intent = normalize_intent(c.get("category"))
    handling = c.get("handling")
    if handling == CAPABILITY:
        return "low"
    if intent == IntentType.OUTAGE_REPORT:
        return "urgent" if c.get("wide_impact") else "high"
    if c.get("complaint") or c.get("account_compromised"):
        return "high"
    if handling in {HUMAN, ANSWER_THEN_REVIEW} and intent in _WRITE_CATEGORIES:
        return "high"
    if intent == IntentType.BILLING_DISPUTE:
        return "medium"
    return intent_defaults(intent).priority


def requires_human(c: Mapping[str, Any] | None) -> bool:
    return bool(c) and c.get("handling") in {HUMAN, ANSWER_THEN_REVIEW}


def sentiment_label(c: Mapping[str, Any]) -> str:
    """情绪只用于观测指标，不参与转人工判断。"""
    if c.get("negative"):
        return "negative"
    if normalize_intent(c.get("category")) == IntentType.FEEDBACK:
        return "positive"
    return "neutral"
