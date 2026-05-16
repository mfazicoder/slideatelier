"""marketing_jobs CRUD + atomic claim.

The job queue is the substrate for cron scheduling (Phase 3b) and any
async-style work that follows. Jobs are claimed atomically via
UPDATE … RETURNING (SQLite ≥ 3.35), so multiple worker processes
could safely poll the same DB if we ever scale beyond a single tick
loop.

Cron rules are implemented as self-rescheduling jobs: a `*_cron` job's
handler enqueues the next instance after running. No separate
scheduler library needed; the worker loop is the scheduler.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class Job:
    id: int
    tenant_id: int | None
    kind: str
    payload: dict[str, Any]
    run_at: str
    attempts: int
    max_attempts: int


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(
    conn: sqlite3.Connection,
    *,
    kind: str,
    tenant_id: int | None = None,
    payload: dict[str, Any] | None = None,
    run_at: str | None = None,
    max_attempts: int = 3,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO marketing_jobs
            (tenant_id, kind, payload_json, run_at, attempts, max_attempts,
             status, created_at)
        VALUES (?, ?, ?, ?, 0, ?, 'queued', ?)
        """,
        (
            tenant_id,
            kind,
            json.dumps(payload or {}, ensure_ascii=False),
            run_at or _now_iso(),
            max_attempts,
            _now_iso(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def claim_one(
    conn: sqlite3.Connection, *, lock_seconds: int = 300
) -> Job | None:
    """Atomically claim the next runnable job. Returns None if none.

    A job is "runnable" if status='queued' and run_at <= now. Stale
    'running' rows whose locked_until has passed are also reclaimed
    (best-effort crash recovery)."""
    now = _now_iso()
    lock_until = (
        datetime.now(timezone.utc) + timedelta(seconds=lock_seconds)
    ).isoformat()

    row = conn.execute(
        """
        UPDATE marketing_jobs
        SET status = 'running',
            locked_until = ?,
            attempts = attempts + 1
        WHERE id = (
            SELECT id FROM marketing_jobs
            WHERE (
                (status = 'queued' AND run_at <= ?)
                OR
                (status = 'running' AND locked_until IS NOT NULL AND locked_until <= ?)
            )
            ORDER BY run_at ASC, id ASC
            LIMIT 1
        )
        RETURNING id, tenant_id, kind, payload_json, run_at, attempts, max_attempts
        """,
        (lock_until, now, now),
    ).fetchone()
    if row is None:
        return None
    return Job(
        id=row["id"],
        tenant_id=row["tenant_id"],
        kind=row["kind"],
        payload=json.loads(row["payload_json"]),
        run_at=row["run_at"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
    )


def mark_done(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE marketing_jobs SET status='done', locked_until=NULL WHERE id=?",
        (job_id,),
    )


def mark_failed(
    conn: sqlite3.Connection, job_id: int, *, error: str,
    backoff_seconds: int = 60,
) -> None:
    """Mark a job failed. If attempts < max_attempts, requeue with
    exponential backoff; else mark terminally failed."""
    row = conn.execute(
        "SELECT attempts, max_attempts FROM marketing_jobs WHERE id=?", (job_id,)
    ).fetchone()
    if row is None:
        return
    if row["attempts"] >= row["max_attempts"]:
        conn.execute(
            "UPDATE marketing_jobs SET status='failed', last_error=?, locked_until=NULL WHERE id=?",
            (error, job_id),
        )
        return
    backoff = backoff_seconds * (2 ** (row["attempts"] - 1))
    next_run = (
        datetime.now(timezone.utc) + timedelta(seconds=backoff)
    ).isoformat()
    conn.execute(
        """
        UPDATE marketing_jobs
        SET status='queued', last_error=?, locked_until=NULL, run_at=?
        WHERE id=?
        """,
        (error, next_run, job_id),
    )


def list_jobs(
    conn: sqlite3.Connection,
    *,
    tenant_id: int | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    where = []
    params: list[Any] = []
    if tenant_id is not None:
        where.append("tenant_id = ?")
        params.append(tenant_id)
    if status is not None:
        where.append("status = ?")
        params.append(status)
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    return conn.execute(
        f"""
        SELECT id, tenant_id, kind, status, run_at, attempts, max_attempts,
               locked_until, last_error, created_at
        FROM marketing_jobs
        {where_clause}
        ORDER BY id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM marketing_jobs GROUP BY status"
    ).fetchall()
    return {r["status"]: int(r["n"]) for r in rows}
