"""
Smoke tests for the GitHub handle validation + re-invite endpoint.

Run from this directory:
    pip install fastapi httpx pydantic email-validator pytest pytest-asyncio
    pytest test_github_invite.py -v

Exercises:
    - POST /api/signup with a bogus github handle → 422 github_user_not_found
    - POST /api/signup with empty github handle → 201 (normal flow, no upstream call)
    - POST /api/signup with a real handle (octocat-style mock) → 201
    - POST /api/signup is resilient to GitHub being slow / failing (fail-open)
    - POST /api/account/sandboxes/{slug}/github-invite with valid session → 200
    - Same endpoint with no GitHub username on record → 400
    - Same endpoint owned by a different customer → 403
    - Pre-existing collaborator (422 "already a collaborator") → 200 success
    - 304 no-change → 200 success
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ─── Fixture infrastructure ──────────────────────────────────────────────
@pytest.fixture
def app_client(monkeypatch):
    """Spin up FastAPI against a fresh tenants dir + a single live tenant
    with a GitHub-configured PAT (so the github validation + invite
    paths are exercised, not bypassed)."""
    tmp = Path(tempfile.mkdtemp(prefix="hatchik-github-invite-test-"))
    tenants_dir = tmp / "tenants"
    tenants_dir.mkdir()
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(tmp / "signups.db"))
    monkeypatch.setenv("HATCHIK_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("HATCHIK_TENANTS_DIR", str(tenants_dir))
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "")
    monkeypatch.setenv("HATCHIK_ALLOWED_ORIGINS", "https://hatchik.com")
    monkeypatch.setenv("HATCHIK_GEO_IP_DISABLED", "1")
    monkeypatch.setenv("HATCHIK_TURNSTILE_SECRET", "")  # dev mode: skip Turnstile
    monkeypatch.setenv("HATCHIK_GITHUB_TOKEN", "fake-pat-for-tests")
    monkeypatch.setenv("HATCHIK_GITHUB_ORG", "hatchik-sandboxes-test")

    sys.path.insert(0, str(Path(__file__).parent))
    for mod in ("main", "cohort_metrics"):
        if mod in sys.modules:
            del sys.modules[mod]
    main = importlib.import_module("main")
    main.init_db()

    # Seed the registry with one tenant owned by owner@example.com.
    slug = "acme"
    registry = {
        "version": 1,
        "tenants": {
            slug: {
                "slug": slug,
                "port": 18000,
                "email": "owner@example.com",
                "product_name": "Acme",
                "signup_id": 1,
                "status": "live",
                "created_at": 0,
                "url": "https://acme.hatchik.com",
                "repo_url": "https://github.com/hatchik-sandboxes-test/acme",
            },
            "other": {
                "slug": "other",
                "port": 18001,
                "email": "someone-else@example.com",
                "product_name": "Other",
                "signup_id": 2,
                "status": "live",
                "created_at": 0,
                "url": "https://other.hatchik.com",
                "repo_url": "https://github.com/hatchik-sandboxes-test/other",
            },
        },
    }
    (tenants_dir / "registry.json").write_text(json.dumps(registry))

    client = TestClient(main.app)
    return client, main, slug


def _stub_github_user_lookup(main, monkeypatch, exists: bool = True, reason: str = "ok"):
    """Replace _github_user_exists with a controllable stub."""
    async def fake(handle):  # noqa: ANN001
        return exists, reason
    monkeypatch.setattr(main, "_github_user_exists", fake)


def _stub_invite(main, monkeypatch, status: str = "invitation_sent", http_status: int = 201):
    """Replace _invite_github_collaborator with a controllable stub."""
    async def fake(slug, handle):  # noqa: ANN001
        return {"status": status, "http_status": http_status}
    monkeypatch.setattr(main, "_invite_github_collaborator", fake)


def _signup_payload(**overrides):
    base = {
        "first_name": "Alice",
        "email": "alice@example.com",
        "product_name": "TestApp",
        "description": "A test app",
        "tier": "sandbox",
    }
    base.update(overrides)
    return base


def _make_session(main, email: str) -> str:
    """Create a session row directly in the DB and return the cookie value."""
    import secrets, sqlite3
    session_id = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(main.DB_PATH) as conn:
        # Seed a signups row so /api/account/me works downstream.
        conn.execute(
            "INSERT INTO signups (created_at, email, first_name, product_name, "
            "description, tier, region, domain_choice, ip_address, user_agent, "
            "github_username, status) VALUES (?, ?, ?, ?, ?, 'sandbox', NULL, NULL, "
            "'127.0.0.1', 'test', ?, 'live')",
            (
                now.isoformat(), email, "Owner", "Acme", "test app",
                None,
            ),
        )
        conn.execute(
            "INSERT INTO sessions (session_id, email, created_at, expires_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                session_id, email, now.isoformat(),
                (now + timedelta(days=1)).isoformat(),
                now.isoformat(),
            ),
        )
        conn.commit()
    return session_id


def _set_github_username(main, email: str, handle: str | None) -> None:
    """Update the signups row for the given email."""
    import sqlite3
    with sqlite3.connect(main.DB_PATH) as conn:
        conn.execute(
            "UPDATE signups SET github_username = ? WHERE LOWER(email) = ?",
            (handle, email.lower()),
        )
        conn.commit()


# ─── /api/signup: handle existence check ─────────────────────────────────
def test_signup_with_bogus_github_handle_returns_422(app_client, monkeypatch):
    """POST /api/signup with github_username=does-not-exist-12345 → 422."""
    client, main, _slug = app_client
    _stub_github_user_lookup(main, monkeypatch, exists=False, reason="not_found")
    r = client.post(
        "/api/signup",
        json=_signup_payload(
            email="bogus-handle@example.com",
            github_username="does-not-exist-12345",
        ),
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "github_user_not_found"
    assert "does-not-exist-12345" in body["message"]
    assert "product name" in body["message"]


def test_signup_with_empty_github_handle_succeeds(app_client, monkeypatch):
    """POST /api/signup with empty github_username → 201 (no upstream call)."""
    client, main, _slug = app_client
    called = {"count": 0}

    async def fake(handle):  # noqa: ANN001
        called["count"] += 1
        return True, "ok"

    monkeypatch.setattr(main, "_github_user_exists", fake)
    # Skip the queue-dispatch background work so the test doesn't hang.
    async def noop(signup_id):  # noqa: ANN001
        return "dispatched"
    monkeypatch.setattr(main, "enqueue_or_dispatch", noop)

    r = client.post(
        "/api/signup",
        json=_signup_payload(email="blank-handle@example.com", github_username=""),
    )
    assert r.status_code == 201, r.text
    # The handle-existence check should be a no-op for an empty string —
    # we never even reach the GitHub API path.
    assert called["count"] == 0


def test_signup_with_real_handle_succeeds_when_github_confirms_existence(app_client, monkeypatch):
    """POST /api/signup with github_username=octocat → 201 (mock 200 from GitHub)."""
    client, main, _slug = app_client
    _stub_github_user_lookup(main, monkeypatch, exists=True, reason="ok")
    async def noop(signup_id):  # noqa: ANN001
        return "dispatched"
    monkeypatch.setattr(main, "enqueue_or_dispatch", noop)

    r = client.post(
        "/api/signup",
        json=_signup_payload(email="octocat-fan@example.com", github_username="octocat"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["ok"] is True


def test_signup_is_resilient_to_github_being_slow_or_failing(app_client, monkeypatch):
    """If the GitHub lookup itself errors (timeout, 5xx, rate-limit),
    the check fails open and the signup still goes through."""
    client, main, _slug = app_client
    # exists=True, reason="skipped" mirrors the fail-open branch in
    # _github_user_exists (timeout / 5xx / rate-limit).
    _stub_github_user_lookup(main, monkeypatch, exists=True, reason="skipped")
    async def noop(signup_id):  # noqa: ANN001
        return "dispatched"
    monkeypatch.setattr(main, "enqueue_or_dispatch", noop)

    r = client.post(
        "/api/signup",
        json=_signup_payload(
            email="failopen@example.com",
            github_username="anyone-really",
        ),
    )
    assert r.status_code == 201, r.text


# ─── /api/account/sandboxes/{slug}/github-invite ─────────────────────────
def test_reinvite_happy_path_with_valid_session_and_handle(app_client, monkeypatch):
    """Valid session + valid handle on the customer's signup row → 200."""
    client, main, slug = app_client
    session_id = _make_session(main, "owner@example.com")
    _set_github_username(main, "owner@example.com", "alice")
    _stub_invite(main, monkeypatch, status="invitation_sent", http_status=201)

    r = client.post(
        f"/api/account/sandboxes/{slug}/github-invite",
        cookies={"hatchik_session": session_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["invited"] == "alice"
    assert body["status"] == "invitation_sent"


def test_reinvite_no_github_username_on_record_returns_400(app_client, monkeypatch):
    """No GitHub username on the signup row → 400 no_github_username."""
    client, main, slug = app_client
    session_id = _make_session(main, "no-handle@example.com")
    # Re-route the registry tenant so "no-handle@example.com" owns it.
    reg_path = Path(main._load_registry.__globals__["os"].environ["HATCHIK_TENANTS_DIR"]) / "registry.json"
    reg = json.loads(reg_path.read_text())
    reg["tenants"][slug]["email"] = "no-handle@example.com"
    reg_path.write_text(json.dumps(reg))

    r = client.post(
        f"/api/account/sandboxes/{slug}/github-invite",
        cookies={"hatchik_session": session_id},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "no_github_username"


def test_reinvite_for_sandbox_owned_by_someone_else_returns_403(app_client, monkeypatch):
    """Owner check: a session whose email doesn't own this slug → 403."""
    client, main, _slug = app_client
    # Create a session for someone OTHER than the slug's owner.
    session_id = _make_session(main, "intruder@example.com")
    _set_github_username(main, "intruder@example.com", "intruder-handle")
    # Stub the upstream invite so a passing owner check would 200 — that
    # way if the owner check leaks, the test loudly fails on a 200.
    _stub_invite(main, monkeypatch, status="invitation_sent", http_status=201)

    r = client.post(
        "/api/account/sandboxes/acme/github-invite",
        cookies={"hatchik_session": session_id},
    )
    assert r.status_code == 403, r.text


def test_reinvite_treats_already_collaborator_as_success(app_client, monkeypatch):
    """GitHub returning 422 "already a collaborator" → 200 with status=already_collaborator."""
    client, main, slug = app_client
    session_id = _make_session(main, "owner@example.com")
    _set_github_username(main, "owner@example.com", "alice")
    _stub_invite(main, monkeypatch, status="already_collaborator", http_status=422)

    r = client.post(
        f"/api/account/sandboxes/{slug}/github-invite",
        cookies={"hatchik_session": session_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "already_collaborator"


def test_reinvite_treats_304_no_change_as_success(app_client, monkeypatch):
    """GitHub returning 304 → 200 with status=already_collaborator."""
    client, main, slug = app_client
    session_id = _make_session(main, "owner@example.com")
    _set_github_username(main, "owner@example.com", "alice")
    _stub_invite(main, monkeypatch, status="already_collaborator", http_status=304)

    r = client.post(
        f"/api/account/sandboxes/{slug}/github-invite",
        cookies={"hatchik_session": session_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "already_collaborator"


def test_reinvite_surfaces_user_not_found(app_client, monkeypatch):
    """GitHub 404 → 404 github_user_not_found with the handle in the message."""
    client, main, slug = app_client
    session_id = _make_session(main, "owner@example.com")
    _set_github_username(main, "owner@example.com", "phantom-handle")
    _stub_invite(main, monkeypatch, status="not_found", http_status=404)

    r = client.post(
        f"/api/account/sandboxes/{slug}/github-invite",
        cookies={"hatchik_session": session_id},
    )
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["error"] == "github_user_not_found"
    assert "phantom-handle" in body["message"]


def test_reinvite_surfaces_permission_error_with_founder_message(app_client, monkeypatch):
    """GitHub 403 → 403 github_permission_denied with founder-on-it copy."""
    client, main, slug = app_client
    session_id = _make_session(main, "owner@example.com")
    _set_github_username(main, "owner@example.com", "alice")
    _stub_invite(main, monkeypatch, status="forbidden", http_status=403)

    r = client.post(
        f"/api/account/sandboxes/{slug}/github-invite",
        cookies={"hatchik_session": session_id},
    )
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["error"] == "github_permission_denied"
    assert "founder" in body["message"].lower()


def test_reinvite_requires_session(app_client):
    """No session cookie → 401."""
    client, _main, slug = app_client
    r = client.post(f"/api/account/sandboxes/{slug}/github-invite")
    assert r.status_code == 401
