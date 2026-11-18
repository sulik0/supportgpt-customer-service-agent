"""对用户原文的确定性规则信号，只在 classification.rule_signals 里用一次。

ACTION_OR_DISPUTE 是原则第 20 条里规则说了算的“明确办事说法”；ELIGIBILITY_REQUEST
只在 Jev 对“办事还是问”没有结论时使用（第 21 条）。
"""

import re

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
