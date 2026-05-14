"""
Tests for the Launch-tier TLD allowlist (``domains.py``) and its wiring
into ``POST /api/signup``.

Run from this directory:
    pip install fastapi httpx pydantic email-validator pytest
    pytest test_domains.py -v

Exercises:
    * ``validate_domain`` accept-path: ``.com``, ``.co.uk``, ``.app``.
    * ``validate_domain`` reject-path: blocked TLDs (``.ai``, ``.tv``,
      ``.io``) get a clear, customer-facing message.
    * Unknown TLDs (``.xyz``, ``.shop``) are rejected (phase-1 decision —
      see DOMAIN_REGISTRATION_SCOPE.md).
    * Malformed input is normalised (scheme/path stripped) or rejected.
    * The signup endpoint enforces the allowlist for ``tier='launch'``
      and skips validation for ``tier='sandbox'`` (no domain in Sandbox).
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ─── Unit tests against domains.validate_domain ──────────────────────────

@pytest.fixture(scope="module")
def domains_module():
    sys.path.insert(0, str(Path(__file__).parent))
    if "domains" in sys.modules:
        del sys.modules["domains"]
    return importlib.import_module("domains")


@pytest.mark.parametrize(
    "candidate",
    [
        "foo.com",
        "myapp.co",
        "studio.net",
        "community.org",
        "brand.app",
        "tooling.dev",
        "alex.co.uk",
        "alex.uk",
        "cool.tech",
        "thing.online",
        # Normalisation: scheme/path stripped, case-folded.
        "HTTPS://Foo.Com/some/path",
        "http://bar.co.uk",
        "www.baz.com",
        "qux.com/",
        "quux.org#hash",
    ],
)
def test_allowed_tlds_accepted(domains_module, candidate):
    ok, msg = domains_module.validate_domain(candidate)
    assert ok, f"expected {candidate!r} to be allowed, got: {msg!r}"
    assert msg == ""


@pytest.mark.parametrize(
    "candidate, tld_in_message",
    [
        ("openai.ai", ".ai"),
        ("foo.io", ".io"),
        ("watch.tv", ".tv"),
        ("clan.gg", ".gg"),
        ("link.so", ".so"),
        ("about.me", ".me"),
        ("random.xyz", ".xyz"),
    ],
)
def test_blocked_tlds_rejected_with_message(
    domains_module, candidate, tld_in_message,
):
    ok, msg = domains_module.validate_domain(candidate)
    assert ok is False
    assert tld_in_message in msg
    # Customer-facing copy must point at the BYO escape hatch so they
    # have a path forward.
    assert "hello@hatchik.com" in msg


@pytest.mark.parametrize(
    "candidate",
    [
        # Unknown TLDs are rejected in phase 1 — safer than passthrough.
        "weird.shop",
        "thing.party",
        "foo.bar",  # ``.bar`` is a real TLD but not on our allowlist
    ],
)
def test_unknown_tlds_rejected(domains_module, candidate):
    ok, msg = domains_module.validate_domain(candidate)
    assert ok is False
    # Should surface the supported list so the customer can self-correct.
    assert ".com" in msg


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        None,
        "   ",
        "no-tld-here",  # missing dot
        "foo",  # missing TLD
        ".com",  # bare TLD
        "foo..com",  # double dot
        "foo .com",  # whitespace inside
    ],
)
def test_malformed_input_rejected(domains_module, candidate):
    ok, msg = domains_module.validate_domain(candidate)
    assert ok is False
    assert msg  # non-empty message


def test_co_uk_matched_before_uk(domains_module):
    """``alex.co.uk`` must be matched as ``.co.uk`` (allowed), not ``.uk``
    (also allowed but a different entry). Both happen to be allowed here
    but the longest-match invariant must hold for future asymmetric
    cases.
    """
    ok, _ = domains_module.validate_domain("alex.co.uk")
    assert ok
    tld = domains_module._extract_tld("alex.co.uk")
    assert tld == ".co.uk"


# ─── Integration: /api/signup honours the allowlist for Launch tier ──────

@pytest.fixture
def app_client(monkeypatch):
    """Fresh DB + a fastapi TestClient with abuse gates disabled."""
    tmp = Path(tempfile.mkdtemp(prefix="hatchik-domains-test-"))
    db_path = tmp / "signups.db"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("HATCHIK_ADMIN_TOKEN", "")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "")
    monkeypatch.setenv("HATCHIK_ALLOWED_ORIGINS", "https://hatchik.com")
    monkeypatch.setenv("TURNSTILE_SECRET", "")

    sys.path.insert(0, str(Path(__file__).parent))
    for mod in ("main", "domains", "cohort_metrics"):
        if mod in sys.modules:
            del sys.modules[mod]
    main = importlib.import_module("main")
    main.init_db()

    # No-op the GitHub-existence check — the test customer's handle
    # isn't a real account.
    async def _gh_ok(_):
        return True, "ok"
    monkeypatch.setattr(main, "_github_user_exists", _gh_ok)

    # No-op the email-sending paths (no Resend key in tests anyway, but
    # be explicit so failures don't masquerade as 500s).
    async def _noop(*_a, **_kw):
        return None
    monkeypatch.setattr(main, "send_founder_notification", _noop)
    monkeypatch.setattr(main, "send_customer_acknowledgement", _noop)
    monkeypatch.setattr(main, "enqueue_or_dispatch", _noop)

    client = TestClient(main.app)
    yield client, main


def _payload(**overrides):
    base = {
        "email": "alex@example.com",
        "first_name": "Alex",
        "product_name": "PrepSheet",
        "description": "A meal prep app for my PT clients.",
        "tier": "launch",
        "domain_choice": "prepsheet.com",
        "accepted_terms": True,
    }
    base.update(overrides)
    return base


def test_launch_signup_accepts_allowlisted_domain(app_client):
    client, _main = app_client
    resp = client.post("/api/signup", json=_payload(domain_choice="prepsheet.com"))
    assert resp.status_code == 201, resp.text


def test_launch_signup_rejects_blocked_tld(app_client):
    client, _main = app_client
    resp = client.post("/api/signup", json=_payload(domain_choice="prepsheet.ai"))
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"]["error"] == "domain_not_supported"
    assert ".ai" in body["detail"]["message"]


def test_launch_signup_rejects_unknown_tld(app_client):
    client, _main = app_client
    resp = client.post("/api/signup", json=_payload(domain_choice="prepsheet.shop"))
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"]["error"] == "domain_not_supported"


def test_launch_signup_rejects_empty_domain(app_client):
    """Launch tier MUST have a domain — empty value is a 422 (we can't
    honour 'year 1 included' against nothing).
    """
    client, _main = app_client
    resp = client.post("/api/signup", json=_payload(domain_choice=None))
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"]["error"] == "domain_not_supported"


def test_sandbox_signup_skips_domain_validation(app_client):
    """Sandbox tier doesn't get a custom domain (uses
    slug.hatchik.com). Even if a customer puts garbage in
    ``domain_choice`` it shouldn't 422 — the field is ignored at the
    Sandbox tier.
    """
    client, _main = app_client
    resp = client.post(
        "/api/signup",
        json=_payload(tier="sandbox", domain_choice="something.ai"),
    )
    assert resp.status_code == 201, resp.text
