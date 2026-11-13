from pathlib import Path
from types import SimpleNamespace

import pytest

from src.evaluation.offline_rag import load_evaluation_dataset
from src.evaluation.real_llm_regression import (
    REGRESSION_CASE_IDS,
    build_real_llm_run_plan,
    prepare_judge_environment,
    require_live_confirmation,
    validate_real_llm_settings,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = (
    PROJECT_ROOT / "evaluation" / "baseline" / "supportgpt_baseline_100.json"
)


def _settings(**overrides):
    """构造不包含真实凭据的 Provider 测试配置。"""
    values = {
        "LLM_PROVIDER": "openai",
        "LLM_API_KEY": "unit-test-key",
        "LLM_MODEL_NAME": "compatible-chat-model",
        "LLM_BASE_URL": "https://llm.example.com/v1",
        "AZURE_OPENAI_API_KEY": None,
        "AZURE_OPENAI_ENDPOINT": None,
        "AZURE_OPENAI_DEPLOYMENT": None,
        "OPENAI_API_KEY": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_real_llm_plan_selects_stratified_regression_suite_and_records_safe_metadata():
    cases = load_evaluation_dataset(BASELINE_PATH)

    plan = build_real_llm_run_plan(
        settings=_settings(),
        cases=cases,
        suite="regression",
        explicit_case_ids=None,
        max_workflow_calls=40,
    )

    assert plan.case_ids == REGRESSION_CASE_IDS
    assert plan.estimated_workflow_llm_calls == 27
    assert plan.endpoint_host == "llm.example.com"
    assert plan.report_metadata() == {
        "mode": "real_llm_regression",
        "llm_provider": "openai",
        "llm_model": "compatible-chat-model",
        "endpoint_host": "llm.example.com",
        "suite": "regression",
        "selected_case_count": 12,
        "estimated_workflow_llm_calls": 27,
    }
    assert "unit-test-key" not in str(plan.report_metadata())


def test_real_llm_plan_requires_explicit_budget_for_full_suite():
    cases = load_evaluation_dataset(BASELINE_PATH)

    with pytest.raises(ValueError, match="exceed the configured maximum"):
        build_real_llm_run_plan(
            settings=_settings(),
            cases=cases,
            suite="full",
            explicit_case_ids=None,
            max_workflow_calls=40,
        )

    plan = build_real_llm_run_plan(
        settings=_settings(),
        cases=cases,
        suite="full",
        explicit_case_ids=None,
        max_workflow_calls=300,
    )
    assert len(plan.case_ids) == 100
    assert plan.estimated_workflow_llm_calls == 258


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"LLM_PROVIDER": "mock"}, "requires LLM_PROVIDER"),
        ({"LLM_API_KEY": ""}, "LLM_API_KEY"),
        ({"LLM_MODEL_NAME": "mock-support-v1"}, "non-Mock"),
        (
            {
                "LLM_PROVIDER": "azure",
                "AZURE_OPENAI_API_KEY": "your-azure-api-key-here",
                "AZURE_OPENAI_ENDPOINT": "https://resource.openai.azure.com",
                "AZURE_OPENAI_DEPLOYMENT": "gpt-deployment",
            },
            "AZURE_OPENAI_API_KEY",
        ),
    ],
)
def test_real_llm_configuration_rejects_mock_or_placeholder_values(overrides, message):
    with pytest.raises(ValueError, match=message):
        validate_real_llm_settings(_settings(**overrides))


def test_live_confirmation_is_mandatory():
    with pytest.raises(ValueError, match="--confirm-live"):
        require_live_confirmation(False)

    require_live_confirmation(True)


def test_judge_key_is_only_required_for_external_judges(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    prepare_judge_environment(
        _settings(OPENAI_API_KEY=None),
        rag_engine="local",
        agent_engine="local",
    )

    with pytest.raises(ValueError, match="separate from the Workflow"):
        prepare_judge_environment(
            _settings(OPENAI_API_KEY=None),
            rag_engine="ragas",
            agent_engine="local",
        )

    prepare_judge_environment(
        _settings(OPENAI_API_KEY="judge-test-key"),
        rag_engine="ragas",
        agent_engine="deepeval",
    )
    assert __import__("os").environ["OPENAI_API_KEY"] == "judge-test-key"


def test_regression_suite_includes_online_regression_cases_from_extra_dataset(tmp_path):
    from src.evaluation.real_llm_regression import (
        ONLINE_REGRESSION_CASE_IDS,
        merge_dataset_files,
    )

    extra = PROJECT_ROOT / "evaluation" / "online_regression" / (
        "supportgpt_online_regression.json"
    )
    merged = merge_dataset_files([BASELINE_PATH, extra], tmp_path / "merged.json")
    cases = load_evaluation_dataset(merged)

    plan = build_real_llm_run_plan(
        settings=_settings(),
        cases=cases,
        suite="regression",
        explicit_case_ids=None,
        max_workflow_calls=40,
    )

    assert plan.case_ids == (*REGRESSION_CASE_IDS, *ONLINE_REGRESSION_CASE_IDS)
    # 10 个非攻击用例中新增 4 个，C08 安全短路不计调用
    assert plan.estimated_workflow_llm_calls == 27 + 4 * 3
