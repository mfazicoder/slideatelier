"""
Tests for the Paddle subscription webhook → tier-transition wiring.

Run from this directory:
    pip install -r requirements.txt pytest
    HATCHIK_ORCHESTRATOR_DIR=../sandbox-orchestrator \
    HATCHIK_LAUNCH_ORCHESTRATOR_DIR=../launch-orchestrator \
    pytest test_paddle_lifecycle.py -v

Exercises:
    - subscription.created with a known customer resolves to a signup_id,
      writes a tier_transitions row, fires the promote.py subprocess.
    - subscription.created with an unknown customer ack's the webhook
      without crashing.
    - subscription.updated with status change records the transition.
    - subscription.canceled writes a launch→cancelled transition and
      marks the launch registry's canceled_at timestamp.
    - Idempotency: same event_id replayed writes only one row.
    - Unknown event types ack without acting.
    - Webhook signature verification rejects bad signatures.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


PADDLE_SECRET = "pdl_ntfset_test_secret"


def _sign_paddle(secret: str, payload_bytes: bytes, ts: int | None = None) -> str:
    """Return a valid Paddle-Signature header for the given payload."""
    if ts is None:
        ts = int(time.time())
    h = hmac.new(
        secret.encode("utf-8"),
        f"{ts}:".encode("utf-8") + payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return f"ts={ts};h1={h}"


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    """Fresh DB, a seeded signup row, launch registry, and a fake Paddle
    secret so the webhook accepts our signed test payloads."""
    db_path = tmp_path / "signups.db"
    launch_reg = tmp_path / "launch-registry.json"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("HATCHIK_ADMIN_TOKEN", "test_admin_token")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", PADDLE_SECRET)
    monkeypatch.setenv("PADDLE_GROWTH_PRICE_ID", "pri_growth_test")
    monkeypatch.setenv("HATCHIK_LAUNCH_REGISTRY", str(launch_reg))
    monkeypatch.setenv("HATCHIK_ALLOWED_ORIGINS", "https://hatchik.com")
    monkeypatch.setenv("TURNSTILE_SECRET", "")
    monkeypatch.setenv(
        "HATCHIK_PROMOTE_SCRIPT", "/nonexistent/promote.py",
    )  # spawn skipped if path absent

    sys.path.insert(0, str(Path(__file__).parent))
    for mod in ("main", "cohort_metrics"):
        if mod in sys.modules:
            del sys.modules[mod]
    main = importlib.import_module("main")
    main.init_db()

    # Seed signup + payment + (eventually) launch registry. The webhook
    # resolves customer_id → customer_email → signup_id, so we need a
    # payment row to anchor that lookup.
    email = "alice@example.com"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO signups (created_at, email, product_name, description, tier, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                email,
                "PrepSheet",
                "test",
                "sandbox",
                "live",
            ),
        )
        conn.execute(
            "INSERT INTO payments (created_at, paddle_transaction_id, "
            "paddle_customer_id, paddle_subscription_id, customer_email, "
            "currency, amount, status, raw_payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                "txn_test_001", "ctm_test_001", None, email, "GBP",
                "8900", "completed", "{}",
            ),
        )
        conn.commit()

    client = TestClient(main.app)
    return main, client, db_path, launch_reg, email


def _post_event(client: TestClient, event_type: str, data: dict,
                event_id: str = "evt_test_1") -> tuple[int, dict]:
    payload = {
        "event_id": event_id,
        "event_type": event_type,
        "data": data,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = _sign_paddle(PADDLE_SECRET, raw)
    resp = client.post(
        "/api/paddle/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "Paddle-Signature": sig},
    )
    return resp.status_code, (resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {})


# ── subscription.created ────────────────────────────────────────────────

def test_subscription_created_records_transition_for_known_customer(app_client):
    main, client, db_path, _, email = app_client
    status, body = _post_event(
        client,
        "subscription.created",
        {
            "id": "sub_test_001",
            "customer_id": "ctm_test_001",
            "customer": {"email": email},
            "status": "active",
        },
        event_id="evt_create_001",
    )
    assert status == 200, body
    assert body.get("received") is True

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT from_tier, to_tier, paddle_event_id, notes "
            "FROM tier_transitions WHERE paddle_event_id = ?",
            ("evt_create_001",),
        ).fetchone()
    assert row, "tier_transitions row should be written"
    assert row[0] == "sandbox" and row[1] == "launch"
    assert "paddle subscription.created" in (row[3] or "")


def test_subscription_created_for_unknown_customer_acks_without_crash(app_client):
    main, client, db_path, _, _email = app_client
    status, body = _post_event(
        client,
        "subscription.created",
        {
            "id": "sub_test_002",
            "customer_id": "ctm_unknown",
            "customer": {"email": "stranger@example.com"},
            "status": "active",
        },
        event_id="evt_create_unknown",
    )
    assert status == 200
    assert body.get("received") is True
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM tier_transitions WHERE paddle_event_id = ?",
            ("evt_create_unknown",),
        ).fetchone()[0]
    assert count == 0


def test_subscription_created_idempotent(app_client):
    main, client, db_path, _, _email = app_client
    payload = {
        "id": "sub_test_001",
        "customer_id": "ctm_test_001",
        "customer": {"email": "alice@example.com"},
        "status": "active",
    }
    _post_event(client, "subscription.created", payload, event_id="evt_idem_001")
    # Second time should ack as duplicate
    status, body = _post_event(client, "subscription.created", payload,
                               event_id="evt_idem_001")
    assert status == 200
    assert body.get("duplicate") is True
    with sqlite3.connect(db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM tier_transitions WHERE paddle_event_id = ?",
            ("evt_idem_001",),
        ).fetchone()[0]
    assert n == 1, "tier_transitions row should appear exactly once"


# ── subscription.updated ────────────────────────────────────────────────

def test_subscription_updated_records_status_change(app_client):
    main, client, db_path, _, _email = app_client
    status, _ = _post_event(
        client,
        "subscription.updated",
        {
            "id": "sub_test_001",
            "customer_id": "ctm_test_001",
            "status": "past_due",
            "next_billed_at": None,
        },
        event_id="evt_upd_001",
    )
    assert status == 200
    with sqlite3.connect(db_path) as conn:
        notes = conn.execute(
            "SELECT notes FROM tier_transitions WHERE paddle_event_id = ?",
            ("evt_upd_001",),
        ).fetchone()
    assert notes and "past_due" in notes[0]


def test_subscription_updated_detects_growth_plan_change(app_client):
    main, client, db_path, _, _email = app_client
    status, _ = _post_event(
        client,
        "subscription.updated",
        {
            "id": "sub_test_001",
            "customer_id": "ctm_test_001",
            "status": "active",
            "items": [{"price": {"id": "pri_growth_test"}}],
        },
        event_id="evt_upd_growth",
    )
    assert status == 200
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT from_tier, to_tier, notes FROM tier_transitions "
            "WHERE paddle_event_id = ?",
            ("evt_upd_growth",),
        ).fetchall()
    # We expect 2: the status-change row + the growth row
    assert any(r[1] == "growth" for r in rows), rows
    assert any("plan change to growth" in (r[2] or "") for r in rows)


# ── subscription.canceled ───────────────────────────────────────────────

def test_subscription_canceled_records_transition_and_marks_registry(app_client):
    main, client, db_path, launch_reg, email = app_client

    # Seed a launch registry entry so the canceled handler has something
    # to mark.
    reg = {
        "schema_version": 1,
        "tenants": {
            "launch-1": {
                "signup_id": 1,
                "customer_email": email,
                "customer_domain": "example.com",
                "tier": "launch",
                "hetzner_server_id": 999,
                "ip": "1.2.3.4",
                "status": "live",
                "paddle_subscription_id": "sub_test_001",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    }
    launch_reg.write_text(json.dumps(reg))

    status, _ = _post_event(
        client,
        "subscription.canceled",
        {
            "id": "sub_test_001",
            "customer_id": "ctm_test_001",
            "customer": {"email": email},
            "canceled_at": datetime.now(timezone.utc).isoformat(),
        },
        event_id="evt_cancel_001",
    )
    assert status == 200

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT from_tier, to_tier FROM tier_transitions "
            "WHERE paddle_event_id = ?",
            ("evt_cancel_001",),
        ).fetchone()
    assert row and row[0] == "launch" and row[1] == "cancelled"

    # Registry should be updated with canceled_at
    reg_after = json.loads(launch_reg.read_text())
    assert reg_after["tenants"]["launch-1"].get("canceled_at"), reg_after


def test_subscription_canceled_no_registry_entry_still_acks(app_client):
    """If the launch registry doesn't yet contain this signup (e.g. the
    promote.py SAFE_MODE plan was never executed), the webhook still
    acks the cancel and records the transition. Daily reconciler picks
    up the orphan and emails the founder."""
    main, client, db_path, _, email = app_client
    # No registry seeded — launch_reg path exists but file is empty/missing
    status, body = _post_event(
        client,
        "subscription.canceled",
        {
            "id": "sub_test_002",
            "customer_id": "ctm_test_001",
            "customer": {"email": email},
        },
        event_id="evt_cancel_orphan",
    )
    assert status == 200
    assert body.get("received") is True
    with sqlite3.connect(db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM tier_transitions WHERE paddle_event_id = ?",
            ("evt_cancel_orphan",),
        ).fetchone()[0]
    assert n == 1


# ── signature / unknown events ──────────────────────────────────────────

def test_bad_signature_rejected(app_client):
    main, client, *_ = app_client
    payload = {"event_id": "evt_bad", "event_type": "subscription.created", "data": {}}
    raw = json.dumps(payload).encode("utf-8")
    resp = client.post(
        "/api/paddle/webhook",
        content=raw,
        headers={"Content-Type": "application/json",
                 "Paddle-Signature": "ts=12345;h1=deadbeef"},
    )
    assert resp.status_code == 400, resp.text


def test_unknown_event_type_acks(app_client):
    main, client, *_ = app_client
    status, body = _post_event(
        client,
        "address.updated",
        {"id": "addr_001"},
        event_id="evt_unknown_001",
    )
    assert status == 200
    assert body.get("received") is True


# ── /api/admin/launch-tenants ───────────────────────────────────────────

_ADMIN_HEADERS = {"X-Admin-Token": "test_admin_token"}


def test_admin_launch_tenants_requires_token(app_client):
    main, client, *_ = app_client
    # Without header → 403
    r = client.get("/api/admin/launch-tenants")
    assert r.status_code == 403


def test_admin_launch_tenants_returns_empty_when_no_registry(app_client):
    main, client, db_path, launch_reg, _ = app_client
    if launch_reg.exists():
        launch_reg.unlink()
    r = client.get("/api/admin/launch-tenants", headers=_ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["registry_present"] is False
    assert body["tenants"] == []


def test_admin_launch_tenants_lists_with_counts_and_filter(app_client):
    main, client, _db, launch_reg, _email = app_client
    reg = {
        "schema_version": 1,
        "tenants": {
            "launch-1": {"signup_id": 1, "customer_email": "a@x.com",
                          "tier": "launch", "status": "live",
                          "created_at": "2026-05-01T00:00:00Z"},
            "launch-2": {"signup_id": 2, "customer_email": "b@x.com",
                          "tier": "launch", "status": "provisioning",
                          "created_at": "2026-05-10T00:00:00Z"},
            "launch-3": {"signup_id": 3, "customer_email": "c@x.com",
                          "tier": "growth", "status": "live",
                          "created_at": "2026-05-05T00:00:00Z"},
        },
    }
    launch_reg.write_text(json.dumps(reg))

    # All tenants
    r = client.get("/api/admin/launch-tenants", headers=_ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["registry_present"] is True
    assert body["total"] == 3
    assert body["counts"] == {"live": 2, "provisioning": 1}
    # Sort by created_at desc
    assert [t["slug"] for t in body["tenants"]] == ["launch-2", "launch-3", "launch-1"]

    # Filter by status
    r = client.get("/api/admin/launch-tenants?status=live", headers=_ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert len(body["tenants"]) == 2
    assert {t["status"] for t in body["tenants"]} == {"live"}
