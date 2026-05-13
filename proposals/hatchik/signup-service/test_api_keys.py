"""
Tests for the Bearer-auth flow:
  - POST /api/account/api-keys creates a key, returns plaintext exactly once
  - The plaintext authenticates as the issuing user via Authorization header
  - GET /api/account/api-keys lists keys without leaking plaintext
  - DELETE /api/account/api-keys/{id} revokes; subsequent Bearer auth fails
  - Wrong-account revoke returns 404 (anti-enumeration)
  - Both Cookie and Bearer auth work side-by-side on the same endpoint
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    db_path = tmp_path / "signups.db"
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

    # Seed two signups so the wrong-account test has something to point at
    email = "alice@example.com"
    other = "bob@example.com"
    with sqlite3.connect(db_path) as conn:
        for e in (email, other):
            conn.execute(
                "INSERT INTO signups (created_at, email, product_name, description, tier, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), e, "P", "test", "sandbox", "live"),
            )
        # Sessions for both
        now = datetime.now(timezone.utc)
        for e, sid in [(email, "sess_alice"), (other, "sess_bob")]:
            conn.execute(
                "INSERT INTO sessions (session_id, email, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (sid, e, now.isoformat(),
                 (now + timedelta(days=30)).isoformat()),
            )
        conn.commit()

    client = TestClient(main.app)
    return main, client, db_path, email, other


def _cookie(client: TestClient, session_id: str) -> dict[str, str]:
    return {"hatchik_session": session_id}


# ── Create + use ────────────────────────────────────────────────────────

def test_create_returns_plaintext_exactly_once(app_client):
    main, client, _, _email, _ = app_client
    r = client.post(
        "/api/account/api-keys",
        json={"name": "mcp on macbook"},
        cookies=_cookie(client, "sess_alice"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["key"].startswith("hk_live_"), body
    assert len(body["key"]) > 32  # has actual entropy
    assert "key" in body
    assert "warning" in body
    assert body["name"] == "mcp on macbook"


def test_plaintext_authenticates_via_bearer(app_client):
    main, client, _, email, _ = app_client
    create = client.post(
        "/api/account/api-keys",
        json={"name": "test"},
        cookies=_cookie(client, "sess_alice"),
    )
    plaintext = create.json()["key"]

    # Use the Bearer token to call a protected endpoint
    r = client.get(
        "/api/account/me",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert r.status_code == 200
    assert r.json()["email"] == email


def test_bearer_doesnt_leak_into_wrong_account(app_client):
    main, client, _, email, other = app_client
    create = client.post(
        "/api/account/api-keys",
        json={"name": "alice's"},
        cookies=_cookie(client, "sess_alice"),
    )
    plaintext = create.json()["key"]

    # Bob's session sees only Bob's account, even if Alice's bearer is in
    # the header — cookie takes precedence when both resolve? Per the
    # spec, Bearer wins. So this should return Alice's email.
    r = client.get(
        "/api/account/me",
        headers={"Authorization": f"Bearer {plaintext}"},
        cookies=_cookie(client, "sess_bob"),
    )
    assert r.status_code == 200
    assert r.json()["email"] == email  # Alice, not Bob — Bearer wins


def test_invalid_bearer_falls_back_to_cookie(app_client):
    main, client, _, email, _ = app_client
    # Garbage Bearer + valid cookie → cookie auth wins
    r = client.get(
        "/api/account/me",
        headers={"Authorization": "Bearer hk_live_bogus_invalid_token"},
        cookies=_cookie(client, "sess_alice"),
    )
    assert r.status_code == 200
    assert r.json()["email"] == email


def test_no_auth_at_all_returns_401(app_client):
    main, client, *_ = app_client
    r = client.get("/api/account/me")
    assert r.status_code == 401


# ── List ────────────────────────────────────────────────────────────────

def test_list_returns_metadata_no_plaintext(app_client):
    main, client, *_ = app_client
    client.post("/api/account/api-keys", json={"name": "k1"},
                cookies=_cookie(client, "sess_alice"))
    client.post("/api/account/api-keys", json={"name": "k2"},
                cookies=_cookie(client, "sess_alice"))

    r = client.get("/api/account/api-keys",
                   cookies=_cookie(client, "sess_alice"))
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert len(keys) == 2
    for k in keys:
        assert "key" not in k  # plaintext never returned in list
        assert "key_hash" not in k  # don't leak hash either
        assert k["status"] == "active"
        assert "name" in k and "created_at" in k


def test_list_only_returns_signed_in_users_keys(app_client):
    main, client, *_ = app_client
    client.post("/api/account/api-keys", json={"name": "alice"},
                cookies=_cookie(client, "sess_alice"))
    client.post("/api/account/api-keys", json={"name": "bob"},
                cookies=_cookie(client, "sess_bob"))

    alice_list = client.get("/api/account/api-keys",
                             cookies=_cookie(client, "sess_alice")).json()["keys"]
    bob_list = client.get("/api/account/api-keys",
                           cookies=_cookie(client, "sess_bob")).json()["keys"]
    assert len(alice_list) == 1 and alice_list[0]["name"] == "alice"
    assert len(bob_list) == 1 and bob_list[0]["name"] == "bob"


# ── Revoke ──────────────────────────────────────────────────────────────

def test_revoke_then_bearer_fails(app_client):
    main, client, _, email, _ = app_client
    create = client.post("/api/account/api-keys", json={"name": "k"},
                          cookies=_cookie(client, "sess_alice")).json()
    plaintext = create["key"]
    key_id = create["id"]

    # Confirm bearer works first
    r = client.get("/api/account/me",
                   headers={"Authorization": f"Bearer {plaintext}"})
    assert r.status_code == 200

    # Revoke
    r = client.delete(f"/api/account/api-keys/{key_id}",
                      cookies=_cookie(client, "sess_alice"))
    assert r.status_code == 204

    # Bearer no longer works — falls back to no auth → 401
    r = client.get("/api/account/me",
                   headers={"Authorization": f"Bearer {plaintext}"})
    assert r.status_code == 401

    # List shows revoked status
    listed = client.get("/api/account/api-keys",
                         cookies=_cookie(client, "sess_alice")).json()["keys"]
    assert listed[0]["status"] == "revoked"
    assert listed[0]["revoked_at"] is not None


def test_revoke_someone_elses_key_returns_404(app_client):
    main, client, *_ = app_client
    # Alice creates a key
    create = client.post("/api/account/api-keys", json={"name": "alice's"},
                          cookies=_cookie(client, "sess_alice")).json()
    # Bob tries to revoke it
    r = client.delete(f"/api/account/api-keys/{create['id']}",
                      cookies=_cookie(client, "sess_bob"))
    # 404, not 403 — anti-enumeration (don't reveal that the id exists)
    assert r.status_code == 404


def test_revoke_already_revoked_is_404(app_client):
    main, client, *_ = app_client
    create = client.post("/api/account/api-keys", json={"name": "k"},
                          cookies=_cookie(client, "sess_alice")).json()
    client.delete(f"/api/account/api-keys/{create['id']}",
                  cookies=_cookie(client, "sess_alice"))
    # Second revoke
    r = client.delete(f"/api/account/api-keys/{create['id']}",
                      cookies=_cookie(client, "sess_alice"))
    assert r.status_code == 404


def test_unsigned_revoke_returns_401(app_client):
    main, client, *_ = app_client
    r = client.delete("/api/account/api-keys/1")
    assert r.status_code == 401


# ── last_used_at touches ────────────────────────────────────────────────

def test_last_used_at_updates_on_successful_bearer_use(app_client):
    main, client, _, _email, _ = app_client
    create = client.post("/api/account/api-keys", json={"name": "k"},
                          cookies=_cookie(client, "sess_alice")).json()
    # Before first use: last_used_at is null
    listed = client.get("/api/account/api-keys",
                         cookies=_cookie(client, "sess_alice")).json()["keys"]
    assert listed[0]["last_used_at"] is None

    # Use the key
    client.get("/api/account/me",
               headers={"Authorization": f"Bearer {create['key']}"})

    # After: last_used_at is set
    listed = client.get("/api/account/api-keys",
                         cookies=_cookie(client, "sess_alice")).json()["keys"]
    assert listed[0]["last_used_at"] is not None


# ── Name handling ───────────────────────────────────────────────────────

def test_empty_name_gets_default(app_client):
    main, client, *_ = app_client
    create = client.post("/api/account/api-keys", json={"name": ""},
                          cookies=_cookie(client, "sess_alice")).json()
    assert "unnamed key" in create["name"]


def test_name_over_80_chars_rejected(app_client):
    main, client, *_ = app_client
    r = client.post(
        "/api/account/api-keys",
        json={"name": "x" * 200},
        cookies=_cookie(client, "sess_alice"),
    )
    assert r.status_code == 422  # Pydantic validation
