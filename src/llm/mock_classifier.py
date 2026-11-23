"""Mock LLM 的确定性分类：没有 API Key 时用关键词近似回答分类问题。

只服务于 Mock Provider（本地演示和测试），真实部署不经过这里。答案格式和
`src/decision/questions.py` 的问题一致。
"""

import re
from typing import Any

from src.agents.scope import out_of_scope_reason
from src.models.intents import IntentType

_BILLING_DOMAIN = re.compile(
    r"\b(refund|payment|invoice|billing|charged?|card payment|bank statement)\b|"
    r"退款|支付|发票|账单|扣款|扣费|多扣|乱扣|重复扣|银行卡"
)
_API_INCIDENT = re.compile(
    r"(?:\bapi\b|接口|服务|集群|生产环境|\bcluster\b|\bproduction\b).{0,50}"
    r"(?:\b504\b|\b503\b|error|timeout|timing out|down|offline|crash|broken|"
    r"connectivity|slow|报错|超时|宕机|离线|无法访问|访问不了|挂了|全挂|故障|缓慢)|"
    r"(?:\b504\b|\b503\b|报错|超时|宕机|故障).{0,30}(?:\bapi\b|接口|服务)"
)
_ORDER_STATUS = re.compile(
    r"\b(track|tracking|shipping status|delivery status|order status|where is (?:my )?order|"
    r"has been delivered|current status of order|package has not arrived|not received)\b|"
    r"订单状态|物流|快递|查询订单|订单.*(?:签收|配送)|包裹.*未到"
)
_CANCELLATION_ACTION = re.compile(
    r"\b(?:please |need to |want to |help me )?cancel (?:my |this |the )?order\b|"
    r"请?.{0,6}取消.{0,4}订单"
)
_CANCELLATION_INFORMATION = re.compile(
    r"\b(if i cancel|cancellation fee|cancel an order before|can .* cancel|"
    r"order cancellation policy)\b|取消订单.{0,12}(?:费用|政策|是否|能否)"
)
_ACCOUNT_INCIDENT = re.compile(
    r"\b(?:cannot|can't|unable to) (?:log ?in|sign ?in)|account (?:is )?locked|"
    r"invalid credentials|login keeps failing\b|无法登录|账户被锁|凭据失效|"
    r"登录不了|登不上|登陆不了|密码错误|密码不对"
)
# 明确投诉诉求；“怎么投诉 / 如何投诉”这类流程咨询不算。
# 投诉类词汇只在这里判断（`ACTION_OR_DISPUTE` 不再包含），出现即算投诉，
# 再由 _COMPLAINT_INFORMATION 排除流程咨询。
_COMPLAINT_REQUEST = re.compile(r"投诉|\bcomplain(?:t|ts|ing|ed)?\b")
# 以监管、媒体、法律途径相威胁，或直接辱骂，同样按投诉处理。
_COMPLAINT_THREAT = re.compile(
    r"消协|12315|工商局|市场监管|曝光|找媒体|起诉|告你们|律师|举报|维权|"
    r"骗子|垃圾公司|黑店|"
    r"\b(?:report you|consumer protection|better business bureau|sue you|"
    r"take legal action|legal action|lawyers?|lawsuit|sue|go to the (?:media|press)|"
    r"scam(?:mers?)?)\b"
)
_COMPLAINT_INFORMATION = re.compile(
    r"(?:怎么|如何|哪里|在哪|怎样|渠道|流程|方式).{0,8}投诉|"
    r"投诉(?:的)?(?:电话|渠道|流程|方式|入口|政策|规则)|"
    r"\bhow (?:do|can|to|should)\b.{0,20}complain|\bwhere (?:do|can|to)\b.{0,20}complain|"
    r"\bcomplaints? (?:process|procedure|policy|channel|hotline)\b"
)
_WARRANTY_ACTION = re.compile(
    r"\b(?:file|open|start|submit) (?:a )?(?:warranty )?claim\b|"
    r"\b(?:repair|replace) my (?:device|hardware|item)\b|"
    r"申请保修|发起维修|维修我的|更换我的"
)
# 实体设备故障走保修支持；带 API/服务信号的才算 API 故障。
_DEVICE_FAULT = re.compile(
    r"(?:设备|网关|终端|硬件|路由器|机器).{0,12}"
    r"(?:断网|掉线|坏了|损坏|不亮|无法开机|开不了机|死机|没反应|不工作|连不上网|老是重启|自动重启)|"
    r"\b(?:device|hardware|gateway|router|terminal)\b.{0,40}"
    r"\b(?:is broken|not working|won't turn on|will not turn on|"
    r"keeps? (?:disconnecting|rebooting|dropping)|dead|faulty|defective)\b"
)
_API_SIGNAL = re.compile(
    r"\bapi\b|接口|服务|集群|生产环境|请求|\bcluster\b|\bproduction\b|\bendpoint\b|"
    r"\brequests?\b|\b(?:401|403|429|502|503|504)\b"
)
_FEEDBACK = re.compile(r"\bthank you\b|\bthanks\b|\bgreat service\b|谢谢|感谢")
_CONTEXTUAL_CANCELLATION = re.compile(
    r"\b(?:cancel|stop)\s+(?:it|that|this)\b|取消(?:它|这个|那个|刚才那个)|"
    r"(?:那|那就|请)帮我取消"
)
_CONTEXTUAL_ORDER_STATUS = re.compile(
    r"\b(?:where is|track|status of|what about)\s+(?:it|that|this)\b|"
    r"(?:它|这个|那个|刚才那个).{0,8}(?:到哪|状态|物流)|"
    r"(?:查|看).{0,6}(?:刚才|那个).{0,6}(?:订单|物流)?"
)


def is_explicit_complaint(normalized_text: str) -> bool:
    """识别明确的投诉诉求，排除“怎么投诉”这类流程咨询。"""
    return bool(
        (
            _COMPLAINT_REQUEST.search(normalized_text)
            or _COMPLAINT_THREAT.search(normalized_text)
        )
        and not _COMPLAINT_INFORMATION.search(normalized_text)
    )


# 要求实际办理、表达争议或越权的说法：这些必须等人工。
ACTION_OR_DISPUTE = re.compile(
    r"我要退|要求退|帮我退|给我退|直接退|立即退|马上退|退给我|退我|"
    r"想退|想要退|(?:我要|我想|帮我|给我|请|麻烦)(?:立即|现在|马上|直接)?(?:申请|办理)退|"
    r"退\s*\d|退款工具|无需审批|不用审批|跳过审批|免审批|"
    # 投诉、举报、法律/监管/媒体威胁不在这里判断，统一由 Analyzer 的投诉规则处理
    # （它会排除“怎么投诉”这类流程咨询）。
    r"赔偿|赔付|补偿|"
    # 只出现“取消/撤销/撤回”可能是在问政策（第 11 条），要有办理说法才算。
    r"(?:我|我们)?(?:要|想|想要|需要|要求|决定)(?:立即|现在|马上|直接)?(?:取消|撤销|撤回)|"
    r"(?:取消|撤销|撤回)(?:掉|它|吧|了吧)|"
    r"拒付|不认可|不承认|"
    # “有争议的账户能退款吗”是在问政策；“我有异议/要发起争议”才是在表达争议。
    r"(?:我|我们)(?:要|想|需要)?(?:对|就)?.{0,12}(?:有|提出|发起|申请)(?:争议|异议)|"
    r"(?:发起|提出|提交)(?:争议|异议|申诉)|"
    r"多扣|乱扣|错扣|重复扣|扣错|盗刷|被骗|欺骗|骗子|"
    r"金额不对|金额有误|金额错|扣款有误|不认(?!识)|"
    # 以提问口气提出的办理要求：“能帮我把钱退回来吗”“麻烦处理一下退款可以吗”。
    # 请求对象（帮我/麻烦/请…）后面紧跟办理动作才算；“帮我查一下退款进度”是查询。
    r"(?:帮我|给我|替我|麻烦|(?<!申)请(?!问)|尽快|马上|立即|立刻|安排)"
    r"(?:(?!查|问|看|确认|核实|告诉|说明|解释).){0,12}?(?:退|撤销|取消|赔|补偿|返还|还给)|"
    r"(?:帮我|给我|替我|麻烦|(?<!申)请(?!问))(?:(?!查|问|看|确认|核实|告诉|说明|解释).){0,6}"
    r"(?:操作|办理|处理|办一下)|"
    r"要求.{0,6}退|退掉|退回来|退回给|还给我|把.{0,12}(?:退|还)(?:掉|了|回|给)|"
    r"\b(?:i (?:want|need|demand|would like)|give me|issue|process|"
    r"please (?:refund|cancel|reverse|credit))\b.{0,30}"
    r"\b(?:refund|money back|chargeback|credit|compensation)\b|"
    r"\b(?:want|need|demand) (?:a |my |the |full )*refund\b|"
    r"\b(?:can|could|will|would) you (?:please |kindly |just )*"
    r"(?:refund|cancel|reverse|credit|compensate|reimburse|return my|give .{0,12} back)\b|"
    r"\brefund me\b|\bmoney back\b|\breturn my (?:payment|money)\b|\bcredit my account\b|"
    r"\bi (?:want|need|would like|'d like) (?:one|it|that|this)\b|"
    r"\bi(?:'d| would)? (?:want|need|like|wish|have) to (?:cancel|reverse)\b|"
    r"(?:^|[.!?。！？]\s*)(?:please\s+)?(?:cancel|reverse)\s+(?:my|the|this|that|order|it)\b|"
    r"\bplease (?:process|handle|issue|make|do|return|refund)\b|\bjust do it\b|\bprocess it\b|"
    r"\bi(?:'m| am)? request(?:ing)? (?:a |my |the |full )*refund\b|"
    r"\bi (?:want to |need to |will |would like to |am going to )?dispute\b|"
    r"\b(?:file|open|raise|start|submit)(?:ing)? (?:a |my )?dispute\b|"
    r"\bdisput(?:e|ing) (?:this|the|that|my) (?:charge|payment|invoice|bill|transaction)\b|"
    r"\b(?:chargeback|compensat\w+|fraud|scam|"
    r"overcharged|charged twice|double charged|wrong(?:ly)? charged?|"
    r"incorrect(?:ly)? charged?|without approval|skip approval|do not execute|"
    r"don't execute)\b",
    re.IGNORECASE,
)
# 问“我这笔款能不能退”：确认自己的退款资格，先答后审（第 7 条）。
ELIGIBILITY_REQUEST = re.compile(
    r"\b(?:can|may|could) i\b.{0,40}\b(?:refund|money back|reimburse\w*)\b|"
    r"\bam i (?:eligible|entitled)\b.{0,30}\brefund\b|"
    r"(?:我|我们)(?:的)?.{0,40}(?:可以|可不可以|能不能|能否|可否|能)"
    r".{0,6}(?:申请|办理|要求)?退(?:款|费|货|订)",
    re.IGNORECASE,
)
# 影响面大的故障：即使是提问也按紧急事件通知人工。
WIDE_IMPACT = re.compile(
    r"\b(?:all|every) (?:users?|customers?|clients?|tenants?|regions?)\b|"
    r"\b(?:everyone|everybody|entire|global outage|company[- ]wide)\b|"
    r"全部用户|所有用户|所有人|所有客户|大面积|全站|全线|全挂|全部挂|全都挂|整个(?:系统|服务|平台|集群|网站|生产环境)",
    re.IGNORECASE,
)

_BUSINESS_IMPACT = re.compile(
    r"影响(?:了)?(?:我们|我司|公司)?的?(?:业务|生产|线上|营业|客户)|"
    r"业务(?:停摆|中断|瘫痪|受影响|停了)|生产环境.{0,6}(?:挂|宕|不可用|瘫痪)|"
    r"\bproduction\b.{0,30}\b(?:down|outage|unavailable)\b|"
    r"\b(?:affecting|impacting|blocking) (?:our|my) (?:business|customers|production|operations)\b|"
    r"\bbusiness (?:is )?(?:down|impacted|blocked|stopped)\b",
    re.IGNORECASE,
)
_ACCOUNT_COMPROMISED = re.compile(
    r"被盗|盗号|盗用|被人登录|被别人登录|异地登录|不是我本人|陌生设备登录|被黑|"
    r"\b(?:hacked|compromised|stolen account|account (?:was |has been )?stolen)\b|"
    r"\bunauthori[sz]ed (?:login|access|sign[- ]?in)\b|"
    r"\bsomeone (?:else )?(?:logged|signed|got) (?:in|into)\b",
    re.IGNORECASE,
)
_NEGATIVE_EMOTION = re.compile(
    r"\b(?:angry|upset|furious|unacceptable|terrible|awful|disappointed|ridiculous|"
    r"frustrat\w*|so slow|still (?:haven't|have not|not))\b|"
    r"不满|生气|愤怒|糟糕|离谱|坑人|欺骗|迟迟|这么慢|太慢|怎么还没|到底(?:多久|什么时候|要)|"
    r"烦死|受不了|气死",
    re.IGNORECASE,
)
_RECORD_ENTITY = re.compile(
    r"\bord-[\w-]+\b|我的.{0,12}(?:订单|设备|网关|终端|发票|付款|账单)|"
    r"\bmy (?:\w+ ){0,2}(?:order|device|hardware|gateway|router|terminal|invoice|payment)\b",
    re.I,
)
_RECORD_LOOKUP = re.compile(
    r"查|核实|确认|进度|记录|状态|到哪|保修期内|在保|过保|"
    r"\b(?:check|look up|verify|status|where is|track|coverage|entitlements?|under warranty)\b",
    re.I,
)
_UNCLEAR = re.compile(
    r"^\W*(?:我需要帮助|需要帮助|有个问题|帮帮我|i need help|help|i have a question)\W*$",
    re.I,
)


def _category(text: str) -> IntentType:
    """按关键词猜业务类别；多个类别同时命中时取第一个。"""
    if _BILLING_DOMAIN.search(text):
        return IntentType.BILLING_DISPUTE
    if _API_INCIDENT.search(text):
        return IntentType.OUTAGE_REPORT
    if (
        _CANCELLATION_ACTION.search(text)
        or _CANCELLATION_INFORMATION.search(text)
        or _CONTEXTUAL_CANCELLATION.search(text)
    ):
        return IntentType.ORDER_CANCELLATION
    if _ORDER_STATUS.search(text) or re.search(r"\bord-[\w-]+\b", text):
        return IntentType.ORDER_STATUS
    if _ACCOUNT_INCIDENT.search(text) or _ACCOUNT_COMPROMISED.search(text):
        return IntentType.ACCOUNT_SUPPORT
    if (
        _WARRANTY_ACTION.search(text)
        or _DEVICE_FAULT.search(text)
        or re.search(r"保修|\bwarranty\b", text)
    ):
        return IntentType.WARRANTY_CLAIM
    if _FEEDBACK.search(text):
        return IntentType.FEEDBACK
    return IntentType.INFORMATION_REQUEST


def _request(text: str, category: IntentType) -> str:
    if (
        ACTION_OR_DISPUTE.search(text)
        or _WARRANTY_ACTION.search(text)
        or _CONTEXTUAL_CANCELLATION.search(text)
        or (
            _CANCELLATION_ACTION.search(text)
            and not _CANCELLATION_INFORMATION.search(text)
        )
    ):
        return "action"
    if ELIGIBILITY_REQUEST.search(text):
        return "eligibility"
    if category in {
        IntentType.ORDER_STATUS,
        IntentType.BILLING_DISPUTE,
        IntentType.WARRANTY_CLAIM,
    } and (
        (_RECORD_ENTITY.search(text) and _RECORD_LOOKUP.search(text))
        or _CONTEXTUAL_ORDER_STATUS.search(text)
    ):
        return "record_lookup"
    if _UNCLEAR.search(text):
        return "unclear"
    return "information"


def mock_classification(text: str) -> dict[str, Any]:
    """返回和 LLM 复核同格式的答案：{key: {"answer": ..., "sure": bool}}。"""
    current = text.rsplit("Current Description:", 1)[-1]
    normalized = " ".join(current.lower().split())
    scope_reason = out_of_scope_reason(normalized)
    category = _category(normalized)
    request = _request(normalized, category)
    action = request == "action"
    answers = {
        "support_scope": "out_of_scope" if scope_reason else "support",
        "request": request,
        "complaint": is_explicit_complaint(normalized),
        "account_security": bool(_ACCOUNT_COMPROMISED.search(normalized)),
        "urgent_outage": bool(
            WIDE_IMPACT.search(normalized) or _BUSINESS_IMPACT.search(normalized)
        ),
        "negative_emotion": bool(_NEGATIVE_EMOTION.search(normalized)),
        "answerable_question": bool(
            action and re.search(r"[?？]|多久|几天|怎么|\bhow\b|\bwhen\b", normalized)
            and re.search(r"另外|还有|顺便|\balso\b|\band\b", normalized)
        ),
        "intent": category.value,
    }
    return {key: {"answer": value, "sure": True} for key, value in answers.items()}
