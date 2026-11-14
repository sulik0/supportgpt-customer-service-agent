import datetime
from dataclasses import dataclass
from typing import Dict, Set

from fastapi import HTTPException, status

from src.models.db_models import Ticket


class TicketStatus:
    """集中定义工单生命周期中的合法状态常量。"""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    PENDING_APPROVAL = "pending_approval"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketAction:
    """集中定义可触发工单状态变更的业务动作。"""

    START_WORK = "start_work"
    REQUEST_APPROVAL = "request_approval"
    APPROVE_RESPONSE = "approve_response"
    MODIFY_RESPONSE = "modify_response"
    REJECT_RESPONSE = "reject_response"
    CLOSE = "close"
    WITHDRAW = "withdraw"
    REOPEN = "reopen"


@dataclass(frozen=True)
class Transition:
    """描述一次已执行的工单状态迁移。"""

    source: str
    target: str
    action: str


class TicketStateMachine:
    """统一校验并执行客服工单的合法状态流转。"""

    transitions: Dict[str, Dict[str, str]] = {
        # 提交人撤回：任何未关闭状态都可以直接关闭。
        TicketStatus.OPEN: {
            TicketAction.START_WORK: TicketStatus.IN_PROGRESS,
            TicketAction.REQUEST_APPROVAL: TicketStatus.PENDING_APPROVAL,
            TicketAction.WITHDRAW: TicketStatus.CLOSED,
        },
        TicketStatus.IN_PROGRESS: {
            TicketAction.REQUEST_APPROVAL: TicketStatus.PENDING_APPROVAL,
            TicketAction.WITHDRAW: TicketStatus.CLOSED,
        },
        TicketStatus.PENDING_APPROVAL: {
            TicketAction.APPROVE_RESPONSE: TicketStatus.RESOLVED,
            TicketAction.MODIFY_RESPONSE: TicketStatus.RESOLVED,
            TicketAction.REJECT_RESPONSE: TicketStatus.IN_PROGRESS,
            TicketAction.WITHDRAW: TicketStatus.CLOSED,
        },
        TicketStatus.RESOLVED: {
            TicketAction.CLOSE: TicketStatus.CLOSED,
            TicketAction.REOPEN: TicketStatus.IN_PROGRESS,
            TicketAction.WITHDRAW: TicketStatus.CLOSED,
        },
        TicketStatus.CLOSED: {
            TicketAction.REOPEN: TicketStatus.IN_PROGRESS,
        },
    }

    terminal_statuses: Set[str] = {TicketStatus.CLOSED}

    def transition(self, ticket: Ticket, action: str) -> Transition:
        current = ticket.status or TicketStatus.OPEN
        target = self.transitions.get(current, {}).get(action)
        if not target:
            allowed = sorted(self.transitions.get(current, {}).keys())
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Illegal ticket status transition.",
                    "current_status": current,
                    "requested_action": action,
                    "allowed_actions": allowed,
                },
            )

        ticket.status = target
        ticket.updated_at = datetime.datetime.utcnow()
        return Transition(source=current, target=target, action=action)

    def allowed_actions(self, ticket: Ticket) -> list[str]:
        current = ticket.status or TicketStatus.OPEN
        return sorted(self.transitions.get(current, {}).keys())


ticket_state_machine = TicketStateMachine()
