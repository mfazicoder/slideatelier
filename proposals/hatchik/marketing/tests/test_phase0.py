"""Phase-0 smoke tests — no network, no Anthropic key required.

Exercises: schema init, tenant seed, budget gate, prompt loader, and
that agents/hello.py raises a clean MissingAPIKey when no key is set.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point HATCHIK_SIGNUP_DB at a scratch file and reload config."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("MARKETING_DAILY_CAP_USD", "5.00")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HATCHIK_ANTHROPIC_MASTER_KEY", raising=False)

    # Force reload of config + db so the new env vars take effect.
    import importlib

    from marketing import config, db, schema, tenant, budget, runs, prompts, anthropic_client, seed
    for mod in (config, db, schema, tenant, budget, runs, prompts, anthropic_client, seed):
        importlib.reload(mod)

    # signups table is owned by signup-service; create a minimal stub so
    # the FK on marketing_tenants.signup_id resolves in test DBs.
    conn = db.connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS signups (id INTEGER PRIMARY KEY AUTOINCREMENT)"
    )
    conn.close()

    return db_path


def test_schema_is_idempotent(tmp_db):
    from marketing import db, schema

    schema.ensure_schema()
    schema.ensure_schema()  # twice — should be a no-op

    conn = db.connect()
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'marketing_%'"
        )
    }
    conn.close()
    assert {
        "marketing_tenants",
        "marketing_tenant_api_keys",
        "marketing_prompt_versions",
        "marketing_agent_runs",
        "marketing_strategies",
        "marketing_content_queue",
        "marketing_distributions",
        "marketing_listening_signals",
        "marketing_experiments",
        "marketing_jobs",
    }.issubset(tables)


def test_seed_creates_tenant_idempotently(tmp_db):
    from marketing import db, schema, seed, tenant

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid1 = seed.seed_hatchik_tenant(conn)
        tid2 = seed.seed_hatchik_tenant(conn)
        assert tid1 == tid2
        t = tenant.get_by_slug(conn, "hatchik")
        assert t.slug == "hatchik"
        assert t.signup_id is None
        assert t.status == "active"
        assert t.spend_cap_daily_usd == 5.00
    finally:
        conn.close()


def test_budget_blocks_over_cap(tmp_db):
    from datetime import datetime, timezone

    from marketing import budget, db, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        # Pump in $6 of spend in the last 5 minutes.
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO marketing_agent_runs
                (tenant_id, layer, model, input_json, status, started_at,
                 cost_usd)
            VALUES (?, 'hello', 'claude-opus-4-7', '{}', 'success', ?, 6.00)
            """,
            (tid, now),
        )
        assert budget.spend_last_24h(conn, tid) >= 6.00
        with pytest.raises(budget.OverBudget):
            budget.assert_within_cap(conn, tid, cap_usd=5.00)
    finally:
        conn.close()


def test_tenant_isolation_lookup(tmp_db):
    from marketing import db, schema, tenant

    schema.ensure_schema()
    with pytest.raises(LookupError):
        conn = db.connect()
        try:
            tenant.get_by_slug(conn, "does-not-exist")
        finally:
            conn.close()


def test_prompt_loader_parses_frontmatter(tmp_db, tmp_path, monkeypatch):
    from marketing import prompts

    fake = tmp_path / "prompts"
    (fake / "demo").mkdir(parents=True)
    (fake / "demo" / "v1.md").write_text(
        "---\nmodel: claude-opus-4-7\nparams:\n  max_tokens: 128\n---\nhello body\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(prompts, "PROMPTS_DIR", fake)

    p = prompts.load("demo")
    assert p.name == "demo"
    assert p.version == 1
    assert p.model == "claude-opus-4-7"
    assert p.params == {"max_tokens": 128}
    assert "hello body" in p.body


def test_hello_agent_raises_without_key(tmp_db):
    from marketing import agents, anthropic_client, db, schema, seed
    from marketing.agents import hello

    schema.ensure_schema()
    conn = db.connect()
    try:
        seed.seed_hatchik_tenant(conn)
    finally:
        conn.close()

    with pytest.raises(anthropic_client.MissingAPIKey):
        hello.run(tenant_slug="hatchik")
