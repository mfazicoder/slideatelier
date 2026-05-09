"""Auth + multi-tenant data isolation tests.

Coverage:
1. Sign-up → login → logout flow sets/clears the session cookie correctly.
2. Bad password is rejected and never authenticates.
3. Login rate limit kicks in after 5 failed attempts from the same IP.
4. require_user / authenticated dependency returns 401 for anonymous calls.
5. User A cannot read or publish user B's deck (cross-tenant isolation).
6. Public /web/<slug> stays accessible to anonymous viewers.
7. Ownership is enforced on /api/jobs/<job_id>/publish.
8. The legacy migration registers existing decks under SYSTEM_USER_ID and
   keeps the slug → job mapping resolvable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slideatelier.auth import db as auth_db
from slideatelier.auth import routes as auth_routes
from slideatelier.auth.db import SYSTEM_USER_ID, get_db, migrate_legacy_layout
from slideatelier.auth.passwords import hash_password, verify_password
from slideatelier.web.app import app

# A reasonably long password that satisfies validate_password_strength.
GOOD_PW = "supersecret-shibboleth-1"
OTHER_PW = "another-rabbit-hole-42"


@pytest.fixture(autouse=True)
def _reset_auth_state(tmp_path, monkeypatch):
    """Each test gets a fresh output_dir and a fresh in-memory rate limiter."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    auth_db.reset_db_cache()
    auth_routes.reset_rate_limits()
    yield


def _seed_deck(tmp_path: Path, owner_user_id: int, job_id: str = "deck-1") -> Path:
    """Materialise a deck on disk under the owner's per-user namespace and
    register it in SQLite so resolve_job_dir picks it up."""
    if owner_user_id == SYSTEM_USER_ID:
        job_dir = tmp_path / "workflow" / job_id
    else:
        job_dir = tmp_path / "users" / str(owner_user_id) / "workflow" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "deck.json").write_text(json.dumps({
        "title": "Owned",
        "subtitle": "",
        "core_message": "Test core message stating an answer in one sentence.",
        "narrative_arc": "Open. Defend. Close.",
        "slides": [
            {
                "layout": "title", "title": "Owned", "strap": "",
                "body": [], "body_left": [], "body_right": [],
                "speaker_notes": "", "rationale": "",
                "asset_ref": None, "extras": [],
            }
        ],
    }))
    db = get_db(tmp_path)
    db.record_deck(job_id, owner_user_id)
    return job_dir


# ---------------------------------------------------------------------------
# 1. Sign-up → login → logout flow
# ---------------------------------------------------------------------------

def test_signup_then_login_then_logout_sets_and_clears_session(tmp_path):
    c = TestClient(app)
    # Sign-up redirects to /workflow on success and sets the session cookie.
    r = c.post(
        "/signup",
        data={"email": "alice@example.com", "password": GOOD_PW, "next": "/workflow"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert "atelier_session" in r.cookies
    # /me reflects the authenticated state.
    me = c.get("/me").json()
    assert me["authenticated"] is True
    assert me["user"]["email"] == "alice@example.com"

    # Logout clears the cookie.
    r = c.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    me2 = c.get("/me").json()
    assert me2["authenticated"] is False

    # Re-login from scratch using the same credentials.
    r = c.post(
        "/login",
        data={"email": "alice@example.com", "password": GOOD_PW},
        follow_redirects=False,
    )
    assert r.status_code == 303
    me3 = c.get("/me").json()
    assert me3["authenticated"] is True


# ---------------------------------------------------------------------------
# 2. Bad password rejected
# ---------------------------------------------------------------------------

def test_bad_password_is_rejected(tmp_path):
    c = TestClient(app)
    c.post("/signup", data={"email": "bob@example.com", "password": GOOD_PW})
    # Wrong password → 401 + no session cookie.
    r = c.post(
        "/login",
        data={"email": "bob@example.com", "password": "totally-not-bobs-password"},
        follow_redirects=False,
    )
    assert r.status_code == 401
    assert "atelier_session" not in r.cookies


def test_password_hash_round_trip():
    h = hash_password("hunter2-with-extra-stuffing")
    assert h.startswith("$2")  # bcrypt prefix
    assert verify_password("hunter2-with-extra-stuffing", h)
    assert not verify_password("hunter2", h)
    assert not verify_password("", h)


# ---------------------------------------------------------------------------
# 3. Rate limit triggers after the configured threshold
# ---------------------------------------------------------------------------

def test_login_rate_limit_blocks_after_five_failures(tmp_path):
    c = TestClient(app)
    c.post("/signup", data={"email": "carol@example.com", "password": GOOD_PW})
    c.post("/logout")  # ensure clean state

    # 5 failed logins exhaust the bucket.
    for _ in range(5):
        r = c.post(
            "/login",
            data={"email": "carol@example.com", "password": "wrong-password"},
        )
        assert r.status_code == 401, r.text
    # The 6th attempt — even with the right password — gets 429.
    r = c.post(
        "/login",
        data={"email": "carol@example.com", "password": GOOD_PW},
    )
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# 4. require_user / 401 for anonymous
# ---------------------------------------------------------------------------

def test_require_user_raises_401_for_anonymous_request():
    """Direct unit test of require_user — bypasses FastAPI routing wiring."""
    from fastapi import HTTPException

    from slideatelier.auth import require_user

    class _StateBag:
        user = None

    class _FakeRequest:
        state = _StateBag()

    with pytest.raises(HTTPException) as ei:
        require_user(_FakeRequest())  # type: ignore[arg-type]
    assert ei.value.status_code == 401
    # Location header steers HTML clients to /login.
    assert ei.value.headers and ei.value.headers.get("Location") == "/login"


def test_require_user_returns_user_when_authenticated():
    from slideatelier.auth import require_user
    from slideatelier.auth.db import User

    fake_user = User(id=42, email="x@x.com", password_hash="h", created_at="now")

    class _StateBag:
        user = fake_user

    class _FakeRequest:
        state = _StateBag()

    got = require_user(_FakeRequest())  # type: ignore[arg-type]
    assert got.id == 42


# ---------------------------------------------------------------------------
# 5. User A cannot publish user B's deck (cross-tenant ownership)
# ---------------------------------------------------------------------------

def test_user_a_cannot_publish_user_bs_deck(tmp_path):
    # Two separate clients so cookie state doesn't blur. Each TestClient has
    # its own cookie jar; we drive A and B in parallel.
    a_client = TestClient(app)
    b_client = TestClient(app)

    a_client.post(
        "/signup", data={"email": "user-a@example.com", "password": GOOD_PW},
        follow_redirects=False,
    )
    b_client.post(
        "/signup", data={"email": "user-b@example.com", "password": OTHER_PW},
        follow_redirects=False,
    )

    # User B owns the deck.
    db = get_db(tmp_path)
    user_b = db.get_user_by_email("user-b@example.com")
    assert user_b is not None
    _seed_deck(tmp_path, user_b.id, "deck-of-b")

    # User A tries to publish B's deck.
    r = a_client.post("/api/jobs/deck-of-b/publish")
    assert r.status_code in (403, 404), r.text

    # Sanity: B can publish their own deck.
    r2 = b_client.post("/api/jobs/deck-of-b/publish")
    assert r2.status_code == 200, r2.text


# ---------------------------------------------------------------------------
# 6. Published /web/<slug> stays public
# ---------------------------------------------------------------------------

def test_published_web_url_is_public(tmp_path):
    c = TestClient(app)
    c.post("/signup", data={"email": "publisher@example.com", "password": GOOD_PW})
    db = get_db(tmp_path)
    user = db.get_user_by_email("publisher@example.com")
    assert user is not None
    job_id = "pub-deck"
    _seed_deck(tmp_path, user.id, job_id)

    pub = c.post(f"/api/jobs/{job_id}/publish")
    assert pub.status_code == 200, pub.text
    slug = pub.json()["slug"]

    # Anonymous viewer (no cookie) can still GET /web/<slug>.
    anon = TestClient(app)
    r = anon.get(f"/web/{slug}")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower()


# ---------------------------------------------------------------------------
# 7. Ownership enforced on publish — owner can publish, third parties can't.
# ---------------------------------------------------------------------------

def test_owner_can_publish_their_own_deck(tmp_path):
    c = TestClient(app)
    c.post("/signup", data={"email": "owner@example.com", "password": GOOD_PW})
    db = get_db(tmp_path)
    user = db.get_user_by_email("owner@example.com")
    assert user is not None
    _seed_deck(tmp_path, user.id, "owner-deck")

    r = c.post("/api/jobs/owner-deck/publish")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"].startswith("/web/")


# ---------------------------------------------------------------------------
# 8. Legacy migration registers existing decks under SYSTEM_USER_ID
# ---------------------------------------------------------------------------

def test_migration_seeds_legacy_workflows_and_slugs(tmp_path):
    # Mimic a pre-auth filesystem: a workflow dir + a shared slug index.
    legacy_dir = tmp_path / "workflow" / "legacy-deck"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "deck.json").write_text("{}")
    (tmp_path / "web_slugs.json").write_text(json.dumps({"abc12345": "legacy-deck"}))

    report = migrate_legacy_layout(tmp_path)
    assert report["jobs_seeded"] == 1
    assert report["slugs_seeded"] == 1

    db = get_db(tmp_path)
    deck = db.get_deck("legacy-deck")
    assert deck is not None
    assert deck.owner_user_id == SYSTEM_USER_ID
    assert deck.slug == "abc12345"
    # And by-slug lookup resolves it.
    by_slug = db.get_deck_by_slug("abc12345")
    assert by_slug is not None
    assert by_slug.job_id == "legacy-deck"

    # Idempotent — re-running returns 0/0.
    report2 = migrate_legacy_layout(tmp_path)
    assert report2 == {"jobs_seeded": 0, "slugs_seeded": 0}


# ---------------------------------------------------------------------------
# 9. Anonymous (dev mode) still resolves legacy decks via /web/<slug>.
# ---------------------------------------------------------------------------

def test_anonymous_can_view_legacy_published_deck(tmp_path, monkeypatch):
    """Backwards compat: a deck published under the legacy flat layout
    pre-auth is still publicly viewable post-migration."""
    # Seed a published deck under the legacy flat layout.
    job_id = "legacy-pub"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "deck.json").write_text(json.dumps({
        "title": "Legacy",
        "subtitle": "",
        "core_message": "Legacy core message stating an answer in one sentence.",
        "narrative_arc": "Open. Defend. Close.",
        "slides": [{
            "layout": "title", "title": "Legacy", "strap": "",
            "body": [], "body_left": [], "body_right": [],
            "speaker_notes": "", "rationale": "",
            "asset_ref": None, "extras": [],
        }],
    }))
    # Anonymous publish goes through (dev mode).
    c = TestClient(app)
    pub = c.post(f"/api/jobs/{job_id}/publish")
    assert pub.status_code == 200, pub.text
    slug = pub.json()["slug"]
    # Public viewer resolves.
    anon = TestClient(app)
    r = anon.get(f"/web/{slug}")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 10. /signup rejects weak passwords + duplicate emails.
# ---------------------------------------------------------------------------

def test_signup_rejects_weak_password(tmp_path):
    c = TestClient(app)
    r = c.post("/signup", data={"email": "weak@example.com", "password": "short"})
    assert r.status_code == 400
    assert "8 characters" in r.text


def test_signup_rejects_duplicate_email(tmp_path):
    c = TestClient(app)
    c.post("/signup", data={"email": "dup@example.com", "password": GOOD_PW})
    r2 = c.post("/signup", data={"email": "dup@example.com", "password": GOOD_PW})
    assert r2.status_code == 400
    assert "already exists" in r2.text.lower()
