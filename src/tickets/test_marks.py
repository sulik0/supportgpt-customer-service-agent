"""测试工单标记：测试、冒烟和线上检查产生的工单必须带标记，员工后台默认隐藏。"""

import datetime
import hashlib
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.durable_execution import durable_execution_service
from src.models.db_models import ResponseApproval, Ticket, TicketTestMark
from src.tickets.legacy_test_tickets import LEGACY_PROBE_TICKETS
from src.tickets.state_machine import TicketAction, TicketStatus, ticket_state_machine

logger = logging.getLogger("supportgpt.tickets.test_marks")

# 公开咨询入口带上这个请求头，创建的工单即标记为测试工单。
TEST_HEADER = "X-SupportGPT-Test"


def test_ticket_ids():
    """子查询：所有测试工单的 id。"""
    return select(TicketTestMark.ticket_id)


def normalize_label(value: str | None) -> str | None:
    """请求头的值作为标签，只保留安全字符；空值表示不是测试请求。"""
    label = re.sub(r"[^A-Za-z0-9_.:-]", "", str(value or ""))[:60]
    return label or None


async def mark_test_ticket(
    db: AsyncSession, ticket_id: int, *, label: str, source: str
) -> bool:
    """幂等地给工单打测试标记。"""
    if await db.get(TicketTestMark, ticket_id) is not None:
        return False
    db.add(TicketTestMark(ticket_id=ticket_id, label=label[:60], source=source))
    return True


async def withdraw_ticket(db: AsyncSession, ticket: Ticket) -> int:
    """关闭工单，待审批记录置为 withdrawn 并取消暂停的执行；不删除任何记录。"""
    pending = (
        (
            await db.execute(
                select(ResponseApproval).where(
                    ResponseApproval.ticket_id == ticket.id,
                    ResponseApproval.status == "pending",
                )
            )
        )
        .scalars()
        .all()
    )
    for approval in pending:
        approval.status = "withdrawn"
        await durable_execution_service.cancel_for_approval(db, approval.id)
    if ticket.status != TicketStatus.CLOSED:
        ticket_state_machine.transition(ticket, TicketAction.WITHDRAW)
    return len(pending)


# --- 一次性处理：撤回接口和测试标记上线之前留下的线上测试工单 ---
# 这个时间之前的部署后冒烟工单（固定问句）也按测试工单处理。
LEGACY_CUTOFF = datetime.datetime(2026, 11, 15, 0, 0, 0)
LEGACY_SMOKE_MESSAGE = "你们的退货政策是什么？"


def _subject_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode()).hexdigest()[:16]


async def cleanup_legacy_test_tickets(db: AsyncSession) -> int:
    """启动时执行，幂等：给旧测试工单打标记并撤回。返回本次处理的数量。

    探测工单同时比对 id 和首行文本哈希，换一个数据库时不会误伤同 id 的其他工单。
    """
    candidates = (
        (
            await db.execute(
                select(Ticket).where(
                    Ticket.created_at < LEGACY_CUTOFF,
                    Ticket.id.notin_(test_ticket_ids()),
                    Ticket.id.in_([int(key) for key in LEGACY_PROBE_TICKETS])
                    | (Ticket.description == LEGACY_SMOKE_MESSAGE),
                )
            )
        )
        .scalars()
        .all()
    )
    handled = 0
    for ticket in candidates:
        expected = LEGACY_PROBE_TICKETS.get(str(ticket.id))
        is_probe = expected is not None and expected == _subject_hash(ticket.subject)
        is_smoke = ticket.description == LEGACY_SMOKE_MESSAGE
        if not (is_probe or is_smoke):
            continue
        await mark_test_ticket(
            db,
            ticket.id,
            label="legacy-probe" if is_probe else "legacy-smoke",
            source="legacy",
        )
        await withdraw_ticket(db, ticket)
        handled += 1
    if handled:
        logger.warning("Marked and withdrew %s legacy test tickets.", handled)
    return handled
