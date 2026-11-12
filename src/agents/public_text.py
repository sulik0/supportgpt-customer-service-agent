"""面向终端用户的文本后处理：去掉证据编号等内部引用标记。"""

import re

_ID = r"S\d{1,2}"
_ID_LIST = rf"{_ID}(?:\s*[、,，/和及与]\s*{_ID})*"
_CUE = r"(?:来源|参考|依据|出处|见|参见|source|sources|ref)?\s*[:：]?\s*"
# 括号内只有证据编号（可带“来源：”之类前缀）；支持半角、全角圆括号与方括号。
_BRACKETED = re.compile(
    rf"[\(（\[［【]\s*{_CUE}{_ID_LIST}\s*[\)）\]］】]", re.IGNORECASE
)
# 连续的方括号编号，如 [S1][S2]
# 不带括号的并列编号，至少两个，如 S1、S2。
_BARE_LIST = re.compile(rf"(?<![A-Za-z0-9]){_ID}(?:\s*[、,，/和及与]\s*{_ID})+(?![A-Za-z0-9])")


def strip_internal_citations(text: str) -> str:
    """确定性去掉 [S1]、(S2)、（S2）、S1、S2 等标记，并清理残留空格与标点。"""
    if not text:
        return text
    cleaned, count_a = _BRACKETED.subn("", text)
    cleaned, count_b = _BARE_LIST.subn("", cleaned)
    if not (count_a or count_b):
        return text
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    # 标点前不留空格；连续的中文句末标点只保留一个。
    cleaned = re.sub(r"[ \t]+(?=[，。！？；：、,.;:!?)）\]］】])", "", cleaned)
    cleaned = re.sub(r"([，。！？；：、])\1+", r"\1", cleaned)
    cleaned = re.sub(r"[，、；：]+(?=[。！？])", "", cleaned)
    cleaned = re.sub(r"(?<=[\(（])[ \t]+", "", cleaned)
    return cleaned.strip()
