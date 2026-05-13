"""
Smoke tests for the per-tenant redeploy endpoint.

Run from this directory:
    pip install fastapi httpx pydantic email-validator pytest pytest-asyncio
    pytest test_redeploy.py -v

Exercises:
    - 403 with no token / bad token / bad HMAC signature
    - 200 happy path (subprocess stubbed via monkeypatch)
    - 200 via X-Hub-Signature-256 (HMAC of body verified against deploy_token)
    - 404 for unknown slug
    - 410 for archived / decommissioned tenants
    - 429 when a redeploy is already in flight (per-slug lock)
    - 429 when the rate-limit ceiling is hit
    - HMAC tamper test (good token, mutated body → rejected)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import json
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch):
    """Spin up FastAPI against a fresh tenants dir + a single live tenant."""
    tmp = Path(tempfile.mkdtemp(prefix="hatchik-redeploy-test-"))
    tenants_dir = tmp / "tenants"
    tenants_dir.mkdir()
    log_dir = tmp / "logs"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(tmp / "signups.db"))
    monkeypatch.setenv("HATCHIK_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("HATCHIK_TENANTS_DIR", str(tenants_dir))
    monkeypatch.setenv("HATCHIK_REDEPLOY_LOG_DIR", str(log_dir))
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "")
    monkeypatch.setenv("HATCHIK_ALLOWED_ORIGINS", "https://hatchik.com")
    monkeypatch.setenv("HATCHIK_REDEPLOY_RATE_LIMIT_MAX", "3")
    monkeypatch.setenv("HATCHIK_REDEPLOY_RATE_LIMIT_WINDOW_SECONDS", "300")

    sys.path.insert(0, str(Path(__file__).parent))
    for mod in ("main", "cohort_metrics"):
        if mod in sys.modules:
            del sys.modules[mod]
    main = importlib.import_module("main")
    main.init_db()

    # Seed registry: one live tenant + one archived tenant.
    deploy_token = "test-deploy-token-abcdef"
    slug = "acme"
    tenant_dir = tenants_dir / slug
    tenant_dir.mkdir()
    archived_slug = "ghost"
    (tenants_dir / archived_slug).mkdir()
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
                "deploy_token": deploy_token,
            },
            archived_slug: {
                "slug": archived_slug,
                "port": 18001,
                "email": "x@example.com",
                "product_name": "Ghost",
                "signup_id": 2,
                "status": "archived",
                "created_at": 0,
                "url": "https://ghost.hatchik.com",
                "deploy_token": "ghost-token",
            },
        },
    }
    (tenants_dir / "registry.json").write_text(json.dumps(registry))

    client = TestClient(main.app)
    return client, main, slug, deploy_token, archived_slug


def _stub_subprocess_ok(main, monkeypatch, sha="deadbee"):
    async def fake(slug, target):
        # Touch the per-tenant log so log_tail tests still work.
        await main._append_redeploy_log(slug, "stub: pretend success")
        return True, sha

    monkeypatch.setattr(main, "_run_redeploy_subprocess", fake)


def _stub_subprocess_fail(main, monkeypatch):
    async def fake(slug, target):
        await main._append_redeploy_log(slug, "stub: pretend failure")
        return False, None

    monkeypatch.setattr(main, "_run_redeploy_subprocess", fake)


def test_no_auth_returns_403(app_client):
    client, main, slug, _token, _arch = app_client
    r = client.post(f"/api/tenants/{slug}/redeploy")
    assert r.status_code == 403


def test_bad_deploy_token_returns_403(app_client):
    client, main, slug, _token, _arch = app_client
    r = client.post(
        f"/api/tenants/{slug}/redeploy",
        headers={"X-Deploy-Token": "definitely-wrong"},
    )
    assert r.status_code == 403


def test_unknown_slug_returns_404(app_client):
    client, main, _slug, _token, _arch = app_client
    r = client.post(
        "/api/tenants/nope/redeploy",
        headers={"X-Deploy-Token": "anything"},
    )
    assert r.status_code == 404


def test_archived_returns_410(app_client):
    client, main, _slug, _token, archived = app_client
    r = client.post(
        f"/api/tenants/{archived}/redeploy",
        headers={"X-Deploy-Token": "ghost-token"},
    )
    assert r.status_code == 410


def test_happy_path_via_deploy_token(app_client, monkeypatch):
    client, main, slug, token, _arch = app_client
    _stub_subprocess_ok(main, monkeypatch, sha="cafef00")
    r = client.post(
        f"/api/tenants/{slug}/redeploy",
        headers={"X-Deploy-Token": token},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["slug"] == slug
    assert body["commit"] == "cafef00"
    assert body["via"] == "ai-tool"
    assert body["deployed_at"]

    # Registry was updated.
    reg = main._load_registry()
    assert reg["tenants"][slug]["last_redeploy_commit"] == "cafef00"
    assert reg["tenants"][slug]["last_redeploy_via"] == "ai-tool"


def test_happy_path_via_github_webhook_signature(app_client, monkeypatch):
    client, main, slug, token, _arch = app_client
    _stub_subprocess_ok(main, monkeypatch, sha="abc1234")
    body = json.dumps({"ref": "refs/heads/main", "after": "abc1234abc1234"}).encode()
    sig = "sha256=" + hmac.new(token.encode(), body, hashlib.sha256).hexdigest()
    r = client.post(
        f"/api/tenants/{slug}/redeploy",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["via"] == "github-webhook"


def test_github_signature_rejects_tampered_body(app_client, monkeypatch):
    client, main, slug, token, _arch = app_client
    _stub_subprocess_ok(main, monkeypatch)
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    sig = "sha256=" + hmac.new(token.encode(), body, hashlib.sha256).hexdigest()
    tampered = body.replace(b"main", b"evil")
    r = client.post(
        f"/api/tenants/{slug}/redeploy",
        content=tampered,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
        },
    )
    assert r.status_code == 403


def test_subprocess_failure_returns_500_with_log_tail(app_client, monkeypatch):
    client, main, slug, token, _arch = app_client
    _stub_subprocess_fail(main, monkeypatch)
    r = client.post(
        f"/api/tenants/{slug}/redeploy",
        headers={"X-Deploy-Token": token},
    )
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["ok"] is False
    assert "log_tail" in detail
    assert "pretend failure" in detail["log_tail"]


def test_concurrent_redeploy_returns_429(app_client, monkeypatch):
    """When a redeploy is already in-flight, the next call gets 429."""
    client, main, slug, token, _arch = app_client

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_subprocess(slug, target):
        started.set()
        await release.wait()
        await main._append_redeploy_log(slug, "stub: slow done")
        return True, "slowdone"

    monkeypatch.setattr(main, "_run_redeploy_subprocess", slow_subprocess)

    # Kick off two requests concurrently against the same event loop
    # via the underlying ASGI app — TestClient doesn't give us concurrent
    # in-flight requests on its own.
    import httpx

    async def go():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            first = asyncio.create_task(
                ac.post(
                    f"/api/tenants/{slug}/redeploy",
                    headers={"X-Deploy-Token": token},
                )
            )
            await started.wait()
            second = await ac.post(
                f"/api/tenants/{slug}/redeploy",
                headers={"X-Deploy-Token": token},
            )
            release.set()
            first_resp = await first
            return first_resp.status_code, second.status_code

    first_status, second_status = asyncio.run(go())
    assert first_status == 200
    assert second_status == 429


def test_rate_limit_kicks_in(app_client, monkeypatch):
    """After RATE_LIMIT_MAX successful redeploys in the window, the next gets 429."""
    client, main, slug, token, _arch = app_client
    _stub_subprocess_ok(main, monkeypatch)
    # Limit is 3 (set in the fixture). Four sequential calls = the
    # fourth must be rate-limited.
    for _ in range(3):
        r = client.post(
            f"/api/tenants/{slug}/redeploy",
            headers={"X-Deploy-Token": token},
        )
        assert r.status_code == 200, r.text
    r = client.post(
        f"/api/tenants/{slug}/redeploy",
        headers={"X-Deploy-Token": token},
    )
    assert r.status_code == 429
    assert "rate limit" in r.json()["detail"].lower()


def test_hmac_signature_helper_round_trip(app_client):
    """Direct unit test of the signature verifier."""
    _client, main, _slug, _token, _arch = app_client
    body = b'{"hello":"world"}'
    secret = "s3cret"
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert main._verify_github_signature(body, good, secret) is True
    assert main._verify_github_signature(body, good, "wrong-secret") is False
    assert main._verify_github_signature(body + b"!", good, secret) is False
    assert main._verify_github_signature(body, None, secret) is False
    assert main._verify_github_signature(body, "garbage", secret) is False
    assert main._verify_github_signature(body, "sha1=abc", secret) is False
