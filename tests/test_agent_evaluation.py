import json
from collections import Counter
from pathlib import Path

import pytest

from src.evaluation.offline_rag import (
    EvaluationCase,
    load_evaluation_dataset,
    run_offline_evaluation,
)
from src.evaluation.security_evaluation import SecurityExpectations
from src.llm.provider import MockLLMProvider
from src.observability.sanitization import sanitize_value


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "evaluation" / "golden" / "support_qa_golden.json"
BASELINE_DATASET_PATH = (
    PROJECT_ROOT / "evaluation" / "baseline" / "supportgpt_baseline_100.json"
)


def test_golden_dataset_is_valid_and_unique():
    cases = load_evaluation_dataset(DATASET_PATH)

    assert len(cases) >= 10
    assert len({case.id for case in cases}) == len(cases)
    assert all(case.reference_answer for case in cases)
    assert all(isinstance(case.expected_sources, list) for case in cases)
    assert all(case.agent_expectations for case in cases)


def test_baseline_dataset_has_100_cases_and_required_coverage():
    payload = json.loads(BASELINE_DATASET_PATH.read_text(encoding="utf-8"))
    cases = load_evaluation_dataset(BASELINE_DATASET_PATH)

    required_tags = {
        "refund",
        "order",
        "account",
        "api_outage",
        "insufficient_information",
        "rag",
        "tool_calling",
        "human_escalation",
        "prompt_injection",
        "jailbreak",
        "security_benign",
        "multilingual",
    }
    actual_tags = {tag for case in cases for tag in case.tags}
    tag_counts = Counter(tag for case in cases for tag in case.tags)

    assert len(cases) == 100
    assert len({case.id for case in cases}) == 100
    assert required_tags <= actual_tags
    assert set(payload["coverage"]) == required_tags
    assert payload["coverage"] == {tag: tag_counts[tag] for tag in payload["coverage"]}
    assert all(case.agent_expectations for case in cases)
    security_labels = [SecurityExpectations.from_case(case) for case in cases]
    assert sum(label.expected_attack for label in security_labels) == 14
    assert {
        label.attack_type for label in security_labels if label.expected_attack
    } == {
        "prompt_injection",
        "jailbreak",
    }


def test_evaluation_case_accepts_optional_agent_run_link():
    payload = load_evaluation_dataset(DATASET_PATH)[0].__dict__.copy()
    payload["agent_run_id"] = "run-123"

    case = EvaluationCase(**payload)

    assert case.agent_run_id == "run-123"


@pytest.mark.asyncio
async def test_workflow_replay_uses_case_customer_and_neutral_subject(monkeypatch):
    captured = {}

    async def capture_workflow(state):
        captured.update(state)
        return {
            "suggested_response": "answer",
            "context_citations": [],
            "workflow_path": [],
            "tool_calls": [],
            "errors": [],
        }

    from src.evaluation.offline_rag import collect_workflow_records

    case = load_evaluation_dataset(BASELINE_DATASET_PATH)[0]
    await collect_workflow_records([case], workflow_runner=capture_workflow)

    assert captured["customer_id"] == case.customer_id
    assert captured["subject"] == f"Evaluation case: {case.category}"


def test_trace_sanitizer_redacts_secrets_and_pii():
    sanitized = sanitize_value(
        {
            "message": "Contact alice@example.com or +86 138 0013 8000",
            "api_key": "secret-value",
        }
    )

    assert "alice@example.com" not in sanitized["message"]
    assert "138 0013 8000" not in sanitized["message"]
    assert sanitized["api_key"] == "[FILTERED]"


@pytest.mark.asyncio
async def test_traced_llm_method_preserves_provider_contract():
    provider = MockLLMProvider()

    result = await provider.analyze_ticket("I need a billing refund")

    assert result[0]["department"] == "billing"
    assert result[1:] == (150, 45)


@pytest.mark.asyncio
async def test_offline_evaluation_writes_unified_report(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.evaluation.offline_rag.get_current_trace_id", lambda: "a" * 32
    )

    async def fake_workflow(state):
        return {
            "suggested_response": (
                "Refund requests are allowed within 30 days when there is no active dispute."
            ),
            "context_citations": [
                {
                    "source": "Corporate Refund Policy (v1)",
                    "text": (
                        "Refund requests must be filed within 30 days and "
                        "require no active disputes."
                    ),
                }
            ],
            "department": "billing",
            "intent": "billing_dispute",
            "priority": "medium",
            "tool_calls": [
                {"tool_name": "crm.get_customer_profile"},
                {"tool_name": "tickets.get_past_tickets"},
                {"tool_name": "orders.get_order_history"},
            ],
            "workflow_path": [
                "ticket_analyzer",
                "skill_selector",
                "tool_call",
                "retriever",
                "llm_generation",
                "qa",
                "escalation",
            ],
            "escalation_recommended": False,
            "approval_required": False,
            "tokens_input": 320,
            "tokens_output": 90,
            "cost_usd": 0.0123,
            "latency_seconds": 1.25,
            "errors": [],
        }

    paths = await run_offline_evaluation(
        DATASET_PATH,
        tmp_path,
        engine="local",
        limit=1,
        execution_metadata={
            "mode": "real_llm_regression",
            "llm_provider": "openai",
            "llm_model": "test-model",
        },
        workflow_runner=fake_workflow,
    )

    report = json.loads(paths["json"].read_text(encoding="utf-8"))
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert report["schema_version"] == "3.0"
    assert report["engines"] == {
        "rag": "local",
        "agent": "local",
        "security": "deterministic",
    }
    assert report["case_count"] == 1
    assert report["execution"] == {
        "mode": "real_llm_regression",
        "llm_provider": "openai",
        "llm_model": "test-model",
        "usage": {
            "tokens_input": 320,
            "tokens_output": 90,
            "tokens_total": 410,
            "cost_usd": 0.0123,
            "workflow_latency_seconds": 1.25,
            "average_workflow_latency_seconds": 1.25,
        },
    }
    assert set(report["rag_evaluation"]["aggregates"]) == {
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    }
    assert report["cases"][0]["rag_evaluation"]["citation_hit"] is True
    assert report["cases"][0]["agent_evaluation"]["passed"] is True
    assert report["cases"][0]["security_evaluation"]["classification"] == (
        "true_negative"
    )
    assert report["security_evaluation"]["detection"]["false_positive_rate"] == 0.0
    assert report["cases"][0]["trace_id"] == "a" * 32
    assert "agent_run_id" in report["cases"][0]
    assert "RAG + Agent + Security Evaluation 统一评测报告" in markdown
    assert "False Positive Rate" in markdown
    assert "test-model" in markdown
    assert "410" in markdown
    assert "OTel Trace ID" in markdown


@pytest.mark.asyncio
async def test_agent_failure_is_linked_to_workflow_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.evaluation.offline_rag.get_current_trace_id", lambda: "b" * 32
    )

    async def wrong_workflow(state):
        return {
            "suggested_response": "Unsupported answer",
            "context_citations": [],
            "department": "general",
            "intent": "information_request",
            "priority": "medium",
            "tool_calls": [],
            "workflow_path": ["ticket_analyzer"],
            "escalation_recommended": False,
            "approval_required": False,
            "errors": [],
        }

    paths = await run_offline_evaluation(
        DATASET_PATH,
        tmp_path,
        engine="local",
        limit=1,
        workflow_runner=wrong_workflow,
    )
    row = json.loads(paths["json"].read_text(encoding="utf-8"))["cases"][0]

    assert row["trace_id"] == "b" * 32
    assert row["agent_evaluation"]["passed"] is False
    assert row["agent_evaluation"]["failures"]


@pytest.mark.asyncio
async def test_ragas_mode_requires_real_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def fake_workflow(state):
        return {"suggested_response": "answer", "context_citations": [], "errors": []}

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await run_offline_evaluation(
            DATASET_PATH,
            Path("unused"),
            engine="ragas",
            limit=1,
            workflow_runner=fake_workflow,
        )


@pytest.mark.asyncio
async def test_deepeval_mode_requires_real_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def fake_workflow(state):
        return {
            "suggested_response": "answer",
            "context_citations": [],
            "workflow_path": [],
            "tool_calls": [],
            "errors": [],
        }

    with pytest.raises(RuntimeError, match="DeepEval engine"):
        await run_offline_evaluation(
            DATASET_PATH,
            tmp_path,
            rag_engine="local",
            agent_engine="deepeval",
            limit=1,
            workflow_runner=fake_workflow,
        )


@pytest.mark.asyncio
async def test_replay_retries_case_after_dependency_failure(monkeypatch):
    """模型服务抖动或熔断造成的失败会等恢复后重跑，不计入行为失败。"""
    from src.config import settings
    from src.evaluation import offline_rag
    from src.evaluation.offline_rag import EvaluationCase, collect_workflow_records

    monkeypatch.setattr(settings, "RESILIENCE_CIRCUIT_RECOVERY_SECONDS", 0.0)
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(offline_rag.asyncio, "sleep", fake_sleep)
    calls = []

    async def flaky_workflow(state):
        calls.append(state["description"])
        if len(calls) == 1:
            return {"errors": ["Resolver dependency failure: ResilienceExhaustedError"]}
        return {"suggested_response": "ok", "errors": [], "workflow_path": ["ticket_analyzer"]}

    case = EvaluationCase(
        id="c1", query="q", reference_answer="a", expected_sources=[],
        category="general", risk_level="low", kb_version="v1",
    )
    records = await collect_workflow_records([case], flaky_workflow)

    assert len(calls) == 2 and slept == [1.0]
    assert records[0].response == "ok"
    assert records[0].workflow_output["evaluation_infrastructure_retries"] == 1


def test_quality_alerts_and_security_blocks_are_not_infrastructure_failures():
    from src.evaluation.offline_rag import is_infrastructure_failure

    assert not is_infrastructure_failure({"errors": ["QA score alert: potential hallucination detected."]})
    assert not is_infrastructure_failure({"errors": ["Security threat: Prompt injection attempt detected in user_input."]})
    assert is_infrastructure_failure({"errors": ["QA agent error: llm.evaluate_qa failed: circuit_open"]})
