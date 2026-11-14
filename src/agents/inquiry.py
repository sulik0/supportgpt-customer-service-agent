"""只读咨询判定：账务和故障域里，哪些请求只是在问信息。

判定全部是对用户原文的确定性规则，不使用模型输出。命中后 Analyzer 不再按
业务域默认规则强制转人工；质量、置信度、安全等其他审批条件不受影响。
"""

import re

from src.models.intents import IntentType

READ_ONLY_INQUIRY_INTENTS = frozenset(
    {IntentType.BILLING_DISPUTE, IntentType.OUTAGE_REPORT}
)

# 要求实际办理、表达争议或越权的说法：这些必须等人工。
ACTION_OR_DISPUTE = re.compile(
    r"我要退|要求退|帮我退|给我退|直接退|立即退|马上退|退给我|退我|"
    r"想退|想要退|(?:我要|我想|帮我|给我|请|麻烦)(?:立即|现在|马上|直接)?(?:申请|办理)退|"
    r"退\s*\d|退款工具|无需审批|不用审批|跳过审批|免审批|"
    r"赔偿|赔付|补偿|投诉|举报|曝光|起诉|律师|消协|12315|"
    r"取消|撤销|撤回|拒付|不认可|不承认|"
    # “有争议的账户能退款吗”是在问政策；“我有异议/要发起争议”才是在表达争议。
    r"(?:我|我们)(?:要|想|需要)?(?:对|就)?.{0,12}(?:有|提出|发起|申请)(?:争议|异议)|"
    r"(?:发起|提出|提交)(?:争议|异议|申诉)|"
    r"多扣|乱扣|错扣|重复扣|扣错|盗刷|被骗|欺骗|骗子|"
    r"金额不对|金额有误|金额错|扣款有误|"
    r"\b(?:i (?:want|need|demand|would like)|give me|issue|process|"
    r"please (?:refund|cancel|reverse|credit))\b.{0,30}"
    r"\b(?:refund|money back|chargeback|credit|compensation)\b|"
    r"\b(?:want|need|demand) (?:a |my |the |full )*refund\b|"
    r"\bi(?:'m| am)? request(?:ing)? (?:a |my |the |full )*refund\b|"
    r"\bi (?:want to |need to |will |would like to |am going to )?dispute\b|"
    r"\b(?:file|open|raise|start|submit)(?:ing)? (?:a |my )?dispute\b|"
    r"\bdisput(?:e|ing) (?:this|the|that|my) (?:charge|payment|invoice|bill|transaction)\b|"
    r"\b(?:chargeback|compensat\w+|complain\w*|lawsuit|lawyer|sue|fraud|scam|"
    r"cancel|reverse|overcharged|charged twice|double charged|wrong(?:ly)? charged?|"
    r"incorrect(?:ly)? charged?|without approval|skip approval|do not execute|"
    r"don't execute)\b",
    re.IGNORECASE,
)
# 明确的提问或查询。英文助动词只在句首才算提问，避免把陈述句当成问题。
QUESTION = re.compile(
    r"[?？]|吗|么|多少|多久|什么|怎么|如何|是否|能否|可否|哪|几|查|确认|核实|"
    r"\b(?:what|when|where|which|how|why|whether)\b|"
    r"(?:^|[.!;]\s+)(?:is|are|was|were|does|do|did|can|could|may|will|would|should)\b|"
    r"\b(?:check|confirm|verify|look up|tell me|show me|summari[sz]e|explain|list)\b|"
    r"\bi need (?:the|my)\b.{0,40}\b(?:amount|status|total|number|details)\b",
    re.IGNORECASE,
)
# 影响面大的故障：即使是提问也按紧急事件通知人工。
WIDE_IMPACT = re.compile(
    r"\b(?:all|every) (?:users?|customers?|clients?|tenants?|regions?)\b|"
    r"\b(?:everyone|everybody|entire|global outage|company[- ]wide)\b|"
    r"全部用户|所有用户|所有人|所有客户|大面积|全站|全线|全挂|全部挂|全都挂|整个",
    re.IGNORECASE,
)


def is_read_only_inquiry(intent: IntentType, text: str) -> bool:
    """账务或故障域的只读咨询：是提问，且没有办理、争议或大面积故障的说法。"""
    if intent not in READ_ONLY_INQUIRY_INTENTS:
        return False
    normalized = " ".join(str(text or "").split())
    if not normalized or ACTION_OR_DISPUTE.search(normalized):
        return False
    if not QUESTION.search(normalized):
        return False
    if intent == IntentType.OUTAGE_REPORT and WIDE_IMPACT.search(normalized):
        return False
    return True
