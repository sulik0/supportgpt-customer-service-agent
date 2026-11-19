from types import SimpleNamespace

from src.config_summary import config_warnings, effective_config


def _settings(**overrides):
    values = dict(
        LLM_PROVIDER="openai",
        LLM_MODEL_NAME="deepseek-flash",
        LLM_FAST_MODEL_NAME="qwen-flash",
        LLM_THINKING=None,
        LLM_RESOLVER_MAX_TOKENS=480,
        LLM_QA_MAX_TOKENS=320,
        TOOL_SELECTION_MODE="react",
        DECISION_PROVIDER="jev",
        RESILIENCE_LLM_TIMEOUT_SECONDS=20.0,
        RESILIENCE_LLM_MAX_RETRIES=1,
        LLM_BASE_URL="https://user:pw@api.deepseek.com/v1?key=secret",
        LLM_FAST_BASE_URL="",
        LLM_API_KEY="sk-secret",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_summary_has_host_only_and_no_secrets():
    summary = effective_config(_settings())

    assert summary["LLM_ENDPOINT_HOST"] == "api.deepseek.com"
    assert summary["LLM_FAST_ENDPOINT_HOST"] is None
    text = str(summary)
    assert "sk-secret" not in text and "pw" not in text.replace("provider", "")
    assert "secret" not in text


def test_unset_thinking_is_marked_explicitly():
    assert effective_config(_settings())["LLM_THINKING"] == "unset(provider default)"
    assert effective_config(_settings(LLM_THINKING="disabled"))["LLM_THINKING"] == "disabled"


def test_deepseek_without_thinking_setting_warns():
    assert config_warnings(_settings())
    assert not config_warnings(_settings(LLM_THINKING="disabled"))
    assert not config_warnings(_settings(LLM_BASE_URL="https://api.openai.com/v1"))
    assert not config_warnings(_settings(LLM_BASE_URL=""))
