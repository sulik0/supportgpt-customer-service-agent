import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db_models import AgentReviewContext, AgentRun, Ticket


def _public_workflow_output(state, *, approval_required: bool):
    """构造用户咨询接口所需的最小 Agent 输出。"""
    return {
        **state,
        "suggested_response": "这是可直接展示给用户的回复。",
        "sentiment": "neutral",
        "priority": "medium",
        "department": "general",
        "sla_hours": 24.0,
        "approval_required": approval_required,
        "escalation_recommended": approval_required,
        "qa_score": 0.92,
        "hallucination_detected": False,
        "workflow_path": ["ticket_analyzer", "resolver", "qa"],
        "tool_calls": [],
        "context_citations": [],
        "tokens_input": 80,
        "tokens_output": 30,
        "latency_seconds": 0.15,
    }


@pytest.mark.asyncio
async def test_internal_ticket_endpoints_require_staff_login(client: AsyncClient):
    create_response = await client.post(
        "/tickets",
        json={"customer_id": "cust_101", "subject": "内部工单", "description": "测试"},
    )
    list_response = await client.get("/tickets")
    detail_response = await client.get("/tickets/1/agent-result")

    assert create_response.status_code == 401
    assert list_response.status_code == 401
    assert detail_response.status_code == 401


@pytest.mark.asyncio
async def test_create_ticket_runs_agent_once_and_detail_only_reads_saved_result(
    client: AsyncClient,
    db_session: AsyncSession,
    agent_headers: dict[str, str],
    monkeypatch,
):
    workflow_calls = []

    async def fake_workflow(state):
        workflow_calls.append(state.copy())
        return {
            **state,
            "suggested_response": "已保存的 Agent 回复",
            "sentiment": "negative",
            "priority": "high",
            "department": "billing",
            "sla_hours": 4.0,
            "approval_required": True,
            "escalation_recommended": True,
            "escalation_reason": "Risk Engine classified ticket as high: high_risk_business_intent.",
            "risk_level": "high",
            "risk_score": 0.82,
            "risk_reasons": ["high_risk_business_intent"],
            "analyzer_confidence": 0.91,
            "qa_score": 0.88,
            "hallucination_detected": False,
            "workflow_path": ["ticket_analyzer", "retriever", "llm_generation"],
            "tool_calls": [],
            "context_citations": [
                {
                    "source": "refund_policy.md",
                    "text": "退款申请应在购买后 30 天内提交。",
                    "score": 0.95,
                    "version": state["kb_version"],
                }
            ],
            "tokens_input": 120,
            "tokens_output": 60,
            "latency_seconds": 0.25,
        }

    monkeypatch.setattr("src.main.run_agent_workflow", fake_workflow)
    create_response = await client.post(
        "/tickets",
        json={
            "customer_id": "cust_101",
            "subject": "退款申请",
            "description": "这笔费用需要退款。",
            "kb_version": "v2",
        },
        headers=agent_headers,
    )

    assert create_response.status_code == 201
    ticket_id = create_response.json()["id"]
    assert create_response.json()["status"] == "pending_approval"
    assert len(workflow_calls) == 1
    assert workflow_calls[0]["ticket_id"] == ticket_id
    assert workflow_calls[0]["kb_version"] == "v2"

    first_detail = await client.get(
        f"/tickets/{ticket_id}/agent-result", headers=agent_headers
    )
    second_detail = await client.get(
        f"/tickets/{ticket_id}/agent-result", headers=agent_headers
    )

    assert first_detail.status_code == 200
    assert second_detail.status_code == 200
    assert first_detail.json()["response"] == "已保存的 Agent 回复"
    assert first_detail.json()["kb_version"] == "v2"
    assert first_detail.json()["approval_required"] is True
    assert first_detail.json()["approval_id"] is not None
    assert first_detail.json()["citations"][0]["source"] == "refund_policy.md"
    assert first_detail.json()["escalation_reason"].startswith(
        "Risk Engine classified ticket as high"
    )
    assert first_detail.json()["review_reasons"] == [
        "Risk Engine classified ticket as high: high_risk_business_intent.",
        "high_risk_business_intent",
        "negative_high_priority",
    ]
    assert first_detail.json()["risk_level"] == "high"
    assert first_detail.json()["risk_score"] == 0.82
    assert first_detail.json()["analyzer_confidence"] == 0.91
    assert len(workflow_calls) == 1

    ticket_count = await db_session.scalar(select(func.count()).select_from(Ticket))
    run_count = await db_session.scalar(select(func.count()).select_from(AgentRun))
    review_count = await db_session.scalar(
        select(func.count()).select_from(AgentReviewContext)
    )
    assert ticket_count == 1
    assert run_count == 1
    assert review_count == 1


@pytest.mark.asyncio
async def test_ticket_without_agent_run_returns_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
    agent_headers: dict[str, str],
):
    ticket = Ticket(
        customer_id="legacy_customer",
        subject="历史工单",
        description="这张工单创建于自动处理功能之前。",
        status="open",
    )
    db_session.add(ticket)
    await db_session.commit()
    await db_session.refresh(ticket)

    response = await client.get(
        f"/tickets/{ticket.id}/agent-result", headers=agent_headers
    )

    assert response.status_code == 404
    assert "No persisted Agent result" in response.json()["detail"]


@pytest.mark.asyncio
async def test_public_support_request_returns_only_safe_answer(
    client: AsyncClient, monkeypatch
):
    async def fake_workflow(state):
        return _public_workflow_output(state, approval_required=False)

    monkeypatch.setattr("src.main.run_agent_workflow", fake_workflow)
    response = await client.post(
        "/support/requests",
        json={"customer_id": "cust_101", "message": "如何查看订单状态？"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "answered"
    assert payload["response"] == "这是可直接展示给用户的回复。"
    assert payload["session_id"]
    assert payload["agent_run_id"]
    assert payload["feedback_token"]
    assert set(payload) == {
        "ticket_id",
        "session_id",
        "status",
        "response",
        "message",
        "handling_reason",
        "created_at",
        "agent_run_id",
        "feedback_token",
    }

    feedback = await client.post(
        "/feedback/user",
        json={
            "agent_run_id": payload["agent_run_id"],
            "feedback_token": payload["feedback_token"],
            "rating": 5,
            "comment": "这次回答对我有帮助。",
            "idempotency_key": "public-support-feedback-0001",
        },
    )
    assert feedback.status_code == 201
    assert feedback.json()["agent_run_id"] == payload["agent_run_id"]


@pytest.mark.asyncio
async def test_public_support_request_hides_draft_and_enters_staff_queue(
    client: AsyncClient, monkeypatch
):
    async def fake_workflow(state):
        output = _public_workflow_output(state, approval_required=True)
        output.update({"risk_level": "high", "risk_requires_human": True})
        return output

    monkeypatch.setattr("src.main.run_agent_workflow", fake_workflow)
    response = await client.post(
        "/support/requests",
        json={"customer_id": "cust_101", "message": "请处理高风险退款。"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "pending_human"
    # 非安全原因的转人工不再显示“安全核验”。
    assert payload["handling_reason"] == "business_review"
    assert payload["response"]
    assert payload["response"] != "这是可直接展示给用户的回复。"
    assert "人工客服" in payload["response"]

    register = await client.post(
        "/auth/register",
        json={"username": "queue_agent", "password": "queue-pass", "role": "agent"},
    )
    assert register.status_code == 201
    login = await client.post(
        "/auth/token",
        json={"username": "queue_agent", "password": "queue-pass"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    queue = await client.get("/staff/review-queue", headers=headers)

    assert queue.status_code == 200
    assert [ticket["id"] for ticket in queue.json()] == [payload["ticket_id"]]


@pytest.mark.asyncio
async def test_readonly_billing_question_shows_preliminary_answer_and_keeps_review(
    client: AsyncClient, monkeypatch
):
    """只读账务咨询：用户先看到初步答复，审批记录和人工队列照常保留。"""

    async def fake_workflow(state):
        output = _public_workflow_output(state, approval_required=True)
        output.update(
            {
                "intent": "billing_dispute",
                "risk_level": "high",
                "risk_requires_human": True,
                "risk_reasons": ["high_priority", "high_risk_business_intent"],
            }
        )
        return output

    monkeypatch.setattr("src.main.run_agent_workflow", fake_workflow)
    session_id = "preliminary-session-0001"
    response = await client.post(
        "/support/requests",
        json={
            "customer_id": "cust_101",
            "session_id": session_id,
            "message": "我的发票开了吗？发票号是多少",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "answered"
    assert payload["handling_reason"] == "preliminary_answer"
    assert payload["response"] == "这是可直接展示给用户的回复。"
    assert "人工客服复核" in payload["message"]

    history = await client.get(
        "/support/history",
        params={"customer_id": "cust_101", "session_id": session_id},
    )
    replies = [m for m in history.json()["messages"] if m["role"] == "assistant"]
    assert [m["status"] for m in replies] == ["preliminary"]
    assert replies[0]["content"] == "这是可直接展示给用户的回复。"

    await client.post(
        "/auth/register",
        json={"username": "prelim_agent", "password": "queue-pass", "role": "agent"},
    )
    login = await client.post(
        "/auth/token",
        json={"username": "prelim_agent", "password": "queue-pass"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    queue = await client.get("/staff/review-queue", headers=headers)
    assert payload["ticket_id"] in [ticket["id"] for ticket in queue.json()]


@pytest.mark.asyncio
async def test_preliminary_answer_is_not_fed_into_next_turn_prompt(
    client: AsyncClient, monkeypatch
):
    """未经人工确认的初步答复不进入下一轮 Prompt。"""
    seen: list[str] = []

    async def fake_workflow(state):
        seen.append(str(state.get("memory_prompt_context", "")))
        output = _public_workflow_output(state, approval_required=True)
        output.update(
            {
                "intent": "billing_dispute",
                "risk_level": "high",
                "risk_requires_human": True,
                "risk_reasons": ["high_priority"],
            }
        )
        return output

    monkeypatch.setattr("src.main.run_agent_workflow", fake_workflow)
    body = {"customer_id": "cust_101", "session_id": "preliminary-session-0002"}
    await client.post("/support/requests", json={**body, "message": "发票开了吗？"})
    await client.post("/support/requests", json={**body, "message": "金额是多少？"})

    assert "这是可直接展示给用户的回复。" not in seen[1]
