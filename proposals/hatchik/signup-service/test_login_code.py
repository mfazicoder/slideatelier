"""
Tests for the 6-digit verification-code sign-in fallback.

Run from this directory:
    pip install fastapi httpx pydantic email-validator pytest
    pytest test_login_code.py -v

Exercises:
    - Code generation: 6 digits, varies across calls.
    - Successful code redemption creates a session + sets cookie + marks
      the token consumed (so the magic link can no longer be replayed).
    - Stale (expired) code is rejected.
    - Wrong code is rejected.
    - Rate limit: 6th attempt within 15 min returns 429.
    - After 429, even a correct subsequent attempt fails (token burned).
    - The original magic-link path still works unchanged.
"""

from __future__ import annotations

import importlib
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch):
    """Fresh DB + a single seeded signup so /api/account/login finds it."""
    tmp = Path(tempfile.mkdtemp(prefix="hatchik-logincode-test-"))
    db_path = tmp / "signups.db"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("HATCHIK_ADMIN_TOKEN", "")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "")
    monkeypatch.setenv("HATCHIK_ALLOWED_ORIGINS", "https://hatchik.com")
    monkeypatch.setenv("TURNSTILE_SECRET", "")

    sys.path.insert(0, str(Path(__file__).parent))
    for mod in ("main", "cohort_metrics"):
        if mod in sys.modules:
            del sys.modules[mod]
    main = importlib.import_module("main")
    main.init_db()

    # Seed a signup so /api/account/login doesn't anti-enumerate.
    email = "owner@example.com"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO signups (created_at, email, product_name, description, tier, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                email,
                "Acme",
                "Test product",
                "sandbox",
                "live",
            ),
        )
        conn.commit()

    client = TestClient(main.app)
    return client, main, email, db_path


def _request_login(client, email):
    r = client.post("/api/account/login", json={"email": email})
    assert r.status_code == 202, r.text
    return r


def _current_token_row(db_path, email):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM login_tokens WHERE LOWER(email) = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (email.lower(),),
        ).fetchone()


# ─── Code generation ────────────────────────────────────────────────────


def test_code_generation_is_six_digits(app_client):
    _client, main, _email, _db = app_client
    codes = {main._generate_login_code() for _ in range(40)}
    # All codes are 6 ASCII digits, in [100000, 999999].
    for c in codes:
        assert re.fullmatch(r"\d{6}", c), f"not 6 digits: {c!r}"
        assert 100000 <= int(c) <= 999999
    # Vary across calls — collision in 40 draws out of 900k is ~1 in 22k,
    # so >1 distinct value is overwhelmingly likely.
    assert len(codes) > 1


def test_login_persists_code_on_token_row(app_client):
    client, _main, email, db_path = app_client
    _request_login(client, email)
    row = _current_token_row(db_path, email)
    assert row is not None
    assert row["code"] is not None
    assert re.fullmatch(r"\d{6}", row["code"])
    assert row["code_attempts"] == 0
    assert row["consumed_at"] is None


# ─── Successful code redemption ─────────────────────────────────────────


def test_correct_code_creates_session_and_consumes_token(app_client):
    client, main, email, db_path = app_client
    _request_login(client, email)
    row = _current_token_row(db_path, email)
    code = row["code"]

    r = client.post(
        "/api/account/login-with-code",
        json={"email": email, "code": code},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    # Cookie set on the response.
    assert main.SESSION_COOKIE_NAME in r.cookies
    cookie_value = r.cookies.get(main.SESSION_COOKIE_NAME)
    assert cookie_value and len(cookie_value) > 16

    # Token row is now consumed.
    after = _current_token_row(db_path, email)
    assert after["consumed_at"] is not None

    # And there's exactly one session for this email.
    with sqlite3.connect(db_path) as conn:
        sessions = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE LOWER(email) = ?",
            (email.lower(),),
        ).fetchone()[0]
    assert sessions == 1

    # Replaying the same code now fails (consumed → no pending sign-in).
    r2 = client.post(
        "/api/account/login-with-code",
        json={"email": email, "code": code},
    )
    assert r2.status_code == 404


def test_code_accepts_visual_space_format(app_client):
    """Customers may paste "123 456" — server should accept that."""
    client, _main, email, db_path = app_client
    _request_login(client, email)
    code = _current_token_row(db_path, email)["code"]
    spaced = f"{code[:3]} {code[3:]}"
    r = client.post(
        "/api/account/login-with-code",
        json={"email": email, "code": spaced},
    )
    assert r.status_code == 200, r.text


# ─── Rejection paths ────────────────────────────────────────────────────


def test_no_pending_signin_returns_404(app_client):
    client, _main, email, _db = app_client
    # No POST /login was made → no row.
    r = client.post(
        "/api/account/login-with-code",
        json={"email": email, "code": "123456"},
    )
    assert r.status_code == 404


def test_wrong_code_returns_410(app_client):
    client, _main, email, db_path = app_client
    _request_login(client, email)
    real_code = _current_token_row(db_path, email)["code"]
    # Flip the first digit so we definitely don't collide.
    bad = ("9" if real_code[0] != "9" else "1") + real_code[1:]
    r = client.post(
        "/api/account/login-with-code",
        json={"email": email, "code": bad},
    )
    assert r.status_code == 410


def test_expired_code_rejected(app_client):
    client, _main, email, db_path = app_client
    _request_login(client, email)
    code = _current_token_row(db_path, email)["code"]
    # Backdate the row so expires_at is in the past.
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE login_tokens SET expires_at = ? WHERE LOWER(email) = ?",
            (past, email.lower()),
        )
        conn.commit()
    r = client.post(
        "/api/account/login-with-code",
        json={"email": email, "code": code},
    )
    assert r.status_code == 410


# ─── Rate limit ─────────────────────────────────────────────────────────


def test_sixth_attempt_returns_429(app_client):
    """5 wrong attempts each return 410; the 6th returns 429."""
    client, main, email, db_path = app_client
    _request_login(client, email)
    real_code = _current_token_row(db_path, email)["code"]
    wrong = ("9" if real_code[0] != "9" else "1") + real_code[1:]

    # First LOGIN_CODE_MAX_ATTEMPTS wrong attempts: 410 each.
    for i in range(main.LOGIN_CODE_MAX_ATTEMPTS):
        r = client.post(
            "/api/account/login-with-code",
            json={"email": email, "code": wrong},
        )
        assert r.status_code == 410, f"attempt {i + 1}: {r.status_code} {r.text}"

    # 6th attempt — token is now at the cap, so we get 429 and the row is
    # invalidated.
    r6 = client.post(
        "/api/account/login-with-code",
        json={"email": email, "code": wrong},
    )
    assert r6.status_code == 429

    row = _current_token_row(db_path, email)
    assert row["consumed_at"] is not None


def test_after_429_correct_code_still_fails(app_client):
    """Even the right code can't recover a burned token."""
    client, main, email, db_path = app_client
    _request_login(client, email)
    real_code = _current_token_row(db_path, email)["code"]

    # Pin failed attempts to the cap directly so we don't have to spam.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE login_tokens SET code_attempts = ? WHERE LOWER(email) = ?",
            (main.LOGIN_CODE_MAX_ATTEMPTS, email.lower()),
        )
        conn.commit()

    # Next call — even with the *correct* code — must return 429 and burn
    # the token.
    r = client.post(
        "/api/account/login-with-code",
        json={"email": email, "code": real_code},
    )
    assert r.status_code == 429

    # And a subsequent call with the same correct code → 404 (no unconsumed row).
    r2 = client.post(
        "/api/account/login-with-code",
        json={"email": email, "code": real_code},
    )
    assert r2.status_code == 404


# ─── Magic-link path still works unchanged ──────────────────────────────


def test_magic_link_path_still_works(app_client):
    client, main, email, db_path = app_client
    _request_login(client, email)
    row = _current_token_row(db_path, email)
    token = row["token"]

    # GET the magic-link callback. TestClient follows redirects by default —
    # disable so we can assert the 303 directly.
    r = client.get(f"/api/account/auth?token={token}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/account"
    assert main.SESSION_COOKIE_NAME in r.cookies

    # Token consumed.
    after = _current_token_row(db_path, email)
    assert after["consumed_at"] is not None

    # And the code path can no longer redeem the same row.
    r2 = client.post(
        "/api/account/login-with-code",
        json={"email": email, "code": row["code"]},
    )
    assert r2.status_code == 404


def test_unknown_email_returns_404(app_client):
    client, _main, _email, _db = app_client
    r = client.post(
        "/api/account/login-with-code",
        json={"email": "nobody@example.com", "code": "123456"},
    )
    assert r.status_code == 404


def test_malformed_code_is_validation_error(app_client):
    client, _main, email, _db = app_client
    _request_login(client, email)
    r = client.post(
        "/api/account/login-with-code",
        json={"email": email, "code": "12ab56"},
    )
    # Pydantic validation rejects before our handler runs.
    assert r.status_code == 422
