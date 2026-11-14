import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class IntentType(str, Enum):
    """定义客服 Workflow 唯一允许使用的意图集合。

    规则、LLM、Tool、Risk Engine 和 Evaluation 均应引用本枚举。
    """

    BILLING_DISPUTE = "billing_dispute"
    OUTAGE_REPORT = "outage_report"
    ORDER_CANCELLATION = "order_cancellation"
    ORDER_STATUS = "order_status"
    ACCOUNT_SUPPORT = "account_support"
    WARRANTY_CLAIM = "warranty_claim"
    FEEDBACK = "feedback"
    INFORMATION_REQUEST = "information_request"

    def __str__(self) -> str:
        """确保日志、Prompt 与持久化摘要始终使用枚举值。"""
        return self.value

    @classmethod
    def values(cls) -> tuple[str, ...]:
        """返回可直接写入 Prompt 或 Dataset 的稳定枚举值。"""
        return tuple(item.value for item in cls)


DEFAULT_INTENT = IntentType.INFORMATION_REQUEST


@dataclass(frozen=True)
class IntentDefinition:
    """定义意图的唯一业务语义和下游路由默认值。

    Analyzer、Risk Engine、Tool Routing 和 Prompt 共用这份 Taxonomy。
    """

    description: str
    department: str
    priority: str
    operation_kind: str


INTENT_TAXONOMY = {
    IntentType.BILLING_DISPUTE: IntentDefinition(
        description=(
            "退款、支付、发票、卡扣款、金额或账单争议；即使用户只在询问"
            "政策，仍属于高风险账务域"
        ),
        department="billing",
        priority="high",
        operation_kind="sensitive_business",
    ),
    IntentType.OUTAGE_REPORT: IntentDefinition(
        description=(
            "API 或服务正在报错、超时、宕机、离线或性能严重下降；"
            "包括对这些实际故障的恢复步骤询问"
        ),
        department="technical",
        priority="urgent",
        operation_kind="incident",
    ),
    IntentType.ORDER_CANCELLATION: IntentDefinition(
        description=(
            "用户要求实际取消某个订单；假设性费用、政策或能力咨询不属于此类"
        ),
        department="shipping",
        priority="high",
        operation_kind="write_request",
    ),
    IntentType.ORDER_STATUS: IntentDefinition(
        description="查询具体订单的物流、配送、签收或当前状态",
        department="shipping",
        priority="medium",
        operation_kind="read_request",
    ),
    IntentType.ACCOUNT_SUPPORT: IntentDefinition(
        description=(
            "账户无法登录、被锁定、凭据失效等已发生的账户异常；"
            "菜单路径、设置方法和配置说明不属于此类"
        ),
        department="general",
        priority="medium",
        operation_kind="support_issue",
    ),
    IntentType.WARRANTY_CLAIM: IntentDefinition(
        description=(
            "用户要对已损坏的具体设备发起保修、维修或换货；"
            "保修期限或政策说明不属于此类"
        ),
        department="general",
        priority="medium",
        operation_kind="write_request",
    ),
    IntentType.FEEDBACK: IntentDefinition(
        description="感谢、表扬或不需要业务操作的一般反馈",
        department="general",
        priority="low",
        operation_kind="informational",
    ),
    IntentType.INFORMATION_REQUEST: IntentDefinition(
        description=(
            "知识解释、操作指南、账户导航、能力或政策说明、引用/转述安全内容，"
            "以及尚未给出具体问题的求助"
        ),
        department="general",
        priority="medium",
        operation_kind="informational",
    ),
}


def normalize_intent(value: Any) -> IntentType:
    """将外部或 LLM 意图归一化，未知值统一降级为通用咨询。"""
    if isinstance(value, IntentType):
        return value
    try:
        return IntentType(str(value).strip().lower())
    except (TypeError, ValueError):
        return DEFAULT_INTENT


def intent_prompt_values() -> str:
    """生成供不同 LLM Provider 共用的意图枚举约束。"""
    return "|".join(IntentType.values())


def intent_prompt_guide() -> str:
    """生成供所有 LLM Provider 共用的简明分类边界。"""
    return "\n".join(
        f"- {intent.value}: {definition.description}"
        for intent, definition in INTENT_TAXONOMY.items()
    )


def intent_defaults(value: Any) -> IntentDefinition:
    """根据归一化意图返回可信的 department 和 priority。"""
    return INTENT_TAXONOMY[normalize_intent(value)]


_AUTHORITATIVE_BUSINESS_QUERY = re.compile(
    r"\b(warranty|guaranteed? shipping|shipping eta|cancellation fee|"
    r"cancel an order before|delivery address after|post-shipment address|"
    r"hardware coverage)\b|"
    r"保修|物流时效保证|取消费|发货后.{0,10}地址"
)


def requires_authoritative_business_answer(text: str) -> bool:
    """识别缺少权威政策时不应由 Agent 自动承诺的咨询。"""
    return bool(_AUTHORITATIVE_BUSINESS_QUERY.search(str(text).lower()))


_AUTHORITATIVE_TOPIC_EVIDENCE = (
    (re.compile(r"\bwarranty\b|保修"), re.compile(r"\bwarranty\b|保修")),
    (
        re.compile(r"\b(?:shipping eta|guaranteed? shipping)\b|物流时效保证"),
        re.compile(
            r"\b(?:shipping eta|delivery time|delivery estimate|guaranteed? delivery)\b|"
            r"物流时效|配送时间|送达保证"
        ),
    ),
    (
        re.compile(r"\b(?:cancellation fee|cancel an order before)\b|取消费"),
        re.compile(r"\b(?:cancellation fee|order cancellation)\b|取消费|取消订单"),
    ),
    (
        re.compile(r"\b(?:delivery address after|post-shipment address)\b|发货后.{0,10}地址"),
        re.compile(r"\b(?:delivery address|shipping address|address change)\b|收货地址|配送地址"),
    ),
)


# 问保修期限（多久、几个月）时，只提到“保修”的流程文档不算依据，必须有具体期限。
_WARRANTY_DURATION_QUERY = re.compile(
    r"\b(?:period|how long|how many (?:months|years|days)|duration|length)\b|"
    r"多久|多长|几年|几个月|期限|保修期(?:是|有|为)"
)
_WARRANTY_DURATION_EVIDENCE = re.compile(
    r"(?:warranty|保修).{0,60}?\d+\s*-?\s*(?:months?|years?|days?|个月|年|天)|"
    r"\d+\s*-?\s*(?:months?|years?|days?|个月|年|天).{0,60}?(?:warranty|保修)"
)


def has_authoritative_business_evidence(query: str, evidence: str) -> bool:
    """验证检索证据是否真正覆盖当前高风险业务主题。"""
    normalized_query = str(query).lower()
    normalized_evidence = str(evidence).lower()
    if (
        re.search(r"\bwarranty\b|保修", normalized_query)
        and _WARRANTY_DURATION_QUERY.search(normalized_query)
        and not _WARRANTY_DURATION_EVIDENCE.search(normalized_evidence)
    ):
        return False
    matched_topic = False
    for query_pattern, evidence_pattern in _AUTHORITATIVE_TOPIC_EVIDENCE:
        if not query_pattern.search(normalized_query):
            continue
        matched_topic = True
        if evidence_pattern.search(normalized_evidence):
            return True
    return not matched_topic
