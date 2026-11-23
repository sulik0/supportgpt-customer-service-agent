"""验证 Prompt 内容绑定、并发隔离和发布证据门禁。"""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from src.config import settings
from src.evaluation.baseline_evaluation import _build_experiment_config
from src.promptops.defaults import default_payload
from src.promptops.experiments import assess_experiment, promote_experiment, save_experiment
from src.promptops.registry import PromptBundle, PromptRegistry
from src.promptops.runtime import active_bundle, prompt_scope


def candidate_payload():
    payload = default_payload()
    payload["version"] = "support-v2-test"
    payload["templates"]["resolver"]["system"] += " Keep the reply short."
    return payload


def report(bundle, *, provider="mock", failed=False):
    metrics = ["intent_accuracy", "department_accuracy", "required_tool_hit_rate",
               "forbidden_tool_violation_rate", "hitl_accuracy", "approval_accuracy"]
    return {
        "evaluation_type": "baseline_workflow_replay_v1", "run_id": bundle.version,
        "case_count": 1, "enabled_behavior_metrics": metrics,
        "execution": {"mode": "ci_offline_workflow_replay" if provider == "mock" else "real_llm_regression"},
        "experiment_config": {
            "prompts": {**bundle.metadata(), "snapshot": bundle.payload()},
            "dataset": {"sha256": "fixed-dataset"}, "evaluator": {"version": "v1"},
            "models": {"provider": provider}, "limits": {}, "risk_thresholds": {},
            "workflow": {"source_revision": "a" * 40, "version": "v1"},
        },
        "behavior_summary": {
            **{m: 1.0 for m in metrics}, "forbidden_tool_violation_rate": 0.0,
            "case_pass_rate": 0.0 if failed else 1.0,
        },
        "performance_summary": {
            "end_to_end_latency_seconds": {"average": 1.0, "p50": 1.0, "p95": 1.0},
            "tokens": {"average_total": 100}, "llm": {"call_count": 1},
            "analyzer": {"rule_hit_rate": 0.0},
        },
        "cases": [{"id": "case-1", "prompt_bundle_id": bundle.bundle_id,
                   "behavior_evaluation": {"passed": not failed}}],
    }


def policy():
    metrics = report(PromptBundle(default_payload()))["enabled_behavior_metrics"]
    profile = {
        "allowed_failed_case_ids": ["case-1"],
        "requirements": [{"name": "pass", "path": "behavior_summary.case_pass_rate",
                          "operator": "gte", "value": 0.0}],
    }
    return {"dataset": {"case_count": 1, "sha256": "fixed-dataset", "enabled_behavior_metrics": metrics},
            "profiles": {"pull_request": profile, "release": profile}}


def test_bundle_preserves_default_contract_and_untrusted_placeholders():
    bundle = PromptBundle(default_payload())
    messages = bundle.messages("resolver", subject="订单", description="$context {x}", context="规则")
    assert messages[0]["role"] == "system"
    assert "Description: $context {x}" in messages[1]["content"]
    assert "current Description" in messages[0]["content"]
    payload = bundle.payload()
    payload["templates"]["qa"]["system"] = "changed"
    assert bundle.payload() != payload


@pytest.mark.parametrize("node", ["analyzer", "resolver", "qa"])
def test_bundle_rejects_missing_or_extra_variables(node):
    payload = default_payload()
    payload["templates"][node]["user"] += "$secret_key"
    with pytest.raises(ValueError, match="placeholders"):
        PromptBundle(payload)


def test_registry_detects_tampering_and_rejects_path_traversal(tmp_path):
    registry = PromptRegistry(tmp_path)
    bundle = registry.register(candidate_payload())
    assert registry.register(candidate_payload()).bundle_id == bundle.bundle_id
    path = tmp_path / "bundles" / f"{bundle.bundle_id}.json"
    path.write_text(json.dumps(default_payload()), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        registry.load(bundle.bundle_id)
    with pytest.raises(ValueError, match="SHA256"):
        registry.load("../../secret")


@pytest.mark.asyncio
async def test_concurrent_workflows_keep_distinct_bundles():
    original = active_bundle()
    candidates = [PromptBundle(default_payload()), PromptBundle(candidate_payload())]

    async def run(bundle):
        with prompt_scope(bundle):
            await asyncio.sleep(0)
            with prompt_scope():
                assert active_bundle().bundle_id == bundle.bundle_id
            return active_bundle().bundle_id

    assert await asyncio.gather(*(run(b) for b in candidates)) == [b.bundle_id for b in candidates]
    assert active_bundle().bundle_id == original.bundle_id


def test_baseline_config_captures_actual_content_not_legacy_label(monkeypatch):
    bundle = PromptBundle(candidate_payload())
    monkeypatch.setattr(settings, "PROMPT_VERSION", "wrong-label")
    path = Path(__file__).resolve().parents[1] / "evaluation/baseline/supportgpt_baseline_100.json"
    with prompt_scope(bundle):
        config = _build_experiment_config(path, {})
    assert config["prompts"]["bundle_id"] == bundle.bundle_id
    assert config["prompts"]["snapshot"] == bundle.payload()
    assert config["workflow"]["prompt_version"] == bundle.version


def test_production_rejects_mock_even_when_behavior_passes():
    bundle = PromptBundle(candidate_payload())
    current = PromptBundle(default_payload())
    result = assess_experiment(report=report(bundle), policy=policy(), bundle=bundle,
                               baseline=report(current), environment="production",
                               expected_current=current.bundle_id)
    assert "production_requires_real_llm_evaluation" in result["reasons"]


def test_comparison_rejects_new_failures_despite_allowlist():
    bundle, current = PromptBundle(candidate_payload()), PromptBundle(default_payload())
    result = assess_experiment(report=report(bundle, failed=True), policy=policy(), bundle=bundle,
                               baseline=report(current), environment="staging",
                               expected_current=current.bundle_id)
    assert result["quality_gate"]["passed"]
    assert "new_failed_cases" in result["reasons"]
    assert result["comparison"]["case_transitions"]["PASS→FAIL"]["case_ids"] == ["case-1"]


def test_comparison_rejects_model_drift_and_prompt_mismatch():
    bundle, current = PromptBundle(candidate_payload()), PromptBundle(default_payload())
    candidate = report(bundle)
    candidate["experiment_config"]["models"]["resolver"] = "different-model"
    result = assess_experiment(report=candidate, policy=policy(), bundle=bundle,
                               baseline=report(current), environment="staging",
                               expected_current=current.bundle_id)
    assert "incomparable_models" in result["reasons"]
    candidate["experiment_config"]["prompts"]["snapshot"]["version"] = "forged"
    with pytest.raises(ValueError, match="exact Prompt"):
        assess_experiment(report=candidate, policy=policy(), bundle=bundle,
                          baseline=None, environment="staging", expected_current=current.bundle_id)


def save(registry, *, environment="staging", provider="mock", dirty=False):
    current = registry.register(default_payload())
    bundle = registry.register(candidate_payload())
    path = save_experiment(
        registry, bundle=bundle, report=report(bundle, provider=provider), policy=policy(),
        baseline=report(current, provider=provider), environment=environment,
        expected_current=current.bundle_id, source={"revision": "a" * 40, "dirty": dirty},
    )
    return path, current, bundle


def test_staging_promotion_rollback_and_stale_experiment(tmp_path):
    registry = PromptRegistry(tmp_path)
    path, current, bundle = save(registry)
    event = promote_experiment(registry, experiment_id=path.parent.name, policy=policy(),
                               actor="developer", reason="通过 Mock 工程验证")
    assert event["bundle_id"] == bundle.bundle_id
    assert registry.resolve("staging").bundle_id == bundle.bundle_id
    assert registry.resolve("production").bundle_id == current.bundle_id
    with pytest.raises(ValueError, match="Active Prompt changed"):
        promote_experiment(registry, experiment_id=path.parent.name, policy=policy(), actor="dev", reason="重复")
    registry.rollback(environment="staging", actor="dev", reason="回退验证")
    assert registry.resolve("staging").bundle_id == current.bundle_id
    assert len(registry.state()["history"]) == 2


def test_promotion_revalidates_evidence_and_policy(tmp_path):
    registry = PromptRegistry(tmp_path)
    path, _, _ = save(registry)
    changed_policy = copy.deepcopy(policy())
    changed_policy["policy_name"] = "new"
    with pytest.raises(ValueError, match="evidence or quality gate"):
        promote_experiment(registry, experiment_id=path.parent.name, policy=changed_policy,
                           actor="dev", reason="验证")
    candidate_path = path.parent / "candidate.json"
    candidate = json.loads(candidate_path.read_text())
    candidate["case_count"] = 2
    candidate_path.write_text(json.dumps(candidate))
    with pytest.raises(ValueError, match="evidence or quality gate"):
        promote_experiment(registry, experiment_id=path.parent.name, policy=policy(), actor="dev", reason="验证")


def test_production_requires_clean_matching_source(tmp_path, monkeypatch):
    registry = PromptRegistry(tmp_path)
    path, _, _ = save(registry, environment="production", provider="openai", dirty=True)
    monkeypatch.setattr("src.promptops.experiments.source_state", lambda: {"revision": "a" * 40, "dirty": False})
    with pytest.raises(ValueError, match="clean evaluated"):
        promote_experiment(registry, experiment_id=path.parent.name, policy=policy(), actor="dev", reason="发布")


@pytest.mark.asyncio
async def test_feedback_persists_bundle_id(db_session):
    from src.feedback.service import feedback_service

    bundle_id = "a" + "1234567890" * 6 + "abc"
    run = await feedback_service.record_agent_run(
        db_session, agent_output={"prompt_bundle_id": bundle_id, "suggested_response": "回复"},
        input_text="咨询", endpoint="/chat",
    )
    assert run.prompt_version == bundle_id


def test_trace_hash_not_redacted_as_phone():
    from src.observability.sanitization import sanitize_attributes

    bundle_id = "a" + "1234567890" * 6 + "abc"
    values = sanitize_attributes({"prompt.bundle_id": bundle_id, "phone": "13812345678"})
    assert values["prompt.bundle_id"] == bundle_id
    assert values["phone"] == "[FILTERED]"


def test_production_promotion_with_clean_real_evidence(tmp_path, monkeypatch):
    registry = PromptRegistry(tmp_path)
    path, _, bundle = save(registry, environment="production", provider="openai")
    monkeypatch.setattr("src.promptops.experiments.source_state", lambda: {"revision": "a" * 40, "dirty": False})
    event = promote_experiment(registry, experiment_id=path.parent.name, policy=policy(), actor="dev", reason="发布")
    assert event["environment"] == "production"
    assert registry.resolve("production").bundle_id == bundle.bundle_id


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["OpenAILLMProvider", "AzureOpenAILLMProvider"])
async def test_real_provider_methods_render_pinned_candidate_without_network(provider_name):
    from src.llm import provider as providers

    payload = candidate_payload()
    for node in payload["templates"]:
        payload["templates"][node]["system"] += f" Candidate {node}."
    bundle = PromptBundle(payload)
    provider = object.__new__(getattr(providers, provider_name))
    provider.analyzer_model = provider.qa_model = provider.deployment = "test-model"
    provider.analyzer_client = provider.qa_client = None
    calls = []

    async def call(messages, **kwargs):
        calls.append((messages, kwargs))
        if kwargs["operation"] == "classify_message":
            return '{"intent":{"answer":"information_request","sure":true}}', 10, 5
        if kwargs["operation"] == "evaluate_qa":
            return '{"score":1.0}', 10, 5
        return "回复", 10, 5

    provider._call_gpt = call
    with prompt_scope(bundle):
        await provider.classify_message("问题")
        await provider.generate_resolution("主题", "描述", "证据")
        await provider.evaluate_qa("问题", ["证据"], "回复")
    # 分类问题和 Jev 共用一份定义，不走 PromptOps 模板。
    assert "Candidate" not in calls[0][0][0]["content"]
    for (messages, _), node in zip(calls[1:], ("resolver", "qa")):
        assert messages[0]["content"].endswith(f"Candidate {node}.")
    assert calls[1][1]["max_tokens"] == settings.LLM_RESOLVER_MAX_TOKENS
    assert calls[2][1]["json_mode"] is True
