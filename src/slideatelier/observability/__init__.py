"""Observability primitives for slideAtelier: structured logging, request IDs,
Sentry integration, and rate limiting.

The pieces are designed to compose at the FastAPI app level (see
``slideatelier.web.app.create_app``) and to stay quiet when their backing env
vars are not set — so local dev still feels lightweight.
"""

from .logging import (
    REQUEST_ID_CTX,
    RequestIdMiddleware,
    configure_logging,
    current_request_id,
)
from .rate_limit import RateLimitError, RateLimitMiddleware, default_rate_limits
from .sentry import init_sentry

__all__ = [
    "REQUEST_ID_CTX",
    "RequestIdMiddleware",
    "configure_logging",
    "current_request_id",
    "RateLimitError",
    "RateLimitMiddleware",
    "default_rate_limits",
    "init_sentry",
]
