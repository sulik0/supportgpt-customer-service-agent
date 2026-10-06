import time
import logging
from typing import Dict, Any

from src.llm.provider import llm_provider
from src.observability.metrics import AGENT_EXECUTION_DURATION_SECONDS
from src.agents.evidence import build_resolution_evidence, compact_tool_context
from src.agents.scope import is_scope_boundary_request, scope_boundary_response

logger = logging.getLogger("supportgpt.agents.resolver")


class ResolutionAgent:
    """负责结合工单、Tool Context 和知识库引用生成回复草稿。

    该节点只生成建议回复，不直接执行高风险业务操作。
    """

    async def resolve(self, state: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Resolver Node started for ticket_id: {state.get('ticket_id')}")

        if "Security threat" in "".join(state.get("errors", [])):
            return state

        if is_scope_boundary_request(state):
            return {
                **state,
                "suggested_response": scope_boundary_response(state),
                "response_kind": "capability_boundary",
                "resolution_evidence": [],
                "tool_context": {},
                "context_citations": [],
            }

        subject = state.get("subject", "")
        description = state.get("description", "")
        # 记录实际发给生成模型的证据，QA 不再重新挑选或裁剪。
        evidence = build_resolution_evidence(state)
        state = {**state, "resolution_evidence": evidence}
        combined_context = "\n\n".join(evidence) or "No relevant evidence."

        try:
            # Generate the text from LLM provider
            response_text, in_tok, out_tok = await llm_provider.generate_resolution(
                subject=subject, description=description, context=combined_context
            )
            if not response_text.strip():
                response_text = self._empty_response_fallback(description)

            # Update metrics
            state["tokens_input"] = state.get("tokens_input", 0) + in_tok
            state["tokens_output"] = state.get("tokens_output", 0) + out_tok

            duration = time.time() - start_time
            AGENT_EXECUTION_DURATION_SECONDS.record(
                duration, {"agent_name": "resolution_agent"}
            )

            return {**state, "suggested_response": response_text}

        except Exception as e:
            error_type = e.__class__.__name__
            logger.error(
                "resolver generation failed",
                extra={"error_type": error_type},
            )
            return {
                **state,
                "errors": state.get("errors", [])
                + [f"Resolver dependency failure: {error_type}"],
                "suggested_response": self._failure_response(description),
                "degradation_level": "human_required",
                "degradation_reasons": list(
                    dict.fromkeys(
                        [
                            *state.get("degradation_reasons", []),
                            "llm.generate_resolution:failed",
                        ]
                    )
                ),
            }

    @staticmethod
    def _compact_tool_context(tool_context: Dict[str, Any]) -> str:
        """移除 Tool 审计等生成阶段无需字段。"""
        return compact_tool_context(tool_context)

    @staticmethod
    def _empty_response_fallback(description: str) -> str:
        """模型返回空内容时给出安全、可继续的澄清回复。"""
        if any("\u4e00" <= char <= "\u9fff" for char in description):
            return "请补充您遇到的具体问题或操作目标，我会继续为您查询。"
        return (
            "Please describe the specific support issue or task you need help with, "
            "and I will continue from there."
        )

    @staticmethod
    def _failure_response(description: str) -> str:
        """依赖恢复失败时使用当前输入语言给出保守回复。"""
        if any("\u4e00" <= char <= "\u9fff" for char in description):
            return "抱歉，当前暂时无法生成可靠回复，已转交人工客服复核。"
        return (
            "I’m sorry, but I cannot generate a reliable response right now. "
            "This request has been routed to a support specialist for review."
        )


resolution_agent = ResolutionAgent()
