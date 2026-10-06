"""DecisionProvider 接口及 Jev Adapter。"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Mapping

import httpx

from src.config import settings
from src.decision.models import DecisionAnswer, DecisionResult
from src.observability.metrics import (
    DECISION_CALL_DURATION_SECONDS,
    DECISION_CALLS_TOTAL,
    DECISION_CONFIDENCE,
    DECISION_TOKENS_TOTAL,
)
from src.observability.sanitization import sanitize_value
from src.observability.tracing import (
    get_tracer,
    langsmith_span_attributes,
    observed_span,
    serialize_llm_content,
    set_span_attributes,
)
from src.resilience.executor import resilience_executor
from src.resilience.policies import decision_policy


logger = logging.getLogger("supportgpt.decision.provider")
tracer = get_tracer(__name__)


class DecisionProvider(ABC):
    """统一封闭选项决策模型，不授予任何业务执行权限。"""

    name = "unknown"
    enabled = True

    @abstractmethod
    async def evaluate(
        self,
        *,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        operation: str,
        question_set_version: str,
    ) -> DecisionResult:
        """评估结构化 State 并返回类型化结果。"""

    async def aclose(self) -> None:
        """释放 Provider 持有的网络资源。"""


class DisabledDecisionProvider(DecisionProvider):
    """禁用时返回明确的不可用结果，业务层继续原有路径。"""

    name = "disabled"
    enabled = False

    async def evaluate(
        self,
        *,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        operation: str,
        question_set_version: str,
    ) -> DecisionResult:
        return DecisionResult(
            enabled=False,
            available=False,
            provider=self.name,
            operation=operation,
            question_set_version=question_set_version,
            model="disabled",
            error_code="disabled",
        )


class JevDecisionProvider(DecisionProvider):
    """通过 System One HTTP API 执行结构化 Jev 决策。"""

    name = "jev"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=settings.JEV_TIMEOUT_SECONDS,
                headers={
                    "Authorization": f"Bearer {settings.JEV_API_KEY}",
                    "Content-Type": "application/json",
                    "User-Agent": "supportgpt-decision-provider/1.0",
                },
            )
        return self._client

    async def evaluate(
        self,
        *,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        operation: str,
        question_set_version: str,
    ) -> DecisionResult:
        """先脱敏和限长，再由 Resilience 统一处理超时与熔断。"""
        started = time.perf_counter()
        safe_state = _sanitize_decision_state(state)
        encoded = json.dumps(safe_state, ensure_ascii=False, default=str)
        if len(encoded) > settings.JEV_MAX_STATE_CHARS:
            result = self._unavailable(
                operation,
                question_set_version,
                started,
                "state_too_large",
            )
            self._record_metrics(result)
            return result

        api_questions = _validate_questions(questions)

        async def invoke() -> Any:
            response = await self._get_client().post(
                _system_one_url(settings.JEV_BASE_URL),
                json={
                    "state": safe_state,
                    "questions": api_questions,
                    "model": settings.JEV_MODEL,
                },
            )
            response.raise_for_status()
            return response.json()

        with observed_span(
            tracer,
            f"supportgpt.decision.{operation}",
            {
                **langsmith_span_attributes("llm"),
                "observability.component": "decision",
                "gen_ai.operation.name": "decision",
                "gen_ai.system": "typesafe",
                "gen_ai.provider.name": "typesafe",
                "gen_ai.request.model": settings.JEV_MODEL,
                "decision.provider": self.name,
                "decision.operation": operation,
                "decision.question_set_version": question_set_version,
                "decision.question_count": len(questions),
            },
        ) as span:
            if settings.LANGSMITH_CAPTURE_LLM_CONTENT:
                set_span_attributes(
                    span,
                    {
                        "gen_ai.prompt": serialize_llm_content(
                            {"state": safe_state, "questions": questions}
                        )
                    },
                )
            outcome = await resilience_executor.execute(
                component="decision",
                operation=operation,
                call=invoke,
                policy=decision_policy(),
                circuit_key=f"decision:jev:{operation}",
            )
            if not outcome.success or outcome.value is None:
                error_code = (
                    outcome.event.error_type.value
                    if outcome.event.error_type is not None
                    else "unavailable"
                )
                set_span_attributes(
                    span,
                    {
                        "decision.status": "fallback",
                        "decision.error_code": error_code,
                    },
                )
                result = self._unavailable(
                    operation,
                    question_set_version,
                    started,
                    error_code,
                )
                self._record_metrics(result)
                return result

            try:
                result = self._parse_response(
                    outcome.value,
                    operation=operation,
                    question_set_version=question_set_version,
                    latency_seconds=time.perf_counter() - started,
                )
            except Exception as exc:
                logger.warning(
                    "jev response validation failed",
                    extra={
                        "decision_operation": operation,
                        "error_code": exc.__class__.__name__,
                    },
                )
                result = self._unavailable(
                    operation,
                    question_set_version,
                    started,
                    "malformed_response",
                )

            confidence_values = [
                answer.confidence
                for answer in result.answers.values()
                if answer.confidence is not None
            ]
            set_span_attributes(
                span,
                {
                    "decision.status": "success" if result.available else "fallback",
                    "decision.model": result.model,
                    "decision.confidence_min": (
                        min(confidence_values) if confidence_values else None
                    ),
                    "gen_ai.usage.input_tokens": result.input_tokens,
                    "gen_ai.usage.output_tokens": result.output_tokens,
                    "gen_ai.usage.total_tokens": (
                        result.input_tokens + result.output_tokens
                    ),
                    "gen_ai.completion": (
                        serialize_llm_content(
                            {
                                "model": result.model,
                                "answers": {
                                    name: {
                                        "value": answer.value,
                                        "confidence": answer.confidence,
                                    }
                                    for name, answer in result.answers.items()
                                },
                            }
                        )
                        if settings.LANGSMITH_CAPTURE_LLM_CONTENT
                        else None
                    ),
                },
            )
            self._record_metrics(result)
            return result

    def _parse_response(
        self,
        response: Any,
        *,
        operation: str,
        question_set_version: str,
        latency_seconds: float,
    ) -> DecisionResult:
        payload = _as_mapping(response)
        answers: dict[str, DecisionAnswer] = {}
        for key, raw_value in _as_mapping(payload.get("answers", {})).items():
            raw = _as_mapping(raw_value)
            kind = str(raw.get("type", ""))
            value_name = {"choice": "choice", "score": "score", "noul": "noul"}.get(
                kind
            )
            if value_name is None or value_name not in raw:
                continue
            value = raw[value_name]
            probabilities = _as_mapping(raw.get("probabilities", {}))
            confidence = raw.get("confidence")
            if kind == "choice" and not str(value).strip():
                raise ValueError("Jev returned an empty choice.")
            if kind == "score":
                value = _finite_number(value, field=f"{key}.score")
            if kind == "noul":
                value = _probability(value, field=f"{key}.noul")
            normalized_probabilities = {
                str(name): _probability(
                    probability, field=f"{key}.probabilities.{name}"
                )
                for name, probability in probabilities.items()
            }
            if normalized_probabilities and not math.isclose(
                sum(normalized_probabilities.values()), 1.0, abs_tol=0.02
            ):
                raise ValueError(f"Jev probabilities do not sum to one: {key}")
            answers[str(key)] = DecisionAnswer(
                kind=kind,
                value=float(value) if kind in {"score", "noul"} else str(value),
                probabilities=normalized_probabilities,
                confidence=(
                    _probability(confidence, field=f"{key}.confidence")
                    if confidence is not None
                    else None
                ),
            )
        if not answers:
            raise ValueError("Jev response did not contain typed answers.")
        usage = _as_mapping(payload.get("usage", {}))
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Jev usage cannot be negative.")
        return DecisionResult(
            enabled=True,
            available=True,
            provider=self.name,
            operation=operation,
            question_set_version=question_set_version,
            model=str(payload.get("model", settings.JEV_MODEL)),
            answers=answers,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_seconds=round(max(latency_seconds, 0.0), 4),
        )

    def _unavailable(
        self,
        operation: str,
        question_set_version: str,
        started: float,
        error_code: str,
    ) -> DecisionResult:
        return DecisionResult(
            enabled=True,
            available=False,
            provider=self.name,
            operation=operation,
            question_set_version=question_set_version,
            model=settings.JEV_MODEL,
            latency_seconds=round(time.perf_counter() - started, 4),
            error_code=error_code,
        )

    @staticmethod
    def _record_metrics(result: DecisionResult) -> None:
        """观测失败不影响决策回退。"""
        try:
            status = "success" if result.available else "fallback"
            attrs = {
                "provider": result.provider,
                "operation": result.operation,
                "model": result.model,
                "status": status,
            }
            DECISION_CALLS_TOTAL.add(1, attrs)
            DECISION_CALL_DURATION_SECONDS.record(result.latency_seconds, attrs)
            if result.available:
                DECISION_TOKENS_TOTAL.add(
                    result.input_tokens, {**attrs, "type": "input"}
                )
                DECISION_TOKENS_TOTAL.add(
                    result.output_tokens, {**attrs, "type": "output"}
                )
                for name, answer in result.answers.items():
                    if answer.confidence is not None:
                        DECISION_CONFIDENCE.record(
                            answer.confidence,
                            {**attrs, "question": name},
                        )
        except Exception:
            logger.debug("Unable to record DecisionProvider metrics")

    async def aclose(self) -> None:
        if self._client is None or not self._owns_client:
            return
        closer = getattr(self._client, "aclose", None)
        if closer is not None:
            await closer()


def _sanitize_decision_state(value: Any) -> Any:
    """对外部决策输入脱敏，同一业务 ID 保留等值关系。"""
    return sanitize_value(value, preserve_entity_links=True)


def _validate_questions(
    questions: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """在本地验证 Jev 问题集，避免无效请求进入外部服务。"""
    if not questions:
        raise ValueError("Decision questions cannot be empty.")
    prepared: dict[str, dict[str, Any]] = {}
    for name, definition in questions.items():
        question_type = str(definition.get("type", "")).lower()
        if question_type not in {"choice", "score", "noul"}:
            raise ValueError(f"Unsupported decision question type: {question_type}")
        instructions = str(definition.get("instructions", "")).strip()
        if not instructions:
            raise ValueError(f"Decision question has no instructions: {name}")
        item: dict[str, Any] = {
            "type": question_type,
            "instructions": instructions,
        }
        criteria = definition.get("criteria")
        if criteria is not None:
            item["criteria"] = criteria
        if question_type == "choice":
            if not isinstance(criteria, Mapping) or not 2 <= len(criteria) <= 255:
                raise ValueError(f"Choice requires 2-255 criteria: {name}")
        elif question_type == "score":
            if (
                not isinstance(criteria, (list, tuple))
                or not 2 <= len(criteria) <= 10
            ):
                raise ValueError(f"Score requires 2-10 ordered criteria: {name}")
        elif criteria is not None:
            if not isinstance(criteria, Mapping) or set(criteria) != {"true", "false"}:
                raise ValueError(f"Noul criteria must define true and false: {name}")
        prepared[str(name)] = item
    return prepared


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """兼容 HTTP JSON 和测试中的类映射对象。"""
    if isinstance(value, Mapping):
        return value
    dumper = getattr(value, "model_dump", None)
    if dumper is not None:
        dumped = dumper()
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _system_one_url(base_url: str | None) -> str:
    """兼容 API Root、`/v1` 和完整 System One URL。"""
    normalized = (base_url or "https://api.typesafe.ai").rstrip("/")
    if normalized.endswith("/v1/systemone"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/systemone"
    return f"{normalized}/v1/systemone"


def _finite_number(value: Any, *, field: str) -> float:
    """外部模型数值必须可解析且为有限数。"""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Jev returned a non-finite number: {field}")
    return number


def _probability(value: Any, *, field: str) -> float:
    """置信度和 Noul 概率必须位于 0 到 1。"""
    number = _finite_number(value, field=field)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"Jev probability is outside [0, 1]: {field}")
    return number


def get_decision_provider() -> DecisionProvider:
    """根据配置创建唯一 DecisionProvider。"""
    if settings.DECISION_PROVIDER == "jev":
        return JevDecisionProvider()
    return DisabledDecisionProvider()


decision_provider = get_decision_provider()


async def close_decision_provider() -> None:
    """在 FastAPI 退出时关闭决策客户端。"""
    await decision_provider.aclose()
