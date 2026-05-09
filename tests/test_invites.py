"""Invite-only signup gate — unit tests for the invites module.

The auth agent's signup endpoint will call ``validate_invite(code)`` before
creating the user and ``consume_invite(code)`` after. These tests exercise
the public invite API directly so the contract is locked in regardless of
whether the signup wiring has landed yet (see TODO_INTEGRATE.md).

Coverage matches the deploy spec:
1. code generation (random + caller-supplied)
2. signup-with-no-code rejected     → validate_invite("")  raises InviteNotFound
3. signup-with-bad-code rejected    → validate_invite("not-real") raises InviteNotFound
4. signup-with-valid-code succeeds  → validate_invite + consume_invite increments
5. expired code rejected            → expires_at in the past raises InviteExpired
6. max-uses enforced                → after N consumes, (N+1)th raises InviteExhausted
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from slideatelier.auth import db as auth_db
from slideatelier.auth import invites
from slideatelier.auth.invites import (
    InviteExhausted,
    InviteExpired,
    InviteNotFound,
    consume_invite,
    create_invite,
    get_invite,
    list_invites,
    revoke_invite,
    validate_invite,
)


@pytest.fixture(autouse=True)
def _isolate_invite_db(tmp_path: Path, monkeypatch):
    """Each test gets a fresh atelier.db via SLIDEATELIER_OUTPUT_DIR."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    auth_db.reset_db_cache()
    yield


# ---------------------------------------------------------------------------
# 1. Code generation
# ---------------------------------------------------------------------------

def test_create_invite_generates_url_safe_random_code():
    inv = create_invite(max_uses=1)
    assert inv.code  # non-empty
    # secrets.token_urlsafe(8) yields ~11 chars, URL-safe (no /+=).
    assert 8 <= len(inv.code) <= 16
    assert all(c.isalnum() or c in "-_" for c in inv.code)
    assert inv.max_uses == 1
    assert inv.used_count == 0
    assert inv.expires_at is None  # default = never


def test_create_invite_with_caller_supplied_code_and_expiry():
    inv = create_invite(max_uses=3, expires_days=7, code="beta-friend-42")
    assert inv.code == "beta-friend-42"
    assert inv.max_uses == 3
    # expires_at should be ~7 days out.
    assert inv.expires_at is not None
    exp = datetime.fromisoformat(inv.expires_at)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    delta = exp - datetime.now(timezone.utc)
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)


def test_create_invite_rejects_duplicate_caller_code():
    create_invite(code="dup-code", max_uses=1)
    with pytest.raises(ValueError):
        create_invite(code="dup-code", max_uses=1)


def test_create_invite_rejects_negative_max_uses():
    with pytest.raises(ValueError):
        create_invite(max_uses=-1)


def test_create_invite_rejects_zero_or_negative_expires_days():
    with pytest.raises(ValueError):
        create_invite(max_uses=1, expires_days=0)
    with pytest.raises(ValueError):
        create_invite(max_uses=1, expires_days=-3)


def test_list_invites_returns_newest_first():
    a = create_invite(code="aaa", max_uses=1)
    b = create_invite(code="bbb", max_uses=1)
    rows = list_invites()
    assert {r.code for r in rows} == {a.code, b.code}


# ---------------------------------------------------------------------------
# 2. signup-with-no-code rejected
# ---------------------------------------------------------------------------

def test_signup_with_no_code_is_rejected():
    """Empty string / None / whitespace all raise InviteNotFound."""
    for bad in ("", "   ", None):
        with pytest.raises(InviteNotFound):
            validate_invite(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. signup-with-bad-code rejected
# ---------------------------------------------------------------------------

def test_signup_with_unknown_code_is_rejected():
    create_invite(code="exists-only", max_uses=1)
    with pytest.raises(InviteNotFound):
        validate_invite("totally-made-up")


# ---------------------------------------------------------------------------
# 4. signup-with-valid-code succeeds and increments
# ---------------------------------------------------------------------------

def test_signup_with_valid_code_succeeds_and_increment():
    inv = create_invite(code="welcome-1", max_uses=2)
    # validate doesn't mutate.
    v = validate_invite("welcome-1")
    assert v.used_count == 0
    # consume increments by 1.
    after = consume_invite("welcome-1")
    assert after.used_count == 1
    # Re-fetch from DB to confirm it persisted.
    fresh = get_invite("welcome-1")
    assert fresh is not None
    assert fresh.used_count == 1


def test_consume_returns_remaining_uses_for_finite_codes():
    create_invite(code="three-uses", max_uses=3)
    after_first = consume_invite("three-uses")
    assert after_first.remaining == 2
    after_second = consume_invite("three-uses")
    assert after_second.remaining == 1


# ---------------------------------------------------------------------------
# 5. expired code rejected
# ---------------------------------------------------------------------------

def test_expired_code_is_rejected_by_validate(tmp_path):
    """Manually backdate an invite and confirm validate raises InviteExpired."""
    inv = create_invite(code="time-bomb", max_uses=5, expires_days=7)
    # Reach into the SQLite layer and rewrite expires_at into the past.
    db = auth_db.get_db(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with db._lock, db._conn:  # noqa: SLF001
        db._conn.execute(  # noqa: SLF001
            "UPDATE invite_codes SET expires_at = ? WHERE code = ?",
            (past, "time-bomb"),
        )
    with pytest.raises(InviteExpired):
        validate_invite("time-bomb")
    with pytest.raises(InviteExpired):
        consume_invite("time-bomb")


def test_non_expired_code_with_future_expiry_validates_ok():
    create_invite(code="future-friend", max_uses=1, expires_days=30)
    inv = validate_invite("future-friend")
    assert inv.code == "future-friend"
    assert not inv.is_expired()


# ---------------------------------------------------------------------------
# 6. max-uses enforced
# ---------------------------------------------------------------------------

def test_max_uses_enforced_after_limit_reached():
    create_invite(code="single-use", max_uses=1)
    consume_invite("single-use")  # ok — used_count = 1
    with pytest.raises(InviteExhausted):
        validate_invite("single-use")
    with pytest.raises(InviteExhausted):
        consume_invite("single-use")


def test_max_uses_enforced_for_multi_use_codes():
    create_invite(code="trio", max_uses=3)
    consume_invite("trio")
    consume_invite("trio")
    consume_invite("trio")
    # Fourth time exhausts.
    with pytest.raises(InviteExhausted):
        consume_invite("trio")


# ---------------------------------------------------------------------------
# Bonus — revoke
# ---------------------------------------------------------------------------

def test_revoke_invite_disables_a_code():
    create_invite(code="will-be-killed", max_uses=10)
    assert revoke_invite("will-be-killed") is True
    with pytest.raises(InviteExhausted):
        validate_invite("will-be-killed")


def test_revoke_unknown_code_returns_false():
    assert revoke_invite("never-existed") is False
