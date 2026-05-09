"""Invite-only signup gate.

Layered ON TOP of the auth agent's email+password signup. The signup endpoint
must call ``validate_invite(code)`` before creating the user, then call
``consume_invite(code)`` after successfully creating the user. See
TODO_INTEGRATE.md at the project root for the precise wiring point.

Schema (created idempotently via ``init_invites_schema``; stacks with the
auth agent's ``users`` / ``sessions`` / ``decks`` tables in the same
``${SLIDEATELIER_OUTPUT_DIR}/atelier.db`` SQLite file):

    invite_codes
      code        TEXT    PRIMARY KEY     (URL-safe, ~11 char)
      max_uses    INTEGER NOT NULL        (1+ for finite, 0 for unlimited — discouraged)
      used_count  INTEGER NOT NULL DEFAULT 0
      created_at  TEXT    NOT NULL        (ISO-8601 UTC)
      expires_at  TEXT    NULL            (NULL = never expires)

A code is valid when:
  - it exists, AND
  - (expires_at IS NULL OR expires_at > now), AND
  - used_count < max_uses (or max_uses == 0).

``consume_invite`` increments ``used_count`` atomically. It does NOT delete
the row — kept for audit. Use ``revoke_invite(code)`` to disable proactively
(sets ``max_uses = used_count``).
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Use the same SQLite file as the auth agent — they're per-output-dir,
# created in get_db(). We pull get_db so the schema initialisation happens
# at import time alongside the existing tables.
from .db import get_db

_now = lambda: datetime.now(timezone.utc)
_iso = lambda dt: dt.isoformat()

# Module-level lock — invite_codes lives in the shared SQLite file, but the
# AuthDB already has its own RLock. We hand off through that lock to keep
# write-ordering deterministic. See _with_conn().
_INVITE_LOCK = threading.RLock()

# Sentinel exceptions surfaced to the signup endpoint integrator.
class InviteError(Exception):
    """Base class for all invite-validation failures."""


class InviteNotFound(InviteError):
    pass


class InviteExpired(InviteError):
    pass


class InviteExhausted(InviteError):
    pass


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass
class Invite:
    code: str
    max_uses: int
    used_count: int
    created_at: str
    expires_at: Optional[str]

    @property
    def is_unlimited(self) -> bool:
        return self.max_uses == 0

    @property
    def remaining(self) -> Optional[int]:
        """How many more uses are allowed; None for unlimited."""
        if self.is_unlimited:
            return None
        return max(0, self.max_uses - self.used_count)

    def is_expired(self, at: Optional[datetime] = None) -> bool:
        if not self.expires_at:
            return False
        at = at or _now()
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return False
        # ISO strings stored without tzinfo can sneak in via tests; treat
        # naive as UTC.
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp <= at

    def is_exhausted(self) -> bool:
        if self.is_unlimited:
            return False
        return self.used_count >= self.max_uses


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS invite_codes (
  code        TEXT    PRIMARY KEY,
  max_uses    INTEGER NOT NULL,
  used_count  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT    NOT NULL,
  expires_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_invites_expiry ON invite_codes(expires_at);
"""


def init_invites_schema(output_dir: Optional[Path] = None) -> None:
    """Create the invite_codes table if absent. Idempotent.

    Called automatically the first time any other helper in this module
    runs, but can be invoked explicitly at app startup to surface schema
    errors early.
    """
    db = get_db(output_dir or _output_dir())
    with _INVITE_LOCK, db._lock, db._conn:  # noqa: SLF001
        db._conn.executescript(_SCHEMA_SQL)  # noqa: SLF001


def _output_dir() -> Path:
    return Path(os.getenv("SLIDEATELIER_OUTPUT_DIR", "./output")).resolve()


def _row_to_invite(row: sqlite3.Row) -> Invite:
    return Invite(
        code=row["code"],
        max_uses=row["max_uses"],
        used_count=row["used_count"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def create_invite(
    max_uses: int = 1,
    expires_days: Optional[int] = None,
    *,
    output_dir: Optional[Path] = None,
    code: Optional[str] = None,
) -> Invite:
    """Generate and persist a new invite code.

    Args:
        max_uses: number of signups this code can satisfy. ``0`` = unlimited
            (discouraged for private beta). Default 1.
        expires_days: invite is valid for this many days from creation.
            None = no expiry.
        code: caller-provided code (testing / vanity codes). Default: random
            URL-safe ~11-char string from ``secrets.token_urlsafe(8)``.
    """
    if max_uses < 0:
        raise ValueError("max_uses must be >= 0")
    if expires_days is not None and expires_days <= 0:
        raise ValueError("expires_days must be > 0 if provided")

    init_invites_schema(output_dir)
    db = get_db(output_dir or _output_dir())

    code = code or secrets.token_urlsafe(8)
    now = _now()
    expires_at = _iso(now + timedelta(days=expires_days)) if expires_days else None
    created_at = _iso(now)

    with _INVITE_LOCK, db._lock, db._conn:  # noqa: SLF001
        try:
            db._conn.execute(  # noqa: SLF001
                "INSERT INTO invite_codes(code, max_uses, used_count, created_at, expires_at) "
                "VALUES (?, ?, 0, ?, ?)",
                (code, max_uses, created_at, expires_at),
            )
        except sqlite3.IntegrityError as e:
            # Caller provided a duplicate code.
            raise ValueError(f"invite code already exists: {code}") from e
    return Invite(
        code=code,
        max_uses=max_uses,
        used_count=0,
        created_at=created_at,
        expires_at=expires_at,
    )


def get_invite(code: str, *, output_dir: Optional[Path] = None) -> Optional[Invite]:
    """Return the invite row, or None if absent. Doesn't validate state."""
    if not code:
        return None
    init_invites_schema(output_dir)
    db = get_db(output_dir or _output_dir())
    with db._lock:  # noqa: SLF001
        row = db._conn.execute(  # noqa: SLF001
            "SELECT code, max_uses, used_count, created_at, expires_at "
            "FROM invite_codes WHERE code = ?",
            (code,),
        ).fetchone()
    return _row_to_invite(row) if row else None


def validate_invite(code: str, *, output_dir: Optional[Path] = None) -> Invite:
    """Raise an ``InviteError`` subclass if the code can't be redeemed.

    Returns the Invite on success. Call ``consume_invite`` AFTER the user
    record has been created — keep the two calls in the same code path so
    a partial signup doesn't burn a use.
    """
    code = (code or "").strip()
    if not code:
        raise InviteNotFound("Invite code is required.")
    invite = get_invite(code, output_dir=output_dir)
    if invite is None:
        raise InviteNotFound("That invite code isn't recognised.")
    if invite.is_expired():
        raise InviteExpired("That invite code has expired.")
    if invite.is_exhausted():
        raise InviteExhausted("That invite code has already been used up.")
    return invite


def consume_invite(code: str, *, output_dir: Optional[Path] = None) -> Invite:
    """Atomically increment ``used_count``; raises if the code can't be
    redeemed at the moment of the write. Returns the post-update Invite.

    Caller pattern:

        try:
            invites.validate_invite(code)
        except invites.InviteError as e:
            return error_response(str(e))
        user = db.create_user(...)
        invites.consume_invite(code)   # only after user creation succeeds
    """
    code = (code or "").strip()
    if not code:
        raise InviteNotFound("Invite code is required.")

    init_invites_schema(output_dir)
    db = get_db(output_dir or _output_dir())

    with _INVITE_LOCK, db._lock, db._conn:  # noqa: SLF001
        # Re-read inside the transaction so we get the current row.
        row = db._conn.execute(  # noqa: SLF001
            "SELECT code, max_uses, used_count, created_at, expires_at "
            "FROM invite_codes WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None:
            raise InviteNotFound("That invite code isn't recognised.")
        invite = _row_to_invite(row)
        if invite.is_expired():
            raise InviteExpired("That invite code has expired.")
        if invite.is_exhausted():
            raise InviteExhausted("That invite code has already been used up.")
        # Increment.
        db._conn.execute(  # noqa: SLF001
            "UPDATE invite_codes SET used_count = used_count + 1 WHERE code = ?",
            (code,),
        )
        invite.used_count += 1
    return invite


def revoke_invite(code: str, *, output_dir: Optional[Path] = None) -> bool:
    """Force-disable a code by setting max_uses = used_count.

    Returns True if a row was updated, False if the code didn't exist.
    """
    code = (code or "").strip()
    if not code:
        return False
    init_invites_schema(output_dir)
    db = get_db(output_dir or _output_dir())
    with _INVITE_LOCK, db._lock, db._conn:  # noqa: SLF001
        # Set max_uses = -1 (sentinel "revoked" — neither unlimited nor a
        # real cap). is_unlimited (max_uses==0) → False, is_exhausted
        # (used_count >= max_uses) → True even for fresh codes where
        # used_count was 0. Fixes the original bug where revoke(used=0,
        # max=10) → max=0, which is_unlimited misread as "unlimited".
        cur = db._conn.execute(  # noqa: SLF001
            "UPDATE invite_codes SET max_uses = -1, used_count = 0 WHERE code = ?",
            (code,),
        )
        return cur.rowcount > 0


def list_invites(*, output_dir: Optional[Path] = None) -> list[Invite]:
    """Return every invite, newest first. Useful for an admin CLI listing."""
    init_invites_schema(output_dir)
    db = get_db(output_dir or _output_dir())
    with db._lock:  # noqa: SLF001
        rows = db._conn.execute(  # noqa: SLF001
            "SELECT code, max_uses, used_count, created_at, expires_at "
            "FROM invite_codes ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_invite(r) for r in rows]
