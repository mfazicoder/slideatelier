"""marketing_* table definitions.

PRAGMA-gated `CREATE TABLE IF NOT EXISTS` blocks, matching the
signup-service convention. Idempotent — safe to call on every startup
or CLI invocation. Tables are prefixed `marketing_` because they share
the SQLite file with signup-service (signups, payments, sessions, …).

Tenant identity: `marketing_tenants.signup_id` is an optional FK to
`signups(id)`. For Hatchik's own marketing tenant the FK is NULL.
When the Autonomous Growth tier ships, each customer-tenant row gets
its signup_id populated.

Tenant isolation: SQLite has no RLS. Every query that touches a
tenant-scoped table MUST take tenant_id as a parameter — enforced in
Python helpers (see runs.py).
"""

from __future__ import annotations

import sqlite3

from . import db

DDL_STATEMENTS: list[str] = [
    # ───────────────────────────────────────────────────────────────────
    # marketing_tenants — one row per marketed product. `signup_id` is a
    # *logical* reference to substrate's signups(id); not declared as a
    # SQL FK because SQLite enforces the FK target at every insert (even
    # with NULL) once `PRAGMA foreign_keys = ON`, and we want this code
    # to work standalone in dev where signups doesn't exist. The link is
    # enforced in Python (see tenant.bind_signup() — Phase 5).
    # ───────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS marketing_tenants (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        slug                TEXT NOT NULL UNIQUE,
        signup_id           INTEGER,
        product_url         TEXT,
        status              TEXT NOT NULL DEFAULT 'active'
                             CHECK(status IN ('active','paused','archived')),
        spend_cap_daily_usd REAL NOT NULL DEFAULT 5.00,
        settings_json       TEXT NOT NULL DEFAULT '{}',
        created_at          TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_marketing_tenants_signup ON marketing_tenants(signup_id)",

    # ───────────────────────────────────────────────────────────────────
    # marketing_tenant_api_keys — per-tenant provider keys (Phase 5).
    # For tenant 1 (Hatchik) we read from env vars; this table comes
    # into play when customer tenants bring their own X/Resend/etc keys.
    # `ciphertext` is opaque blob — encryption helper TBD pre-Phase 5.
    # ───────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS marketing_tenant_api_keys (
        tenant_id   INTEGER NOT NULL REFERENCES marketing_tenants(id) ON DELETE CASCADE,
        provider    TEXT NOT NULL,
        ciphertext  BLOB NOT NULL,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (tenant_id, provider)
    )
    """,

    # ───────────────────────────────────────────────────────────────────
    # marketing_prompt_versions — mirror of files in prompts/ for the
    # purpose of joining agent_runs to a specific prompt body. The files
    # in prompts/ are the source of truth; this table is a write-through
    # cache populated by prompts.py at load time.
    # ───────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS marketing_prompt_versions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        version      INTEGER NOT NULL,
        model        TEXT NOT NULL,
        body         TEXT NOT NULL,
        params_json  TEXT NOT NULL DEFAULT '{}',
        created_at   TEXT NOT NULL,
        UNIQUE(name, version)
    )
    """,

    # ───────────────────────────────────────────────────────────────────
    # marketing_agent_runs — every LLM invocation. Cost rollup over a
    # rolling 24h window is the input to the per-tenant budget gate.
    # ───────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS marketing_agent_runs (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id      INTEGER NOT NULL REFERENCES marketing_tenants(id),
        layer          TEXT NOT NULL,
        prompt_name    TEXT,
        prompt_version INTEGER,
        model          TEXT NOT NULL,
        input_json     TEXT NOT NULL,
        output_json    TEXT,
        tokens_in      INTEGER,
        tokens_out     INTEGER,
        cost_usd       REAL,
        status         TEXT NOT NULL
                        CHECK(status IN ('running','success','error','over_budget')),
        error          TEXT,
        started_at     TEXT NOT NULL,
        completed_at   TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_marketing_agent_runs_tenant_time ON marketing_agent_runs(tenant_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_marketing_agent_runs_cost_day ON marketing_agent_runs(tenant_id, started_at, cost_usd)",

    # ───────────────────────────────────────────────────────────────────
    # marketing_strategies — Layer 1 output. JSON payload holds ICP,
    # sub-personas, voice/tone, pillars[], angles[]. is_current is a
    # convenience flag — exactly one current strategy per tenant.
    # ───────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS marketing_strategies (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id     INTEGER NOT NULL REFERENCES marketing_tenants(id),
        version       INTEGER NOT NULL,
        payload_json  TEXT NOT NULL,
        source_run_id INTEGER REFERENCES marketing_agent_runs(id),
        is_current    INTEGER NOT NULL DEFAULT 1,
        generated_at  TEXT NOT NULL,
        UNIQUE(tenant_id, version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_marketing_strategies_current ON marketing_strategies(tenant_id) WHERE is_current = 1",

    # ───────────────────────────────────────────────────────────────────
    # marketing_content_queue — Layer 2 drafts awaiting approval, plus
    # downstream lifecycle states. parent_id links thread parts / replies.
    # ───────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS marketing_content_queue (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id         INTEGER NOT NULL REFERENCES marketing_tenants(id),
        channel           TEXT NOT NULL
                           CHECK(channel IN ('x_tweet','x_thread','linkedin','blog','email','reddit_draft','discord_draft')),
        body              TEXT NOT NULL,
        metadata_json     TEXT NOT NULL DEFAULT '{}',
        status            TEXT NOT NULL DEFAULT 'pending'
                           CHECK(status IN ('pending','approved','rejected','scheduled','posted','failed')),
        rejection_reason  TEXT,
        scheduled_for     TEXT,
        posted_at         TEXT,
        source_run_id     INTEGER REFERENCES marketing_agent_runs(id),
        parent_id         INTEGER REFERENCES marketing_content_queue(id),
        created_at        TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_marketing_content_queue_pending ON marketing_content_queue(tenant_id, status, scheduled_for)",

    # ───────────────────────────────────────────────────────────────────
    # marketing_distributions — actual posts/sends; one row per
    # external_id. metrics_json is updated on each pull.
    # ───────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS marketing_distributions (
        id                       INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id                INTEGER NOT NULL REFERENCES marketing_tenants(id),
        content_queue_id         INTEGER NOT NULL REFERENCES marketing_content_queue(id) ON DELETE CASCADE,
        provider                 TEXT NOT NULL,
        external_id              TEXT,
        url                      TEXT,
        posted_at                TEXT NOT NULL,
        last_metrics_fetched_at  TEXT,
        metrics_json             TEXT NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_marketing_distributions_recent ON marketing_distributions(tenant_id, posted_at DESC)",

    # ───────────────────────────────────────────────────────────────────
    # marketing_listening_signals — Layer 4 inputs. Raw payload kept
    # verbatim; sentiment populated post-hoc by the analyze agent.
    # ───────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS marketing_listening_signals (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id    INTEGER NOT NULL REFERENCES marketing_tenants(id),
        source       TEXT NOT NULL,
        query        TEXT,
        raw_json     TEXT NOT NULL,
        sentiment    TEXT CHECK(sentiment IN ('pos','neg','neutral') OR sentiment IS NULL),
        captured_at  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_marketing_listening_signals_source ON marketing_listening_signals(tenant_id, source, captured_at DESC)",

    # ───────────────────────────────────────────────────────────────────
    # marketing_experiments — Layer 5. A/B variants and outcomes.
    # ───────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS marketing_experiments (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       INTEGER NOT NULL REFERENCES marketing_tenants(id),
        name            TEXT NOT NULL,
        hypothesis      TEXT,
        variant_a_json  TEXT NOT NULL,
        variant_b_json  TEXT NOT NULL,
        metric          TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'running'
                         CHECK(status IN ('running','complete','aborted')),
        winner          TEXT CHECK(winner IN ('a','b','tie') OR winner IS NULL),
        started_at      TEXT NOT NULL,
        ended_at        TEXT
    )
    """,

    # ───────────────────────────────────────────────────────────────────
    # marketing_jobs — minimal queue with retries. Replaces n8n. Drained
    # by the worker loop; cron entries enqueue into this table.
    # ───────────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS marketing_jobs (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id      INTEGER REFERENCES marketing_tenants(id),
        kind           TEXT NOT NULL,
        payload_json   TEXT NOT NULL DEFAULT '{}',
        run_at         TEXT NOT NULL,
        attempts       INTEGER NOT NULL DEFAULT 0,
        max_attempts   INTEGER NOT NULL DEFAULT 3,
        status         TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued','running','done','failed')),
        last_error     TEXT,
        locked_until   TEXT,
        created_at     TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_marketing_jobs_runnable ON marketing_jobs(status, run_at)",
]


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    """Run every DDL statement. Safe to call repeatedly."""
    owned = False
    if conn is None:
        conn = db.connect()
        owned = True
    try:
        for stmt in DDL_STATEMENTS:
            conn.execute(stmt)
    finally:
        if owned:
            conn.close()
