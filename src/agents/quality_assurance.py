import logging
import json
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, Any

from src.agents.evidence import build_resolution_evidence
from src.agents.scope import is_verified_scope_response
from src.decision import decision_service
from src.llm.provider import llm_provider
from src.guardrails.response_filter import filter_response
from src.observability.metrics import (
    AGENT_EXECUTION_DURATION_SECONDS,
    QA_SCORE_HISTOGRAM,
)
from src.risk.engine import risk_engine
from src.models.intents import (
    has_authoritative_business_evidence,
    defers_unsupported_topic,
    requires_authoritative_business_answer,
)

logger = logging.getLogger("supportgpt.agents.quality_assurance")

_NUMBER_TEXT = r"\d[\d,]*(?:\.\d+)?"
_NUMBER_PATTERN = re.compile(rf"(?<![\d.]){_NUMBER_TEXT}")
# 带币种标记的金额：符号或币种代码在前，或币种在后。
_AMOUNT_PATTERN = re.compile(
    rf"(?:US\$|\$|¥|￥|€|£|(?<![A-Za-z])(?:USD|CNY|RMB|EUR)\s?)\s?"
    rf"(?P<pre>{_NUMBER_TEXT})"
    # 中文紧邻数字时 \w 会命中汉字，这里只排除 ASCII 字符。
    rf"|(?<![A-Za-z0-9_.-])(?P<post>{_NUMBER_TEXT})\s?"
    rf"(?:USD|CNY|RMB|EUR|美元|人民币|元)",
    re.IGNORECASE,
)
# 大写前缀加连字符、含数字的编号，例如 ORD-7001、INV-DEMO-7001、DEMO-CN-12001。
_BUSINESS_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9-])[A-Z]{2,6}(?:-[A-Z0-9]+)+(?![A-Za-z0-9])"
)
# 常见技术名词，不是业务单号。
_NON_BUSINESS_ID_PREFIXES = {
    "AES",
    "COVID",
    "GB",
    "HTTP",
    "HTTPS",
    "IPV",
    "ISO",
    "MD",
    "RFC",
    "RSA",
    "SHA",
    "SSL",
    "TLS",
    "UTF",
}
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)"),
    re.compile(r"(\d{4})\s?年\s?(\d{1,2})\s?月\s?(\d{1,2})\s?日"),
)


class QualityAssuranceAgent:
    """负责校验回复质量、事实一致性和潜在幻觉。

    在输出前执行内容泄露过滤，并生成 QA 评分与风险结论。
    """

    async def verify(self, state: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(
            f"QA Agent started verifying response for ticket: {state.get('ticket_id')}"
        )

        if "Security threat" in "".join(state.get("errors", [])):
            return state

        query = str(state.get("description", ""))
        memory_entities = state.get("memory_active_entities") or {}
        if memory_entities:
            query = (
                f"{query}\nResolved conversation entities: "
                f"{json.dumps(memory_entities, ensure_ascii=False)}"
            )
        raw_response = state.get("suggested_response", "")
        filtered_response_text = filter_response(raw_response)

        try:
            # 旧 Checkpoint 没有此字段时，才使用相同构造器兼容历史状态。
            context_texts = (
                list(state["resolution_evidence"])
                if "resolution_evidence" in state
                else build_resolution_evidence(state)
            )
            # 规则校验也只读取生成时可见的证据，避免使用后来补入的字段。
            evidence_citations = [
                {"text": text}
                for text in context_texts
                if re.match(r"^\[S[1-9][0-9]*\]", text)
            ]
            evidence_tools = {}
            for text in context_texts:
                if text.startswith("[TOOL] "):
                    evidence_tools = json.loads(text[len("[TOOL] ") :])
            # 确定性安全结论直接短路，正向证据结论交给 Jev 复核。
            if filtered_response_text == raw_response and is_verified_scope_response(
                state, raw_response
            ):
                # 能力说明没有外部事实，不要求天气证据，也不免除真实业务断言校验。
                verdict = _VERDICTS["capability_statement"]
                rule_result, rule_terminal = verdict.fields(), verdict.terminal
            else:
                rule_result, rule_terminal = self._rule_evaluation(
                    query=query,
                    raw_response=raw_response,
                    filtered_response=filtered_response_text,
                    citations=evidence_citations,
                    tool_context=evidence_tools,
                    context_texts=context_texts,
                )
            decision_records = list(state.get("decision_records", []))
            if not rule_terminal:
                decision = await decision_service.judge_response(
                    query=query,
                    evidence=context_texts,
                    response=filtered_response_text,
                )
                in_tok = decision.result.input_tokens
                out_tok = decision.result.output_tokens
                if decision.result.enabled:
                    decision_records.append(
                        decision.result.audit_record(
                            accepted=decision.accepted,
                            fallback_reason=decision.fallback_reason,
                        )
                    )
                if decision.accepted and decision.evaluation is not None:
                    qa_eval = dict(decision.evaluation)
                    strategy = "jev"
                else:
                    # 规则的正向结论只是词面支持，Jev 不可用时仍要由 LLM 复核；
                    # 规则要求的人工跟进在后面合并，评判结果不能取消。
                    qa_eval, llm_in_tok, llm_out_tok = await llm_provider.evaluate_qa(
                        query=query,
                        context=context_texts,
                        response=filtered_response_text,
                    )
                    in_tok += llm_in_tok
                    out_tok += llm_out_tok
                    strategy = "llm"
            else:
                assert rule_result is not None
                qa_eval = rule_result
                in_tok = 0
                out_tok = 0
                strategy = "rule"

            # Update metrics
            state["tokens_input"] = state.get("tokens_input", 0) + in_tok
            state["tokens_output"] = state.get("tokens_output", 0) + out_tok

            qa_score = qa_eval.get("score", qa_eval.get("qa_score", 0.0))
            hallucinated = qa_eval.get("hallucination_detected", False)
            citation_verified = qa_eval.get("citation_verified", False)
            response_grounded = bool(
                qa_eval.get(
                    "response_grounded",
                    citation_verified and not hallucinated,
                )
            )
            response_requires_human = bool(
                qa_eval.get("response_requires_human", False)
                # 规则已确认某个主题缺权威依据时，评判模型的结论不能取消人工跟进。
                or (rule_result or {}).get("response_requires_human", False)
            )

            # Observe score distribution
            QA_SCORE_HISTOGRAM.record(qa_score)

            if filtered_response_text != raw_response:
                logger.warning(
                    "Response guardrail triggered: leaked instructions were scrubbed."
                )
                # Force high priority/escalation or low QA score if a leak occurred
                qa_score = 0.5
                hallucinated = True

            duration = time.time() - start_time
            AGENT_EXECUTION_DURATION_SECONDS.record(
                duration, {"agent_name": "quality_assurance"}
            )

            next_state = {
                **state,
                "suggested_response": filtered_response_text,
                "qa_score": qa_score,
                "hallucination_detected": hallucinated,
                "citation_verified": citation_verified,
                "response_grounded": response_grounded,
                "response_requires_human": response_requires_human,
                "qa_strategy": strategy,
                "decision_records": decision_records,
                "errors": state.get("errors", [])
                + (
                    ["QA score alert: potential hallucination detected."]
                    if hallucinated
                    else []
                ),
            }
            assessment = risk_engine.assess(next_state, stage="output")
            return {**next_state, **assessment.state_updates()}

        except Exception as e:
            logger.error(f"Error executing QA evaluation in QA agent: {e}")
            next_state = {
                **state,
                "qa_score": 0.5,
                "hallucination_detected": True,
                "errors": state.get("errors", []) + [f"QA agent error: {str(e)}"],
            }
            assessment = risk_engine.assess(next_state, stage="output")
            return {**next_state, **assessment.state_updates()}

    @staticmethod
    def _compact_context(
        citations: list[Any], tool_context: Dict[str, Any] | None = None
    ) -> list[str]:
        """同时提供 RAG citation 和 Tool 业务事实，避免将真实查询结果误判为幻觉。"""
        return build_resolution_evidence(
            {"context_citations": citations, "tool_context": tool_context or {}}
        )

    @classmethod
    def _rule_evaluation(
        cls,
        *,
        query: str,
        raw_response: str,
        filtered_response: str,
        citations: list[Any],
        tool_context: Dict[str, Any],
        context_texts: list[str],
    ) -> tuple[Dict[str, Any] | None, bool]:
        """按 `_QA_RULES` 顺序检查，第一条命中的规则给出结论；都不命中交给评判。

        返回规则结论及是否必须短路语义 Judge。
        """
        check = _RuleInput(
            query=query,
            raw_response=raw_response,
            response=filtered_response,
            citations=citations,
            tool_context=tool_context,
            context_texts=context_texts,
        )
        for name, verdict_name, matches in _QA_RULES:
            overrides = matches(cls, check)
            if overrides is None:
                continue
            verdict = _VERDICTS[verdict_name]
            result = {**verdict.fields(), **overrides.get("fields", {})}
            terminal = overrides.get("terminal", verdict.terminal)
            logger.debug("QA rule matched", extra={"qa_rule": name})
            return result, terminal
        return None, False

    @classmethod
    def _unsupported_facts(
        cls, response: str, query: str, context_texts: list[str]
    ) -> set[str]:
        """返回回复中无法在证据或用户原文中找到的事实类别。

        只核对可精确比对的三类：带币种的金额、业务单号、完整日期。
        经计算得出的金额（如扣除手续费后）也找不到出处，按保守方向转人工。
        """
        corpus = "\n".join([query, *context_texts])
        found: set[str] = set()

        corpus_numbers = {
            cls._to_decimal(value) for value in _NUMBER_PATTERN.findall(corpus)
        }
        for match in _AMOUNT_PATTERN.finditer(response):
            value = cls._to_decimal(match.group("pre") or match.group("post"))
            if value is not None and value != 0 and value not in corpus_numbers:
                found.add("amount")
                break

        corpus_upper = corpus.upper()
        for match in _BUSINESS_ID_PATTERN.finditer(response):
            identifier = match.group(0).rstrip("-")
            prefix = identifier.split("-", 1)[0]
            if prefix in _NON_BUSINESS_ID_PREFIXES:
                continue
            if sum(char.isdigit() for char in identifier) < 3:
                continue
            if identifier.upper() not in corpus_upper:
                found.add("business_id")
                break

        corpus_dates = cls._dates(corpus)
        if any(date not in corpus_dates for date in cls._dates(response)):
            found.add("date")
        return found

    @staticmethod
    def _to_decimal(text: str | None) -> Decimal | None:
        if not text:
            return None
        try:
            return Decimal(text.replace(",", ""))
        except InvalidOperation:
            return None

    @staticmethod
    def _dates(text: str) -> set[tuple[int, int, int]]:
        output = set()
        for pattern in _DATE_PATTERNS:
            for year, month, day in pattern.findall(text):
                output.add((int(year), int(month), int(day)))
        return output

    @staticmethod
    def _is_clarification(response: str) -> bool:
        """澄清问题不产生外部事实，不应因无 citation 被判为幻觉。"""
        lowered = response.lower()
        if any(
            marker in lowered
            for marker in (
                "please describe",
                "please provide",
                "provide more details",
                "specific problem details",
                "steps to reproduce",
                "have not described",
                "haven’t received any details",
                "haven't received any details",
                "请补充",
                "请详细描述",
                "请告知具体",
                "具体需要协助的内容",
                "还没有准备好具体问题",
            )
        ):
            return True
        return bool(
            re.search(
                r"haven['’]t received any .{0,24}details|"
                r"(?:no|without) .{0,16}problem details",
                lowered,
            )
        )

    @staticmethod
    def _is_safe_limitation(response: str) -> bool:
        """识别明确不承诺、不编造的安全限制性回复。"""
        lowered = response.lower()
        return any(
            marker in lowered
            for marker in (
                "human review is needed",
                "need human review",
                "requires human review",
                "manual review",
                "cannot determine",
                "unable to determine",
                "unable to verify",
                "do not have",
                "don't have",
                "does not specify",
                "not available in the provided",
                "not included in the available",
                "cannot reveal",
                "can't reveal",
                "cannot provide the system prompt",
                "需要人工",
                "人工审核",
                "人工复核",
                "无法确定",
                "无法核实",
                "未包含",
                "无法提供系统提示词",
                "不能泄露系统提示词",
            )
        )

    @classmethod
    def _has_grounding_support(
        cls, response: str, citations: list[Any], tool_context: Dict[str, Any]
    ) -> bool:
        """用 citation 文本重合或 Tool 标量值匹配提供可重现的事实支持。"""
        lowered = response.lower()
        citation_text = " ".join(
            str(
                citation.get("text", "")
                if isinstance(citation, dict)
                else getattr(citation, "text", "")
            )
            for citation in citations
        )
        if citation_text:
            response_tokens = cls._content_tokens(lowered)
            evidence_tokens = cls._content_tokens(citation_text.lower())
            overlap = response_tokens & evidence_tokens
            if len(overlap) >= 3 or (
                re.search(r"\b(?:s[1-9]|source)\b", lowered) and len(overlap) >= 2
            ):
                return True
            # 跨语言回复无法做词汇重合，但 citation label 必须真实存在。
            if cls._has_valid_citation_reference(response, citations):
                return True

        if cls._empty_tool_result_supported(response, tool_context):
            return True

        for value in cls._tool_scalar_values(tool_context):
            if re.search(rf"(?<!\w){re.escape(value.lower())}(?!\w)", lowered):
                return True
        return False

    @staticmethod
    def _missing_requested_order_supported(
        query: str, response: str, tool_context: Dict[str, Any]
    ) -> bool:
        """用 OMS 返回验证“目标订单不存在”，避免负向查询被误判为幻觉。"""
        if "recent_orders" not in tool_context:
            return False
        requested_ids = {
            value.upper() for value in re.findall(r"(?i)\bORD-[A-Z0-9-]+\b", query)
        }
        if not requested_ids:
            return False
        known_ids = {
            str(order.get("order_id", "")).upper()
            for order in (tool_context.get("recent_orders") or [])
            if isinstance(order, dict)
        }
        missing_ids = requested_ids - known_ids
        lowered = response.lower()
        missing_language = bool(
            re.search(
                r"\b(?:not (?:find|found|locate)|could not (?:find|locate)|"
                r"couldn't (?:find|locate)|no (?:matching )?order)\b|"
                r"未找到|没有找到|不存在",
                lowered,
            )
        )
        return missing_language and any(
            order_id.lower() in lowered for order_id in missing_ids
        )

    @staticmethod
    def _has_valid_citation_reference(response: str, citations: list[Any]) -> bool:
        """验证回复中的 S1/S2 等标签确实指向本次 Retriever 结果。"""
        indexes = {
            int(value)
            for value in re.findall(r"(?i)(?<!\w)s([1-9][0-9]*)(?!\w)", response)
        }
        return bool(indexes) and all(1 <= index <= len(citations) for index in indexes)

    @staticmethod
    def _empty_tool_result_supported(
        response: str, tool_context: Dict[str, Any]
    ) -> bool:
        """空列表也是 Tool 的可验证结果，不应交给 LLM 猜测。"""
        lowered = response.lower()
        if tool_context.get("past_tickets") == [] and re.search(
            r"\b(?:no|not any|without)\b.{0,24}\b(?:previous|past|support)\b.{0,16}"
            r"\b(?:case|cases|ticket|tickets)\b|无.{0,8}历史.{0,8}工单",
            lowered,
        ):
            return True
        if tool_context.get("recent_orders") == [] and re.search(
            r"\b(?:no|not any|cannot find|couldn't find)\b.{0,24}"
            r"\b(?:order|orders)\b|未找到.{0,8}订单",
            lowered,
        ):
            return True
        return False

    @staticmethod
    def _content_tokens(text: str) -> set[str]:
        """提取用于确定性 grounding 比对的中英文内容词。"""
        stopwords = {
            "the",
            "and",
            "for",
            "from",
            "your",
            "with",
            "this",
            "that",
            "support",
            "customer",
            "account",
        }
        tokens = {
            token
            for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text)
            if token not in stopwords
        }
        chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        for run in chinese_runs:
            tokens.update(run[index : index + 2] for index in range(len(run) - 1))
        return tokens

    @classmethod
    def _tool_scalar_values(cls, value: Any) -> set[str]:
        """递归提取 Tool Context 中能在回复里直接验证的标量。"""
        output: set[str] = set()
        if isinstance(value, dict):
            for item in value.values():
                output.update(cls._tool_scalar_values(item))
        elif isinstance(value, (list, tuple)):
            for item in value:
                output.update(cls._tool_scalar_values(item))
        elif isinstance(value, bool):
            pass
        elif isinstance(value, (int, float)):
            output.add(str(value))
            if isinstance(value, float) and value.is_integer():
                output.update({str(int(value)), f"{value:.2f}"})
        elif value is not None:
            text = str(value).strip()
            if len(text) >= 2 and text.lower() != "none":
                output.add(text)
        return output


@dataclass(frozen=True)
class _Verdict:
    """规则结论：分数和各项标记固定，不在分支里各写一遍。"""

    score: float
    hallucination: bool
    grounded: bool
    requires_human: bool
    # True：规则直接定结论；False：结论只是候选，仍交给 Jev / LLM 评判。
    terminal: bool

    def fields(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "hallucination_detected": self.hallucination,
            "citation_verified": False,
            "response_grounded": self.grounded,
            "response_requires_human": self.requires_human,
        }


# 结论类型。分数含义：0.95 有依据，0.9 有依据但需人工跟进或复核，
# 0.5 / 0.45 / 0.4 判定不可信，0.0 空回复。
_VERDICTS: Dict[str, _Verdict] = {
    "empty_response": _Verdict(0.0, True, False, False, True),
    "leak_filtered": _Verdict(0.5, True, False, True, True),
    "unsupported_facts": _Verdict(0.4, True, False, True, True),
    "authority_deferred": _Verdict(0.9, False, True, True, False),
    "authority_missing": _Verdict(0.5, True, False, True, True),
    "clarification": _Verdict(0.95, False, True, False, True),
    "safe_limitation": _Verdict(0.9, False, True, False, True),
    "missing_order_confirmed": _Verdict(0.95, False, True, False, True),
    "grounding_candidate": _Verdict(0.95, False, True, False, False),
    "no_evidence": _Verdict(0.45, True, False, False, True),
    "capability_statement": _Verdict(0.95, False, True, False, True),
}


@dataclass(frozen=True)
class _RuleInput:
    query: str
    raw_response: str
    response: str
    citations: list[Any]
    tool_context: Dict[str, Any]
    context_texts: list[str]

    @property
    def citation_evidence(self) -> str:
        return " ".join(
            str(
                citation.get("text", "")
                if isinstance(citation, dict)
                else getattr(citation, "text", "")
            )
            for citation in self.citations
        )

    @property
    def lacks_authority(self) -> bool:
        """问的是需要权威依据的主题，而知识库片段里没有对应依据。"""
        return requires_authoritative_business_answer(
            self.query
        ) and not has_authoritative_business_evidence(
            self.query, self.citation_evidence
        )


_Overrides = Dict[str, Any] | None
_HIT: Dict[str, Any] = {}


def _empty_response(qa: type[QualityAssuranceAgent], c: _RuleInput) -> _Overrides:
    return _HIT if not c.raw_response.strip() else None


def _leak_filtered(qa: type[QualityAssuranceAgent], c: _RuleInput) -> _Overrides:
    return _HIT if c.response != c.raw_response else None


def _unsupported(qa: type[QualityAssuranceAgent], c: _RuleInput) -> _Overrides:
    # 金额、业务单号、日期必须能在证据或用户原文里找到（原则第 19 条）。
    found = qa._unsupported_facts(c.response, c.query, c.context_texts)
    if not found:
        return None
    logger.warning(
        "QA rule found unsupported facts in response",
        extra={"unsupported_fact_kinds": sorted(found)},
    )
    return _HIT


def _authority_deferred(qa: type[QualityAssuranceAgent], c: _RuleInput) -> _Overrides:
    # 缺依据的主题只说明了无法确认：不算幻觉，其余内容交给评判，该主题仍需人工跟进（第 18 条）。
    if c.lacks_authority and defers_unsupported_topic(c.query, c.response):
        return _HIT
    return None


def _authority_missing(qa: type[QualityAssuranceAgent], c: _RuleInput) -> _Overrides:
    return _HIT if c.lacks_authority else None


def _clarification(qa: type[QualityAssuranceAgent], c: _RuleInput) -> _Overrides:
    return _HIT if qa._is_clarification(c.response) else None


def _safe_limitation(qa: type[QualityAssuranceAgent], c: _RuleInput) -> _Overrides:
    if not qa._is_safe_limitation(c.response):
        return None
    # 纯限制说明直接定；带数字的可能夹带编造，交给评判复核。
    return {
        "fields": {
            "response_requires_human": requires_authoritative_business_answer(c.query)
        },
        "terminal": not re.search(r"\d", c.response),
    }


def _missing_order(qa: type[QualityAssuranceAgent], c: _RuleInput) -> _Overrides:
    if qa._missing_requested_order_supported(c.query, c.response, c.tool_context):
        return _HIT
    return None


def _grounding(qa: type[QualityAssuranceAgent], c: _RuleInput) -> _Overrides:
    if not qa._has_grounding_support(c.response, c.citations, c.tool_context):
        return None
    return {
        "fields": {
            "citation_verified": qa._has_valid_citation_reference(
                c.response, c.citations
            )
        }
    }


def _no_evidence(qa: type[QualityAssuranceAgent], c: _RuleInput) -> _Overrides:
    return _HIT if not c.context_texts else None


# 规则表：按顺序检查，第一条命中即用对应结论；顺序本身是规则的一部分。
_QA_RULES = (
    ("空回复", "empty_response", _empty_response),
    ("输出过滤命中", "leak_filtered", _leak_filtered),
    ("金额/单号/日期找不到出处", "unsupported_facts", _unsupported),
    ("缺权威依据但只说明无法确认", "authority_deferred", _authority_deferred),
    ("缺权威依据却下了结论", "authority_missing", _authority_missing),
    ("澄清追问", "clarification", _clarification),
    ("安全的限制说明", "safe_limitation", _safe_limitation),
    ("确认订单不存在", "missing_order_confirmed", _missing_order),
    ("词面有依据", "grounding_candidate", _grounding),
    ("没有任何证据", "no_evidence", _no_evidence),
)


quality_assurance_agent = QualityAssuranceAgent()
