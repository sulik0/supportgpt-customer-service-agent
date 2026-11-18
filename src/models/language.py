"""回复语言的统一判断：系统固定文案、Mock 回复和 Resolver 语言指令都用这里。

规则：用户明确要求某种语言时按要求；否则原文含中文就用中文，不含就用英文。
"""

import re
from typing import Literal

Language = Literal["zh", "en"]

_ENGLISH_REQUEST = re.compile(
    r"(?:用|使用|以)(?:英文|英语)|英文回复|英语回复|"
    r"\b(?:reply|respond|answer) in english\b",
    re.IGNORECASE,
)
_CHINESE_REQUEST = re.compile(
    r"(?:用|使用|以)(?:中文|汉语)|中文回复|"
    r"\b(?:reply|respond|answer) in chinese\b",
    re.IGNORECASE,
)
_CJK = re.compile(r"[一-鿿]")


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def requested_language(text: str) -> Language | None:
    """用户明确要求的回复语言；没有要求时返回 None。"""
    text = text or ""
    if _ENGLISH_REQUEST.search(text):
        return "en"
    if _CHINESE_REQUEST.search(text):
        return "zh"
    return None


def reply_language(text: str) -> Language:
    """系统文案用的语言：明确要求优先，否则看原文是否含中文。"""
    return requested_language(text) or ("zh" if has_cjk(text) else "en")
