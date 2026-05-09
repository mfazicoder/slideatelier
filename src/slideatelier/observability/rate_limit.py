"""In-memory token-bucket rate limiter middleware.

Defaults (tweak ``DEFAULT_RATE_LIMITS`` below — no recompile required):

  * ``/api/jobs/*/publish``: 30/hour per IP
  * ``/api/generate``, ``/workflow/storyboard`` (any AI-spending route):
    10/hour per IP
  * everything else: 600/hour per IP

LIMITATION: state is process-local. With multiple uvicorn workers, each
worker has its own bucket — the effective rate is roughly
``workers × limit``. v1 launches with one worker so this is fine; before
scaling out, swap the storage layer for Redis (or SQLite) so all workers
share counters. The middleware design isolates the storage in
``_TokenBucketStore`` to make that swap easy.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
import time
from dataclasses import dataclass
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    """Raised (and caught by the middleware) when a bucket is empty.

    Also exported for direct use: any route can raise this and the
    registered exception handler in app.py will turn it into a friendly 429.
    """

    def __init__(self, retry_after_s: int, limit: int, window_s: int) -> None:
        self.retry_after_s = max(1, int(retry_after_s))
        self.limit = limit
        self.window_s = window_s
        super().__init__(
            f"Rate limit exceeded ({limit} per {window_s}s); "
            f"retry in {self.retry_after_s}s"
        )


@dataclass(frozen=True)
class RateLimitRule:
    """One rule: glob-pattern → (limit, window_seconds)."""

    pattern: str
    limit: int
    window_s: int

    def matches(self, path: str) -> bool:
        return fnmatch.fnmatchcase(path, self.pattern)


# The user can edit this dict directly. Order matters: first match wins, so
# more specific patterns (with wildcards inside) come before the catch-all.
# Keeping it as a list-of-rules at module scope makes it tweakable without
# touching middleware code or restarting any kind of build.
DEFAULT_RATE_LIMITS: tuple[RateLimitRule, ...] = (
    RateLimitRule("/api/jobs/*/publish", limit=30, window_s=3600),
    RateLimitRule("/api/generate", limit=10, window_s=3600),
    RateLimitRule("/workflow/storyboard", limit=10, window_s=3600),
    RateLimitRule("/workflow/storyboard/*/expand", limit=10, window_s=3600),
    # Catch-all — any path not matched above falls through to here.
    RateLimitRule("*", limit=600, window_s=3600),
)


def default_rate_limits() -> tuple[RateLimitRule, ...]:
    """Return a fresh copy of the default rules (so tests can mutate freely)."""
    return tuple(DEFAULT_RATE_LIMITS)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class _TokenBucketStore:
    """In-memory per-key bucket. Thread-safe. Buckets refill smoothly so we
    don't get the bursty edge-of-window thundering-herd you'd see from a fixed
    window counter.

    Trade-off: we keep one entry per (path-rule, ip) pair. Bounded by
    ``max_buckets`` to keep memory predictable; LRU eviction.
    """

    def __init__(self, max_buckets: int = 50_000) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}
        self._max_buckets = max_buckets

    def take(self, key: tuple[str, str], rule: RateLimitRule, now: float) -> tuple[bool, float, float]:
        """Try to consume one token from the bucket.

        Returns ``(allowed, tokens_remaining, retry_after_s)``. When allowed,
        retry_after_s is 0.
        """
        rate_per_s = rule.limit / rule.window_s
        with self._lock:
            tokens, last_refill = self._buckets.get(key, (float(rule.limit), now))
            elapsed = max(0.0, now - last_refill)
            tokens = min(float(rule.limit), tokens + elapsed * rate_per_s)
            if tokens >= 1.0:
                tokens -= 1.0
                self._buckets[key] = (tokens, now)
                self._evict_if_needed()
                return True, tokens, 0.0
            # Not enough — compute when one full token will be available.
            deficit = 1.0 - tokens
            retry_after = deficit / rate_per_s if rate_per_s > 0 else float(rule.window_s)
            self._buckets[key] = (tokens, now)
            return False, tokens, retry_after

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def _evict_if_needed(self) -> None:
        # Cheap LRU-ish — drop ~10% of oldest entries when we hit the cap.
        if len(self._buckets) <= self._max_buckets:
            return
        items = sorted(self._buckets.items(), key=lambda kv: kv[1][1])
        cull = len(self._buckets) // 10 or 1
        for k, _ in items[:cull]:
            self._buckets.pop(k, None)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


def _client_key(request: Request) -> str:
    """Best-effort client identifier. Uses X-Forwarded-For first hop when
    present (set by sane reverse proxies), otherwise the socket peer."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        first = fwd.split(",", 1)[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply the first matching rule to each incoming request. On reject,
    builds a 429 response directly — exceptions raised inside Starlette's
    BaseHTTPMiddleware bypass FastAPI's typed exception handlers, so we
    handle the response inline here. Route code can still raise
    ``RateLimitError`` and have the registered handler in ``app.py`` render
    the friendly HTML page.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        rules: Iterable[RateLimitRule] | None = None,
        store: _TokenBucketStore | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(app)
        self.rules: tuple[RateLimitRule, ...] = tuple(rules) if rules is not None else DEFAULT_RATE_LIMITS
        self.store = store or _TokenBucketStore()
        self.enabled = enabled

    def _match(self, path: str) -> RateLimitRule | None:
        for rule in self.rules:
            if rule.matches(path):
                return rule
        return None

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not self.enabled:
            return await call_next(request)
        # Don't throttle static assets / health probes.
        path = request.url.path
        if path.startswith(("/static/", "/thumbnails/", "/user-fonts/")) or path == "/api/health":
            return await call_next(request)

        rule = self._match(path)
        if rule is None:
            return await call_next(request)

        key = (rule.pattern, _client_key(request))
        allowed, _remaining, retry_after = self.store.take(key, rule, time.time())
        if not allowed:
            retry_s = int(retry_after) + 1
            logger.info(
                "rate limit exceeded",
                extra={
                    "event": "rate_limit_block",
                    "path": path,
                    "client": key[1],
                    "rule": rule.pattern,
                    "limit": rule.limit,
                    "window_s": rule.window_s,
                    "retry_after_s": retry_s,
                },
            )
            return self._build_429(request, retry_s, rule)
        response: Response = await call_next(request)
        # Surface a hint so clients can self-throttle without parsing 429s.
        response.headers["X-RateLimit-Limit"] = str(rule.limit)
        response.headers["X-RateLimit-Window"] = str(rule.window_s)
        return response

    def _build_429(self, request: Request, retry_s: int, rule: RateLimitRule) -> Response:
        """Build a 429 response inline. API requests get JSON; HTML browsers
        get a minimal HTML body — the registered exception handler renders
        the full themed template when a route raises ``RateLimitError``
        directly, but middleware-level rejection is rendered here to keep
        Starlette happy.
        """
        request_id = getattr(request.state, "request_id", "-")
        accept = request.headers.get("accept", "")
        wants_json = request.url.path.startswith("/api/") or (
            "application/json" in accept and "text/html" not in accept
        )
        headers = {"Retry-After": str(retry_s)}
        if wants_json:
            return JSONResponse(
                {
                    "error": "rate_limited",
                    "retry_after_s": retry_s,
                    "limit": rule.limit,
                    "window_s": rule.window_s,
                    "request_id": request_id,
                },
                status_code=429,
                headers=headers,
            )
        # Minimal HTML body. The themed template is reachable by raising
        # RateLimitError in route code and letting the app-level handler render.
        body = (
            f"<!doctype html><html><head><title>Slow down · slideAtelier</title></head>"
            f"<body style='font-family:sans-serif;max-width:540px;margin:60px auto;padding:0 20px'>"
            f"<p style='font-size:11px;text-transform:uppercase;color:#888'>429</p>"
            f"<h1 style='font-size:28px;margin:8px 0'>Easy there — too many requests</h1>"
            f"<p>You've hit the rate limit. Try again in about <strong>{retry_s} seconds</strong>.</p>"
            f"<p><a href='/' style='color:#b45309'>Back to home</a></p>"
            f"<p style='color:#888;font-size:12px;margin-top:32px'>Request ID: "
            f"<code>{request_id}</code></p>"
            f"</body></html>"
        )
        return Response(content=body, status_code=429, media_type="text/html", headers=headers)
