import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.feedback.service import feedback_service
from src.models.db_models import AgentRun, FeedbackEvent, Ticket


@pytest.mark.asyncio
async def test_chat_run_and_user_feedback_are_trace_linked(
    client: AsyncClient, db_session, agent_headers: dict[str, str]
):
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"

    chat = await client.post(
        "/chat",
        headers=agent_headers,
        json={
            "session_id": "feedback-session",
            "customer_id": "cust_101",
            "message": (
                "Email alice@example.com, api_key=sk-abcdefghijklmnopqrstuvwxyz. "
                "I need a billing refund."
            ),
            "kb_version": "v1",
        },
    )
    assert chat.status_code == 200
    run_id = chat.json()["agent_run_id"]
    feedback_token = chat.json()["feedback_token"]
    assert run_id
    assert feedback_token

    feedback = await client.post(
        "/feedback/user",
        json={
            "agent_run_id": run_id,
            "feedback_token": feedback_token,
            "rating": 5,
            "comment": "Please contact alice@example.com; this was helpful.",
            "idempotency_key": "feedback-request-0001",
        },
    )
    assert feedback.status_code == 201
    row = feedback.json()
    assert row["agent_run_id"] == run_id
    assert row["rating"] == 5
    assert "alice@example.com" not in row["comment"]

    run = await db_session.get(AgentRun, run_id)
    assert "alice@example.com" not in run.input_text
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in run.input_text
    assert run.session_id_hash == feedback_service._identifier_hash("feedback-session")
    assert run.session_id_hash != "feedback-session"
    assert row["trace_id"] == run.trace_id
    ticket = await db_session.get(Ticket, run.ticket_id)
    assert ticket is not None
    assert ticket.customer_id == "cust_101"

    event = await db_session.get(FeedbackEvent, row["id"])
    assert event is not None
    assert event.agent_run_id == run.id
    assert event.ticket_id == ticket.id

    untrusted_evaluation = await client.post(
        "/evaluate-response",
        json={
            "query": "Question",
            "context": ["Context"],
            "response": "Answer",
            "agent_run_id": run_id,
        },
    )
    assert untrusted_evaluation.status_code == 401


@pytest.mark.asyncio
async def test_user_feedback_is_idempotent_and_token_scoped(
    client: AsyncClient, agent_headers: dict[str, str]
):
    chat = await client.post(
        "/chat",
        headers=agent_headers,
        json={
            "session_id": "owned-session",
            "customer_id": "cust_101",
            "message": "How do I update account preferences?",
            "kb_version": "v1",
        },
    )
    run_id = chat.json()["agent_run_id"]
    feedback_token = chat.json()["feedback_token"]
    payload = {
        "agent_run_id": run_id,
        "feedback_token": feedback_token,
        "rating": 4,
        "comment": "Useful",
        "idempotency_key": "feedback-request-0002",
    }
    first = await client.post("/feedback/user", json=payload)
    second = await client.post("/feedback/user", json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    payload["idempotency_key"] = "feedback-request-0003"
    replayed = await client.post("/feedback/user", json=payload)
    assert replayed.status_code == 201
    assert replayed.json()["id"] == first.json()["id"]

    payload["feedback_token"] = "x" * 43
    denied = await client.post("/feedback/user", json=payload)
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_human_correction_and_evaluation_export_sft_dpo(
    client: AsyncClient, db_session, tmp_path
):
    register = await client.post(
        "/auth/register",
        json={
            "username": "feedback_manager",
            "password": "password",
            "role": "manager",
        },
    )
    assert register.status_code == 201
    login = await client.post(
        "/auth/token",
        json={"username": "feedback_manager", "password": "password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    chat = await client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": "training-session",
            "customer_id": "cust_101",
            "message": "I need a billing refund.",
            "kb_version": "v1",
        },
    )
    payload = chat.json()
    run_id = payload["agent_run_id"]
    approval_id = payload["approval_id"]

    approval = await client.post(
        f"/approvals/{approval_id}",
        headers=headers,
        json={
            "approval_id": approval_id,
            "modified_response": "A manager reviewed the policy and approved this response.",
            "status": "modified",
        },
    )
    assert approval.status_code == 200

    run_detail = await client.get(f"/feedback/runs/{run_id}", headers=headers)
    assert run_detail.status_code == 200
    response_trace_id = chat.headers.get("X-Trace-ID")
    if response_trace_id:
        assert run_detail.json()["trace_id"] == response_trace_id
    assert run_detail.json()["prompt_version"]
    assert run_detail.json()["skill_selection"]["skill_name"] == "refund_support"
    assert run_detail.json()["skill_selection"]["skill_version"] == "v1.1"
    snapshot = run_detail.json()["execution_snapshot"]
    assert snapshot["analyzer_strategy"] == "rule"
    assert snapshot["analyzer_result"] == {
        "intent": "billing_dispute",
        "department": "billing",
        "priority": "high",
        "sentiment": "neutral",
        "confidence": 0.95,
    }
    assert snapshot["qa_strategy"] == "llm"
    assert snapshot["qa_result"]["score"] == run_detail.json()["qa_score"]
    assert isinstance(snapshot["decision_records"], list)
    assert any(
        event["source"] == "human_review"
        for event in run_detail.json()["feedback_events"]
    )

    run_list = await client.get(
        "/observability/runs?limit=10&offset=0", headers=headers
    )
    assert run_list.status_code == 200
    page = run_list.json()
    assert page["total"] >= 1
    assert page["limit"] == 10
    listed = next(item for item in page["items"] if item["id"] == run_id)
    assert listed["trace_id"] == run_detail.json()["trace_id"]
    assert listed["workflow_path"]
    assert listed["skill_selection"]["selection_strategy"] == "intent_rule"
    assert "input_text" not in listed

    ticket_id = run_detail.json()["ticket_id"]
    filtered_runs = await client.get(
        f"/observability/runs?limit=10&offset=0&ticket_id={ticket_id}",
        headers=headers,
    )
    assert filtered_runs.status_code == 200
    filtered_page = filtered_runs.json()
    assert filtered_page["total"] >= 1
    assert all(item["ticket_id"] == ticket_id for item in filtered_page["items"])

    missing_runs = await client.get(
        "/observability/runs?ticket_id=99999999", headers=headers
    )
    assert missing_runs.status_code == 200
    assert missing_runs.json()["total"] == 0
    assert missing_runs.json()["items"] == []

    invalid_ticket_filter = await client.get(
        "/observability/runs?ticket_id=0", headers=headers
    )
    assert invalid_ticket_filter.status_code == 422

    await client.post(
        "/auth/register",
        json={
            "username": "observability_agent",
            "password": "password",
            "role": "agent",
        },
    )
    agent_login = await client.post(
        "/auth/token",
        json={"username": "observability_agent", "password": "password"},
    )
    agent_headers = {"Authorization": f"Bearer {agent_login.json()['access_token']}"}
    denied_runs = await client.get("/observability/runs", headers=agent_headers)
    assert denied_runs.status_code == 403

    evaluation = await client.post(
        "/evaluate-response",
        headers=headers,
        json={
            "query": "I need a billing refund.",
            "context": ["Refund requests require policy review."],
            "response": approval.json()["final_response"],
            "agent_run_id": run_id,
            "external_ref": "online-eval-case-1",
        },
    )
    assert evaluation.status_code == 200

    summary = await feedback_service.export_training_candidates(db_session, tmp_path)
    assert summary["sft_count"] == 1
    assert summary["dpo_count"] == 1
    assert (tmp_path / "manifest.json").exists()

    sft = json.loads((tmp_path / "sft_candidates.jsonl").read_text().strip())
    dpo = json.loads((tmp_path / "dpo_candidates.jsonl").read_text().strip())
    assert sft["metadata"]["agent_run_id"] == run_id
    assert sft["metadata"]["skill_name"] == "refund_support"
    assert sft["metadata"]["skill_version"] == "v1.1"
    assert dpo["chosen"] == approval.json()["final_response"]
    assert dpo["chosen"] != dpo["rejected"]

    events = await db_session.execute(
        select(FeedbackEvent).where(FeedbackEvent.agent_run_id == run_id)
    )
    assert {event.source for event in events.scalars()} >= {
        "human_review",
        "evaluation",
    }


@pytest.mark.asyncio
async def test_positive_user_feedback_alone_does_not_enter_sft(db_session, tmp_path):
    run = AgentRun(
        id="positive-feedback-run",
        ticket_id=None,
        session_id_hash=feedback_service._identifier_hash("session-1"),
        request_id="request-1",
        trace_id="f" * 32,
        feedback_token_hash=feedback_service._token_hash("z" * 43),
        endpoint="/chat",
        workflow_version="workflow-v1",
        prompt_version="prompt-v1",
        model_provider="mock",
        model_name="mock-v1",
        kb_version="v1",
        input_text="How do I update preferences?",
        output_text="Open Settings and Preferences.",
        workflow_path=[],
        tool_calls=[],
        citations=[],
        qa_score=0.95,
        hallucination_detected=False,
        escalation_recommended=False,
        approval_required=False,
        workflow_errors=[],
        tokens_input=10,
        tokens_output=10,
        latency_seconds=0.1,
    )
    db_session.add(run)
    await db_session.flush()
    await feedback_service.record_user_feedback(
        db_session,
        agent_run_id=run.id,
        feedback_token="z" * 43,
        rating=5,
        comment="Helpful",
        idempotency_key="positive-feedback-only",
    )
    await db_session.commit()

    before_eval = await feedback_service.export_training_candidates(
        db_session, tmp_path / "before"
    )
    assert before_eval["sft_count"] == 0

    await feedback_service.record_evaluation(
        db_session,
        agent_run_id=run.id,
        metrics={"overall_quality_score": 0.95},
        passed=True,
        external_ref="positive-feedback-eval",
    )
    await db_session.commit()
    after_eval = await feedback_service.export_training_candidates(
        db_session, tmp_path / "after"
    )
    assert after_eval["sft_count"] == 1
    assert after_eval["dpo_count"] == 0

    await feedback_service.record_evaluation(
        db_session,
        agent_run_id=run.id,
        metrics={"overall_quality_score": 0.4},
        passed=False,
        external_ref="positive-feedback-regression",
    )
    await db_session.commit()
    evaluation_events = await db_session.execute(
        select(FeedbackEvent).where(
            FeedbackEvent.agent_run_id == run.id,
            FeedbackEvent.source == "evaluation",
        )
    )
    evaluation_event = evaluation_events.scalars().one()
    assert evaluation_event.sequence == 2
    assert evaluation_event.evaluation_passed is False
    assert len(evaluation_event.evaluation_metrics["_history"]) == 1
    after_regression = await feedback_service.export_training_candidates(
        db_session, tmp_path / "after-regression"
    )
    assert after_regression["sft_count"] == 0
