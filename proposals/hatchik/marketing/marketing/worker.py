"""Job worker.

Drains `marketing_jobs` one at a time. The "scheduler" is just a set
of self-rescheduling cron jobs — each `*_cron` handler enqueues the
next instance after running. So a single tick loop is both scheduler
and worker.

Use:
  python -m marketing.cli scheduler init   # enqueue the cron seeds
  python -m marketing.cli scheduler start  # foreground loop
  python -m marketing.cli worker tick      # single-shot for debugging
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import db, jobs as jobs_mod

_log = logging.getLogger(__name__)


# ─── cron rule definitions ──────────────────────────────────────────────


def _next_at(hour: int, minute: int = 0) -> str:
    """Next UTC datetime at HH:MM in the future."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.isoformat()


def _next_top_of_hour() -> str:
    now = datetime.now(timezone.utc)
    target = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return target.isoformat()


# Seed entries enqueued once by `scheduler init`. After that, each
# handler reschedules itself, so the queue self-sustains.
CRON_SEEDS: list[dict[str, Any]] = [
    {
        "kind": "daily_content_cron",
        "tenant_slug": "hatchik",
        "first_run_at": lambda: _next_at(9, 0),  # 09:00 UTC daily
    },
    {
        "kind": "hourly_distribute_cron",
        "tenant_slug": "hatchik",
        "first_run_at": lambda: _next_top_of_hour(),
    },
]


# ─── handlers ───────────────────────────────────────────────────────────


def _handle_run_content(conn: sqlite3.Connection, job: jobs_mod.Job) -> None:
    from .agents.content import BatchPlan, run as run_content

    plan_kw = job.payload.get("plan", {})
    plan = BatchPlan(**plan_kw) if plan_kw else BatchPlan()
    summary = run_content(
        tenant_slug=job.payload.get("tenant_slug", "hatchik"),
        plan=plan,
    )
    _log.info("run_content done: %d items queued (cost $%.4f)",
              summary["items_queued"], summary["cost_usd"])


def _handle_distribute_due(conn: sqlite3.Connection, job: jobs_mod.Job) -> None:
    from . import distribute as dist_mod, tenant as tenant_mod

    t = tenant_mod.get_by_slug(conn, job.payload.get("tenant_slug", "hatchik"))
    results = dist_mod.distribute_due(
        conn,
        tenant_id=t.id,
        limit=int(job.payload.get("limit", 5)),
        dry_run=bool(job.payload.get("dry_run", False)),
    )
    ok = sum(1 for r in results if "error" not in r)
    err = len(results) - ok
    _log.info("distribute_due: %d ok, %d err", ok, err)


def _handle_daily_content_cron(conn: sqlite3.Connection, job: jobs_mod.Job) -> None:
    """Enqueue today's content batch + schedule next daily run."""
    jobs_mod.enqueue(
        conn,
        kind="run_content",
        tenant_id=job.tenant_id,
        payload={"tenant_slug": job.payload.get("tenant_slug", "hatchik")},
    )
    jobs_mod.enqueue(
        conn,
        kind="daily_content_cron",
        tenant_id=job.tenant_id,
        payload=job.payload,
        run_at=_next_at(9, 0),
    )


def _handle_hourly_distribute_cron(conn: sqlite3.Connection, job: jobs_mod.Job) -> None:
    jobs_mod.enqueue(
        conn,
        kind="distribute_due",
        tenant_id=job.tenant_id,
        payload={"tenant_slug": job.payload.get("tenant_slug", "hatchik")},
    )
    jobs_mod.enqueue(
        conn,
        kind="hourly_distribute_cron",
        tenant_id=job.tenant_id,
        payload=job.payload,
        run_at=_next_top_of_hour(),
    )


HANDLERS: dict[str, Callable[[sqlite3.Connection, jobs_mod.Job], None]] = {
    "run_content": _handle_run_content,
    "distribute_due": _handle_distribute_due,
    "daily_content_cron": _handle_daily_content_cron,
    "hourly_distribute_cron": _handle_hourly_distribute_cron,
}


# ─── tick loop ──────────────────────────────────────────────────────────


def tick(conn: sqlite3.Connection) -> jobs_mod.Job | None:
    """Run at most one job. Returns the Job if one ran (success or
    failure), None if the queue was empty."""
    job = jobs_mod.claim_one(conn)
    if job is None:
        return None
    handler = HANDLERS.get(job.kind)
    if handler is None:
        jobs_mod.mark_failed(
            conn, job.id, error=f"no handler registered for kind {job.kind!r}"
        )
        return job
    try:
        handler(conn, job)
        jobs_mod.mark_done(conn, job.id)
    except Exception as exc:
        _log.exception("job %d (%s) failed", job.id, job.kind)
        jobs_mod.mark_failed(conn, job.id, error=f"{type(exc).__name__}: {exc}")
    return job


def seed_cron(conn: sqlite3.Connection) -> list[int]:
    """Enqueue the cron seed entries if they're not already pending.
    Idempotent — won't double-enqueue if the queue already has a
    `*_cron` job in queued/running state."""
    enqueued: list[int] = []
    for seed in CRON_SEEDS:
        existing = conn.execute(
            """
            SELECT 1 FROM marketing_jobs
            WHERE kind = ? AND status IN ('queued','running')
            LIMIT 1
            """,
            (seed["kind"],),
        ).fetchone()
        if existing is not None:
            continue
        job_id = jobs_mod.enqueue(
            conn,
            kind=seed["kind"],
            payload={"tenant_slug": seed["tenant_slug"]},
            run_at=seed["first_run_at"](),
        )
        enqueued.append(job_id)
    return enqueued


def run_forever(
    sleep_seconds: float = 10.0,
    *,
    max_iterations: int | None = None,
) -> None:
    """Foreground loop. Ctrl-C to stop. `max_iterations` is for tests."""
    conn = db.connect()
    try:
        iteration = 0
        while True:
            if max_iterations is not None and iteration >= max_iterations:
                return
            iteration += 1
            ran = tick(conn)
            if ran is None:
                time.sleep(sleep_seconds)
    finally:
        conn.close()
