import json
from pathlib import Path

import pytest

from src.evaluation.baseline_evaluation import (
    BaselineExpectations,
    _build_experiment_config,
    _replace_latest_copy,
    aggregate_behavior,
    build_metric_failure_index,
    evaluate_baseline_behavior,
    run_baseline_evaluation_v1,
)
from src.evaluation.baseline_diff import build_baseline_diff, generate_baseline_diff
from src.models.intents import IntentType
from src.observability.tracing import get_tracer, observed_span, trace_operation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = (
    PROJECT_ROOT / "evaluation" / "baseline" / "supportgpt_baseline_100.json"
)


def _diff_report(run_id, passed_values, *, intent=0.5, latency=2.0, calls=8):
    """构造只包含 Diff 所需字段的固定报告。"""
    return {
        "run_id": run_id,
        "generated_at": f"2026-08-30T{run_id[-6:]}+00:00",
        "experiment_config": {
            "dataset": {"sha256": "d" * 64},
            "models": {"provider": "test"},
        },
        "behavior_summary": {
            "intent_accuracy": intent,
            "department_accuracy": 0.7,
            "required_tool_hit_rate": 0.8,
            "forbidden_tool_violation_rate": 0.1,
            "hitl_accuracy": 0.6,
            "approval_accuracy": 0.6,
            "case_pass_rate": sum(passed_values) / len(passed_values),
        },
        "performance_summary": {
            "end_to_end_latency_seconds": {
                "average": latency,
                "p50": latency - 0.2,
                "p95": latency + 0.8,
            },
            "tokens": {"average_total": 400.0},
            "llm": {"call_count": calls},
            "analyzer": {"rule_hit_rate": 0.5},
        },
        "cases": [
            {
                "id": f"case-{index}",
                "dataset_case": {"query": f"query-{index}"},
                "trace_id": str(index) * 32,
                "behavior_evaluation": {
                    "passed": passed,
                    "failures": [] if passed else ["intent mismatch"],
                },
            }
            for index, passed in enumerate(passed_values, start=1)
        ],
    }


def test_baseline_diff_compares_metrics_and_all_case_transitions(tmp_path):
    previous = _diff_report("20260830_120000", [False, True, False, True])
    current = _diff_report(
        "20260830_130000",
        [True, False, False, True],
        intent=0.75,
        latency=1.5,
        calls=6,
    )

    diff = build_baseline_diff(previous, current)

    assert diff["case_transitions"]["FAIL→PASS"]["case_ids"] == ["case-1"]
    assert diff["case_transitions"]["PASS→FAIL"]["case_ids"] == ["case-2"]
    assert diff["case_transitions"]["FAIL→FAIL"]["case_ids"] == ["case-3"]
    assert diff["case_transitions"]["PASS→PASS"]["case_ids"] == ["case-4"]
    metric = {row["metric"]: row for row in diff["metrics"]}
    assert metric["Intent Accuracy"]["delta"] == 0.25
    assert metric["Average Latency"]["delta"] == -0.5
    assert metric["LLM Calls"]["delta"] == -2.0

    previous_path = tmp_path / "baseline_v1_20260830_120000.json"
    current_path = tmp_path / "baseline_v1_20260830_130000.json"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")
    current_path.write_text(json.dumps(current), encoding="utf-8")
    paths = generate_baseline_diff(current_path, tmp_path)

    assert paths is not None
    assert paths["markdown"].is_symlink() is False
    assert paths["json"].is_symlink() is False
    assert paths["snapshot_markdown"].name == (
        "baseline_diff_20260830_120000_to_20260830_130000.md"
    )
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "FAIL→PASS" in markdown
    assert "Analyzer Rule Hit Rate" in markdown


def test_v1_behavior_pass_ignores_future_dataset_fields():
    """Priority、节点、安全和语义字段暂时不得影响第一版 Case Pass。"""
    expectations = BaselineExpectations.from_mapping(
        {
            "expected_department": "billing",
            "expected_intent": "billing_dispute",
            "required_tools": ["crm.get_customer_profile"],
            "forbidden_tools": ["orders.check_refund_eligibility"],
            "should_escalate": True,
            "should_require_approval": True,
            "expected_priority": {"invalid": "ignored"},
            "expected_nodes": "not-read-by-v1",
            "security_label": object(),
        }
    )
    output = {
        "department": "billing",
        "intent": IntentType.BILLING_DISPUTE,
        "priority": "low",
        "workflow_path": ["different-node"],
        "tool_calls": [{"tool_name": "crm.get_customer_profile"}],
        "escalation_recommended": True,
        "approval_required": True,
        "errors": ["ignored in V1 pass"],
    }

    result = evaluate_baseline_behavior(output, expectations)

    assert result.passed is True
    assert result.failures == ()
    assert set(result.checks) == {
        "intent_accuracy",
        "department_accuracy",
        "required_tool_hit_rate",
        "forbidden_tool_violation_rate",
        "hitl_accuracy",
        "approval_accuracy",
    }


def test_v1_behavior_aggregates_using_real_check_denominators():
    expectations = BaselineExpectations(
        expected_department="billing",
        expected_intent=IntentType.BILLING_DISPUTE,
        required_tools=["crm", "orders"],
        forbidden_tools=["refund_execute"],
        should_escalate=True,
        should_require_approval=True,
    )
    passed = evaluate_baseline_behavior(
        {
            "department": "billing",
            "intent": "billing_dispute",
            "tool_calls": [{"tool_name": "crm"}, {"tool_name": "orders"}],
            "escalation_recommended": True,
            "approval_required": True,
        },
        expectations,
    )
    failed = evaluate_baseline_behavior(
        {
            "department": "general",
            "intent": "information_request",
            "tool_calls": [
                {"tool_name": "crm"},
                {"tool_name": "refund_execute"},
            ],
            "escalation_recommended": False,
            "approval_required": False,
        },
        expectations,
    )

    summary = aggregate_behavior([passed, failed])

    assert summary["intent_accuracy"] == 0.5
    assert summary["department_accuracy"] == 0.5
    assert summary["required_tool_hit_rate"] == 0.75
    assert summary["forbidden_tool_violation_rate"] == 0.5
    assert summary["hitl_accuracy"] == 0.5
    assert summary["approval_accuracy"] == 0.5
    assert summary["case_pass_rate"] == 0.5


def test_metric_failure_index_groups_failed_cases_by_metric():
    """指标索引应排除 N/A Case，并保留期望、实际值与 Trace。"""
    rows = [
        {
            "id": "case-tool-missing",
            "dataset_case": {"query": "Please check my order."},
            "trace_id": "a" * 32,
            "behavior_evaluation": {
                "checks": {
                    "intent_accuracy": 1.0,
                    "department_accuracy": None,
                    "required_tool_hit_rate": 0.5,
                    "forbidden_tool_violation_rate": 0.0,
                    "hitl_accuracy": 0.0,
                    "approval_accuracy": 1.0,
                },
                "expected": {
                    "intent": "order_issue",
                    "department": None,
                    "required_tools": ["crm", "orders"],
                    "forbidden_tools": ["refund_execute"],
                    "hitl": True,
                    "approval": True,
                },
                "actual": {
                    "intent": "order_issue",
                    "department": "general",
                    "tools": ["crm"],
                    "hitl": False,
                    "approval": True,
                },
            },
        }
    ]

    index = build_metric_failure_index(rows)

    assert index["intent_accuracy"]["failed_case_count"] == 0
    assert index["department_accuracy"]["evaluated_case_count"] == 0
    tool_failure = index["required_tool_hit_rate"]["cases"][0]
    assert tool_failure["case_id"] == "case-tool-missing"
    assert tool_failure["query"] == "Please check my order."
    assert tool_failure["expected"] == ["crm", "orders"]
    assert tool_failure["actual"] == ["crm"]
    assert tool_failure["reason"] == "missing required tools: orders"
    assert tool_failure["trace_id"] == "a" * 32
    assert index["hitl_accuracy"]["failed_case_ids"] == ["case-tool-missing"]
    assert index["forbidden_tool_violation_rate"]["failed_case_count"] == 0


def test_latest_report_replaces_existing_symlink_with_regular_file(tmp_path):
    """latest 必须是 Typora 可直接打开的普通文件。"""
    snapshot = tmp_path / "baseline_v1_20260827_120000.md"
    latest = tmp_path / "baseline_v1_latest.md"
    snapshot.write_text("# report\n", encoding="utf-8")
    latest.symlink_to(snapshot.name)

    _replace_latest_copy(latest, snapshot)

    assert latest.is_symlink() is False
    assert latest.read_text(encoding="utf-8") == "# report\n"


def test_experiment_config_preserves_valid_git_revision(monkeypatch):
    """Git SHA 不得被 PII 电话号规则误脱敏。"""
    revision = "5302a47e910ecd66ca799350d806004675bd53b5"
    monkeypatch.setattr(
        "src.evaluation.baseline_evaluation._source_revision", lambda: revision
    )

    config = _build_experiment_config(BASELINE_PATH, {})

    assert config["workflow"]["source_revision"] == revision


@pytest.mark.asyncio
async def test_v1_replays_full_ticket_state_and_records_trace_performance(tmp_path):
    tracer = get_tracer("tests.baseline_v1")

    @trace_operation(name="supportgpt.llm.generate_resolution", component="llm")
    async def fake_resolution():
        return "Refund guidance", 10, 5

    async def fake_workflow(state):
        with observed_span(tracer, "agent.analyzer"):
            pass
        with observed_span(tracer, "agent.tooling"):
            pass
        with observed_span(tracer, "agent.retriever"):
            pass
        with observed_span(tracer, "agent.resolver"):
            response, _, _ = await fake_resolution()
        with observed_span(tracer, "agent.qa"):
            pass
        return {
            **state,
            "trace_id": "c" * 32,
            "suggested_response": response,
            "context_citations": [],
            "department": "billing",
            "intent": IntentType.BILLING_DISPUTE,
            "priority": "high",
            "tool_calls": [
                {"tool_name": "crm.get_customer_profile"},
                {"tool_name": "tickets.get_past_tickets"},
                {"tool_name": "orders.get_order_history"},
            ],
            "workflow_path": [
                "ticket_analyzer",
                "tool_call",
                "retriever",
                "llm_generation",
                "qa",
                "escalation",
            ],
            "escalation_recommended": True,
            "approval_required": True,
            "analyzer_strategy": "rule",
            "tokens_input": 10,
            "tokens_output": 5,
            "latency_seconds": 0.25,
            "errors": [],
        }

    paths = await run_baseline_evaluation_v1(
        BASELINE_PATH,
        tmp_path,
        limit=1,
        execution_metadata={"llm_model": "test-model"},
        workflow_runner=fake_workflow,
    )

    report = json.loads(paths["json"].read_text(encoding="utf-8"))
    row = report["cases"][0]
    assert report["evaluation_type"] == "baseline_workflow_replay_v1"
    assert report["schema_version"] == "1.1"
    assert report["run_id"]
    assert report["case_count"] == 1
    assert "rag_evaluation" not in report
    assert "security_evaluation" not in report
    assert row["behavior_evaluation"]["passed"] is True
    assert row["dataset_case"]["reference_answer"]
    assert row["dataset_case"]["agent_expectations"]["expected_priority"] == "high"
    assert row["ticket_state"]["request_id"].startswith("offline-eval-")
    assert row["ticket_state"]["intent"] == "information_request"
    assert row["trace_id"] == "c" * 32
    assert row["performance"]["tokens"]["total"] == 15
    assert row["performance"]["llm_call_count"] == 1
    assert row["performance"]["analyzer_strategy"] == "rule"
    assert all(
        row["performance"]["node_latency_seconds"][node] is not None
        for node in ("analyzer", "tool", "rag", "resolver", "qa")
    )
    assert report["performance_summary"]["analyzer"]["rule_hit_rate"] == 1.0
    assert set(report["metric_failure_index"]) == {
        "intent_accuracy",
        "department_accuracy",
        "required_tool_hit_rate",
        "forbidden_tool_violation_rate",
        "hitl_accuracy",
        "approval_accuracy",
    }
    assert all(
        summary["failed_case_count"] == 0
        for summary in report["metric_failure_index"].values()
    )
    assert "reference_answer" in report["ignored_dataset_fields"]
    assert len(report["experiment_config"]["dataset"]["sha256"]) == 64
    assert report["experiment_config"]["dataset"]["dataset_name"] == (
        "supportgpt_business_baseline_100"
    )
    assert report["experiment_config"]["dataset"]["version"] == "2.2"
    assert report["experiment_config"]["workflow"]["version"]
    assert report["experiment_config"]["models"]["provider"] == "mock"
    assert paths["snapshot_json"].name.startswith("baseline_v1_20")
    assert paths["snapshot_markdown"].name.startswith("baseline_v1_20")
    assert paths["json"].is_symlink() is False
    assert paths["markdown"].is_symlink() is False
    assert paths["json"].read_bytes() == paths["snapshot_json"].read_bytes()
    assert paths["markdown"].read_bytes() == paths["snapshot_markdown"].read_bytes()
    assert paths["error_analysis"].name == "error_analysis_latest.md"
    assert paths["error_analysis"].is_symlink() is False
    assert paths["error_analysis_snapshot"].name.startswith(
        "error_analysis_20"
    )
    assert (
        paths["error_analysis"].read_bytes()
        == paths["error_analysis_snapshot"].read_bytes()
    )
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "Baseline Workflow Replay V1" in markdown
    assert "Analyzer Rule Hit Rate" in markdown
    assert "实验配置" in markdown
    assert "按指标定位失败 Case" in markdown
    error_analysis = paths["error_analysis"].read_text(encoding="utf-8")
    assert "FAIL Cases：0" in error_analysis
    assert "本次没有 FAIL Case" in error_analysis
