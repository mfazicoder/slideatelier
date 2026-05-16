"""
Agent registry + DB schema.

Registry is a module-level dict keyed by agent NAME → Agent class.
Agents register themselves at import time via the @register decorator;
the runtime walks REGISTRY at startup, computes due agents, and runs
them.

Schema:
  agent_runs        — one row per agent invocation per tenant
  agent_actions     — actions emitted (1:N from agent_runs)
  agent_state       — per-agent per-tenant kv store (last seen ids, etc.)
  agent_enabled     — per-tenant on/off toggle (override DEFAULT_ON_TIERS)
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Type

from agents.base import Agent

DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))


REGISTRY: dict[str, Type[Agent]] = {}


def register(cls: Type[Agent]) -> Type[Agent]:
    """@register decorator — agents call it at module level so the
    runtime discovers them via `import agents.support` etc."""
    if not cls.NAME:
        raise ValueError(f"{cls.__name__} has empty NAME — set NAME on the Agent subclass")
    if cls.NAME in REGISTRY:
        # Idempotent on re-import (e.g. tests).
        return cls
    REGISTRY[cls.NAME] = cls
    return cls


SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name      TEXT NOT NULL,
    signup_id       INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'running',  -- running | ok | error
    notes           TEXT,
    metrics_json    TEXT,
    error_message   TEXT,
    -- Cost accounting (LLM tokens spent during this run, in raw pence
    -- before the customer-facing × 1.6 markup applied by ai_credit).
    llm_cost_pence  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS agent_runs_signup_idx
    ON agent_runs(signup_id, started_at DESC);
CREATE INDEX IF NOT EXISTS agent_runs_agent_idx
    ON agent_runs(agent_name, started_at DESC);

CREATE TABLE IF NOT EXISTS agent_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL,
    signup_id       INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    summary         TEXT NOT NULL,
    payload_json    TEXT,
    rationale       TEXT,
    confidence      REAL,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',
        -- pending (awaiting approval), executed, skipped, rejected, failed
    decided_at      TEXT,
    decider         TEXT,                              -- 'auto' | 'founder' | 'agent'
    result_json     TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_actions_run_idx
    ON agent_actions(run_id);
CREATE INDEX IF NOT EXISTS agent_actions_signup_status_idx
    ON agent_actions(signup_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_state (
    agent_name      TEXT NOT NULL,
    signup_id       INTEGER NOT NULL,
    key             TEXT NOT NULL,
    value_json      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (agent_name, signup_id, key)
);

CREATE TABLE IF NOT EXISTS agent_enabled (
    agent_name      TEXT NOT NULL,
    signup_id       INTEGER NOT NULL,
    enabled         INTEGER NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (agent_name, signup_id)
);
"""


def init_schema(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    """Context-manager for an opened sqlite3 connection.
    Public — consumed by both runtime.py and main.py routes."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    init_schema(db)
    try:
        yield db
        db.commit()
    finally:
        db.close()


# ─── Enablement helpers ──────────────────────────────────────────────────
def is_enabled(agent_name: str, signup_id: int, tier: str) -> bool:
    """Honour the customer's explicit override; otherwise fall back to
    the agent's DEFAULT_ON_TIERS."""
    with conn() as db:
        row = db.execute(
            "SELECT enabled FROM agent_enabled WHERE agent_name=? AND signup_id=?",
            (agent_name, signup_id),
        ).fetchone()
    if row is not None:
        return bool(row["enabled"])
    cls = REGISTRY.get(agent_name)
    if not cls:
        return False
    return tier in cls.DEFAULT_ON_TIERS


def set_enabled(agent_name: str, signup_id: int, enabled: bool) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with conn() as db:
        db.execute(
            """INSERT INTO agent_enabled (agent_name, signup_id, enabled, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(agent_name, signup_id)
               DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (agent_name, signup_id, 1 if enabled else 0, now),
        )


def list_for_tenant(signup_id: int, tier: str) -> list[dict[str, Any]]:
    """For the /account → Agents tab: every registered agent with its
    enabled state + brief description for the customer."""
    with conn() as db:
        rows = db.execute(
            "SELECT agent_name, enabled FROM agent_enabled WHERE signup_id=?",
            (signup_id,),
        ).fetchall()
    overrides = {r["agent_name"]: bool(r["enabled"]) for r in rows}
    out: list[dict[str, Any]] = []
    for name, cls in REGISTRY.items():
        out.append({
            "agent_name": name,
            "description": cls.DESCRIPTION,
            "schedule": cls.SCHEDULE,
            "enabled": overrides.get(name, tier in cls.DEFAULT_ON_TIERS),
            "default_on_tiers": list(cls.DEFAULT_ON_TIERS),
            "overridden": name in overrides,
        })
    return out


# ─── Run + action persistence ────────────────────────────────────────────
def begin_run(agent_name: str, signup_id: int, started_at: str) -> int:
    with conn() as db:
        cur = db.execute(
            """INSERT INTO agent_runs (agent_name, signup_id, started_at, status)
               VALUES (?, ?, ?, 'running')""",
            (agent_name, signup_id, started_at),
        )
        return int(cur.lastrowid or 0)


def finish_run(
    run_id: int, *, status: str, finished_at: str,
    notes: str = "", metrics: dict[str, Any] | None = None,
    error: str = "", llm_cost_pence: int = 0,
) -> None:
    with conn() as db:
        db.execute(
            """UPDATE agent_runs
                  SET finished_at=?, status=?, notes=?, metrics_json=?,
                      error_message=?, llm_cost_pence=?
                WHERE id=?""",
            (finished_at, status, notes,
             json.dumps(metrics) if metrics else None,
             error[:1000] if error else None,
             llm_cost_pence, run_id),
        )


def record_action(
    run_id: int, signup_id: int, action_dict: dict[str, Any], created_at: str,
) -> int:
    status = "pending" if action_dict.get("requires_approval") else "executed"
    with conn() as db:
        cur = db.execute(
            """INSERT INTO agent_actions
                 (run_id, signup_id, kind, summary, payload_json,
                  rationale, confidence, requires_approval, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, signup_id,
             action_dict["kind"], action_dict["summary"],
             json.dumps(action_dict.get("payload") or {}),
             action_dict.get("rationale", ""),
             action_dict.get("confidence", 1.0),
             1 if action_dict.get("requires_approval") else 0,
             status, created_at),
        )
        return int(cur.lastrowid or 0)


def update_action_status(
    action_id: int, status: str, decider: str = "auto",
    result: dict[str, Any] | None = None,
) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with conn() as db:
        db.execute(
            """UPDATE agent_actions
                  SET status=?, decided_at=?, decider=?, result_json=?
                WHERE id=?""",
            (status, now, decider,
             json.dumps(result) if result else None, action_id),
        )


def pending_actions_for(signup_id: int, limit: int = 25) -> list[dict[str, Any]]:
    with conn() as db:
        rows = db.execute(
            """SELECT agent_actions.id        AS id,
                      agent_actions.run_id    AS run_id,
                      agent_actions.kind      AS kind,
                      agent_actions.summary   AS summary,
                      agent_actions.payload_json AS payload_json,
                      agent_actions.rationale AS rationale,
                      agent_actions.confidence AS confidence,
                      agent_actions.created_at AS created_at,
                      agent_actions.signup_id AS signup_id,
                      ar.agent_name           AS agent_name
                 FROM agent_actions
                 JOIN agent_runs ar ON ar.id = agent_actions.run_id
                WHERE agent_actions.signup_id = ?
                  AND agent_actions.status = 'pending'
                ORDER BY agent_actions.created_at DESC LIMIT ?""",
            (signup_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "run_id": r["run_id"],
            "agent_name": r["agent_name"], "kind": r["kind"],
            "summary": r["summary"],
            "payload": json.loads(r["payload_json"] or "{}"),
            "rationale": r["rationale"],
            "confidence": r["confidence"],
            "created_at": r["created_at"],
        })
    return out


def recent_runs_for(signup_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with conn() as db:
        rows = db.execute(
            """SELECT id, agent_name, started_at, finished_at, status,
                      notes, metrics_json, llm_cost_pence
                 FROM agent_runs
                WHERE signup_id = ?
                ORDER BY started_at DESC LIMIT ?""",
            (signup_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "agent_name": r["agent_name"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "status": r["status"], "notes": r["notes"],
            "metrics": json.loads(r["metrics_json"]) if r["metrics_json"] else {},
            "llm_cost_pence": r["llm_cost_pence"] or 0,
        })
    return out
