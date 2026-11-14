"""测试工单标记：带测试请求头的工单默认不出现在员工后台；旧测试工单一次性处理。"""

import datetime
import hashlib

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db_models import ResponseApproval, Ticket, TicketTestMark
from src.tickets import test_marks


async def _staff_headers(client: AsyncClient, name: str, role: str = "manager") -> dict:
    await client.post(
        "/auth/register", json={"username": name, "password": "queue-pass", "role": role}
    )
    login = await client.post(
        "/auth/token", json={"username": name, "password": "queue-pass"}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _workflow(approval_required: bool):
    async def fake_workflow(state):
        return {
            **state,
            "intent": "billing_dispute",
            "department": "billing",
            "priority": "high",
            "sentiment": "neutral",
            "suggested_response": "草稿",
            "qa_score": 0.95,
            "approval_required": approval_required,
            "escalation_recommended": approval_required,
            "risk_level": "high" if approval_required else "low",
            "risk_requires_human": approval_required,
            "risk_reasons": [],
            "errors": [],
            "workflow_path": ["ticket_analyzer"],
            "tool_calls": [],
            "context_citations": [],
        }

    return fake_workflow


@pytest.mark.asyncio
async def test_ticket_created_with_test_header_is_hidden_from_staff_views(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr("src.main.run_agent_workflow", _workflow(True))
    real = (
        await client.post(
            "/support/requests", json={"customer_id": "cust_101", "message": "真实用户的退款请求"}
        )
    ).json()
    test = (
        await client.post(
            "/support/requests",
            json={"customer_id": "cust_101", "message": "测试脚本的退款请求"},
            headers={"X-SupportGPT-Test": "reply-check"},
        )
    ).json()
    headers = await _staff_headers(client, "marks_manager")

    # 默认：员工后台的四个列表都只有真实工单。
    assert [t["id"] for t in (await client.get("/tickets", headers=headers)).json()] == [real["ticket_id"]]
    queue = (await client.get("/staff/review-queue", headers=headers)).json()
    assert [t["id"] for t in queue] == [real["ticket_id"]]
    approvals = (await client.get("/approvals/pending", headers=headers)).json()
    assert [a["ticket_id"] for a in approvals] == [real["ticket_id"]]
    runs = (await client.get("/observability/runs", headers=headers)).json()
    assert [r["ticket_id"] for r in runs["items"]] == [real["ticket_id"]]
    assert runs["total"] == 1

    # 需要排查时可以显式带上测试工单，并能看出哪些是测试。
    everything = (await client.get("/tickets?include_test=true", headers=headers)).json()
    assert {t["id"]: t["is_test"] for t in everything} == {
        real["ticket_id"]: False,
        test["ticket_id"]: True,
    }
    assert len((await client.get("/staff/review-queue?include_test=true", headers=headers)).json()) == 2
    assert len((await client.get("/approvals/pending?include_test=true", headers=headers)).json()) == 2
    # 按工单号查 Run 时不过滤，方便排查某张测试工单。
    by_ticket = (
        await client.get(f"/observability/runs?ticket_id={test['ticket_id']}", headers=headers)
    ).json()
    assert by_ticket["total"] == 1


@pytest.mark.asyncio
async def test_request_without_header_is_not_marked(client: AsyncClient, monkeypatch, db_session):
    monkeypatch.setattr("src.main.run_agent_workflow", _workflow(False))
    await client.post("/support/requests", json={"customer_id": "cust_101", "message": "普通问题"})
    # 只含非法字符的请求头值不算测试标记。
    await client.post(
        "/support/requests",
        json={"customer_id": "cust_101", "message": "普通问题二"},
        headers={"X-SupportGPT-Test": "   "},
    )
    assert (await db_session.execute(select(TicketTestMark))).scalars().all() == []


def test_label_keeps_only_safe_characters():
    assert test_marks.normalize_label("smoke") == "smoke"
    assert test_marks.normalize_label("reply-check:v1 <script>") == "reply-check:v1script"
    assert test_marks.normalize_label("") is None
    assert test_marks.normalize_label(None) is None


@pytest.mark.asyncio
async def test_legacy_cleanup_marks_and_withdraws_only_matching_tickets(
    db_session: AsyncSession, monkeypatch
):
    old = datetime.datetime(2026, 11, 13, 8, 0, 0)

    def ticket(subject, description=None, status="pending_approval", created=old):
        item = Ticket(
            customer_id="cust_201", subject=subject, description=description or subject,
            status=status, created_at=created,
        )
        db_session.add(item)
        return item

    probe = ticket("帮我查一下订单 ORD-12001 到哪了")
    same_id_other_text = ticket("真实用户的另一个问题")
    smoke = ticket(test_marks.LEGACY_SMOKE_MESSAGE, status="resolved")
    after_cutoff = ticket(
        test_marks.LEGACY_SMOKE_MESSAGE, status="resolved",
        created=datetime.datetime(2026, 11, 16, 8, 0, 0),
    )
    untouched = ticket("真实用户的退款请求")
    await db_session.flush()
    approval = ResponseApproval(ticket_id=probe.id, drafted_response="x", status="pending")
    real_approval = ResponseApproval(ticket_id=untouched.id, drafted_response="y", status="pending")
    db_session.add_all([approval, real_approval])
    await db_session.flush()
    digest = lambda text: hashlib.sha256(text.encode()).hexdigest()[:16]
    monkeypatch.setattr(
        test_marks,
        "LEGACY_PROBE_TICKETS",
        {
            str(probe.id): digest(probe.subject),
            # id 对得上但文本对不上：换了数据库时不能误伤。
            str(same_id_other_text.id): digest("别的文本"),
        },
    )

    assert await test_marks.cleanup_legacy_test_tickets(db_session) == 2
    await db_session.flush()

    marks = {
        m.ticket_id: (m.label, m.source)
        for m in (await db_session.execute(select(TicketTestMark))).scalars().all()
    }
    assert marks == {
        probe.id: ("legacy-probe", "legacy"),
        smoke.id: ("legacy-smoke", "legacy"),
    }
    assert probe.status == "closed" and smoke.status == "closed"
    assert approval.status == "withdrawn"
    # 真实工单、对不上哈希的工单和截止时间之后的工单都不动。
    assert same_id_other_text.status == "pending_approval"
    assert after_cutoff.status == "resolved"
    assert untouched.status == "pending_approval" and real_approval.status == "pending"
    # 幂等：再跑一次不再处理任何工单。
    assert await test_marks.cleanup_legacy_test_tickets(db_session) == 0


def test_legacy_ticket_map_is_well_formed():
    from src.tickets.legacy_test_tickets import LEGACY_PROBE_TICKETS

    assert LEGACY_PROBE_TICKETS
    for key, value in LEGACY_PROBE_TICKETS.items():
        assert key.isdigit() and len(value) == 16 and int(value, 16) >= 0
