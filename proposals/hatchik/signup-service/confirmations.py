"""
confirmations.py — short-lived browser confirmation tokens for destructive
MCP-initiated actions.

The MCP signup-flow spec calls for a "defence-in-depth" pattern: any MCP
action that costs money or could break things is NOT executed when the
tool is called. Instead the MCP returns a confirm_url; the customer
opens it in their browser, sees the action plainly, and clicks Yes/No.
Only then does the backend execute.

This module owns the token lifecycle:

  1. issue(signup_id, action, summary, payload)
        → returns (token, confirm_url, expires_at)
  2. lookup(token) → ConfirmationRecord | None (read-only inspect)
  3. decide(token, decide, remote_ip)
        → returns the original payload + signup_id when confirmed,
          marks the token consumed; rejects on TTL miss / IP miss /
          already-decided.

Action handlers register themselves by name (see ACTION_HANDLERS dict)
so confirm/decide knows what to run server-side once the customer
clicks Yes.

Token TTL: 5 minutes (default), one-time-use, IP-pinned (the IP that
made the original MCP call must match the IP that hits /confirm).
Configurable via env. IP pin can be loosened with
HATCHIK_CONFIRM_REQUIRE_IP_MATCH=0 for testing.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
TOKEN_TTL = timedelta(seconds=int(os.environ.get("HATCHIK_CONFIRM_TTL_SECONDS", "300")))
REQUIRE_IP_MATCH = os.environ.get("HATCHIK_CONFIRM_REQUIRE_IP_MATCH", "1") != "0"

ConfirmStatus = Literal["pending", "confirmed", "rejected", "expired"]


# ─── Schema ───────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_confirmations (
    token           TEXT PRIMARY KEY,           -- tc_<32 url-safe>
    signup_id       INTEGER NOT NULL,
    action          TEXT NOT NULL,              -- 'apply_migration' etc.
    summary         TEXT NOT NULL,              -- shown to customer at confirm UI
    payload_json    TEXT NOT NULL,              -- action-specific args
    requested_at    TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    requester_ip    TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    decided_at      TEXT,
    decider_ip      TEXT,
    result_json     TEXT                        -- handler output once executed
);
CREATE INDEX IF NOT EXISTS pending_conf_signup_idx
    ON pending_confirmations(signup_id, requested_at);
CREATE INDEX IF NOT EXISTS pending_conf_status_idx
    ON pending_confirmations(status, expires_at);
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


# ─── Data shape ───────────────────────────────────────────────────────────
@dataclass
class ConfirmationRecord:
    token: str
    signup_id: int
    action: str
    summary: str
    payload: dict[str, Any]
    requested_at: datetime
    expires_at: datetime
    requester_ip: str | None
    status: ConfirmStatus
    decided_at: datetime | None
    decider_ip: str | None
    result: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "signup_id": self.signup_id,
            "action": self.action,
            "summary": self.summary,
            "payload": self.payload,
            "requested_at": self.requested_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "result": self.result,
        }


# ─── Action handler registry ──────────────────────────────────────────────
# Each registered action takes (signup_id, payload) and returns a dict
# that's persisted as result_json + returned to the caller (MCP polls
# /api/confirmations/{token} to discover the outcome).
ActionHandler = Callable[[int, dict[str, Any]], dict[str, Any]]
ACTION_HANDLERS: dict[str, ActionHandler] = {}


def register_action(name: str) -> Callable[[ActionHandler], ActionHandler]:
    """Decorator: @register_action('apply_migration') def _h(signup_id, payload): ..."""
    def _wrap(fn: ActionHandler) -> ActionHandler:
        ACTION_HANDLERS[name] = fn
        return fn
    return _wrap


# ─── Token gen ────────────────────────────────────────────────────────────
def _gen_token() -> str:
    return "tc_" + secrets.token_urlsafe(24)


def _parse(row: sqlite3.Row) -> ConfirmationRecord:
    return ConfirmationRecord(
        token=row["token"],
        signup_id=row["signup_id"],
        action=row["action"],
        summary=row["summary"],
        payload=json.loads(row["payload_json"] or "{}"),
        requested_at=datetime.fromisoformat(row["requested_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
        requester_ip=row["requester_ip"],
        status=row["status"],
        decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
        decider_ip=row["decider_ip"],
        result=json.loads(row["result_json"]) if row["result_json"] else None,
    )


# ─── Public API ───────────────────────────────────────────────────────────
def issue(
    signup_id: int, action: str, summary: str,
    payload: dict[str, Any], requester_ip: str | None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Mint a token. Returns {token, confirm_url, expires_at, expires_in_seconds}."""
    if action not in ACTION_HANDLERS:
        raise ValueError(
            f"Unknown confirmable action: {action}. "
            f"Registered: {sorted(ACTION_HANDLERS.keys())}"
        )
    token = _gen_token()
    now = datetime.now(timezone.utc)
    expires = now + TOKEN_TTL
    with _conn() as db:
        db.execute(
            """INSERT INTO pending_confirmations
                 (token, signup_id, action, summary, payload_json,
                  requested_at, expires_at, requester_ip, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (token, signup_id, action, summary, json.dumps(payload),
             now.isoformat(), expires.isoformat(), requester_ip),
        )
    base = base_url or os.environ.get("HATCHIK_PUBLIC_URL", "https://hatchik.com")
    return {
        "token": token,
        "confirm_url": f"{base}/confirm/{token}",
        "expires_at": expires.isoformat(),
        "expires_in_seconds": int(TOKEN_TTL.total_seconds()),
    }


def lookup(token: str) -> ConfirmationRecord | None:
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM pending_confirmations WHERE token = ?", (token,),
        ).fetchone()
    if not row:
        return None
    rec = _parse(row)
    # Lazy expiry.
    if rec.status == "pending" and datetime.now(timezone.utc) > rec.expires_at:
        _mark(token, "expired", None)
        rec.status = "expired"
    return rec


def decide(
    token: str, decision: Literal["confirm", "reject"],
    decider_ip: str | None,
) -> tuple[ConfirmationRecord, dict[str, Any] | None]:
    """Execute the action server-side and persist the result. Returns
    (record, action_result). action_result is None for rejects/expired/
    invalid; populated for confirms.

    Raises ValueError on token-not-found.
    """
    rec = lookup(token)
    if not rec:
        raise ValueError("token not found")
    if rec.status != "pending":
        return rec, None
    if datetime.now(timezone.utc) > rec.expires_at:
        _mark(token, "expired", decider_ip)
        rec.status = "expired"
        return rec, None
    if REQUIRE_IP_MATCH and rec.requester_ip and decider_ip and rec.requester_ip != decider_ip:
        _mark(token, "rejected", decider_ip,
              result={"error": "ip_mismatch",
                      "requester_ip": rec.requester_ip,
                      "decider_ip": decider_ip})
        rec.status = "rejected"
        return rec, None

    if decision == "reject":
        _mark(token, "rejected", decider_ip)
        rec.status = "rejected"
        return rec, None

    # decision == "confirm": run the action handler.
    handler = ACTION_HANDLERS.get(rec.action)
    if not handler:
        _mark(token, "rejected", decider_ip,
              result={"error": "handler_missing", "action": rec.action})
        rec.status = "rejected"
        return rec, None
    try:
        result = handler(rec.signup_id, rec.payload)
    except Exception as e:  # noqa: BLE001
        _mark(token, "rejected", decider_ip,
              result={"error": str(e)[:500]})
        rec.status = "rejected"
        return rec, {"error": str(e)[:500]}
    _mark(token, "confirmed", decider_ip, result=result)
    rec.status = "confirmed"
    rec.result = result
    return rec, result


def _mark(token: str, status: ConfirmStatus, decider_ip: str | None,
          result: dict[str, Any] | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    fields = ["status = ?", "decided_at = ?", "decider_ip = ?"]
    values: list[Any] = [status, now, decider_ip]
    if result is not None:
        fields.append("result_json = ?")
        values.append(json.dumps(result))
    values.append(token)
    with _conn() as db:
        db.execute(
            f"UPDATE pending_confirmations SET {', '.join(fields)} WHERE token = ?",
            values,
        )


def recent_for(signup_id: int, limit: int = 25) -> list[ConfirmationRecord]:
    """Confirmation history for a signup (for the dashboard 'Activity' tab)."""
    with _conn() as db:
        rows = db.execute(
            """SELECT * FROM pending_confirmations
                WHERE signup_id = ?
                ORDER BY requested_at DESC LIMIT ?""",
            (signup_id, limit),
        ).fetchall()
    return [_parse(r) for r in rows]
