"""一次生成、原样复用的回复证据；只接收已经过安全检查的上下文。"""

import json
from copy import deepcopy
from typing import Any

from src.config import settings


_ORDER_TOOL = "orders.get_order_history"
# 证据里对应的记录类型 → 负责查询它的 Tool 前缀。
_RECORD_TOOLS = {
    "orders": ("orders.",),
    "shipments": ("shipping.",),
    "warranty": ("warranty.",),
    "billing_invoices": ("billing.",),
}


def compact_tool_context(
    tool_context: dict[str, Any],
    budget: int | None = None,
    called_tools: set[str] | None = None,
) -> str:
    """优先保留当前业务结果，并按完整字段裁剪，避免截断 JSON 或业务事实。

    called_tools：本次实际调用过的 Tool。没查订单时不输出空的 recent_orders，
    并列出未查询的记录类型，否则模型会把“没查”说成“没有记录”。
    """
    budget = budget or settings.LLM_RESOLVER_MAX_TOOL_CHARS
    if not tool_context:
        return ""
    profile = tool_context.get("customer_profile") or {}
    service = deepcopy(tool_context.get("service_query") or {})
    data = service.get("data")
    if isinstance(data, dict):
        data.pop("customer_id", None)
        data["records"] = (data.get("records") or [])[:2]
    compact = {
        "service_query": service,
        "resolved_request_entities": tool_context.get("resolved_request_entities", {}),
        "customer": {
            key: profile[key]
            for key in ("tier", "open_tickets_count")
            if key in profile
        },
        "recent_orders": [
            {
                key: order[key]
                for key in (
                    "order_id",
                    "status",
                    "items",
                    "total_amount",
                    "currency",
                    "order_date",
                )
                if key in order
            }
            for order in (tool_context.get("recent_orders") or [])[:2]
        ],
        "past_tickets": [
            {
                key: ticket[key]
                for key in ("subject", "status", "resolution")
                if key in ticket
            }
            for ticket in (tool_context.get("past_tickets") or [])[:2]
        ],
    }

    if called_tools is not None:
        if _ORDER_TOOL not in called_tools:
            compact.pop("recent_orders", None)
        not_queried = [
            record
            for record, prefixes in _RECORD_TOOLS.items()
            if not any(name.startswith(prefixes) for name in called_tools)
        ]
        if not_queried:
            # 放在最前，预算紧张时也不会被裁掉。
            compact = {
                "records_not_queried": not_queried,
                "records_not_queried_note": (
                    "These record types were NOT looked up in this turn. Never say the "
                    "customer has no such records; ask for the order number or say it "
                    "needs to be checked."
                ),
                **compact,
            }

    def encode() -> str:
        return json.dumps(
            compact, default=str, ensure_ascii=False, separators=(",", ":")
        )

    # 历史信息先退出预算，当前业务状态和下一步最后保留。
    for key in (
        "past_tickets",
        "recent_orders",
        "customer",
        "resolved_request_entities",
    ):
        if len(encode()) <= budget:
            return encode()
        compact.pop(key, None)
    if isinstance(data, dict) and len(encode()) > budget:
        core_keys = (
            "order_id",
            "status",
            "exception",
            "next_step",
            "payment_status",
            "invoice_status",
            "coverage",
            "repair_request_status",
        )
        data["records"] = [
            {key: record[key] for key in core_keys if key in record}
            for record in data.get("records", [])
            if isinstance(record, dict)
        ][:1]
    if len(encode()) > budget:
        # 超大单字段也不输出半份事实，由正常的证据不足流程接手。
        return '{"business_evidence":"omitted_due_to_size"}'
    return encode()


def build_resolution_evidence(state: dict[str, Any]) -> list[str]:
    """在共同预算内保留 Tool、KB 和会话内容，供 Resolver 与 QA 共享。"""
    remaining = settings.LLM_QA_MAX_CONTEXT_CHARS
    evidence: list[str] = []
    # 没有调用记录（旧 Checkpoint、单元测试）时不推断“未查询”。
    called_tools = (
        {
            str(call.get("tool_name"))
            for call in state.get("tool_calls") or []
            if isinstance(call, dict) and call.get("status") == "success"
        }
        if state.get("tool_calls")
        else None
    )
    tool = compact_tool_context(
        state.get("tool_context") or {},
        min(settings.LLM_RESOLVER_MAX_TOOL_CHARS, max(remaining * 2 // 3 - 7, 100)),
        called_tools=called_tools,
    )
    if tool:
        evidence.append("[TOOL] " + tool)
        remaining -= len(evidence[-1])
    memory = str(state.get("memory_prompt_context") or "").strip()
    memory_budget = min(len(memory) + 60, remaining // 4, 1000) if memory else 0
    rag_budget = min(settings.LLM_RESOLVER_MAX_RAG_CHARS, remaining - memory_budget)
    citations = (state.get("context_citations") or [])[:2]
    for index, citation in enumerate(citations, start=1):
        item = citation if isinstance(citation, dict) else citation.model_dump()
        source = str(item.get("source") or f"doc-{index}")
        version = f" (version={item['version']})" if item.get("version") else ""
        prefix = f"[S{index}] {source}{version}: "
        available = rag_budget // (len(citations) - index + 1)
        if available <= len(prefix):
            break
        block = prefix + str(item.get("text") or "")[: available - len(prefix)]
        evidence.append(block)
        rag_budget -= len(block)
    if memory_budget > 60:
        prefix = "[CONVERSATION: untrusted history, not business authority] "
        evidence.append(prefix + memory[: memory_budget - len(prefix)])
    return evidence
