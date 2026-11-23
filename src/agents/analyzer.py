import logging
import time
from typing import Any, Dict

from src.config import settings
from src.decision import decision_service
from src.agents.scope import out_of_scope_reason, scope_context_updates
from src.guardrails.jailbreak_detection import detect_jailbreak
from src.guardrails.pii_detection import anonymize_pii
from src.guardrails.prompt_injection import analyze_prompt_injection
from src.guardrails.qwen3_guard import merge_qwen3_guard_result, qwen3_guard
from src.guardrails.security_policy import build_security_block
from src.llm.provider import llm_provider
from src.models.intents import (
    DEFAULT_INTENT,
    IntentType,
    intent_defaults,
    normalize_intent,
)
from src.observability.metrics import (
    AGENT_EXECUTION_DURATION_SECONDS,
    TICKET_SENTIMENT_TOTAL,
)
from src.observability.sanitization import redact_text
from src.risk.engine import risk_engine
from src.agents.classification import (
    AUTO_ANSWER,
    CAPABILITY,
    build_classification,
    fallback_classification,
    needs_review,
    normalize_answers,
    sentiment_label,
)

logger = logging.getLogger("supportgpt.agents.analyzer")


class TicketAnalyzerAgent:
    """负责分析客服请求并执行输入侧安全检查。

    输出情绪、优先级、意图和业务部门，供后续节点路由使用。
    """

    async def analyze(self, state: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Analyzer Node started for customer: {state.get('customer_id')}")

        original_text = state.get("description", "")
        subject = state.get("subject", "")
        combined_text = f"Subject: {subject}\nDescription: {original_text}"

        # 1. 使用多层检测阻断直接 Prompt Injection。
        injection = analyze_prompt_injection(combined_text, source="user_input")
        if injection.detected:
            logger.warning(
                "Prompt injection detected by layered guardrails",
                extra={
                    "ticket_id": state.get("ticket_id"),
                    "risk_score": injection.risk_score,
                    "security_source": injection.source,
                },
            )
            return build_security_block(
                state,
                threat_type="Prompt injection attempt",
                source=injection.source,
                risk_score=injection.risk_score,
                findings=[*injection.layers, *injection.signals],
            )

        # 2. Security Check: Jailbreak Detection
        if detect_jailbreak(combined_text):
            logger.warning("Jailbreak pattern detected by guardrails.")
            return build_security_block(
                state,
                threat_type="Jailbreak vector",
                source="user_input",
                risk_score=0.95,
                findings=["jailbreak_signature"],
            )

        # 3. 调用外部模型前先移除客户 PII。
        clean_description = anonymize_pii(original_text)
        clean_subject = anonymize_pii(subject)
        memory_context = str(state.get("memory_prompt_context", ""))
        semantic_text = redact_text(
            f"Subject: {clean_subject}\nDescription: {clean_description}"
        )

        # 4. 规则通过后再使用 Qwen3Guard 检测语义风险。
        semantic_result = await qwen3_guard.classify(semantic_text, source="user_input")
        state = merge_qwen3_guard_result(state, semantic_result)
        if semantic_result.block_recommended:
            return build_security_block(
                state,
                threat_type="Qwen3Guard semantic safety violation",
                source=semantic_result.source,
                risk_score=semantic_result.policy_score,
                findings=[
                    f"semantic_severity:{semantic_result.severity}",
                    *(
                        f"semantic_category:{item}"
                        for item in semantic_result.categories
                    ),
                ],
            )

        current_text = clean_description or clean_subject

        # 5. Jev 回答分类问题；重要情况 LLM 复核；Jev 不可用时 LLM 为主（原则第三、五、七节）。
        try:
            in_tok = 0
            out_tok = 0
            decision_records = list(state.get("decision_records", []))
            classifier_input = self._classifier_input(
                clean_subject,
                clean_description,
                memory_context,
            )
            try:
                decision = await decision_service.classify_ticket(classifier_input)
            except Exception as exc:
                # Jev 调用异常按不可用处理，交给 LLM（第七节）。
                logger.warning("Jev classification unavailable: %s", exc)
                decision = None
            jev = None
            if decision is not None:
                in_tok += decision.result.input_tokens
                out_tok += decision.result.output_tokens
                if decision.result.enabled:
                    decision_records.append(
                        decision.result.audit_record(
                            accepted=decision.accepted,
                            fallback_reason=decision.fallback_reason,
                        )
                    )
                if decision.accepted and decision.analysis is not None:
                    jev = normalize_answers(decision.analysis)
            jev_status = "accepted" if jev is not None else "unavailable"

            llm = None
            if jev is None:
                review_status = "primary"
            elif settings.CLASSIFICATION_REVIEW_ENABLED and needs_review(jev):
                review_status = "done"
            else:
                review_status = "not_needed"
            if review_status in {"primary", "done"}:
                try:
                    llm, llm_in_tok, llm_out_tok = await llm_provider.classify_message(
                        classifier_input
                    )
                    in_tok += llm_in_tok
                    out_tok += llm_out_tok
                except Exception as exc:
                    logger.warning("LLM classification unavailable: %s", exc)
                    review_status = "unavailable"

            if jev is None and llm is None:
                classification = fallback_classification(
                    out_of_scope_reason(current_text)
                )
            else:
                classification = build_classification(
                    jev=jev,
                    llm=llm,
                    jev_status=jev_status,
                    review_status=review_status,
                )
            intent = normalize_intent(classification["category"])
            sentiment = sentiment_label(classification)

            state["tokens_input"] = state.get("tokens_input", 0) + in_tok
            state["tokens_output"] = state.get("tokens_output", 0) + out_tok
            TICKET_SENTIMENT_TOTAL.add(1, {"sentiment": sentiment})
            AGENT_EXECUTION_DURATION_SECONDS.record(
                time.time() - start_time, {"agent_name": "ticket_analyzer"}
            )

            defaults = intent_defaults(intent)
            next_state = {
                **state,
                "description": clean_description,
                "subject": clean_subject,
                "sentiment": sentiment,
                "priority": classification["priority"],
                "classification": classification,
                # 旧字段保留给 Trace 和旧 Checkpoint：账务/故障咨询且不转人工。
                "read_only_inquiry": bool(
                    intent in {IntentType.BILLING_DISPUTE, IntentType.OUTAGE_REPORT}
                    and classification["handling"] == AUTO_ANSWER
                ),
                "intent": intent,
                "department": defaults.department,
                # 只给没有分类结论的旧打分路径用；有结论时不参与转人工。
                "analyzer_confidence": 0.5 if classification["mode"] == "unsure" else 0.95,
                "analyzer_strategy": classification["category_source"],
                "decision_records": decision_records,
                "errors": state.get("errors", []),
            }
            if classification["handling"] == CAPABILITY:
                # 是否范围外由模型判断；规则只挑能力说明的措辞（如“无法查询天气”）。
                reason = (
                    out_of_scope_reason(current_text)
                    or classification["scope_reason"]
                )
                classification["scope_reason"] = reason
                next_state.update(
                    scope_context_updates(reason, classification["scope_source"])
                )
            assessment = risk_engine.assess(next_state, stage="input")
            return {**next_state, **assessment.state_updates()}
        except Exception as e:
            logger.error(f"Error executing ticket classification: {e}")
            next_state = {
                **state,
                "errors": state.get("errors", []) + [f"Analyzer agent error: {str(e)}"],
                "sentiment": "neutral",
                "priority": "medium",
                "department": "general",
                "intent": DEFAULT_INTENT,
                "analyzer_confidence": 0.0,
            }
            assessment = risk_engine.assess(next_state, stage="input")
            return {**next_state, **assessment.state_updates()}

    @staticmethod
    def _classifier_input(subject: str, description: str, memory_context: str) -> str:
        """明确分隔历史和当前输入，当前任务始终拥有更高优先级。"""
        current = f"Current Subject: {subject}\nCurrent Description: {description}"
        return f"{memory_context}\n\n{current}" if memory_context else current


ticket_analyzer_agent = TicketAnalyzerAgent()
