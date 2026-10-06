import functools
import inspect
import json
import logging
import socket
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Callable, Dict, Iterator, Optional
from urllib.parse import urlparse

from src.config import settings
from src.observability.sanitization import (
    redact_text,
    sanitize_attributes,
    sanitize_value,
)

logger = logging.getLogger("supportgpt.observability.tracing")
_initialized = False
_request_id_context: ContextVar[Optional[str]] = ContextVar(
    "supportgpt_request_id", default=None
)
_langsmith_agent_context: ContextVar[bool] = ContextVar(
    "supportgpt_langsmith_agent_context", default=False
)
_agent_trace_id_context: ContextVar[Optional[Dict[str, Optional[str]]]] = ContextVar(
    "supportgpt_agent_trace_id", default=None
)
_trace_performance_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "supportgpt_trace_performance", default=None
)


def get_tracer(name: str = "supportgpt"):
    from opentelemetry import trace

    return trace.get_tracer(name)


def _should_enable_otlp_exporter(endpoint: Optional[str], signal: str) -> bool:
    """本地启动时预检 Collector，避免后台 exporter 重试刷屏。"""
    if not endpoint:
        return False
    is_local = settings.APP_ENV.lower() in {"development", "dev", "local", "test"}
    if not settings.OTEL_EXPORTER_PREFLIGHT_ENABLED or not is_local:
        return True

    parsed = urlparse(endpoint)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        logger.warning("Skipping OTLP %s exporter: invalid endpoint.", signal)
        return False
    try:
        with socket.create_connection(
            (host, port), timeout=settings.OTEL_EXPORTER_PREFLIGHT_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        logger.warning(
            "Skipping OTLP %s exporter because Collector is unreachable at %s:%s; "
            "start Collector and restart backend to enable export.",
            signal,
            host,
            port,
        )
        return False


def set_span_attributes(span: Any, attributes: Dict[str, Any]) -> None:
    """Set sanitized attributes without allowing telemetry to break business code."""
    try:
        span.set_attributes(sanitize_attributes(attributes))
    except Exception as exc:
        logger.debug("Unable to attach span attributes: %s", exc)


def bind_request_id(request_id: str) -> Token:
    return _request_id_context.set(
        sanitize_value({"request_id": request_id})["request_id"][:128]
    )


def reset_request_id(token: Token) -> None:
    try:
        _request_id_context.reset(token)
    except Exception:
        return


def get_request_id() -> Optional[str]:
    return _request_id_context.get()


def get_current_trace_id() -> Optional[str]:
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if context and context.is_valid:
            return format(context.trace_id, "032x")
    except Exception:
        return None
    return None


@contextmanager
def capture_trace_performance() -> Iterator[Dict[str, Any]]:
    """采集本次 OTel Span 与 LLM 调用摘要，供离线评测生成性能报告。"""
    capture: Dict[str, Any] = {"spans": [], "llm_calls": []}
    token = _trace_performance_context.set(capture)
    try:
        yield capture
    finally:
        _trace_performance_context.reset(token)


def _capture_span(
    name: str,
    *,
    duration_seconds: float,
    status: str,
    attributes: Optional[Dict[str, Any]],
) -> None:
    """将 Span 性能摘要写入当前评测上下文。"""
    capture = _trace_performance_context.get()
    if capture is None:
        return
    capture["spans"].append(
        {
            "name": name,
            "duration_seconds": round(max(duration_seconds, 0.0), 6),
            "status": status,
            "attributes": sanitize_attributes(attributes or {}),
        }
    )


def _capture_llm_call(
    operation: str,
    *,
    duration_seconds: float,
    status: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """记录与 OTel LLM Span 一致的模型、Token 和耗时。"""
    capture = _trace_performance_context.get()
    if capture is None:
        return
    capture["llm_calls"].append(
        {
            "operation": operation,
            "model": _llm_metric_model(operation),
            "duration_seconds": round(max(duration_seconds, 0.0), 6),
            "status": status,
            "input_tokens": max(int(input_tokens or 0), 0),
            "output_tokens": max(int(output_tokens or 0), 0),
            "total_tokens": max(int(input_tokens or 0), 0)
            + max(int(output_tokens or 0), 0),
        }
    )


def bind_agent_trace_id() -> Token:
    """为当前请求初始化 Agent Trace ID 上下文。"""
    # Starlette 会在子 Task 中执行端点，共享可变 Holder 才能回传给中间件。
    return _agent_trace_id_context.set({"trace_id": None})


def set_agent_trace_id(trace_id: Optional[str]) -> None:
    holder = _agent_trace_id_context.get()
    if holder is None:
        _agent_trace_id_context.set({"trace_id": trace_id})
    else:
        holder["trace_id"] = trace_id


def get_agent_trace_id() -> Optional[str]:
    holder = _agent_trace_id_context.get()
    return holder.get("trace_id") if holder else None


def reset_agent_trace_id(token: Token) -> None:
    try:
        _agent_trace_id_context.reset(token)
    except Exception:
        return


@contextmanager
def langsmith_agent_trace_context() -> Iterator[None]:
    """仅在主 Agent Workflow 执行期间允许 Span 进入 LangSmith。"""
    token = _langsmith_agent_context.set(True)
    try:
        yield
    finally:
        _langsmith_agent_context.reset(token)


def langsmith_span_attributes(
    kind: str, *, trace_name: Optional[str] = None, force: bool = False
) -> Dict[str, Any]:
    """为主 Agent 链路补充 LangSmith 可识别的 Run 类型。"""
    if not force and not _langsmith_agent_context.get():
        return {}
    attributes: Dict[str, Any] = {
        "langsmith.export": True,
        "langsmith.span.kind": kind,
    }
    if trace_name:
        attributes["langsmith.trace.name"] = trace_name
    return attributes


def _llm_span_attributes() -> Dict[str, Any]:
    """记录模型识别信息，不上报 Prompt 和回复正文。"""
    from src.promptops.runtime import active_bundle

    bundle = active_bundle()
    provider = settings.LLM_PROVIDER.lower()
    if provider == "azure":
        model = settings.AZURE_OPENAI_DEPLOYMENT or "azure"
    elif provider == "openai":
        model = settings.LLM_MODEL_NAME or "openai-compatible"
    else:
        model = "mock"
    return {
        **langsmith_span_attributes("llm"),
        "gen_ai.operation.name": "chat",
        "gen_ai.system": provider,
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
        "prompt.bundle_id": bundle.bundle_id,
        "prompt.version": bundle.version,
    }


def _llm_metric_model(operation: str) -> str:
    """返回当前 LLM 节点实际使用的模型名。"""
    provider = settings.LLM_PROVIDER.lower()
    if provider == "openai":
        if operation.endswith("analyze_ticket"):
            node_model = (
                settings.LLM_ANALYZER_MODEL_NAME or settings.LLM_FAST_MODEL_NAME
            )
            if node_model:
                return node_model
        if operation.endswith("evaluate_qa"):
            node_model = settings.LLM_QA_MODEL_NAME or settings.LLM_FAST_MODEL_NAME
            if node_model:
                return node_model
        return settings.LLM_MODEL_NAME or "openai-compatible"
    if provider == "azure":
        if operation.endswith("analyze_ticket") and settings.LLM_ANALYZER_MODEL_NAME:
            return settings.LLM_ANALYZER_MODEL_NAME
        if operation.endswith("evaluate_qa") and settings.LLM_QA_MODEL_NAME:
            return settings.LLM_QA_MODEL_NAME
        return settings.AZURE_OPENAI_DEPLOYMENT or "azure"
    return "mock"


def _record_llm_metrics(
    operation: str,
    *,
    duration_seconds: float,
    status: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """按 LLM 节点记录 Token 和调用耗时。"""
    try:
        from src.observability.metrics import LLM_LATENCY_SECONDS, LLM_TOKENS_TOTAL

        attributes = {
            "operation": operation,
            "model": _llm_metric_model(operation),
            "status": status,
        }
        LLM_LATENCY_SECONDS.record(duration_seconds, attributes)
        if status == "success":
            LLM_TOKENS_TOTAL.add(input_tokens, {**attributes, "type": "input"})
            LLM_TOKENS_TOTAL.add(output_tokens, {**attributes, "type": "output"})
    except Exception as exc:
        logger.debug("Unable to record LLM metrics: %s", exc)


def serialize_llm_content(value: Any) -> str:
    """将 LLM 内容脱敏并限长后写入 Trace。"""
    safe_value = sanitize_value(value, preserve_entity_links=True)
    if isinstance(safe_value, str):
        content = safe_value
    else:
        content = json.dumps(safe_value, ensure_ascii=False, default=str)
    max_chars = settings.LANGSMITH_LLM_CONTENT_MAX_CHARS
    if len(content) > max_chars:
        return f"{content[:max_chars]}\n[TRUNCATED]"
    return content


def record_current_llm_io(
    *, input_value: Any = None, output_value: Any = None, model: Optional[str] = None
) -> None:
    """向当前 LLM Span 记录脱敏后的实际输入输出。"""
    if not _langsmith_agent_context.get():
        return
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        attributes: Dict[str, Any] = {}
        if settings.LANGSMITH_CAPTURE_LLM_CONTENT and input_value is not None:
            attributes["gen_ai.prompt"] = serialize_llm_content(input_value)
        if settings.LANGSMITH_CAPTURE_LLM_CONTENT and output_value is not None:
            attributes["gen_ai.completion"] = serialize_llm_content(output_value)
        if model:
            attributes["gen_ai.request.model"] = model
        set_span_attributes(span, attributes)
    except Exception as exc:
        logger.debug("Unable to record LLM input/output: %s", exc)


def _bound_llm_inputs(
    function: Callable, args: tuple[Any, ...], kwargs: Dict[str, Any]
) -> Dict[str, Any]:
    """从 Provider 方法参数中提取可展示的 LLM 输入。"""
    try:
        bound = inspect.signature(function).bind_partial(*args, **kwargs)
        bound.arguments.pop("self", None)
        return dict(bound.arguments)
    except Exception:
        return {
            "argument_count": max(len(args) - 1, 0),
            "keyword_keys": sorted(kwargs),
        }


def mark_span_error(span: Any, error: BaseException) -> None:
    """Record a sanitized exception while preserving the original exception flow."""
    try:
        from opentelemetry.trace import Status, StatusCode

        span.add_event(
            "exception",
            {
                "exception.type": error.__class__.__name__,
                "exception.escaped": True,
            },
        )
        span.set_status(Status(StatusCode.ERROR))
    except Exception:
        return


@contextmanager
def observed_span(
    tracer: Any,
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    *,
    root: bool = False,
) -> Iterator[Any]:
    """创建统一 OTel Span，并以脱敏属性记录耗时和异常。"""
    started = time.perf_counter()
    span_context = None
    if root:
        from opentelemetry.context import Context

        span_context = Context()
    with tracer.start_as_current_span(
        name,
        context=span_context,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        set_span_attributes(
            span,
            {
                **(attributes or {}),
                "request.id": (attributes or {}).get("request.id") or get_request_id(),
            },
        )
        try:
            yield span
        except BaseException as exc:
            duration = time.perf_counter() - started
            set_span_attributes(span, {"operation.status": "error"})
            mark_span_error(span, exc)
            _capture_span(
                name,
                duration_seconds=duration,
                status="error",
                attributes=attributes,
            )
            raise
        else:
            duration = time.perf_counter() - started
            from opentelemetry.trace import StatusCode

            # Tool 等节点可能把失败转成业务结果，保留已经设置的错误状态。
            outcome = (
                "error"
                if getattr(getattr(span, "status", None), "status_code", None)
                == StatusCode.ERROR
                else "success"
            )
            set_span_attributes(
                span,
                {
                    "operation.status": outcome,
                    "operation.duration_seconds": duration,
                },
            )
            _capture_span(
                name,
                duration_seconds=duration,
                status=outcome,
                attributes=attributes,
            )


def trace_operation(*, name: str, component: str) -> Callable:
    """使用纯 OpenTelemetry 记录操作耗时和脱敏后的 LLM 内容。"""

    def decorator(function: Callable) -> Callable:
        tracer = get_tracer(function.__module__)

        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                started = time.perf_counter()
                try:
                    with observed_span(
                        tracer,
                        name,
                        {
                            "observability.component": component,
                            "code.function.name": function.__qualname__,
                            "llm.operation": name if component == "llm" else None,
                            **(_llm_span_attributes() if component == "llm" else {}),
                        },
                    ) as span:
                        if (
                            component == "llm"
                            and settings.LANGSMITH_CAPTURE_LLM_CONTENT
                        ):
                            record_current_llm_io(
                                input_value=_bound_llm_inputs(function, args, kwargs)
                            )
                        result = await function(*args, **kwargs)
                        if (
                            component == "llm"
                            and isinstance(result, tuple)
                            and len(result) >= 3
                        ):
                            input_tokens = int(result[-2] or 0)
                            output_tokens = int(result[-1] or 0)
                            set_span_attributes(
                                span,
                                {
                                    "gen_ai.usage.input_tokens": input_tokens,
                                    "gen_ai.usage.output_tokens": output_tokens,
                                    "gen_ai.usage.total_tokens": (
                                        input_tokens + output_tokens
                                    ),
                                },
                            )
                            record_current_llm_io(output_value=result[0])
                            _record_llm_metrics(
                                name,
                                duration_seconds=time.perf_counter() - started,
                                status="success",
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                            )
                            _capture_llm_call(
                                name,
                                duration_seconds=time.perf_counter() - started,
                                status="success",
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                            )
                        return result
                except BaseException:
                    if component == "llm":
                        _record_llm_metrics(
                            name,
                            duration_seconds=time.perf_counter() - started,
                            status="error",
                        )
                        _capture_llm_call(
                            name,
                            duration_seconds=time.perf_counter() - started,
                            status="error",
                        )
                    raise

            return async_wrapper

        @functools.wraps(function)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with observed_span(
                tracer,
                name,
                {
                    "observability.component": component,
                    "code.function.name": function.__qualname__,
                    **(_llm_span_attributes() if component == "llm" else {}),
                },
            ):
                if component == "llm" and settings.LANGSMITH_CAPTURE_LLM_CONTENT:
                    record_current_llm_io(
                        input_value=_bound_llm_inputs(function, args, kwargs)
                    )
                result = function(*args, **kwargs)
                if component == "llm":
                    output_value = result[0] if isinstance(result, tuple) else result
                    record_current_llm_io(output_value=output_value)
                return result

        return sync_wrapper

    return decorator


def init_tracing() -> None:
    """初始化 OpenTelemetry Trace、Metrics 和统一 OTLP exporter。"""
    global _initialized
    if _initialized or not settings.OTEL_ENABLED:
        return

    # OpenTelemetry SDK initialization is deliberately fail-open.
    try:
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create(
            {
                "service.name": settings.OTEL_SERVICE_NAME,
                "service.version": "1.0.0",
                "deployment.environment": settings.APP_ENV,
            }
        )

    except Exception as e:
        logger.warning("Could not create OpenTelemetry resource: %s", e)
        _initialized = True
        return

    traces_otlp_enabled = _should_enable_otlp_exporter(
        settings.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT, "traces"
    )
    metrics_otlp_enabled = _should_enable_otlp_exporter(
        settings.OTEL_EXPORTER_OTLP_METRICS_ENDPOINT, "metrics"
    )

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        current_tracer_provider = trace.get_tracer_provider()
        if isinstance(current_tracer_provider, TracerProvider):
            tracer_provider = current_tracer_provider
        else:
            tracer_provider = TracerProvider(
                resource=resource,
                sampler=ParentBased(
                    TraceIdRatioBased(settings.OTEL_TRACE_SAMPLE_RATIO)
                ),
            )
            trace.set_tracer_provider(tracer_provider)

        if traces_otlp_enabled:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=settings.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
                        timeout=settings.OTEL_EXPORTER_OTLP_TIMEOUT_SECONDS,
                    )
                )
            )
        if settings.OTEL_CONSOLE_EXPORTER:
            tracer_provider.add_span_processor(
                BatchSpanProcessor(ConsoleSpanExporter())
            )
    except Exception as exc:
        logger.warning("Could not initialize OpenTelemetry traces: %s", exc)

    try:
        from opentelemetry import metrics
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.metrics.view import (
            ExplicitBucketHistogramAggregation,
            View,
        )

        current_meter_provider = metrics.get_meter_provider()
        if not isinstance(current_meter_provider, MeterProvider):
            metric_readers = []
            if metrics_otlp_enabled:
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                    OTLPMetricExporter,
                )

                metric_readers.append(
                    PeriodicExportingMetricReader(
                        OTLPMetricExporter(
                            endpoint=settings.OTEL_EXPORTER_OTLP_METRICS_ENDPOINT,
                            timeout=settings.OTEL_EXPORTER_OTLP_TIMEOUT_SECONDS,
                        ),
                        export_interval_millis=(
                            settings.OTEL_METRIC_EXPORT_INTERVAL_MILLISECONDS
                        ),
                    )
                )

            metrics.set_meter_provider(
                MeterProvider(
                    resource=resource,
                    metric_readers=metric_readers,
                    views=[
                        View(
                            instrument_name="qa_score_ratio",
                            aggregation=ExplicitBucketHistogramAggregation(
                                boundaries=(
                                    0.1,
                                    0.2,
                                    0.3,
                                    0.4,
                                    0.5,
                                    0.6,
                                    0.7,
                                    0.8,
                                    0.9,
                                    1.0,
                                )
                            ),
                        )
                    ],
                )
            )
    except Exception as exc:
        logger.warning("Could not initialize OpenTelemetry metrics: %s", exc)

    _initialized = True
    logger.info(
        "OpenTelemetry initialized: service=%s, traces_otlp=%s, metrics_otlp=%s",
        settings.OTEL_SERVICE_NAME,
        traces_otlp_enabled,
        metrics_otlp_enabled,
    )
