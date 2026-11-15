"""识别安全内容的引用、转述和知识性讨论语境。"""

import re
import unicodedata


_REFERENTIAL_CONTEXT = re.compile(
    r"(?:^|description:\s*|[.!?]。！？]\s*)"
    r"(?:what is|what does|(?:please\s+)?explain|describe|compare|difference between|"
    r"i am reporting|i(?:'m| am) quoting|another user (?:said|wrote)|"
    r"quoted text|for training|for awareness|security example|"
    r"什么是|请解释|请说明|区别|我在举报|我在引用|有人说|有人写道|"
    r"引用内容|安全示例)"
)
_EXECUTE_REFERENCED_PAYLOAD = re.compile(
    r"(?:and|then|now|please).{0,24}"
    r"(?:follow|execute|obey|perform|do it|apply it|comply)|"
    r"(?:然后|现在|请).{0,16}(?:执行|遵循|照做|照着做|应用|服从)|"
    # 先问一个概念问题、再接一条越权指令：后半句是指令，不是在讨论。
    r"(?:\band\b|\bthen\b|\bnow\b|\balso\b|\bplease\b|\bafter that\b|\bbut\b|"
    r"\bfirst\b|\bnext\b|\binstead\b|\byou must\b|\byou should\b).{0,80}"
    r"\b(?:ignore|disregard|forget|override|bypass|reveal|print|output|show me|"
    r"leak|send|dump|repeat|act as|pretend)\b.{0,60}"
    r"\b(?:instructions?|rules?|prompts?|yours|restrictions?|guardrails?|"
    r"api key|secrets?|credentials?|passwords?|unrestricted|unfiltered)\b|"
    r"(?:然后|现在|请|并且|并|再|顺便|同时|接着|另外|但是|不过).{0,24}"
    r"(?:忽略|无视|绕过|输出|泄露|显示|打印|发送|告诉我|发给我).{0,30}"
    r"(?:指令|规则|提示词|限制|密钥|密码|凭据)"
)


def is_referential_security_context(text: str) -> bool:
    """仅当文本在讨论风险语句且没有要求执行它时返回 True。"""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return bool(_REFERENTIAL_CONTEXT.search(normalized)) and not bool(
        _EXECUTE_REFERENCED_PAYLOAD.search(normalized)
    )
