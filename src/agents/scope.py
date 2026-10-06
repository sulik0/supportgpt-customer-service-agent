"""识别明确超出客服范围的请求，并生成不含外部事实的能力说明。"""

import re
from typing import Any, Mapping

from src.models.intents import IntentType, normalize_intent


_SUPPORT_TOPIC = re.compile(
    r"订单|退款|退货|退费|支付|发票|账单|扣款|账户|账号|登录|密码|"
    r"保修|维修|换货|物流|快递|配送|包裹|接口|故障|超时|报错|"
    r"商品|产品|发货|签收|运单|取消|投诉|赔偿|赔付|扣费|订阅|"
    r"\b(?:order|refund|return|payment|invoice|billing|charge|account|login|"
    r"password|warranty|repair|shipping|delivery|package|api|timeout|outage|"
    r"product|subscription|cancel|complaint|complain|compensation)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_TOPICS = (
    (
        "weather",
        re.compile(
            r"天气|气温|天气预报|\b(?:weather|forecast|temperature)\b", re.IGNORECASE
        ),
    ),
    (
        "creative_request",
        re.compile(
            r"(?:写|创作).{0,12}(?:诗|故事|小说|歌词)|讲.{0,5}(?:笑话|故事)|\b(?:write|tell|compose).{0,30}\b(?:poem|story|joke|lyrics)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "general_knowledge",
        re.compile(
            r"(?:首都|行星|太阳系|恐龙).{0,15}(?:哪|什么|多少|介绍)|(?:哪|什么).{0,15}(?:首都|行星|太阳系)|\b(?:capital of|solar system|dinosaurs?|planets?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "general_programming",
        re.compile(
            r"(?:写|实现).{0,12}(?:算法|排序代码|小游戏)|\b(?:write|implement).{0,25}\b(?:sorting algorithm|game)\b",
            re.IGNORECASE,
        ),
    ),
)


def out_of_scope_reason(text: str) -> str | None:
    """只识别明确的无关主题；含客服业务或多意图时保留原处理路径。"""
    if _SUPPORT_TOPIC.search(text):
        return None
    for reason, pattern in _UNSUPPORTED_TOPICS:
        if pattern.search(text):
            return reason
    return None


def has_support_topic(text: str) -> bool:
    """保留业务请求和混合意图，不能用范围外分类绕过原业务判断。"""
    return bool(_SUPPORT_TOPIC.search(text))


def is_scope_boundary_request(state: Mapping[str, Any]) -> bool:
    """能力说明不能覆盖安全、依赖故障或已有人工处理要求。"""
    return bool(
        state.get("request_scope") == "out_of_scope"
        and normalize_intent(state.get("intent")) == IntentType.INFORMATION_REQUEST
        and (
            out_of_scope_reason(
                str(state.get("description") or state.get("subject") or "")
            )
            == state.get("scope_reason")
            or (
                state.get("scope_reason") == "no_support_capability"
                and state.get("scope_strategy") == "jev"
                and state.get("analyzer_strategy") == "jev"
                and not has_support_topic(
                    str(state.get("description") or state.get("subject") or "")
                )
            )
        )
        and state.get("scope_reason")
        and not state.get("security_threat_detected")
        and not state.get("risk_block_automation")
        and not state.get("risk_requires_human")
        and not state.get("semantic_guard_degraded")
        and str(state.get("semantic_guard_label", "not_run")).lower()
        not in {"unsafe", "controversial"}
        and not state.get("errors")
        and state.get("degradation_level", "none") == "none"
    )


def scope_boundary_response(state: Mapping[str, Any]) -> str:
    """按当前输入语言说明现有能力，不查询天气或作出业务承诺。"""
    text = str(state.get("description") or state.get("subject") or "")
    english_requested = bool(
        re.search(
            r"(?:用|使用|以)英语|reply in english|answer in english",
            text,
            re.IGNORECASE,
        )
    )
    chinese_requested = bool(
        re.search(
            r"(?:用|使用|以)中文|reply in chinese|answer in chinese",
            text,
            re.IGNORECASE,
        )
    )
    chinese = chinese_requested or (
        not english_requested and bool(re.search(r"[\u4e00-\u9fff]", text))
    )
    if chinese:
        limitation = (
            "暂时无法查询实时天气"
            if state.get("scope_reason") == "weather"
            else "暂时无法处理这类问题"
        )
        return f"我目前主要处理订单、退款、账户、保修和售后相关问题，{limitation}。如果你有相关服务问题，我可以继续帮你处理。"
    limitation = (
        "cannot check live weather"
        if state.get("scope_reason") == "weather"
        else "cannot help with this topic"
    )
    return f"I help with orders, refunds, accounts, warranties and after-sales support, and currently {limitation}. If you have a related service question, I can help you with it."


def scope_context_updates(reason: str, strategy: str) -> dict[str, Any]:
    """隔离本轮无关历史，不删除数据库中的会话或业务实体。"""
    return {
        "request_scope": "out_of_scope",
        "scope_reason": reason,
        "scope_strategy": strategy,
        "priority": "low",
        "sentiment": "neutral",
        "memory_recent_turns": [],
        "memory_summary": "",
        "memory_active_entities": {},
        "memory_resolved_slots": {},
        "memory_last_intent": None,
        "memory_last_department": None,
        "memory_prompt_context": "",
        "memory_retrieval_context": "",
    }


def is_verified_scope_response(state: Mapping[str, Any], response: str) -> bool:
    """验证纯能力说明；附加天气或业务事实继续接受正常 QA 校验。"""
    if not is_scope_boundary_request(state):
        return False
    if response == scope_boundary_response(state):
        return True
    # 全文匹配简短能力说明，避免夹带“今天晴”等事实。
    return state.get("scope_reason") == "weather" and bool(
        re.fullmatch(
            r"(?:我(?:目前|现在)?(?:主要|只能)(?:处理|帮助处理|提供)"
            r"(?:(?:订单|退款|账户|账号|保修|售后|客服|相关|问题|服务|支持)|[、和及\s])+[，,])?"
            r"(?:暂时|目前|现在)?(?:无法|不能|不支持)(?:查询|获取|提供)(?:实时)?天气(?:信息|预报)?[。.!！]?",
            response.strip(),
        )
    )
