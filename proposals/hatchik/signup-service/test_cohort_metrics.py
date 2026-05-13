"""
Smoke test for the cohort-metrics dashboard endpoints.

Run from this directory:
    pip install fastapi httpx pydantic email-validator pytest
    pytest test_cohort_metrics.py -v

Exercises:
    - empty DB → all three endpoints return empty/zero shapes without error
    - one signup → cohort appears with total_signups=1
    - admin-token gating (403 without header)
    - cohort_metrics.py pure functions against an in-memory connection
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch):
    """Spin up the FastAPI app against a fresh on-disk SQLite + admin token."""
    tmp = Path(tempfile.mkdtemp(prefix="hatchik-metrics-test-"))
    db_path = tmp / "signups.db"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("HATCHIK_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("HATCHIK_TENANTS_DIR", str(tmp))
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", "")
    monkeypatch.setenv("HATCHIK_ALLOWED_ORIGINS", "https://hatchik.com")

    sys.path.insert(0, str(Path(__file__).parent))
    for mod in ("main", "cohort_metrics"):
        if mod in sys.modules:
            del sys.modules[mod]
    main = importlib.import_module("main")
    main.init_db()
    return TestClient(main.app), db_path


def _seed_signup(db_path: Path, *, tier: str = "sandbox", created_at: str | None = None) -> int:
    """Insert a signup row directly (bypassing the HTTP path so the test
    doesn't depend on Resend/provisioning side-effects)."""
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO signups (created_at, email, first_name, product_name, description, tier, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'new')",
            (created_at, "alice@example.com", "Alice", "Widget", "", tier),
        )
        signup_id = cur.lastrowid
        conn.execute(
            "INSERT INTO tier_transitions (signup_id, from_tier, to_tier, occurred_at, paddle_event_id, notes) "
            "VALUES (?, NULL, ?, ?, NULL, 'initial signup')",
            (signup_id, tier, created_at),
        )
        conn.commit()
    return signup_id


def test_empty_db_returns_empty_cohorts(app_client):
    client, _ = app_client
    headers = {"X-Admin-Token": "test-admin-token"}

    r = client.get("/api/admin/metrics/cohorts", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["cohorts"] == []
    assert body["granularity"] == "week"

    r = client.get("/api/admin/metrics/funnel", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total_signups"] == 0
    assert body["total_active"] == 0
    assert body["sandbox_to_launch_pct"] is None

    r = client.get("/api/admin/metrics/distribution", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total_live"] == 0
    assert body["by_tier"] == {"sandbox": 0, "launch": 0, "growth": 0}


def test_admin_token_required(app_client):
    client, _ = app_client
    for path in (
        "/api/admin/metrics/cohorts",
        "/api/admin/metrics/funnel",
        "/api/admin/metrics/distribution",
    ):
        r = client.get(path)
        assert r.status_code == 403, path


def test_one_signup_appears_in_cohort(app_client):
    client, db_path = app_client
    _seed_signup(db_path, tier="sandbox")
    headers = {"X-Admin-Token": "test-admin-token"}

    r = client.get("/api/admin/metrics/cohorts", headers=headers)
    assert r.status_code == 200
    cohorts = r.json()["cohorts"]
    assert len(cohorts) == 1
    c = cohorts[0]
    assert c["total_signups"] == 1
    assert c["signups_by_initial_tier"]["sandbox"] == 1
    assert c["currently_live"] == 1
    assert c["converted_to_launch_within_30d"] == 0
    assert c["mean_days_to_launch_upgrade"] is None
    assert c["churn_rate_30d"] == 0.0

    r = client.get("/api/admin/metrics/funnel", headers=headers)
    funnel = r.json()
    assert funnel["total_signups"] == 1
    assert funnel["total_active"] == 1
    assert funnel["sandbox_to_launch_pct"] == 0.0


def test_month_granularity_groups_correctly(app_client):
    client, db_path = app_client
    base = datetime(2026, 4, 10, tzinfo=timezone.utc)
    _seed_signup(db_path, created_at=base.isoformat())
    _seed_signup(db_path, created_at=(base + timedelta(days=2)).isoformat())
    _seed_signup(db_path, created_at=(base + timedelta(days=35)).isoformat())
    headers = {"X-Admin-Token": "test-admin-token"}

    r = client.get("/api/admin/metrics/cohorts?granularity=month", headers=headers)
    cohorts = r.json()["cohorts"]
    assert {c["cohort_label"] for c in cohorts} == {"2026-04", "2026-05"}
    by_label = {c["cohort_label"]: c for c in cohorts}
    assert by_label["2026-04"]["total_signups"] == 2
    assert by_label["2026-05"]["total_signups"] == 1


def test_launch_upgrade_transition_counted(app_client):
    client, db_path = app_client
    created = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    upgrade = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    sid = _seed_signup(db_path, tier="sandbox", created_at=created)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO tier_transitions (signup_id, from_tier, to_tier, occurred_at, paddle_event_id, notes) "
            "VALUES (?, 'sandbox', 'launch', ?, 'evt_123', 'paddle webhook')",
            (sid, upgrade),
        )
        conn.commit()

    r = client.get("/api/admin/metrics/funnel", headers={"X-Admin-Token": "test-admin-token"})
    funnel = r.json()
    assert funnel["total_upgraded_to_launch"] == 1
    assert funnel["sandbox_to_launch_pct"] == 100.0


def test_since_filter_limits_cohorts(app_client):
    client, db_path = app_client
    _seed_signup(db_path, created_at="2026-03-01T00:00:00+00:00")
    _seed_signup(db_path, created_at="2026-05-01T00:00:00+00:00")
    headers = {"X-Admin-Token": "test-admin-token"}

    r = client.get(
        "/api/admin/metrics/cohorts?granularity=month&since=2026-04-01",
        headers=headers,
    )
    cohorts = r.json()["cohorts"]
    assert len(cohorts) == 1
    assert cohorts[0]["cohort_label"] == "2026-05"
