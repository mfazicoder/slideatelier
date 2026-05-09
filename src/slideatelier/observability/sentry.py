"""Sentry integration, env-gated.

If ``SENTRY_DSN`` is not set, ``init_sentry()`` is a no-op (no import cost
beyond the conditional). When set, we enable the FastAPI + httpx integrations
and scrub known-sensitive fields from breadcrumbs and events before they leave
the process.

Why scrub at the SDK boundary: Anthropic API keys travel as headers on
outbound httpx calls, and password-like form fields can land in breadcrumbs
from middleware. We strip them defensively even though we never knowingly
log them ourselves.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# Field names whose VALUES we always replace with "[redacted]" before sending.
# Match conservatively (case-insensitive substring) — false positives just
# redact a debug field that wouldn't have helped anyway.
_REDACT_KEY_PATTERNS = re.compile(
    r"(api[_-]?key|authorization|password|secret|token|cookie|session|x-api-key)",
    re.IGNORECASE,
)


def _scrub(value: Any) -> Any:
    """Walk a JSON-ish structure and redact sensitive keys."""
    if isinstance(value, dict):
        return {
            k: ("[redacted]" if _REDACT_KEY_PATTERNS.search(str(k)) else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:  # noqa: ARG001
    """Sentry ``before_send`` hook — applied to every outgoing event."""
    try:
        # request.headers / request.data
        if "request" in event:
            event["request"] = _scrub(event["request"])
        # extra/contexts
        for k in ("extra", "contexts", "tags"):
            if k in event:
                event[k] = _scrub(event[k])
        # breadcrumbs each carry a "data" dict
        for crumb in event.get("breadcrumbs", {}).get("values", []) or []:
            if "data" in crumb:
                crumb["data"] = _scrub(crumb["data"])
            if "message" in crumb and isinstance(crumb["message"], str):
                # Scrub Bearer tokens in free-form messages.
                crumb["message"] = re.sub(
                    r"(Bearer\s+)[A-Za-z0-9._\-]+",
                    r"\1[redacted]",
                    crumb["message"],
                )
    except Exception:  # noqa: BLE001 — defensive; never break sending
        pass
    return event


def _before_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:  # noqa: ARG001
    if "data" in crumb:
        crumb["data"] = _scrub(crumb["data"])
    return crumb


def init_sentry(*, dsn: str | None = None, environment: str | None = None) -> bool:
    """Initialise Sentry if DSN is available. Returns True iff Sentry was
    enabled. Safe to call multiple times — the SDK ignores re-init."""
    dsn = dsn or os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("Sentry DSN not set — telemetry disabled")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.httpx import HttpxIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed; skipping init"
        )
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=environment or os.getenv("SLIDEATELIER_ENV", "development"),
        traces_sample_rate=0.1,
        send_default_pii=False,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            HttpxIntegration(),
        ],
        before_send=_before_send,
        before_breadcrumb=_before_breadcrumb,
    )
    logger.info("Sentry initialised", extra={"sentry_environment": environment})
    return True
