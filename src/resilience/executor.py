"""实现有界 Retry、Circuit Breaker 和 Fallback 的统一执行器。"""

import asyncio
import inspect
import logging
import time
from typing import Awaitable, Callable, Generic, Optional, TypeVar

from src.config import settings
from src.observability.sanitization import redact_text
from src.observability.metrics import (
    DEPENDENCY_CALL_DURATION_SECONDS,
    DEPENDENCY_CALLS_TOTAL,
    DEPENDENCY_FALLBACKS_TOTAL,
    DEPENDENCY_RETRIES_TOTAL,
)
from src.observability.tracing import (
    get_tracer,
    mark_span_error,
    observed_span,
    set_span_attributes,
)
from src.resilience.circuit_breaker import (
    CircuitOpenError,
    circuit_breakers,
)
from src.resilience.context import record_resilience_event
from src.resilience.error_classifier import classify_dependency_error, is_retryable_error
from src.resilience.models import (
    DegradationLevel,
    DependencyErrorType,
    DependencyEvent,
    ResiliencePolicy,
    ResilienceResult,
)


T = TypeVar("T")
logger = logging.getLogger("supportgpt.resilience")
tracer = get_tracer(__name__)


class ResilienceExecutor(Generic[T]):
    """统一包装外部依赖，并保证观测失败不影响业务。"""

    async def execute(
        self,
        *,
        component: str,
        operation: str,
        call: Callable[[], Awaitable[T]],
        policy: ResiliencePolicy,
        fallback: Optional[Callable[[], Awaitable[T] | T]] = None,
        fallback_name: Optional[str] = None,
        circuit_key: Optional[str] = None,
    ) -> ResilienceResult[T]:
        key = circuit_key or f"{component}:{operation}"
        started = time.perf_counter()
        attempts = 0
        last_error: BaseException | None = None
        last_type: DependencyErrorType | None = None
        circuit_state = "closed"
        failed_fallback_name: str | None = None

        if not settings.RESILIENCE_ENABLED:
            try:
                value = await call()
                event = self._event(
                    component,
                    operation,
                    "success",
                    1,
                    started,
                    DegradationLevel.NONE,
                    None,
                )
                return ResilienceResult(True, value, event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                event = self._event(
                    component,
                    operation,
                    "failed",
                    1,
                    started,
                    policy.failure_degradation,
                    classify_dependency_error(exc),
                )
                return ResilienceResult(False, None, event, exc)

        with observed_span(
            tracer,
            "supportgpt.resilience.call",
            {
                "resilience.component": component,
                "resilience.operation": operation,
                "resilience.circuit_key": key,
            },
        ) as span:
            try:
                circuit_state = await circuit_breakers.before_call(
                    key, policy.circuit_recovery_seconds
                )
            except CircuitOpenError as exc:
                if fallback is not None:
                    fallback_result = await self._run_fallback(
                        fallback, policy.timeout_seconds
                    )
                    if fallback_result[0]:
                        event = self._event(
                            component,
                            operation,
                            "fallback",
                            attempts,
                            started,
                            DegradationLevel.PARTIAL,
                            DependencyErrorType.CIRCUIT_OPEN,
                            fallback_used=fallback_name or "fallback",
                            circuit_state="open",
                        )
                        self._record(event)
                        return ResilienceResult(True, fallback_result[1], event)
                    fallback_error = fallback_result[2] or RuntimeError(
                        "fallback failed"
                    )
                    event = self._event(
                        component,
                        operation,
                        "failed",
                        attempts,
                        started,
                        policy.failure_degradation,
                        classify_dependency_error(fallback_error),
                        fallback_used=f"{fallback_name or 'fallback'}:failed",
                        circuit_state="open",
                    )
                    set_span_attributes(
                        span,
                        {
                            "resilience.status": "failed",
                            "resilience.error_type": event.error_type.value,
                            "resilience.fallback": event.fallback_used,
                        },
                    )
                    mark_span_error(span, fallback_error)
                    self._record(event)
                    return ResilienceResult(False, None, event, fallback_error)
                event = self._event(
                    component,
                    operation,
                    "circuit_open",
                    attempts,
                    started,
                    policy.failure_degradation,
                    DependencyErrorType.CIRCUIT_OPEN,
                    circuit_state="open",
                )
                set_span_attributes(
                    span,
                    {
                        "resilience.status": "circuit_open",
                        "resilience.error_type": DependencyErrorType.CIRCUIT_OPEN.value,
                    },
                )
                mark_span_error(span, exc)
                self._record(event)
                self._log_failure(event, exc)
                return ResilienceResult(False, None, event, exc)

            total_attempts = policy.max_retries + 1
            for attempt in range(total_attempts):
                attempts = attempt + 1
                try:
                    value = await asyncio.wait_for(
                        call(), timeout=policy.timeout_seconds
                    )
                    previous_state = await circuit_breakers.record_success(key)
                    status = "recovered" if attempts > 1 else "success"
                    event = self._event(
                        component,
                        operation,
                        status,
                        attempts,
                        started,
                        DegradationLevel.NONE,
                        None,
                        circuit_state="closed",
                    )
                    set_span_attributes(
                        span,
                        {
                            "resilience.status": status,
                            "resilience.attempts": attempts,
                            "resilience.previous_circuit_state": previous_state,
                        },
                    )
                    self._record(event)
                    return ResilienceResult(True, value, event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                    last_type = classify_dependency_error(exc)
                    retryable = is_retryable_error(last_type)
                    if retryable:
                        circuit_state = await circuit_breakers.record_failure(
                            key, policy.circuit_failure_threshold
                        )
                    else:
                        await circuit_breakers.record_success(key)
                        circuit_state = "closed"
                    if not retryable or attempt >= total_attempts - 1:
                        break
                    self._record_retry(component, operation, last_type)
                    delay = settings.RESILIENCE_RETRY_BASE_DELAY_SECONDS * (2**attempt)
                    if delay > 0:
                        await asyncio.sleep(delay)

            if fallback is not None:
                fallback_result = await self._run_fallback(
                    fallback, policy.timeout_seconds
                )
                if fallback_result[0]:
                    event = self._event(
                        component,
                        operation,
                        "fallback",
                        attempts,
                        started,
                        DegradationLevel.PARTIAL,
                        last_type,
                        fallback_used=fallback_name or "fallback",
                    )
                    set_span_attributes(
                        span,
                        {
                            "resilience.status": "fallback",
                            "resilience.attempts": attempts,
                            "resilience.fallback": event.fallback_used,
                        },
                    )
                    self._record(event)
                    return ResilienceResult(True, fallback_result[1], event)
                last_error = fallback_result[2]
                failed_fallback_name = f"{fallback_name or 'fallback'}:failed"
                last_type = classify_dependency_error(
                    last_error or RuntimeError("fallback failed")
                )

            event = self._event(
                component,
                operation,
                "failed",
                attempts,
                started,
                policy.failure_degradation,
                last_type or DependencyErrorType.UNKNOWN,
                fallback_used=failed_fallback_name,
                circuit_state=circuit_state,
            )
            set_span_attributes(
                span,
                {
                    "resilience.status": "failed",
                    "resilience.attempts": attempts,
                    "resilience.error_type": event.error_type.value,
                },
            )
            mark_span_error(
                span, last_error or RuntimeError("dependency operation failed")
            )
            self._record(event)
            self._log_failure(event, last_error)
            return ResilienceResult(False, None, event, last_error)

    @staticmethod
    def _log_failure(event: DependencyEvent, cause: Optional[BaseException]) -> None:
        """失败时记下原始原因，否则只能看到“重试用完”而查不出是超时、限流还是熔断。"""
        cause_text = redact_text(str(cause))[:200] if cause else "-"
        logger.warning(
            "dependency failed: %s.%s error_type=%s attempts=%s circuit=%s "
            "latency_ms=%.0f cause=%s: %s",
            event.component,
            event.operation,
            event.error_type.value if event.error_type else "unknown",
            event.attempts,
            event.circuit_state,
            event.latency_ms,
            cause.__class__.__name__ if cause else "-",
            cause_text,
        )

    @staticmethod
    async def _run_fallback(
        fallback: Callable[[], Awaitable[T] | T], timeout: float
    ) -> tuple[bool, Optional[T], Optional[BaseException]]:
        """Fallback 单次执行，不在内部嵌套无限 Retry。"""
        try:
            value = fallback()
            if inspect.isawaitable(value):
                value = await asyncio.wait_for(value, timeout=timeout)
            return True, value, None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return False, None, exc

    @staticmethod
    def _event(
        component: str,
        operation: str,
        status: str,
        attempts: int,
        started: float,
        degradation: DegradationLevel,
        error_type: DependencyErrorType | None,
        *,
        fallback_used: str | None = None,
        circuit_state: str = "closed",
    ) -> DependencyEvent:
        return DependencyEvent(
            component=component,
            operation=operation,
            status=status,
            attempts=attempts,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            degradation_level=degradation,
            error_type=error_type,
            fallback_used=fallback_used,
            circuit_state=circuit_state,
        )

    @staticmethod
    def _record(event: DependencyEvent) -> None:
        record_resilience_event(event)
        try:
            attributes = {
                "component": event.component,
                "operation": event.operation,
                "status": event.status,
                "error_type": event.error_type.value if event.error_type else "none",
            }
            DEPENDENCY_CALLS_TOTAL.add(1, attributes)
            DEPENDENCY_CALL_DURATION_SECONDS.record(
                event.latency_ms / 1000.0,
                {"component": event.component, "operation": event.operation},
            )
            if event.fallback_used:
                DEPENDENCY_FALLBACKS_TOTAL.add(
                    1,
                    {
                        "component": event.component,
                        "operation": event.operation,
                        "fallback": event.fallback_used,
                    },
                )
        except Exception:
            logger.debug("Unable to record resilience metrics")

    @staticmethod
    def _record_retry(
        component: str, operation: str, error_type: DependencyErrorType
    ) -> None:
        try:
            DEPENDENCY_RETRIES_TOTAL.add(
                1,
                {
                    "component": component,
                    "operation": operation,
                    "error_type": error_type.value,
                },
            )
        except Exception:
            logger.debug("Unable to record retry metric")


resilience_executor: ResilienceExecutor[object] = ResilienceExecutor()
