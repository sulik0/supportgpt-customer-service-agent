"""一次生成、原样复用的回复证据；只接收已经过安全检查的上下文。"""

import json
from copy import deepcopy
from typing import Any

from src.config import settings


def compact_tool_context(
    tool_context: dict[str, Any], budget: int | None = None
) -> str:
    """优先保留当前业务结果，并按完整字段裁剪，避免截断 JSON 或业务事实。"""
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
    tool = compact_tool_context(
        state.get("tool_context") or {},
        min(settings.LLM_RESOLVER_MAX_TOOL_CHARS, max(remaining * 2 // 3 - 7, 100)),
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
