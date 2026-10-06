import logging
import json
import re
import time
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
    requires_authoritative_business_answer,
)

logger = logging.getLogger("supportgpt.agents.quality_assurance")


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
                rule_result, rule_terminal = {
                    "score": 0.95,
                    "hallucination_detected": False,
                    "citation_verified": False,
                    "response_grounded": True,
                    "response_requires_human": False,
                }, True
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
                elif rule_result is not None:
                    # Jev 不可用时保留已有的可验证规则结论。
                    qa_eval = rule_result
                    strategy = "rule"
                else:
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
        """返回规则结论及是否必须短路语义 Judge。"""
        if not raw_response.strip():
            return (
                {
                    "score": 0.0,
                    "hallucination_detected": True,
                    "citation_verified": False,
                    "response_grounded": False,
                    "response_requires_human": False,
                },
                True,
            )
        if filtered_response != raw_response:
            return (
                {
                    "score": 0.5,
                    "hallucination_detected": True,
                    "citation_verified": False,
                    "response_grounded": False,
                    "response_requires_human": True,
                },
                True,
            )
        citation_evidence = " ".join(
            str(
                citation.get("text", "")
                if isinstance(citation, dict)
                else getattr(citation, "text", "")
            )
            for citation in citations
        )
        if requires_authoritative_business_answer(
            query
        ) and not has_authoritative_business_evidence(query, citation_evidence):
            return (
                {
                    "score": 0.5,
                    "hallucination_detected": True,
                    "citation_verified": False,
                    "response_grounded": False,
                    "response_requires_human": True,
                },
                True,
            )
        if cls._is_clarification(filtered_response):
            return (
                {
                    "score": 0.95,
                    "hallucination_detected": False,
                    "citation_verified": False,
                    "response_grounded": True,
                    "response_requires_human": False,
                },
                True,
            )
        if cls._is_safe_limitation(filtered_response):
            return (
                {
                    "score": 0.9,
                    "hallucination_detected": False,
                    "citation_verified": False,
                    "response_grounded": True,
                    "response_requires_human": requires_authoritative_business_answer(
                        query
                    ),
                },
                True,
            )
        if cls._missing_requested_order_supported(
            query, filtered_response, tool_context
        ):
            return (
                {
                    "score": 0.95,
                    "hallucination_detected": False,
                    "citation_verified": False,
                    "response_grounded": True,
                    "response_requires_human": False,
                },
                True,
            )
        if cls._has_grounding_support(filtered_response, citations, tool_context):
            return (
                {
                    "score": 0.95,
                    "hallucination_detected": False,
                    "citation_verified": cls._has_valid_citation_reference(
                        filtered_response, citations
                    ),
                    "response_grounded": True,
                    "response_requires_human": False,
                },
                False,
            )
        if not context_texts:
            return (
                {
                    "score": 0.45,
                    "hallucination_detected": True,
                    "citation_verified": False,
                    "response_grounded": False,
                    "response_requires_human": False,
                },
                True,
            )
        return None, False

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
                "revisión humana",
                "revisión manual",
                "no encuentro en la información disponible",
                "no dispongo de información",
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


quality_assurance_agent = QualityAssuranceAgent()
