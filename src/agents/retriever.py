import logging
import time
from typing import Any, Dict

from src.guardrails.prompt_injection import analyze_prompt_injection
from src.guardrails.qwen3_guard import merge_qwen3_guard_result, qwen3_guard
from src.guardrails.security_policy import build_security_block
from src.observability.metrics import AGENT_EXECUTION_DURATION_SECONDS
from src.observability.sanitization import redact_text
from src.observability.tracing import (
    get_tracer,
    langsmith_span_attributes,
    observed_span,
    set_span_attributes,
)
from src.rag.vector_store import vector_store
from src.risk.engine import risk_engine

logger = logging.getLogger("supportgpt.agents.retriever")
tracer = get_tracer(__name__)


class KnowledgeRetrievalAgent:
    """负责从版本化知识库中检索政策、流程和 FAQ。

    支持类别过滤与无类别回退，并将结果作为 citation 返回。
    """

    async def retrieve(self, state: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Retriever Node started for version: {state.get('kb_version')}")

        if "Security threat" in "".join(state.get("errors", [])):
            # Bypass node if security threats are already detected
            return state

        subject = state.get("subject", "")
        description = state.get("description", "")
        kb_version = state.get("kb_version", "v1")
        category_filter = state.get("department")
        skill_categories = tuple(state.get("skill_rag_categories", []))
        if (
            category_filter not in {None, "general"}
            and skill_categories
            and category_filter not in skill_categories
        ):
            # 分类异常时仍不允许检索越出 Skill 知识边界。
            category_filter = skill_categories[0]

        # Use a unified query string
        query_str = f"{subject} {description}"
        memory_query = str(state.get("memory_retrieval_context", "")).strip()
        if memory_query:
            query_str = f"{query_str} Prior customer context: {memory_query}"

        try:
            # Run semantic query on our ChromaDB manager
            with observed_span(
                tracer,
                "supportgpt.rag.hybrid_retriever",
                {
                    **langsmith_span_attributes("retriever"),
                    "request.id": state.get("request_id"),
                    "ticket.id": state.get("ticket_id"),
                    "customer.id": state.get("customer_id"),
                    "kb.version": kb_version,
                    "rag.category_filter": category_filter,
                    "skill.name": state.get("skill_name"),
                    "skill.version": state.get("skill_version"),
                    "rag.query_length": len(query_str),
                    "rag.top_k": 3,
                },
            ) as span:
                citations = await vector_store.query_kb(
                    query=query_str,
                    version=kb_version,
                    top_k=3,
                    category_filter=(
                        category_filter if category_filter != "general" else None
                    ),
                )
                set_span_attributes(span, {"rag.citation_count": len(citations)})

            # Fallback query without category filter if no documents found
            if not citations and category_filter != "general":
                logger.info("Retrying query without category filter...")
                with observed_span(
                    tracer,
                    "rag.query_fallback",
                    {
                        **langsmith_span_attributes("retriever"),
                        "request.id": state.get("request_id"),
                        "ticket.id": state.get("ticket_id"),
                        "kb.version": kb_version,
                        "rag.original_category_filter": category_filter,
                        "rag.top_k": 3,
                    },
                ) as span:
                    citations = await vector_store.query_kb(
                        query=query_str, version=kb_version, top_k=3
                    )
                    set_span_attributes(span, {"rag.citation_count": len(citations)})

            # RAG 文档可能被污染，在交给生成模型前检测间接注入。
            retrieved_text = "\n".join(
                f"{citation.source}: {citation.text}" for citation in citations
            )
            # 逐篇检测：不同文档里的词凑在一起不算注入（如一篇的“显示”和另一篇的“API Key”）。
            injection = max(
                (
                    analyze_prompt_injection(
                        f"{citation.source}: {citation.text}",
                        source="rag_document",
                    )
                    for citation in citations
                ),
                key=lambda item: item.risk_score,
                default=analyze_prompt_injection("", source="rag_document"),
            )
            if injection.detected:
                logger.warning(
                    "Indirect prompt injection detected in RAG document",
                    extra={
                        "ticket_id": state.get("ticket_id"),
                        "risk_score": injection.risk_score,
                        "security_source": injection.source,
                    },
                )
                return build_security_block(
                    state,
                    threat_type="Indirect prompt injection",
                    source=injection.source,
                    risk_score=injection.risk_score,
                    findings=[*injection.layers, *injection.signals],
                )

            semantic_result = await qwen3_guard.classify(
                redact_text(retrieved_text), source="rag_document"
            )
            guarded_state = merge_qwen3_guard_result(state, semantic_result)
            assessment = risk_engine.assess(guarded_state, stage="input")
            guarded_state = {**guarded_state, **assessment.state_updates()}
            if semantic_result.block_recommended:
                return build_security_block(
                    guarded_state,
                    threat_type="Qwen3Guard semantic safety violation",
                    source=semantic_result.source,
                    risk_score=semantic_result.policy_score,
                    findings=[
                        f"semantic_severity:{semantic_result.severity}",
                        *(
                            f"semantic_category:{item}"
                            for item in semantic_result.categories
                        ),
                    ],
                )
            if semantic_result.degraded:
                return {
                    **guarded_state,
                    "context_citations": [],
                    "errors": list(guarded_state.get("errors", []))
                    + ["Semantic guard unavailable; RAG context isolated."],
                }

            duration = time.time() - start_time
            AGENT_EXECUTION_DURATION_SECONDS.record(
                duration, {"agent_name": "knowledge_retriever"}
            )

            return {**guarded_state, "context_citations": citations}

        except Exception as e:
            logger.error(f"Error querying vector store in retriever: {e}")
            return {
                **state,
                "errors": state.get("errors", [])
                + [f"Retriever agent error: {str(e)}"],
                "context_citations": [],
            }


knowledge_retriever_agent = KnowledgeRetrievalAgent()
