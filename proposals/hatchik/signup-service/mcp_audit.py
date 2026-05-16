"""
mcp_audit.py — every MCP ops-mode action gets a row here.

Customer-facing promise (from mcp-signup-flow.md "Trust signals"): a
visible audit log in the dashboard showing every MCP-initiated action.

Each tool's handler in main.py is expected to call ``audit.record(...)``
near the start, so the row is persisted even if the underlying action
fails. Confirmation-gated actions log twice: once when the MCP requests
the token (action='request:apply_migration', status='token_issued') and
once when the customer confirms/rejects in the browser
(action='apply_migration', status='confirmed' | 'rejected').

Read side powers a new /api/account/audit endpoint + the "Activity" tab
in /account.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))

AuditStatus = Literal[
    "ok", "error", "token_issued", "confirmed", "rejected", "expired",
]


SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signup_id       INTEGER NOT NULL,
    occurred_at     TEXT NOT NULL,
    action          TEXT NOT NULL,
    status          TEXT NOT NULL,
    tool_caller     TEXT,                       -- 'mcp' | 'web' | 'cli'
    remote_ip       TEXT,
    payload_json    TEXT,
    result_json     TEXT,
    confirmation_token TEXT                     -- joined to pending_confirmations
);
CREATE INDEX IF NOT EXISTS mcp_audit_signup_idx
    ON mcp_audit_log(signup_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS mcp_audit_action_idx
    ON mcp_audit_log(action, occurred_at DESC);
"""


def init_schema(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    init_schema(db)
    try:
        yield db
        db.commit()
    finally:
        db.close()


@dataclass
class AuditRow:
    id: int
    signup_id: int
    occurred_at: datetime
    action: str
    status: AuditStatus
    tool_caller: str | None
    remote_ip: str | None
    payload: dict[str, Any]
    result: dict[str, Any] | None
    confirmation_token: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "occurred_at": self.occurred_at.isoformat(),
            "action": self.action,
            "status": self.status,
            "tool_caller": self.tool_caller,
            "payload": self.payload,
            "result": self.result,
            "confirmation_token": self.confirmation_token,
        }


def record(
    signup_id: int, action: str, status: AuditStatus,
    *, payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    tool_caller: str = "mcp",
    remote_ip: str | None = None,
    confirmation_token: str | None = None,
) -> int:
    """Log one MCP action. Returns the row id."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        cur = db.execute(
            """INSERT INTO mcp_audit_log
                 (signup_id, occurred_at, action, status, tool_caller,
                  remote_ip, payload_json, result_json, confirmation_token)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (signup_id, now, action, status, tool_caller, remote_ip,
             json.dumps(payload) if payload else None,
             json.dumps(result) if result else None,
             confirmation_token),
        )
        return int(cur.lastrowid or 0)


def recent_for(signup_id: int, limit: int = 50) -> list[AuditRow]:
    with _conn() as db:
        rows = db.execute(
            """SELECT * FROM mcp_audit_log
                WHERE signup_id = ?
                ORDER BY occurred_at DESC LIMIT ?""",
            (signup_id, limit),
        ).fetchall()
    return [
        AuditRow(
            id=r["id"], signup_id=r["signup_id"],
            occurred_at=datetime.fromisoformat(r["occurred_at"]),
            action=r["action"], status=r["status"],
            tool_caller=r["tool_caller"], remote_ip=r["remote_ip"],
            payload=json.loads(r["payload_json"]) if r["payload_json"] else {},
            result=json.loads(r["result_json"]) if r["result_json"] else None,
            confirmation_token=r["confirmation_token"],
        )
        for r in rows
    ]
