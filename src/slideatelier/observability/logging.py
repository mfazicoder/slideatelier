"""Structured logging via stdlib ``logging``.

Production: every log line is a single JSON object with timestamp, level,
logger, message, and the request_id (from contextvars) so we can grep by
request across services. Dev: plain readable text with the request_id appended
when we have one, no JSON noise.

The request_id contextvar is populated by ``RequestIdMiddleware`` and read by
the formatter automatically — your application code can just call
``logger = logging.getLogger(__name__)`` and not worry about it.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

# Contextvar populated per-request. Default is "-" so logs emitted outside
# request scope (startup, background jobs without an explicit context) are
# still well-formed.
REQUEST_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "slideatelier_request_id", default="-"
)


def current_request_id() -> str:
    """Return the active request_id, or '-' if there is none."""
    return REQUEST_ID_CTX.get()


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    """Single-line JSON per log record. Never raises — falls back to repr()
    for unserialisable extras so a bad log call can't take down the app."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", current_request_id()),
        }
        # Surface any structured `extra={...}` fields the caller passed in.
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_KEYS or key.startswith("_"):
                continue
            if key in payload:
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _PlainFormatter(logging.Formatter):
    """Human-readable dev output. Appends the request_id when present."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        rid = getattr(record, "request_id", current_request_id())
        if rid and rid != "-":
            base = f"{base} [rid={rid}]"
        return base


# Standard LogRecord attributes we never want to leak into JSON output as
# duplicate keys.
_RESERVED_LOG_KEYS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "request_id", "asctime", "message",
    "taskName",
}


class _RequestIdFilter(logging.Filter):
    """Attach the current request_id to every record so formatters always have it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


# ---------------------------------------------------------------------------
# Configuration entry point
# ---------------------------------------------------------------------------


def configure_logging(
    *,
    env: str | None = None,
    level: str | None = None,
    json_logs: bool | None = None,
) -> None:
    """Idempotent: replaces the root logger's handlers with one that uses our
    formatter. Safe to call multiple times (re-init in tests, etc.).

    - ``env``: defaults to ``$SLIDEATELIER_ENV`` or "development"
    - ``level``: defaults to ``$LOG_LEVEL`` or INFO
    - ``json_logs``: defaults to True iff env != "development" (override with
      ``$LOG_FORMAT=json|plain``)
    """
    env = env or os.getenv("SLIDEATELIER_ENV", "development")
    level_name = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    level_val = getattr(logging, level_name, logging.INFO)

    if json_logs is None:
        fmt_env = os.getenv("LOG_FORMAT", "").lower()
        if fmt_env in ("json", "plain"):
            json_logs = fmt_env == "json"
        else:
            json_logs = env != "development"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter() if json_logs else _PlainFormatter())
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    # Drop existing handlers so re-configuration is clean (uvicorn installs
    # its own; we want a single canonical formatter).
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level_val)

    # Quiet down libraries that are otherwise chatty in production.
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel(max(level_val, logging.WARNING))


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generate (or propagate) a request_id, stash it in contextvars, expose
    it as ``X-Request-Id`` on the response, and emit a single
    request-summary log line when the request ends.

    Honours an inbound ``X-Request-Id`` header so upstream tracers can correlate.
    """

    def __init__(self, app: ASGIApp, *, logger_name: str = "slideatelier.request") -> None:
        super().__init__(app)
        self._logger = logging.getLogger(logger_name)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        inbound = request.headers.get("x-request-id", "").strip()
        # Cap to avoid log injection via absurdly long header values.
        request_id = (inbound[:64] if inbound else uuid.uuid4().hex)
        token = REQUEST_ID_CTX.set(request_id)
        request.state.request_id = request_id
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-Id"] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            user = getattr(request.state, "user", None)
            user_id: str | None = None
            if user is not None:
                user_id = getattr(user, "id", None) or getattr(user, "email", None)
                if user_id is None and isinstance(user, dict):
                    user_id = user.get("id") or user.get("email")
            self._logger.info(
                "request",
                extra={
                    "event": "request_summary",
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "duration_ms": duration_ms,
                    "user_id": user_id,
                    "request_id": request_id,
                },
            )
            REQUEST_ID_CTX.reset(token)
