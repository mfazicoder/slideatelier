"""
Tests for the Launch-tier TLD allowlist (``domains.py``) and its wiring
into ``POST /api/signup``.

Run from this directory:
    pip install fastapi httpx pydantic email-validator pytest
    pytest test_domains.py -v

Exercises:
    * ``validate_domain`` accept-path: ``.com``, ``.co.uk``, ``.app``.
    * ``validate_domain`` passthrough-path: ``.ai``, ``.io``, ``.tv``
      etc. are now ACCEPTED (we register them and pass on the cost
      above £14/yr at Launch checkout).
    * Unknown TLDs (``.shop``, ``.party``) still rejected — see
      DOMAIN_REGISTRATION_SCOPE.md.
    * ``passthrough_info`` returns the right per-TLD extra cost.
    * Malformed input is normalised (scheme/path stripped) or rejected.
    * The signup endpoint accepts passthrough TLDs at ``tier='launch'``
      and rejects only truly unknown TLDs.
    * Sandbox tier skips domain validation entirely.
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
    "candidate, tld, expected_extra_gbp",
    [
        ("openai.ai",   ".ai",  76),  # 90 - 14
        ("foo.io",      ".io",  16),  # 30 - 14
        ("watch.tv",    ".tv",  16),  # 30 - 14
        ("clan.gg",     ".gg",  56),  # 70 - 14
        ("link.so",     ".so",  11),  # 25 - 14
        ("about.me",    ".me",   1),  # 15 - 14
        ("random.xyz",  ".xyz",  6),  # 20 - 14
    ],
)
def test_passthrough_tlds_accepted(
    domains_module, candidate, tld, expected_extra_gbp,
):
    # Accepted at validate_domain — we register them.
    ok, msg = domains_module.validate_domain(candidate)
    assert ok is True, f"{candidate!r} should now be accepted (passthrough)"
    assert msg == ""

    # The signup endpoint (and Launch checkout) uses passthrough_info
    # to compute the extra line item.
    info = domains_module.passthrough_info(candidate)
    assert info is not None
    matched_tld, extra_gbp, _label = info
    assert matched_tld == tld
    assert extra_gbp == expected_extra_gbp


def test_passthrough_info_none_for_allowlisted(domains_module):
    """Allowlisted TLDs aren't passthrough — no extra cost line item."""
    assert domains_module.passthrough_info("prepsheet.com") is None
    assert domains_module.passthrough_info("alex.co.uk") is None


def test_passthrough_info_none_for_unknown(domains_module):
    """Unknown TLDs are rejected upstream; passthrough_info also
    returns None so callers don't accidentally bill for them."""
    assert domains_module.passthrough_info("weird.shop") is None


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


def test_launch_signup_accepts_passthrough_tld(app_client):
    """Passthrough TLDs (.ai, .io, .tv etc.) are now accepted at signup.
    Launch checkout adds the extra-cost line item separately."""
    client, _main = app_client
    resp = client.post("/api/signup", json=_payload(domain_choice="prepsheet.ai"))
    assert resp.status_code == 201, resp.text


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
