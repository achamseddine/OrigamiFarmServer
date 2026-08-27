from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")

CORRELATION_ID_HEADER = "X-Correlation-Id"


def get_correlation_id() -> str:
    return _correlation_id.get()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", level=level)

    def add_correlation_id(_logger, _method_name, event_dict):
        cid = get_correlation_id()
        if cid:
            event_dict["correlation_id"] = cid
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_correlation_id,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Assigns/propagates a request correlation ID for logs, audit events,
    and client-side troubleshooting. Never log auth tokens or payload bodies
    here — this middleware only touches headers and IDs.
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(CORRELATION_ID_HEADER)
        correlation_id = incoming or str(uuid.uuid4())
        token = _correlation_id.set(correlation_id)
        try:
            response = await call_next(request)
        finally:
            _correlation_id.reset(token)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
        response.headers.setdefault("Cache-Control", "no-store")
        return response
