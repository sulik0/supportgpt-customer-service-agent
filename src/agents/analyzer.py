import logging
import re
import time
from typing import Any, Dict

from src.decision import decision_service
from src.agents.scope import has_support_topic, out_of_scope_reason, scope_context_updates
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

logger = logging.getLogger("supportgpt.agents.analyzer")

_BILLING_DOMAIN = re.compile(
    r"\b(refund|payment|invoice|billing|charged?|card payment|bank statement)\b|"
    r"退款|支付|发票|账单|扣款|银行卡"
)
_BILLING_INFORMATION_QUERY = re.compile(
    r"(?:了解|咨询|请问).{0,12}(?:退款|退费|退钱|发票|账单)|"
    r"(?:退款|退费|退钱|发票|账单).{0,16}"
    r"(?:条件|要求|资格|政策|规则|流程|时限|期限|范围|怎么|如何|什么)|"
    r"(?:什么|哪些|怎么|如何).{0,12}(?:退款|退费|退钱)|"
    r"\b(?:refund (?:policy|conditions?|requirements?|eligibility|rules?|process)|"
    r"what (?:conditions?|requirements?).{0,20}refund|how does (?:a )?refund work|"
    r"invoice (?:policy|rules?|total))\b"
)
_BILLING_ACTION_REQUEST = re.compile(
    r"(?:我要|需要|要求|申请|办理|帮我|给我)"
    r"(?:立即|现在|马上)?(?:退款|退费|退钱)|"
    r"我想(?:要)?(?:申请|办理)?(?:退款|退费|退钱)|"
    r"(?:退款|退费|退钱)(?:申请|办理)|"
    r"\b(?:i (?:need|want)(?: to request)?|please|request(?:ing)?)\b.{0,20}\brefund\b"
)
_EXPLICIT_NEGATIVE_SENTIMENT = re.compile(
    r"\b(?:angry|upset|furious|unacceptable|terrible|awful|disappointed|complain|complaint|"
    r"overcharged|wrongly charged|charged twice|failed|rejected|never arrived)\b|"
    r"不满|生气|愤怒|投诉|糟糕|离谱|坑人|欺骗|乱扣|错误扣款|"
    r"多扣|重复扣款|不能退|无法退|退款失败|退款被拒|未收到|没收到|迟迟"
)
_API_INCIDENT = re.compile(
    r"(?:\bapi\b|接口|服务).{0,50}"
    r"(?:\b504\b|\b503\b|error|timeout|timing out|down|offline|crash|broken|"
    r"connectivity|slow|报错|超时|宕机|离线|无法访问|故障|缓慢)|"
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
    r"invalid credentials|login keeps failing\b|无法登录|账户被锁|凭据失效"
)
_WARRANTY_ACTION = re.compile(
    r"\b(?:file|open|start|submit) (?:a )?(?:warranty )?claim\b|"
    r"\b(?:repair|replace) my (?:device|hardware|item)\b|"
    r"申请保修|发起维修|维修我的|更换我的"
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

        # 明确无关的新问题不继承历史业务实体，也不需要业务意图模型判断。
        scope_reason = out_of_scope_reason(clean_description or clean_subject)
        if (
            scope_reason
            and not semantic_result.degraded
            and not state.get("risk_requires_human")
        ):
            next_state = {
                **state,
                "description": clean_description,
                "subject": clean_subject,
                "intent": IntentType.INFORMATION_REQUEST,
                "department": "general",
                "priority": "low",
                "sentiment": "neutral",
                "analyzer_confidence": 0.95,
                "analyzer_strategy": "rule",
                **scope_context_updates(scope_reason, "rule"),
            }
            assessment = risk_engine.assess(next_state, stage="input")
            return {**next_state, **assessment.state_updates()}

        # 5. 规则生成可回退候选，Jev 启用时优先做封闭语义决策。
        try:
            rule_analysis = (
                None
                if semantic_result.degraded
                else self._match_rule(clean_description or clean_subject)
            )
            rule_analysis = self._apply_memory_rule(
                clean_description or clean_subject,
                state,
                rule_analysis,
            )
            analysis = rule_analysis
            strategy = "rule" if rule_analysis else "llm"
            in_tok = 0
            out_tok = 0
            decision_records = list(state.get("decision_records", []))
            classifier_input = self._classifier_input(
                clean_subject,
                clean_description,
                memory_context,
            )
            decision = await decision_service.classify_ticket(classifier_input)
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
                analysis = dict(decision.analysis)
                strategy = "jev"
            elif rule_analysis is None:
                analysis, llm_in_tok, llm_out_tok = (
                    await llm_provider.analyze_ticket(classifier_input)
                )
                in_tok += llm_in_tok
                out_tok += llm_out_tok
                strategy = "llm"

            # 所有分类结果在进入 State 前统一收敛到 IntentType。
            raw_intent = analysis.get("intent")
            normalized_intent = normalize_intent(raw_intent)
            intent_is_known = isinstance(raw_intent, IntentType) or (
                str(raw_intent).strip().lower() in IntentType.values()
            )
            analyzer_confidence = self._confidence(
                analysis.get("confidence_score", analysis.get("confidence", 0.0))
            )
            if not intent_is_known:
                analyzer_confidence = min(analyzer_confidence, 0.5)
            sentiment = self._normalize_sentiment(
                f"{clean_subject} {clean_description}".strip(),
                normalized_intent,
                analysis.get("sentiment", "neutral"),
            )

            # Increment token and latency stats
            state["tokens_input"] = state.get("tokens_input", 0) + in_tok
            state["tokens_output"] = state.get("tokens_output", 0) + out_tok

            # Track sentiment through OpenTelemetry Metrics.
            TICKET_SENTIMENT_TOTAL.add(
                1, {"sentiment": sentiment}
            )

            # Record execution latency
            duration = time.time() - start_time
            AGENT_EXECUTION_DURATION_SECONDS.record(
                duration, {"agent_name": "ticket_analyzer"}
            )

            defaults = intent_defaults(normalized_intent)
            next_state = {
                **state,
                "description": clean_description,
                "subject": clean_subject,
                "sentiment": sentiment,
                "priority": defaults.priority,
                "intent": normalized_intent,
                "department": defaults.department,
                "analyzer_confidence": analyzer_confidence,
                "analyzer_strategy": strategy,
                "decision_records": decision_records,
                "errors": state.get("errors", []),
            }
            if (
                strategy == "jev"
                and analysis.get("request_scope") == "out_of_scope"
                and normalized_intent == IntentType.INFORMATION_REQUEST
                and not has_support_topic(clean_description or clean_subject)
                and not semantic_result.degraded
            ):
                next_state.update(scope_context_updates("no_support_capability", "jev"))
            assessment = risk_engine.assess(next_state, stage="input")
            return {**next_state, **assessment.state_updates()}
        except Exception as e:
            logger.error(f"Error executing LLM ticket analysis: {e}")
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
    def _match_rule(text: str) -> Dict[str, Any] | None:
        """先区分实际业务操作与说明性咨询，歧义才交给 LLM。"""
        normalized = " ".join(text.lower().split())
        candidates: list[IntentType] = []
        if _BILLING_DOMAIN.search(normalized):
            candidates.append(IntentType.BILLING_DISPUTE)
            if _ORDER_STATUS.search(normalized):
                candidates.append(IntentType.ORDER_STATUS)
        elif _API_INCIDENT.search(normalized):
            candidates.append(IntentType.OUTAGE_REPORT)
        else:
            if _CANCELLATION_ACTION.search(
                normalized
            ) and not _CANCELLATION_INFORMATION.search(normalized):
                candidates.append(IntentType.ORDER_CANCELLATION)
            if _ORDER_STATUS.search(normalized):
                candidates.append(IntentType.ORDER_STATUS)
            if _ACCOUNT_INCIDENT.search(normalized):
                candidates.append(IntentType.ACCOUNT_SUPPORT)
            if _WARRANTY_ACTION.search(normalized):
                candidates.append(IntentType.WARRANTY_CLAIM)
            if _FEEDBACK.search(normalized):
                candidates.append(IntentType.FEEDBACK)

        if len(set(candidates)) > 1:
            return None
        intent = candidates[0] if candidates else IntentType.INFORMATION_REQUEST
        defaults = intent_defaults(intent)
        rule_sentiment = (
            "negative"
            if intent
            in {
                IntentType.BILLING_DISPUTE,
                IntentType.OUTAGE_REPORT,
                IntentType.ORDER_CANCELLATION,
                IntentType.ACCOUNT_SUPPORT,
            }
            else "positive" if intent == IntentType.FEEDBACK else "neutral"
        )
        return {
            "intent": intent,
            "priority": defaults.priority,
            "department": defaults.department,
            "sentiment": TicketAnalyzerAgent._normalize_sentiment(
                normalized, intent, rule_sentiment
            ),
            "confidence_score": 0.95,
        }

    @staticmethod
    def _confidence(value: Any) -> float:
        """将 LLM 分类置信度约束在 0 到 1。"""
        try:
            return min(max(float(value), 0.0), 1.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _normalize_sentiment(
        text: str, intent: IntentType, candidate: Any
    ) -> str:
        """将情绪与业务意图解耦，避免把中性政策咨询标成负向。"""
        normalized = " ".join(str(text).lower().split())
        sentiment = str(candidate or "neutral").strip().lower()
        if _EXPLICIT_NEGATIVE_SENTIMENT.search(normalized):
            return "negative"
        if (
            intent == IntentType.BILLING_DISPUTE
            and _BILLING_INFORMATION_QUERY.search(normalized)
            and not _BILLING_ACTION_REQUEST.search(normalized)
        ):
            return "neutral"
        return sentiment if sentiment in {"positive", "neutral", "negative"} else "neutral"

    @staticmethod
    def _classifier_input(subject: str, description: str, memory_context: str) -> str:
        """明确分隔历史和当前输入，当前任务始终拥有更高优先级。"""
        current = f"Current Subject: {subject}\nCurrent Description: {description}"
        return f"{memory_context}\n\n{current}" if memory_context else current

    @staticmethod
    def _apply_memory_rule(
        text: str,
        state: Dict[str, Any],
        analysis: Dict[str, Any] | None,
    ) -> Dict[str, Any] | None:
        """仅对显式指代做确定性意图续接，不用历史覆盖新意图。"""
        if analysis is None or analysis.get("intent") != IntentType.INFORMATION_REQUEST:
            return analysis
        normalized = " ".join(text.lower().split())
        order_id = (state.get("memory_active_entities") or {}).get("order_id")
        if not order_id:
            return analysis
        contextual_intent: IntentType | None = None
        if _CONTEXTUAL_CANCELLATION.search(normalized):
            contextual_intent = IntentType.ORDER_CANCELLATION
        elif _CONTEXTUAL_ORDER_STATUS.search(normalized):
            contextual_intent = IntentType.ORDER_STATUS
        if contextual_intent is None:
            return analysis
        defaults = intent_defaults(contextual_intent)
        return {
            "intent": contextual_intent,
            "priority": defaults.priority,
            "department": defaults.department,
            "sentiment": (
                "negative"
                if contextual_intent == IntentType.ORDER_CANCELLATION
                else "neutral"
            ),
            "confidence_score": 0.95,
        }


ticket_analyzer_agent = TicketAnalyzerAgent()
