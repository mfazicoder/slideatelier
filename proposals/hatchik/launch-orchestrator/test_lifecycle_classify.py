"""
Tests for launch_lifecycle.classify_tenant() — the pure-function core
of the daily reconciler. The action ladder depends only on three
inputs (registry tenant + paddle subscription status + now), so we can
exercise every branch without touching the network.

Run from this directory:
    pytest test_lifecycle_classify.py -v
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def ll(monkeypatch, tmp_path):
    """Import launch_lifecycle with an isolated DB / registry."""
    db_path = tmp_path / "signups.db"
    reg_path = tmp_path / "registry.json"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("HATCHIK_LAUNCH_REGISTRY", str(reg_path))
    monkeypatch.setenv("RESEND_API_KEY", "")

    # Bootstrap an empty payments table so _latest_subscription_status
    # doesn't crash when the test asks about a known-empty subscription.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS payments ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "  paddle_subscription_id TEXT, status TEXT)"
        )
        conn.commit()

    sys.path.insert(0, str(Path(__file__).parent))
    if "launch_lifecycle" in sys.modules:
        del sys.modules["launch_lifecycle"]
    return importlib.import_module("launch_lifecycle")


def _now() -> datetime:
    return datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)


def _seed_payment_status(db_path: Path, sub_id: str, status: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO payments (paddle_subscription_id, status) VALUES (?, ?)",
            (sub_id, status),
        )
        conn.commit()


# ── Active tenant → no action ───────────────────────────────────────────

def test_active_tenant_is_noop(ll, tmp_path):
    _seed_payment_status(tmp_path / "signups.db", "sub_001", "active")
    plan = ll.classify_tenant("launch-1",
                              {"status": "live", "paddle_subscription_id": "sub_001"},
                              _now())
    assert plan["action"] == "none"
    assert "healthy" in plan["reason"]


# ── Past-due ladder ─────────────────────────────────────────────────────

def test_past_due_day_1_no_action_yet(ll, tmp_path):
    _seed_payment_status(tmp_path / "signups.db", "sub_001", "past_due")
    now = _now()
    pds = (now - timedelta(days=1)).isoformat()
    plan = ll.classify_tenant("launch-1", {
        "status": "live",
        "paddle_subscription_id": "sub_001",
        "past_due_since": pds,
        "customer_email": "alice@example.com",
    }, now)
    assert plan["action"] == "none"
    assert "Paddle is still retrying" in plan["reason"]


def test_past_due_day_3_notifies(ll, tmp_path):
    _seed_payment_status(tmp_path / "signups.db", "sub_001", "past_due")
    now = _now()
    pds = (now - timedelta(days=4)).isoformat()
    plan = ll.classify_tenant("launch-1", {
        "status": "live",
        "paddle_subscription_id": "sub_001",
        "past_due_since": pds,
        "customer_email": "alice@example.com",
    }, now)
    assert plan["action"] == "notify_past_due"
    assert plan["days"] == 4


def test_past_due_day_7_warns(ll, tmp_path):
    _seed_payment_status(tmp_path / "signups.db", "sub_001", "past_due")
    now = _now()
    pds = (now - timedelta(days=7)).isoformat()
    plan = ll.classify_tenant("launch-1", {
        "status": "live",
        "paddle_subscription_id": "sub_001",
        "past_due_since": pds,
        "customer_email": "alice@example.com",
    }, now)
    assert plan["action"] == "warn_past_due"


def test_past_due_day_10_suspends(ll, tmp_path):
    _seed_payment_status(tmp_path / "signups.db", "sub_001", "past_due")
    now = _now()
    pds = (now - timedelta(days=10)).isoformat()
    plan = ll.classify_tenant("launch-1", {
        "status": "live",
        "paddle_subscription_id": "sub_001",
        "past_due_since": pds,
        "customer_email": "alice@example.com",
    }, now)
    assert plan["action"] == "suspend"


def test_past_due_day_31_decoms(ll, tmp_path):
    _seed_payment_status(tmp_path / "signups.db", "sub_001", "past_due")
    now = _now()
    pds = (now - timedelta(days=31)).isoformat()
    plan = ll.classify_tenant("launch-1", {
        "status": "live",
        "paddle_subscription_id": "sub_001",
        "past_due_since": pds,
        "customer_email": "alice@example.com",
    }, now)
    assert plan["action"] == "decom"


# ── Canceled ladder ─────────────────────────────────────────────────────

def test_canceled_day_0_grace(ll, tmp_path):
    now = _now()
    plan = ll.classify_tenant("launch-1", {
        "status": "live",
        "canceled_at": now.isoformat(),
        "customer_email": "alice@example.com",
    }, now)
    assert plan["action"] == "cancel_grace"


def test_canceled_day_25_warns(ll, tmp_path):
    now = _now()
    canceled = (now - timedelta(days=25)).isoformat()
    plan = ll.classify_tenant("launch-1", {
        "status": "live",
        "canceled_at": canceled,
        "customer_email": "alice@example.com",
    }, now)
    assert plan["action"] == "cancel_warn"


def test_canceled_day_31_decoms(ll, tmp_path):
    now = _now()
    canceled = (now - timedelta(days=31)).isoformat()
    plan = ll.classify_tenant("launch-1", {
        "status": "live",
        "canceled_at": canceled,
        "customer_email": "alice@example.com",
    }, now)
    assert plan["action"] == "decom"


# ── Edge cases ──────────────────────────────────────────────────────────

def test_already_decommissioned_is_skipped(ll):
    plan = ll.classify_tenant("launch-1", {"status": "decommissioned"}, _now())
    assert plan["action"] == "none"
    assert "already" in plan["reason"]


def test_unknown_paddle_status_flags_for_review(ll, tmp_path):
    _seed_payment_status(tmp_path / "signups.db", "sub_001", "paused")
    plan = ll.classify_tenant("launch-1", {
        "status": "live",
        "paddle_subscription_id": "sub_001",
    }, _now())
    assert plan["action"] == "unknown_status"


def test_canceled_takes_priority_over_past_due(ll, tmp_path):
    """If both canceled_at and past_due status are present, cancel wins.

    Customer canceling a past-due account is common — they don't want
    to come back. Treat as cancel grace, not past_due ladder.
    """
    _seed_payment_status(tmp_path / "signups.db", "sub_001", "past_due")
    now = _now()
    plan = ll.classify_tenant("launch-1", {
        "status": "live",
        "paddle_subscription_id": "sub_001",
        "past_due_since": (now - timedelta(days=5)).isoformat(),
        "canceled_at": (now - timedelta(days=2)).isoformat(),
        "customer_email": "alice@example.com",
    }, now)
    assert plan["action"] == "cancel_grace"


# ── reconcile() dry-run ─────────────────────────────────────────────────

def test_reconcile_dry_run_summarises(ll, tmp_path):
    reg_path = tmp_path / "registry.json"
    now = datetime.now(timezone.utc)
    reg = {
        "schema_version": 1,
        "tenants": {
            "launch-1": {"status": "live", "paddle_subscription_id": "sub_active"},
            "launch-2": {"status": "live",
                          "canceled_at": (now - timedelta(days=10)).isoformat(),
                          "customer_email": "x@y.com"},
            "launch-3": {"status": "decommissioned"},
        },
    }
    import json
    reg_path.write_text(json.dumps(reg))
    _seed_payment_status(tmp_path / "signups.db", "sub_active", "active")

    result = ll.reconcile(execute=False, dry_run=True)
    assert result["summary"]["tenants"] == 3
    counts = result["summary"]["counts"]
    assert counts.get("none", 0) >= 2  # active + already decom
    assert counts.get("cancel_grace", 0) == 1
