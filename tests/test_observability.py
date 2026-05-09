"""Launch-readiness tests for observability: friendly error pages, structured
logging, request IDs, and rate limiting."""
from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from slideatelier.observability import (
    RateLimitMiddleware,
    RequestIdMiddleware,
    configure_logging,
    current_request_id,
)
from slideatelier.observability.rate_limit import (
    DEFAULT_RATE_LIMITS,
    RateLimitRule,
    _TokenBucketStore,
)
from slideatelier.observability.sentry import _scrub
from slideatelier.web.app import app


client = TestClient(app)


# ---------------------------------------------------------------------------
# Friendly error pages
# ---------------------------------------------------------------------------


def test_404_returns_friendly_html_page():
    r = client.get("/this-page-does-not-exist-anywhere-xyz")
    assert r.status_code == 404
    body = r.text.lower()
    # Friendly copy + a link back home.
    assert "not found" in body or "deck not found" in body
    assert 'href="/"' in r.text


def test_404_includes_request_id_for_support():
    r = client.get("/another-missing-route")
    assert r.status_code == 404
    # Request_id is rendered into the template AND surfaced as response header.
    assert "X-Request-Id" in r.headers
    rid = r.headers["X-Request-Id"]
    assert rid in r.text


def test_404_for_api_returns_json():
    r = client.get("/api/jobs/nonexistent")
    assert r.status_code == 404
    body = r.json()
    assert "request_id" in body
    assert "detail" in body


# ---------------------------------------------------------------------------
# Request-ID middleware
# ---------------------------------------------------------------------------


def test_request_id_header_present_on_every_response():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert "X-Request-Id" in r.headers
    assert len(r.headers["X-Request-Id"]) >= 16


def test_request_id_propagated_from_inbound_header():
    r = client.get("/api/health", headers={"X-Request-Id": "abcd-1234-test"})
    assert r.headers["X-Request-Id"] == "abcd-1234-test"


def test_request_id_unique_per_request():
    r1 = client.get("/api/health")
    r2 = client.get("/api/health")
    assert r1.headers["X-Request-Id"] != r2.headers["X-Request-Id"]


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


def test_request_summary_log_line_emitted(caplog):
    """Every request should produce a single 'request' summary log record
    carrying the canonical fields the user can grep for in production."""
    # Don't re-call configure_logging here — it clears root handlers and
    # would wipe pytest's caplog handler.
    with caplog.at_level(logging.INFO, logger="slideatelier.request"):
        r = client.get("/api/health")
    assert r.status_code == 200

    matching = [rec for rec in caplog.records if rec.name == "slideatelier.request"]
    assert matching, "expected at least one 'slideatelier.request' log record"
    rec = matching[-1]
    assert getattr(rec, "event", None) == "request_summary"
    assert getattr(rec, "method", None) == "GET"
    assert getattr(rec, "path", None) == "/api/health"
    assert getattr(rec, "status", None) == 200
    assert isinstance(getattr(rec, "duration_ms", None), float)
    assert getattr(rec, "request_id", None)


def test_json_formatter_emits_valid_json_with_request_id(capsys):
    configure_logging(json_logs=True, level="DEBUG")
    log = logging.getLogger("slideatelier.test_json")
    log.info("hello structured world", extra={"event": "test_event", "k": 1})
    captured = capsys.readouterr()
    line = [ln for ln in captured.out.splitlines() if "test_event" in ln][-1]
    payload = json.loads(line)
    assert payload["message"] == "hello structured world"
    assert payload["event"] == "test_event"
    assert payload["k"] == 1
    assert "ts" in payload and "level" in payload and "request_id" in payload
    # restore plain logs for the rest of the suite
    configure_logging(json_logs=False, level="DEBUG")


def test_current_request_id_default_outside_request():
    # Outside any request scope, the default sentinel keeps logs well-formed.
    assert current_request_id() == "-"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def _build_limited_app(rules):
    """Tiny isolated app with our middleware and a single hot endpoint."""
    test_app = FastAPI()
    test_app.add_middleware(RateLimitMiddleware, rules=rules)
    test_app.add_middleware(RequestIdMiddleware)

    @test_app.get("/burn")
    def _burn():
        return {"ok": True}

    @test_app.get("/api/burn")
    def _api_burn():
        return {"ok": True}

    return TestClient(test_app)


def test_rate_limit_triggers_after_threshold():
    # Tiny budget so we hit the wall fast.
    rules = (RateLimitRule("/burn", limit=3, window_s=3600),)
    c = _build_limited_app(rules)

    # First 3 should pass.
    for i in range(3):
        r = c.get("/burn")
        assert r.status_code == 200, f"call {i} should succeed"

    # 4th should 429.
    r = c.get("/burn")
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 1


def test_rate_limit_429_json_for_api_routes():
    """API path → JSON body with retry_after_s; HTML body for browser routes."""
    rules = (RateLimitRule("/api/burn", limit=1, window_s=3600),
             RateLimitRule("*", limit=600, window_s=3600))
    c = _build_limited_app(rules)

    assert c.get("/api/burn").status_code == 200
    r2 = c.get("/api/burn")
    assert r2.status_code == 429
    body = r2.json()
    assert body["error"] == "rate_limited"
    assert body["retry_after_s"] >= 1
    assert "request_id" in body


def test_token_bucket_refills_over_time():
    store = _TokenBucketStore()
    rule = RateLimitRule("/x", limit=2, window_s=2)  # 1 token/sec
    key = ("/x", "1.2.3.4")

    t = 1000.0
    a, _, _ = store.take(key, rule, t); assert a
    a, _, _ = store.take(key, rule, t); assert a
    a, _, retry = store.take(key, rule, t)
    assert not a and retry > 0

    # After enough simulated time, a token is available again.
    a, _, _ = store.take(key, rule, t + 1.5)
    assert a


def test_health_endpoint_not_rate_limited():
    """Even pounding /api/health should never 429 — it's a probe."""
    for _ in range(50):
        r = client.get("/api/health")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Sentry scrubber
# ---------------------------------------------------------------------------


def test_sentry_scrub_redacts_api_keys_and_passwords():
    payload = {
        "headers": {
            "Authorization": "Bearer sk-secret-1234",
            "X-API-Key": "live_key_abc",
            "Content-Type": "application/json",
        },
        "body": {
            "username": "farha",
            "password": "hunter2",
            "ANTHROPIC_API_KEY": "sk-ant-aaa",
            "nested": {"session_token": "abc", "ok": "fine"},
        },
        "list_of_things": [{"cookie": "x"}, "plain"],
    }
    scrubbed = _scrub(payload)
    assert scrubbed["headers"]["Authorization"] == "[redacted]"
    assert scrubbed["headers"]["X-API-Key"] == "[redacted]"
    assert scrubbed["headers"]["Content-Type"] == "application/json"
    assert scrubbed["body"]["password"] == "[redacted]"
    assert scrubbed["body"]["ANTHROPIC_API_KEY"] == "[redacted]"
    assert scrubbed["body"]["username"] == "farha"
    assert scrubbed["body"]["nested"]["session_token"] == "[redacted]"
    assert scrubbed["body"]["nested"]["ok"] == "fine"
    assert scrubbed["list_of_things"][0]["cookie"] == "[redacted]"
    assert scrubbed["list_of_things"][1] == "plain"


def test_default_rate_limits_includes_expected_routes():
    """Smoke-check: configuration constants are sensible — the user can grep
    these to confirm what's gated."""
    patterns = {r.pattern: (r.limit, r.window_s) for r in DEFAULT_RATE_LIMITS}
    assert "/api/jobs/*/publish" in patterns
    assert patterns["/api/jobs/*/publish"] == (30, 3600)
    assert "/api/generate" in patterns
    assert patterns["/api/generate"] == (10, 3600)
    assert "/workflow/storyboard" in patterns
    assert patterns["/workflow/storyboard"] == (10, 3600)
    assert "*" in patterns
    assert patterns["*"][0] >= 100  # catch-all is generous
