import logging
import re
import time
from typing import Any, Dict

from src.decision import decision_service
from src.agents.record_queries import requested_record_tool
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
from src.agents.classification import (
    AUTO_ANSWER,
    build_classification,
    sentiment_label,
)

logger = logging.getLogger("supportgpt.agents.analyzer")

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
        record_tool = requested_record_tool(current_text)
        complaint_rule = is_explicit_complaint(
            " ".join(f"{clean_subject} {clean_description}".lower().split())
        )

        # 明确无关的新问题不继承历史业务实体，也不需要业务意图模型判断（第 3 条）。
        scope_reason = out_of_scope_reason(current_text)
        if (
            scope_reason
            and not semantic_result.degraded
            and not state.get("risk_requires_human")
        ):
            classification = build_classification(
                text=current_text,
                category=IntentType.INFORMATION_REQUEST,
                category_source="rule",
                jev_status="not_run",
                jev_analysis=None,
                complaint_rule=False,
                record_tool=None,
                out_of_scope=True,
                scope_reason=scope_reason,
                scope_source="rule",
            )
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
                "classification": classification,
                "read_only_inquiry": False,
                **scope_context_updates(scope_reason, "rule"),
            }
            assessment = risk_engine.assess(next_state, stage="input")
            return {**next_state, **assessment.state_updates()}

        # 5. Jev 为主；Jev 不可用或没把握时，规则有明确结论用规则，否则 LLM（第 20、21 条）。
        try:
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
                jev_status = "accepted"
            elif decision.result.available:
                jev_status = "unsure"
            else:
                jev_status = "unavailable"

            if jev_status == "accepted":
                analysis = dict(decision.analysis)
                strategy = "jev"
            else:
                rule_analysis = (
                    None
                    if semantic_result.degraded
                    else self._match_rule(current_text)
                )
                rule_analysis = self._apply_memory_rule(
                    current_text, state, rule_analysis
                )
                # 关键词零命中只是兜底的通用咨询，不算规则结论。
                if rule_analysis is not None and rule_analysis.get("rule_fallback"):
                    rule_analysis = None
                if rule_analysis is not None:
                    analysis = rule_analysis
                    strategy = "rule"
                else:
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
            if strategy == "llm":
                # 兜底时具体设备的保修查询和实体设备故障按保修处理。
                normalized_intent = self._device_intent_override(
                    current_text, normalized_intent
                )
            analyzer_confidence = self._confidence(
                analysis.get("confidence_score", analysis.get("confidence", 0.0))
            )
            if not intent_is_known:
                analyzer_confidence = min(analyzer_confidence, 0.5)

            jev_out_of_scope = bool(
                strategy == "jev"
                and analysis.get("out_of_scope")
                and normalized_intent == IntentType.INFORMATION_REQUEST
                and not has_support_topic(current_text)
                and not semantic_result.degraded
            )
            classification = build_classification(
                text=current_text,
                category=normalized_intent,
                category_source=strategy,
                jev_status=jev_status,
                jev_analysis=decision.analysis,
                complaint_rule=complaint_rule,
                record_tool=record_tool,
                out_of_scope=jev_out_of_scope,
                scope_reason="no_support_capability" if jev_out_of_scope else None,
                scope_source="jev" if jev_out_of_scope else None,
            )
            sentiment = sentiment_label(classification)

            state["tokens_input"] = state.get("tokens_input", 0) + in_tok
            state["tokens_output"] = state.get("tokens_output", 0) + out_tok
            TICKET_SENTIMENT_TOTAL.add(1, {"sentiment": sentiment})
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
                "priority": classification["priority"],
                "classification": classification,
                # 旧字段保留给 Trace 和旧 Checkpoint：账务/故障咨询且不转人工。
                "read_only_inquiry": bool(
                    normalized_intent
                    in {IntentType.BILLING_DISPUTE, IntentType.OUTAGE_REPORT}
                    and classification["handling"] == AUTO_ANSWER
                ),
                "intent": normalized_intent,
                "department": defaults.department,
                "analyzer_confidence": analyzer_confidence,
                "analyzer_strategy": strategy,
                "decision_records": decision_records,
                "errors": state.get("errors", []),
            }
            if jev_out_of_scope:
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
            # 查询订单的发票不是物流请求；保留明确的订单状态/物流歧义。
            if _ORDER_STATUS.search(normalized.replace("查询订单", "订单")):
                candidates.append(IntentType.ORDER_STATUS)
        elif _API_INCIDENT.search(normalized):
            candidates.append(IntentType.OUTAGE_REPORT)
        elif is_explicit_complaint(normalized):
            # 项目没有独立投诉 Intent：归 feedback，再由升级逻辑提到 high 优先级。
            candidates.append(IntentType.FEEDBACK)
        else:
            if _CANCELLATION_ACTION.search(
                normalized
            ) and not _CANCELLATION_INFORMATION.search(normalized):
                candidates.append(IntentType.ORDER_CANCELLATION)
            if _ORDER_STATUS.search(normalized):
                candidates.append(IntentType.ORDER_STATUS)
            if _ACCOUNT_INCIDENT.search(normalized):
                candidates.append(IntentType.ACCOUNT_SUPPORT)
            if (
                _WARRANTY_ACTION.search(normalized)
                or requested_record_tool(normalized) == "warranty.get_entitlements"
                or _DEVICE_FAULT.search(normalized)
            ):
                candidates.append(IntentType.WARRANTY_CLAIM)
            if _FEEDBACK.search(normalized):
                candidates.append(IntentType.FEEDBACK)

        if len(set(candidates)) > 1:
            return None
        intent = candidates[0] if candidates else IntentType.INFORMATION_REQUEST
        defaults = intent_defaults(intent)
        return {
            "intent": intent,
            "priority": defaults.priority,
            "department": defaults.department,
            "confidence_score": 0.95,
            # 没有任何业务关键词命中；仅供记忆续接使用，不作为最终分类。
            "rule_fallback": not candidates,
        }

    @staticmethod
    def _device_intent_override(text: str, intent: IntentType) -> IntentType:
        """模型把“我的设备还在保修期内吗 / 设备老断网”判成通用咨询或 API 故障时纠正。

        只改这两类明确场景：其他意图、以及带 API/服务信号的故障保持原判。
        """
        if intent not in {IntentType.INFORMATION_REQUEST, IntentType.OUTAGE_REPORT}:
            return intent
        normalized = " ".join(text.lower().split())
        if _API_SIGNAL.search(normalized):
            return intent
        if (
            requested_record_tool(normalized) == "warranty.get_entitlements"
            or _DEVICE_FAULT.search(normalized)
        ):
            return IntentType.WARRANTY_CLAIM
        return intent

    @staticmethod
    def _confidence(value: Any) -> float:
        """将 LLM 分类置信度约束在 0 到 1。"""
        try:
            return min(max(float(value), 0.0), 1.0)
        except (TypeError, ValueError):
            return 0.0

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
