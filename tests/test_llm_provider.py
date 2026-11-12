from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.llm.provider import (
    MockLLMProvider,
    OpenAILLMProvider,
    _resolution_messages,
    resolution_language_directive,
)
from src.models.intents import IntentType
from src.rag.embedding import MockEmbeddingProvider, get_embedding_provider


def test_openai_compatible_provider_requires_api_key(monkeypatch):
    monkeypatch.setattr("src.llm.provider.settings.LLM_API_KEY", None)
    monkeypatch.setattr("src.llm.provider.settings.LLM_MODEL_NAME", "chat-model")

    with pytest.raises(ValueError, match="LLM_API_KEY"):
        OpenAILLMProvider()


def test_openai_compatible_provider_requires_model(monkeypatch):
    monkeypatch.setattr("src.llm.provider.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("src.llm.provider.settings.LLM_MODEL_NAME", None)

    with pytest.raises(ValueError, match="LLM_MODEL_NAME"):
        OpenAILLMProvider()


def test_openai_compatible_provider_uses_default_sdk_url(monkeypatch):
    monkeypatch.setattr("src.llm.provider.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("src.llm.provider.settings.LLM_MODEL_NAME", "chat-model")
    monkeypatch.setattr("src.llm.provider.settings.LLM_BASE_URL", None)

    client = MagicMock()
    with patch("openai.AsyncOpenAI", return_value=client) as client_factory:
        provider = OpenAILLMProvider()

    client_factory.assert_called_once_with(
        api_key="test-key", base_url=None, max_retries=0
    )
    assert provider.client is client
    assert provider.model == "chat-model"


def test_fast_provider_requires_its_own_key_for_separate_endpoint(monkeypatch):
    monkeypatch.setattr("src.llm.provider.settings.LLM_API_KEY", "main-key")
    monkeypatch.setattr("src.llm.provider.settings.LLM_MODEL_NAME", "main-model")
    monkeypatch.setattr("src.llm.provider.settings.LLM_FAST_MODEL_NAME", "qwen-turbo")
    monkeypatch.setattr(
        "src.llm.provider.settings.LLM_FAST_BASE_URL", "https://fast.example/v1"
    )
    monkeypatch.setattr("src.llm.provider.settings.LLM_FAST_API_KEY", None)

    with pytest.raises(ValueError, match="LLM_FAST_API_KEY"):
        OpenAILLMProvider()


def test_compatible_chat_does_not_require_openai_embedding_key(monkeypatch):
    monkeypatch.setattr("src.rag.embedding.settings.LLM_PROVIDER", "openai")
    monkeypatch.setattr("src.rag.embedding.settings.OPENAI_API_KEY", None)

    assert isinstance(get_embedding_provider(), MockEmbeddingProvider)


@pytest.mark.asyncio
async def test_mock_provider_follows_current_input_language():
    provider = MockLLMProvider()

    chinese, _, _ = await provider.generate_resolution(
        "Support ticket", "我要申请退款", "policy"
    )
    english, _, _ = await provider.generate_resolution(
        "客服工单", "Please help with my refund", "policy"
    )
    switched, _, _ = await provider.generate_resolution(
        "客服工单", "请用英文回复退款流程", "policy"
    )

    assert "感谢" in chinese
    assert "Thank you" in english
    assert "Thank you" in switched


@pytest.mark.asyncio
async def test_openai_compatible_prompts_define_response_language_policy(monkeypatch):
    monkeypatch.setattr("src.llm.provider.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("src.llm.provider.settings.LLM_MODEL_NAME", "chat-model")
    monkeypatch.setattr("src.llm.provider.settings.LLM_BASE_URL", None)

    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(content="ok"))]
    completion.usage = MagicMock(prompt_tokens=10, completion_tokens=2)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=completion)

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = OpenAILLMProvider()

    await provider.generate_resolution("English subject", "我要查询物流", "English context")
    resolution_call = client.chat.completions.create.await_args
    resolution_messages = resolution_call.kwargs["messages"]
    assert resolution_call.kwargs["max_tokens"] == settings.LLM_RESOLVER_MAX_TOKENS
    assert "current Description" in resolution_messages[0]["content"]
    assert (
        "explicitly asks for a different response language"
        in resolution_messages[0]["content"]
    )

    await provider.run_chat(
        [
            {"role": "user", "content": "Please reply in English"},
            {"role": "assistant", "content": "Previous response"},
            {"role": "user", "content": "现在物流到哪里了？"},
        ],
        "English context",
    )
    chat_messages = client.chat.completions.create.await_args.kwargs["messages"]
    assert "latest user message" in chat_messages[0]["content"]
    assert "Do not preserve a previous response language" in chat_messages[0]["content"]


@pytest.mark.asyncio
async def test_qa_uses_compact_schema_limits_and_optional_fast_model(monkeypatch):
    monkeypatch.setattr("src.llm.provider.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("src.llm.provider.settings.LLM_MODEL_NAME", "main-model")
    monkeypatch.setattr("src.llm.provider.settings.LLM_QA_MODEL_NAME", "fast-judge")

    completion = MagicMock()
    completion.choices = [
        MagicMock(
            message=MagicMock(
                content='{"score":0.9,"hallucination_detected":false,'
                '"citation_verified":true}'
            )
        )
    ]
    completion.usage = MagicMock(prompt_tokens=120, completion_tokens=20)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=completion)

    with patch("openai.AsyncOpenAI", return_value=client):
        provider = OpenAILLMProvider()

    result, _, _ = await provider.evaluate_qa("question", ["evidence"], "answer")
    call = client.chat.completions.create.await_args

    assert result["score"] == 0.9
    assert call.kwargs["model"] == "fast-judge"
    assert call.kwargs["max_tokens"] == settings.LLM_QA_MAX_TOKENS
    assert "reasons" not in call.kwargs["messages"][1]["content"]


@pytest.mark.asyncio
async def test_analyzer_and_qa_use_separate_fast_provider(monkeypatch):
    monkeypatch.setattr("src.llm.provider.settings.LLM_API_KEY", "main-key")
    monkeypatch.setattr("src.llm.provider.settings.LLM_MODEL_NAME", "main-model")
    monkeypatch.setattr("src.llm.provider.settings.LLM_BASE_URL", "https://main.example/v1")
    monkeypatch.setattr("src.llm.provider.settings.LLM_FAST_MODEL_NAME", "qwen-turbo")
    monkeypatch.setattr(
        "src.llm.provider.settings.LLM_FAST_BASE_URL", "https://fast.example/v1"
    )
    monkeypatch.setattr("src.llm.provider.settings.LLM_FAST_API_KEY", "fast-key")
    monkeypatch.setattr("src.llm.provider.settings.LLM_ANALYZER_MODEL_NAME", None)
    monkeypatch.setattr("src.llm.provider.settings.LLM_QA_MODEL_NAME", None)

    analyzer_completion = MagicMock()
    analyzer_completion.choices = [
        MagicMock(
            message=MagicMock(
                content='{"intent":"general_query","priority":"medium",'
                '"department":"general","sentiment":"neutral",'
                '"confidence_score":0.8}'
            )
        )
    ]
    analyzer_completion.usage = MagicMock(prompt_tokens=50, completion_tokens=15)
    qa_completion = MagicMock()
    qa_completion.choices = [
        MagicMock(
            message=MagicMock(
                content='{"score":0.9,"hallucination_detected":false,'
                '"citation_verified":true}'
            )
        )
    ]
    qa_completion.usage = MagicMock(prompt_tokens=70, completion_tokens=12)
    resolver_completion = MagicMock()
    resolver_completion.choices = [MagicMock(message=MagicMock(content="reply"))]
    resolver_completion.usage = MagicMock(prompt_tokens=100, completion_tokens=20)

    main_client = MagicMock()
    main_client.chat.completions.create = AsyncMock(return_value=resolver_completion)
    fast_client = MagicMock()
    fast_client.chat.completions.create = AsyncMock(
        side_effect=[analyzer_completion, qa_completion]
    )

    with patch("openai.AsyncOpenAI", side_effect=[main_client, fast_client]) as factory:
        provider = OpenAILLMProvider()

    analysis, _, _ = await provider.analyze_ticket("Unclear support request")
    await provider.generate_resolution("subject", "description", "context")
    await provider.evaluate_qa("question", ["evidence"], "answer")

    assert factory.call_count == 2
    assert factory.call_args_list[1].kwargs == {
        "api_key": "fast-key",
        "base_url": "https://fast.example/v1",
        "max_retries": 0,
    }
    fast_models = [
        call.kwargs["model"] for call in fast_client.chat.completions.create.await_args_list
    ]
    assert fast_models == ["qwen-turbo", "qwen-turbo"]
    assert main_client.chat.completions.create.await_args.kwargs["model"] == "main-model"
    assert analysis["intent"] is IntentType.INFORMATION_REQUEST
    assert analysis["confidence_score"] == 0.5
    analyzer_prompt = fast_client.chat.completions.create.await_args_list[0].kwargs[
        "messages"
    ][1]["content"]
    assert "billing_dispute|outage_report|order_cancellation" in analyzer_prompt


@pytest.mark.asyncio
async def test_openai_provider_uses_compatible_fallback_model(monkeypatch):
    monkeypatch.setattr("src.llm.provider.settings.LLM_API_KEY", "main-key")
    monkeypatch.setattr("src.llm.provider.settings.LLM_MODEL_NAME", "main-model")
    monkeypatch.setattr("src.llm.provider.settings.LLM_BASE_URL", "https://main/v1")
    monkeypatch.setattr(
        "src.llm.provider.settings.LLM_FALLBACK_API_KEY", "backup-key"
    )
    monkeypatch.setattr(
        "src.llm.provider.settings.LLM_FALLBACK_MODEL_NAME", "backup-model"
    )
    monkeypatch.setattr(
        "src.llm.provider.settings.LLM_FALLBACK_BASE_URL", "https://backup/v1"
    )
    monkeypatch.setattr("src.llm.provider.settings.RESILIENCE_LLM_MAX_RETRIES", 0)

    main_client = MagicMock()
    main_client.chat.completions.create = AsyncMock(
        side_effect=ConnectionError("primary unavailable")
    )
    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(content="backup reply"))]
    completion.usage = MagicMock(prompt_tokens=8, completion_tokens=2)
    backup_client = MagicMock()
    backup_client.chat.completions.create = AsyncMock(return_value=completion)

    with patch(
        "openai.AsyncOpenAI", side_effect=[main_client, backup_client]
    ) as factory:
        provider = OpenAILLMProvider()
        response, input_tokens, output_tokens = await provider.run_chat(
            [{"role": "user", "content": "hello"}], ""
        )

    assert factory.call_count == 2
    assert response == "backup reply"
    assert (input_tokens, output_tokens) == (8, 2)
    assert backup_client.chat.completions.create.await_args.kwargs["model"] == (
        "backup-model"
    )


def _completion(content="ok"):
    completion = MagicMock()
    completion.choices = [MagicMock(message=MagicMock(content=content), finish_reason="stop")]
    completion.usage = MagicMock(prompt_tokens=10, completion_tokens=2)
    return completion


def _patch_openai_settings(monkeypatch, thinking):
    monkeypatch.setattr("src.llm.provider.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("src.llm.provider.settings.LLM_MODEL_NAME", "deepseek-flash")
    monkeypatch.setattr("src.llm.provider.settings.LLM_BASE_URL", None)
    monkeypatch.setattr("src.llm.provider.settings.LLM_THINKING", thinking)


@pytest.mark.asyncio
async def test_thinking_disabled_is_sent_to_primary_model(monkeypatch):
    # 思考 Token 会占用 max_tokens，关闭后 DeepSeek 不再截断回复。
    _patch_openai_settings(monkeypatch, "disabled")
    monkeypatch.setattr("src.llm.provider.settings.LLM_FALLBACK_MODEL_NAME", None)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_completion())
    with patch("openai.AsyncOpenAI", return_value=client):
        provider = OpenAILLMProvider()

    await provider.generate_resolution("subject", "我要查询物流", "context")

    kwargs = client.chat.completions.create.await_args.kwargs
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_thinking_unset_sends_no_extra_body(monkeypatch):
    _patch_openai_settings(monkeypatch, None)
    monkeypatch.setattr("src.llm.provider.settings.LLM_FALLBACK_MODEL_NAME", None)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_completion())
    with patch("openai.AsyncOpenAI", return_value=client):
        provider = OpenAILLMProvider()

    await provider.generate_resolution("subject", "我要查询物流", "context")

    assert "extra_body" not in client.chat.completions.create.await_args.kwargs


@pytest.mark.asyncio
async def test_thinking_flag_is_not_sent_to_fallback_model(monkeypatch):
    # 备用模型可能不是 DeepSeek，不能传它不认识的参数。
    _patch_openai_settings(monkeypatch, "disabled")
    monkeypatch.setattr("src.llm.provider.settings.LLM_FALLBACK_MODEL_NAME", "backup")
    monkeypatch.setattr("src.llm.provider.settings.LLM_FALLBACK_BASE_URL", "http://backup")
    monkeypatch.setattr("src.llm.provider.settings.LLM_FALLBACK_API_KEY", "backup-key")
    primary = MagicMock()
    primary.chat.completions.create = AsyncMock(side_effect=RuntimeError("down"))
    backup = MagicMock()
    backup.chat.completions.create = AsyncMock(return_value=_completion())
    with patch("openai.AsyncOpenAI", side_effect=[primary, backup]):
        provider = OpenAILLMProvider()
    assert provider.fallback_client is backup

    await provider.generate_resolution("subject", "我要查询物流", "context")

    assert primary.chat.completions.create.await_args.kwargs["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
    assert "extra_body" not in backup.chat.completions.create.await_args.kwargs


def test_language_directive_follows_current_description():
    assert (
        resolution_language_directive("Hi, can you tell me the status of my order ORD-7001?")
        == "Respond in English."
    )
    assert resolution_language_directive("订单 ORD-7001 到哪里了？") == "Respond in Chinese."
    # 用户明确指定语言、或无法可靠判断时不追加指令
    assert resolution_language_directive("请用英文回复退款流程") == ""
    assert resolution_language_directive("Please reply in English 你好") == ""
    assert resolution_language_directive("Où est ma commande?") == ""


def test_resolution_messages_append_language_directive_to_user_message():
    english = _resolution_messages("订单", "Where is my order, please help?", "中文上下文")
    chinese = _resolution_messages("order", "我的订单到哪里了", "English context")

    assert english[-1]["role"] == "user"
    assert english[-1]["content"].endswith("Respond in English.")
    assert chinese[-1]["content"].endswith("Respond in Chinese.")
