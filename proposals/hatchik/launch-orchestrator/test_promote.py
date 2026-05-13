"""
Tests for promote.py — Sandbox → Launch migration.

Focus on the SAFE_MODE path (no Hetzner / Cloudflare network) plus the
plan-computation pure function. Real --execute is covered by the
end-to-end smoke test in LIFECYCLE_SMOKE_TEST.md.

Run from this directory:
    pytest test_promote.py -v
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Isolate DB + registries; promote.py picks them up from env."""
    db_path = tmp_path / "signups.db"
    launch_reg = tmp_path / "launch-registry.json"
    sandbox_reg = tmp_path / "sandbox-registry.json"

    # Mirror signup-service init_db() shape for the columns promote.py uses
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE signups (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              email TEXT NOT NULL,
              first_name TEXT,
              product_name TEXT,
              description TEXT,
              tier TEXT NOT NULL,
              region TEXT,
              domain_choice TEXT,
              ip_address TEXT,
              user_agent TEXT,
              status TEXT NOT NULL DEFAULT 'new',
              github_username TEXT,
              country_code TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE tier_transitions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              signup_id INTEGER NOT NULL,
              from_tier TEXT,
              to_tier TEXT NOT NULL,
              occurred_at TEXT NOT NULL,
              paddle_event_id TEXT,
              notes TEXT
            )
        """)
        conn.execute(
            "INSERT INTO signups "
            "(created_at, email, first_name, product_name, description, tier, "
            "region, domain_choice, status, github_username) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                "alice@example.com", "Alice", "PrepSheet", "test app",
                "sandbox", "eu-central", "byo", "live", "alicegit",
            ),
        )
        # Sandbox registry mocking
        conn.commit()
    sandbox_reg.write_text(json.dumps({
        "tenants": {"prepsheet": {"signup_id": 1, "status": "live"}},
    }))

    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("HATCHIK_LAUNCH_REGISTRY", str(launch_reg))
    monkeypatch.setenv("HATCHIK_SANDBOX_REGISTRY", str(sandbox_reg))
    monkeypatch.setenv("RESEND_API_KEY", "")  # stdout fallback
    monkeypatch.setenv("HATCHIK_LOG_DIR", str(tmp_path / "logs"))

    sys.path.insert(0, str(Path(__file__).parent))
    if "promote" in sys.modules:
        del sys.modules["promote"]
    return importlib.import_module("promote"), db_path, launch_reg


def test_fetch_signup_round_trip(env):
    promote, db_path, _ = env
    s = promote._fetch_signup(1)
    assert s is not None
    assert s["email"] == "alice@example.com"
    assert s["tier"] == "sandbox"
    assert s["region"] == "eu-central"


def test_fetch_signup_missing(env):
    promote, _, _ = env
    assert promote._fetch_signup(9999) is None


def test_find_sandbox_slug(env):
    promote, _, _ = env
    assert promote._find_sandbox_slug(1) == "prepsheet"
    assert promote._find_sandbox_slug(9999) is None


def test_compute_plan_includes_all_steps(env):
    promote, _, _ = env
    signup = promote._fetch_signup(1)
    plan = promote.compute_plan(signup)
    assert plan["signup_id"] == 1
    assert plan["customer_email"] == "alice@example.com"
    assert plan["region"] == "eu-central"
    assert plan["hetzner_location"] == "nbg1"
    assert plan["sandbox_slug"] == "prepsheet"
    assert plan["server_type"] == "cax31"
    assert any("Hetzner" in s for s in plan["steps"])
    assert any("decommission" in s.lower() for s in plan["steps"])


def test_record_transition_writes_row(env):
    promote, db_path, _ = env
    promote._record_transition(1, "sandbox", "launch", "test transition",
                                paddle_event_id="evt_x")
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT signup_id, from_tier, to_tier, paddle_event_id, notes "
            "FROM tier_transitions"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0] == (1, "sandbox", "launch", "evt_x", "test transition")


def test_registry_save_load_round_trip(env, tmp_path):
    promote, _, launch_reg = env
    promote._save_registry({"schema_version": 1, "tenants": {"abc": {"signup_id": 2}}})
    reg = promote._load_registry()
    assert reg["tenants"]["abc"]["signup_id"] == 2
    # And the file is valid JSON
    assert json.loads(launch_reg.read_text())["tenants"]["abc"]["signup_id"] == 2


def test_safe_mode_main_records_transition_without_executing(env, capsys, monkeypatch):
    """promote.py --signup-id 1 (no --execute) should:
       - record a tier_transitions row
       - NOT call Hetzner / Cloudflare
       - emit a plan summary
       - exit 0
    """
    promote, db_path, launch_reg = env
    monkeypatch.setattr(sys, "argv", ["promote.py", "--signup-id", "1", "--json"])
    rc = promote.main()
    assert rc == 0

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT to_tier, notes FROM tier_transitions"
        ).fetchall()
    assert any(r[0] == "launch" and "SAFE_MODE" in (r[1] or "") for r in rows), rows

    # Registry must remain empty (nothing was provisioned)
    if launch_reg.exists():
        reg = json.loads(launch_reg.read_text())
        assert not reg.get("tenants"), \
            "SAFE_MODE should not write to the launch registry"

    # Stdout has the JSON plan
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert parsed["mode"] == "SAFE_MODE"


def test_main_rejects_unknown_signup(env, monkeypatch):
    promote, *_ = env
    monkeypatch.setattr(sys, "argv", ["promote.py", "--signup-id", "9999", "--json"])
    rc = promote.main()
    assert rc == 2


def test_mark_live_flips_tenant(env, monkeypatch):
    """--mark-live (manual handle after substrate bootstrap) should flip
    the registry status to 'live' and update signups.tier."""
    promote, db_path, launch_reg = env
    # Seed a registry entry as if promote --execute had provisioned
    reg = {"schema_version": 1, "tenants": {"launch-1": {
        "signup_id": 1,
        "customer_email": "alice@example.com",
        "customer_domain": "alice.dev",
        "tier": "launch",
        "hetzner_server_id": 999,
        "ip": "1.2.3.4",
        "status": "provisioning",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }}}
    launch_reg.write_text(json.dumps(reg))

    monkeypatch.setattr(sys, "argv",
                        ["promote.py", "--signup-id", "1", "--mark-live", "--json"])
    rc = promote.main()
    assert rc == 0

    reg_after = json.loads(launch_reg.read_text())
    assert reg_after["tenants"]["launch-1"]["status"] == "live"

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT tier, status FROM signups WHERE id = 1").fetchone()
    assert row == ("launch", "live-launch")
