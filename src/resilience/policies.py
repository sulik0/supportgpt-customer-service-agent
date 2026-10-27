"""为 LLM、RAG 和 Tool 构建一致的恢复策略。"""

from src.config import settings
from src.resilience.models import DegradationLevel, OperationType, ResiliencePolicy


def llm_policy(operation: str) -> ResiliencePolicy:
    """生成 LLM 策略，回复与 QA 失败时强制人工介入。"""
    degradation = (
        DegradationLevel.PARTIAL
        if operation == "analyze_ticket"
        else DegradationLevel.HUMAN_REQUIRED
    )
    return ResiliencePolicy(
        timeout_seconds=settings.RESILIENCE_LLM_TIMEOUT_SECONDS,
        max_retries=settings.RESILIENCE_LLM_MAX_RETRIES,
        circuit_failure_threshold=settings.RESILIENCE_CIRCUIT_FAILURE_THRESHOLD,
        circuit_recovery_seconds=settings.RESILIENCE_CIRCUIT_RECOVERY_SECONDS,
        failure_degradation=degradation,
    )


def tool_planner_policy() -> ResiliencePolicy:
    """ReAct 规划失败时回退确定性 Tool 路由，不单独触发 HITL。"""
    return ResiliencePolicy(
        timeout_seconds=settings.RESILIENCE_LLM_TIMEOUT_SECONDS,
        max_retries=settings.RESILIENCE_LLM_MAX_RETRIES,
        circuit_failure_threshold=settings.RESILIENCE_CIRCUIT_FAILURE_THRESHOLD,
        circuit_recovery_seconds=settings.RESILIENCE_CIRCUIT_RECOVERY_SECONDS,
        failure_degradation=DegradationLevel.PARTIAL,
    )


def decision_policy() -> ResiliencePolicy:
    """DecisionProvider 失败时回退原有 LLM，不单独触发 HITL。"""
    return ResiliencePolicy(
        timeout_seconds=settings.JEV_TIMEOUT_SECONDS,
        max_retries=settings.JEV_MAX_RETRIES,
        circuit_failure_threshold=settings.RESILIENCE_CIRCUIT_FAILURE_THRESHOLD,
        circuit_recovery_seconds=settings.RESILIENCE_CIRCUIT_RECOVERY_SECONDS,
        failure_degradation=DegradationLevel.PARTIAL,
    )


def rag_policy() -> ResiliencePolicy:
    """检索失败允许用另一路候选集继续提供有限回答。"""
    return ResiliencePolicy(
        timeout_seconds=settings.RESILIENCE_RAG_TIMEOUT_SECONDS,
        max_retries=settings.RESILIENCE_RAG_MAX_RETRIES,
        circuit_failure_threshold=settings.RESILIENCE_CIRCUIT_FAILURE_THRESHOLD,
        circuit_recovery_seconds=settings.RESILIENCE_CIRCUIT_RECOVERY_SECONDS,
        failure_degradation=DegradationLevel.PARTIAL,
    )


def tool_policy(
    *, timeout_seconds: float, operation_type: OperationType, high_risk: bool
) -> ResiliencePolicy:
    """仅低风险读 Tool 可重试，高风险或写操作一律单次。"""
    retryable = operation_type is OperationType.READ and not high_risk
    return ResiliencePolicy(
        timeout_seconds=timeout_seconds,
        max_retries=(
            settings.RESILIENCE_TOOL_READ_MAX_RETRIES if retryable else 0
        ),
        operation_type=operation_type,
        idempotent=retryable,
        circuit_failure_threshold=settings.RESILIENCE_CIRCUIT_FAILURE_THRESHOLD,
        circuit_recovery_seconds=settings.RESILIENCE_CIRCUIT_RECOVERY_SECONDS,
        failure_degradation=(
            DegradationLevel.HUMAN_REQUIRED
            if high_risk or operation_type is OperationType.WRITE
            else DegradationLevel.PARTIAL
        ),
    )
