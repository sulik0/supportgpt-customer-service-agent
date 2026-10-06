import json
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Tuple
from src.config import settings
from src.models.intents import (
    DEFAULT_INTENT,
    IntentType,
    normalize_intent,
)
from src.observability.tracing import (
    record_current_llm_io,
    set_span_attributes,
    trace_operation,
)
from src.resilience.executor import resilience_executor
from src.resilience.policies import llm_policy
from src.promptops.defaults import RESOLUTION_LANGUAGE_POLICY, RESOLUTION_OUTPUT_POLICY
from src.promptops.runtime import active_bundle


CHAT_LANGUAGE_POLICY = (
    "Reply in the language used in the latest user message. If that message explicitly "
    "asks for a different response language, use the requested language instead. "
    "Do not preserve a previous response language unless the latest user message asks you to."
)


def _resolution_messages(
    subject: str, description: str, context: str
) -> List[Dict[str, str]]:
    """旧的已发布 Bundle 也补充输出预算约束，实际 Prompt 仍记录到 Trace。"""
    messages = active_bundle().messages(
        "resolver", subject=subject, description=description, context=context
    )
    if RESOLUTION_OUTPUT_POLICY not in messages[0]["content"]:
        messages[0]["content"] += "\n" + RESOLUTION_OUTPUT_POLICY
    return messages


async def _complete_chat(
    client: Any,
    model: str,
    messages: List[Dict[str, str]],
    kwargs: dict,
    *,
    operation: str,
    json_mode: bool,
) -> Tuple[str, int, int]:
    """统一处理兼容服务的停止原因；只对截断的客服回复重写一次。"""
    from opentelemetry import trace

    input_tokens = output_tokens = 0
    request_messages = messages
    resolver = operation == "generate_resolution"
    for attempt in range(2 if resolver else 1):
        record_current_llm_io(input_value=request_messages, model=model)
        response = await client.chat.completions.create(
            model=model, messages=request_messages, temperature=0.0, **kwargs
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        input_tokens += int(response.usage.prompt_tokens or 0)
        output_tokens += int(response.usage.completion_tokens or 0)
        finish_reason = getattr(choice, "finish_reason", None)
        record_current_llm_io(output_value=content)
        set_span_attributes(
            trace.get_current_span(),
            {
                "gen_ai.response.finish_reasons": (
                    [finish_reason] if isinstance(finish_reason, str) else []
                ),
                "llm.completion_requests": attempt + 1,
                "llm.truncation_retries": attempt,
                "resolver.output_policy": "concise-v1" if resolver else None,
            },
        )
        if resolver and finish_reason == "length":
            if attempt:
                raise ValueError(
                    "Resolver reply remains truncated after one concise rewrite"
                )
            # 使用原始证据重新作答，不把被截断的草稿当作新证据。
            request_messages = [dict(message) for message in messages]
            request_messages[0]["content"] += (
                "\nThe previous generation exceeded its output budget. Rewrite the full answer "
                "in at most 3 short sentences, about 100 Chinese characters or 60 English words. "
                "Include the current status, exception and supported next step first. "
                "Omit optional policy and history. End with a complete sentence."
            )
            continue
        if json_mode:
            json.loads(content)
        return content, input_tokens, output_tokens
    raise RuntimeError("No completion returned")


def _ticket_classifier_prompt(text: str) -> str:
    """所有真实 Provider 共用同一份 Intent Taxonomy 与输出约束。"""
    return active_bundle().messages("analyzer", text=text)[1]["content"]


def _normalize_ticket_analysis(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """约束 Provider 分类输出，未知意图降级并降低置信度。"""
    normalized = dict(analysis)
    raw_intent = normalized.get("intent")
    known = isinstance(raw_intent, IntentType) or (
        str(raw_intent).strip().lower() in IntentType.values()
    )
    normalized["intent"] = normalize_intent(raw_intent)
    if not known:
        try:
            confidence = float(normalized.get("confidence_score", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        normalized["confidence_score"] = min(max(confidence, 0.0), 0.5)
    return normalized


def _mock_uses_chinese(text: str) -> bool:
    """让 Mock 在中英文演示中遵循当前输入和显式切换要求。"""
    lowered = text.lower()
    english_requests = (
        "用英文",
        "用英语",
        "使用英文",
        "英文回复",
        "reply in english",
        "respond in english",
        "answer in english",
    )
    chinese_requests = (
        "用中文",
        "用汉语",
        "使用中文",
        "中文回复",
        "reply in chinese",
        "respond in chinese",
        "answer in chinese",
    )
    if any(item in lowered for item in english_requests):
        return False
    if any(item in lowered for item in chinese_requests):
        return True
    return any("\u4e00" <= char <= "\u9fff" for char in text)


class BaseLLMProvider(ABC):
    """定义工单分析、回复生成、QA 和对话能力的统一接口。"""

    @abstractmethod
    async def analyze_ticket(self, text: str) -> Tuple[Dict[str, Any], int, int]:
        """
        Analyze sentiment, priority, intent, and department.
        Returns: (analysis_dict, input_tokens, output_tokens)
        """
        pass

    @abstractmethod
    async def generate_resolution(
        self, subject: str, description: str, context: str
    ) -> Tuple[str, int, int]:
        """
        Generate response text based on document context.
        Returns: (response_text, input_tokens, output_tokens)
        """
        pass

    @abstractmethod
    async def evaluate_qa(
        self, query: str, context: List[str], response: str
    ) -> Tuple[Dict[str, Any], int, int]:
        """
        Perform hallucination detection and response quality scoring.
        Returns: (qa_evaluation_dict, input_tokens, output_tokens)
        """
        pass

    @abstractmethod
    async def run_chat(
        self, history: List[Dict[str, str]], context: str
    ) -> Tuple[str, int, int]:
        """
        Run generic conversational completion.
        Returns: (completion, input_tokens, output_tokens)
        """
        pass


class MockLLMProvider(BaseLLMProvider):
    """提供无需外部凭据的确定性 Mock LLM，便于本地演示与测试。"""

    @trace_operation(name="supportgpt.llm.analyze_ticket", component="llm")
    async def analyze_ticket(self, text: str) -> Tuple[Dict[str, Any], int, int]:
        text_lower = text.lower()
        sentiment = "neutral"
        priority = "medium"
        department = "general"
        intent = DEFAULT_INTENT

        if any(
            x in text_lower
            for x in ["refund", "billing", "charge", "invoice", "payment", "card"]
        ):
            sentiment = "negative"
            priority = "high"
            department = "billing"
            intent = IntentType.BILLING_DISPUTE
        elif any(
            x in text_lower
            for x in ["down", "crash", "error", "bug", "broken", "offline", "slow"]
        ):
            sentiment = "negative"
            priority = "urgent"
            department = "technical"
            intent = IntentType.OUTAGE_REPORT
        elif "thank" in text_lower or "great" in text_lower or "love" in text_lower:
            sentiment = "positive"
            priority = "low"
            intent = IntentType.FEEDBACK

        analysis = {
            "sentiment": sentiment,
            "priority": priority,
            "department": department,
            "intent": intent,
            "confidence_score": 0.95,
        }
        return _normalize_ticket_analysis(analysis), 150, 45

    @trace_operation(name="supportgpt.llm.generate_resolution", component="llm")
    async def generate_resolution(
        self, subject: str, description: str, context: str
    ) -> Tuple[str, int, int]:
        desc_lower = description.lower()
        use_chinese = _mock_uses_chinese(description)
        if use_chinese and ("退款" in description or "账单" in description):
            response = (
                "感谢你联系我们处理账单问题。根据退款政策，退款申请需要在购买后 30 天内提交。"
                "我已为这笔交易发起退款审批，审批通过后预计 3 至 5 个工作日到账。"
            )
        elif use_chinese and any(item in description for item in ("故障", "报错", "无法访问", "宕机")):
            response = (
                "很抱歉服务中断。根据系统状态和技术文档，API 服务层发生了短暂故障，"
                "运维团队已部署修复。请清理缓存后重试，如仍有问题请继续告知我们。"
            )
        elif use_chinese:
            response = (
                "感谢你联系客户支持。我已查询相关产品指南，请进入“设置 → 偏好设置”并验证邮箱。"
                "如果还需要其他帮助，请继续告诉我。"
            )
        elif "billing" in desc_lower or "refund" in desc_lower:
            response = (
                "Thank you for reaching out regarding your billing issue. According to our refund policy: "
                "refund requests must be submitted within 30 days of purchase. I have initiated the refund "
                "approval process for your transaction, and it should reflect in your account within 3-5 business days."
            )
        elif "down" in desc_lower or "crash" in desc_lower or "error" in desc_lower:
            response = (
                "I apologize for the service disruption. Based on our system status and the technical documentation: "
                "we had a minor outage in our API server layer. Our DevOps team has deployed a patch, and "
                "services are now fully operational. Please clear your cache and try again. Let me know if you still see errors."
            )
        else:
            response = (
                "Thank you for contacting customer support. I have retrieved our product guides: "
                "To configure your account, please head to Settings -> Preferences, and verify your email. "
                "Let me know if you need any additional help!"
            )

        return response, 250, 80

    @trace_operation(name="supportgpt.llm.evaluate_qa", component="llm")
    async def evaluate_qa(
        self, query: str, context: List[str], response: str
    ) -> Tuple[Dict[str, Any], int, int]:
        # Simple heuristics for mock QA:
        # If response mentions policy and context has policy, high score.
        qa_score = 0.92
        hallucination_detected = False

        if len(context) == 0:
            qa_score = 0.45
            hallucination_detected = True

        evaluation = {
            "score": qa_score,
            "hallucination_detected": hallucination_detected,
            "citation_verified": len(context) > 0,
        }
        return evaluation, 220, 30

    @trace_operation(name="supportgpt.llm.run_chat", component="llm")
    async def run_chat(
        self, history: List[Dict[str, str]], context: str
    ) -> Tuple[str, int, int]:
        last_user_message = next(
            (
                message.get("content", "")
                for message in reversed(history)
                if message.get("role") == "user"
            ),
            "",
        )
        if _mock_uses_chinese(last_user_message):
            response = f"这是 SupportGPT 的自动回复。我已收到你的消息：“{last_user_message}”。还有什么可以帮助你？"
        else:
            response = f"This is an automated response from SupportGPT. I received your message: '{last_user_message}'. How else can I assist you today?"
        return response, 200, 40


class OpenAILLMProvider(BaseLLMProvider):
    """通过 OpenAI-compatible Chat Completions 提供统一 LLM 能力。

    可连接 OpenAI、DeepSeek、Qwen、vLLM 等兼容服务。
    """

    def __init__(self):
        from openai import AsyncOpenAI

        if not settings.LLM_API_KEY:
            raise ValueError("LLM_PROVIDER=openai 时必须配置 LLM_API_KEY")
        if not settings.LLM_MODEL_NAME:
            raise ValueError("LLM_PROVIDER=openai 时必须配置 LLM_MODEL_NAME")

        # base_url 为空时使用 OpenAI SDK 默认地址，配置后可连接兼容服务。
        self.client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL or None,
            max_retries=0,
        )
        self.model = settings.LLM_MODEL_NAME
        self.fast_client = self.client
        has_fast_endpoint = bool(
            settings.LLM_FAST_BASE_URL or settings.LLM_FAST_API_KEY
        )
        has_fast_model = bool(
            settings.LLM_FAST_MODEL_NAME
            or settings.LLM_ANALYZER_MODEL_NAME
            or settings.LLM_QA_MODEL_NAME
        )
        if has_fast_endpoint:
            if not has_fast_model:
                raise ValueError("独立 Fast Model 服务必须配置模型名")
            if not settings.LLM_FAST_API_KEY:
                raise ValueError("配置 LLM_FAST_BASE_URL 时必须同时配置 LLM_FAST_API_KEY")
            # 小模型可部署在与主模型不同的 OpenAI-compatible 服务。
            self.fast_client = AsyncOpenAI(
                api_key=settings.LLM_FAST_API_KEY,
                base_url=settings.LLM_FAST_BASE_URL or None,
                max_retries=0,
            )
        self.analyzer_model = (
            settings.LLM_ANALYZER_MODEL_NAME
            or settings.LLM_FAST_MODEL_NAME
            or self.model
        )
        self.qa_model = (
            settings.LLM_QA_MODEL_NAME or settings.LLM_FAST_MODEL_NAME or self.model
        )
        self.analyzer_client = (
            self.fast_client
            if has_fast_endpoint
            and (settings.LLM_ANALYZER_MODEL_NAME or settings.LLM_FAST_MODEL_NAME)
            else self.client
        )
        self.qa_client = (
            self.fast_client
            if has_fast_endpoint
            and (settings.LLM_QA_MODEL_NAME or settings.LLM_FAST_MODEL_NAME)
            else self.client
        )
        self.fallback_client = None
        self.fallback_model = settings.LLM_FALLBACK_MODEL_NAME
        if self.fallback_model:
            self.fallback_client = AsyncOpenAI(
                api_key=settings.LLM_FALLBACK_API_KEY,
                base_url=settings.LLM_FALLBACK_BASE_URL,
                max_retries=0,
            )

    async def _call_gpt(
        self,
        messages: List[Dict[str, str]],
        json_mode: bool = False,
        max_tokens: int | None = None,
        model: str | None = None,
        client: Any = None,
        operation: str = "chat",
    ) -> Tuple[str, int, int]:
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        selected_model = model or self.model
        selected_client = client or self.client

        async def invoke(target_client: Any, target_model: str) -> Tuple[str, int, int]:
            """单次 SDK 调用禁用内建重试，由 Resilience 统一管理。"""
            return await _complete_chat(
                target_client,
                target_model,
                messages,
                kwargs,
                operation=operation,
                json_mode=json_mode,
            )

        fallback = None
        if self.fallback_client is not None and self.fallback_model:
            fallback = lambda: invoke(self.fallback_client, self.fallback_model)
        result = await resilience_executor.execute(
            component="llm",
            operation=operation,
            call=lambda: invoke(selected_client, selected_model),
            policy=llm_policy(operation),
            fallback=fallback,
            fallback_name=self.fallback_model,
            circuit_key=f"llm:{selected_model}",
        )
        return result.unwrap()

    @trace_operation(name="supportgpt.llm.analyze_ticket", component="llm")
    async def analyze_ticket(self, text: str) -> Tuple[Dict[str, Any], int, int]:
        messages = active_bundle().messages("analyzer", text=text)
        content, in_tok, out_tok = await self._call_gpt(
            messages,
            json_mode=True,
            max_tokens=settings.LLM_ANALYZER_MAX_TOKENS,
            model=self.analyzer_model,
            client=self.analyzer_client,
            operation="analyze_ticket",
        )
        return _normalize_ticket_analysis(json.loads(content)), in_tok, out_tok

    @trace_operation(name="supportgpt.llm.generate_resolution", component="llm")
    async def generate_resolution(
        self, subject: str, description: str, context: str
    ) -> Tuple[str, int, int]:
        messages = _resolution_messages(subject, description, context)
        return await self._call_gpt(
            messages,
            json_mode=False,
            max_tokens=settings.LLM_RESOLVER_MAX_TOKENS,
            operation="generate_resolution",
        )

    @trace_operation(name="supportgpt.llm.evaluate_qa", component="llm")
    async def evaluate_qa(
        self, query: str, context: List[str], response: str
    ) -> Tuple[Dict[str, Any], int, int]:
        messages = active_bundle().messages(
            "qa", query=query, context=json.dumps(context, ensure_ascii=False), response=response
        )
        content, in_tok, out_tok = await self._call_gpt(
            messages,
            json_mode=True,
            max_tokens=settings.LLM_QA_MAX_TOKENS,
            model=self.qa_model,
            client=self.qa_client,
            operation="evaluate_qa",
        )
        return json.loads(content), in_tok, out_tok

    @trace_operation(name="supportgpt.llm.run_chat", component="llm")
    async def run_chat(
        self, history: List[Dict[str, str]], context: str
    ) -> Tuple[str, int, int]:
        system_msg = (
            "You are SupportGPT, a customer support AI assistant. Answer using the "
            f"retrieved context if available. {CHAT_LANGUAGE_POLICY}"
        )
        if context:
            system_msg += f"\n\nContext:\n{context}"

        messages = [{"role": "system", "content": system_msg}]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        return await self._call_gpt(
            messages, json_mode=False, operation="run_chat"
        )


class AzureOpenAILLMProvider(BaseLLMProvider):
    """通过 Azure OpenAI 部署实现统一 LLM Provider 接口。"""

    def __init__(self):
        from openai import AsyncAzureOpenAI, AsyncOpenAI

        self.client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            max_retries=0,
        )
        self.deployment = settings.AZURE_OPENAI_DEPLOYMENT
        self.fallback_client = None
        self.fallback_model = settings.LLM_FALLBACK_MODEL_NAME
        if self.fallback_model:
            self.fallback_client = AsyncOpenAI(
                api_key=settings.LLM_FALLBACK_API_KEY,
                base_url=settings.LLM_FALLBACK_BASE_URL,
                max_retries=0,
            )

    async def _call_gpt(
        self,
        messages: List[Dict[str, str]],
        json_mode: bool = False,
        max_tokens: int | None = None,
        model: str | None = None,
        operation: str = "chat",
    ) -> Tuple[str, int, int]:
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        selected_model = model or self.deployment

        async def invoke(target_client: Any, target_model: str) -> Tuple[str, int, int]:
            """将 Azure 与备用兼容服务收敛到同一故障策略。"""
            return await _complete_chat(
                target_client,
                target_model,
                messages,
                kwargs,
                operation=operation,
                json_mode=json_mode,
            )

        fallback = None
        if self.fallback_client is not None and self.fallback_model:
            fallback = lambda: invoke(self.fallback_client, self.fallback_model)
        result = await resilience_executor.execute(
            component="llm",
            operation=operation,
            call=lambda: invoke(self.client, selected_model),
            policy=llm_policy(operation),
            fallback=fallback,
            fallback_name=self.fallback_model,
            circuit_key=f"llm:{selected_model}",
        )
        return result.unwrap()

    @trace_operation(name="supportgpt.llm.analyze_ticket", component="llm")
    async def analyze_ticket(self, text: str) -> Tuple[Dict[str, Any], int, int]:
        messages = active_bundle().messages("analyzer", text=text)
        content, in_tok, out_tok = await self._call_gpt(
            messages,
            json_mode=True,
            max_tokens=settings.LLM_ANALYZER_MAX_TOKENS,
            model=settings.LLM_ANALYZER_MODEL_NAME or self.deployment,
            operation="analyze_ticket",
        )
        return _normalize_ticket_analysis(json.loads(content)), in_tok, out_tok

    @trace_operation(name="supportgpt.llm.generate_resolution", component="llm")
    async def generate_resolution(
        self, subject: str, description: str, context: str
    ) -> Tuple[str, int, int]:
        messages = _resolution_messages(subject, description, context)
        return await self._call_gpt(
            messages,
            json_mode=False,
            max_tokens=settings.LLM_RESOLVER_MAX_TOKENS,
            operation="generate_resolution",
        )

    @trace_operation(name="supportgpt.llm.evaluate_qa", component="llm")
    async def evaluate_qa(
        self, query: str, context: List[str], response: str
    ) -> Tuple[Dict[str, Any], int, int]:
        messages = active_bundle().messages(
            "qa", query=query, context=json.dumps(context, ensure_ascii=False), response=response
        )
        content, in_tok, out_tok = await self._call_gpt(
            messages,
            json_mode=True,
            max_tokens=settings.LLM_QA_MAX_TOKENS,
            model=settings.LLM_QA_MODEL_NAME or self.deployment,
            operation="evaluate_qa",
        )
        return json.loads(content), in_tok, out_tok

    @trace_operation(name="supportgpt.llm.run_chat", component="llm")
    async def run_chat(
        self, history: List[Dict[str, str]], context: str
    ) -> Tuple[str, int, int]:
        system_msg = (
            "You are SupportGPT, a customer support AI assistant. Answer using the "
            f"retrieved context if available. {CHAT_LANGUAGE_POLICY}"
        )
        if context:
            system_msg += f"\n\nContext:\n{context}"

        messages = [{"role": "system", "content": system_msg}]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        return await self._call_gpt(
            messages, json_mode=False, operation="run_chat"
        )


# Provider factory
def get_llm_provider() -> BaseLLMProvider:
    provider_type = settings.LLM_PROVIDER.lower()
    if provider_type == "openai":
        return OpenAILLMProvider()
    elif provider_type == "azure":
        return AzureOpenAILLMProvider()
    else:
        return MockLLMProvider()


llm_provider = get_llm_provider()
