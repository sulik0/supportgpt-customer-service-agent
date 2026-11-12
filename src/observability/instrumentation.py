"""Optional auto-instrumentation for application and infrastructure libraries."""

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from src.config import settings
from src.observability.sanitization import sanitize_attributes


logger = logging.getLogger("supportgpt.observability.instrumentation")
_instrumented = False


def _safe_url(raw_url: str) -> str:
    try:
        parsed = urlsplit(raw_url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except Exception:
        return "[FILTERED_URL]"


def _server_request_hook(span: Any, scope: dict[str, Any]) -> None:
    if not span or not span.is_recording():
        return
    path = scope.get("path", "")
    span.set_attributes(
        sanitize_attributes(
            {
                "http.route": path,
                "url.full": path,
                "url.query": "[FILTERED]" if scope.get("query_string") else "",
            }
        )
    )


def _httpx_request_hook(span: Any, request: Any) -> None:
    if not span or not span.is_recording():
        return
    span.set_attribute("url.full", _safe_url(str(getattr(request, "url", ""))))


def _redis_request_hook(
    span: Any, instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    """Keep the Redis operation name but remove keys and values from the statement."""
    if not span or not span.is_recording():
        return
    command = str(args[0]).upper() if args else "REDIS"
    span.set_attribute("db.statement", command)
    span.set_attribute("db.redis.args_length", len(args))


def instrument_dependencies(app: Any, sqlalchemy_engine: Any) -> None:
    """Instrument supported libraries once; missing packages never block startup."""
    global _instrumented
    if _instrumented or not settings.OTEL_ENABLED:
        return

    instrumentors = []
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        fastapi_kwargs = dict(
            excluded_urls=settings.OTEL_EXCLUDED_URLS,
            server_request_hook=_server_request_hook,
        )
        try:
            # 去掉 ASGI 的 http send / receive 子 Span。
            FastAPIInstrumentor.instrument_app(
                app, exclude_spans=["send", "receive"], **fastapi_kwargs
            )
        except TypeError:
            # 旧版 instrumentation 不支持 exclude_spans。
            FastAPIInstrumentor.instrument_app(app, **fastapi_kwargs)
        instrumentors.append("FastAPI")
    except Exception as exc:
        logger.warning("FastAPI instrumentation unavailable: %s", exc)

    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(engine=sqlalchemy_engine.sync_engine)
        instrumentors.append("SQLAlchemy")
    except Exception as exc:
        logger.warning("SQLAlchemy instrumentation unavailable: %s", exc)

    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument(request_hook=_redis_request_hook)
        instrumentors.append("Redis")
    except Exception as exc:
        logger.warning("Redis instrumentation unavailable: %s", exc)

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument(request_hook=_httpx_request_hook)
        instrumentors.append("HTTPX")
    except Exception as exc:
        logger.warning("HTTPX instrumentation unavailable: %s", exc)

    _instrumented = True
    logger.info("OpenTelemetry instrumentation enabled for: %s", ", ".join(instrumentors))
