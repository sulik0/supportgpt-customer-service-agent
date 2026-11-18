"""QA 用：高风险业务主题是否有权威依据，以及回复是否只做了“无法确认”的说明。

从 `src/models/intents.py` 移出：那里只放意图枚举和默认值。
"""

import re


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


_DEFERRAL = re.compile(
    r"需要?人工|转人工|人工客服|人工核|无法确[定认]|不能确认|无法核实|未查询到|未获取到|"
    r"没有(?:相关|找到|查到)?.{0,6}(?:依据|资料|信息|记录|说明)|(?:另行|进一步|需要?)核实|"
    r"以.{0,12}为准|暂无|核实后|确认后|请提供|请补充|人工确认|需要?根据|"
    r"\bplease provide\b|\b(?:i|we) (?:will |would |still )?need (?:your|the|more|a)\b|\bonce (?:we|you|it|they)\b|\bafter (?:we )?(?:verif|confirm|review)|"
    r"\b(?:cannot|can't|can not|unable to|not able to) (?:confirm|determine|verify|find)\b|"
    r"\b(?:do|does|did) not (?:have|specify|include|mention|cover)\b|\bdon't have\b|"
    r"\bnot (?:available|included|specified|covered|documented)\b|"
    r"\bneeds? to be (?:confirmed|verified|reviewed)\b|"
    r"\b(?:human|specialist|support agent|manual) (?:review|agent|support|confirmation)?\b"
)
_DURATION_FIGURE = re.compile(r"\d+\s*-?\s*(?:months?|years?|days?|个月|年|天)")


def defers_unsupported_topic(query: str, response: str) -> bool:
    """缺少权威依据的主题，回复是否只做了“无法确认、需人工”的说明而没有下结论。

    用于多意图问题：能答的部分照常回答，答不了的主题只要没有编造就不算幻觉。
    """
    normalized_query = str(query).lower()
    topic_patterns = [
        evidence_pattern
        for query_pattern, evidence_pattern in _AUTHORITATIVE_TOPIC_EVIDENCE
        if query_pattern.search(normalized_query)
    ]
    if not topic_patterns:
        return False
    sentences = [
        part.strip()
        for part in re.split(r"[。！？!?；;\n]+|(?<=[a-z0-9])\.\s+", str(response).lower())
        if part.strip()
    ]
    for sentence in sentences:
        if not any(pattern.search(sentence) for pattern in topic_patterns):
            continue
        # 提到该主题的句子必须是在说明无法确认，且不能给出具体期限。
        if not _DEFERRAL.search(sentence) or _DURATION_FIGURE.search(sentence):
            return False
    return True
