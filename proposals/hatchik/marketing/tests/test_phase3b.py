"""Phase-3b tests — jobs CRUD, worker tick, cron self-reschedule, and
Resend email distribution (Resend HTTP call mocked).

No network. No external libs beyond what tests already import.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("MARKETING_DAILY_CAP_USD", "5.00")
    for var in (
        "ANTHROPIC_API_KEY", "HATCHIK_ANTHROPIC_MASTER_KEY",
        "POSTHOG_API_KEY", "RESEND_API_KEY",
        "X_API_CONSUMER_KEY", "X_API_CONSUMER_SECRET",
        "X_API_ACCESS_TOKEN", "X_API_ACCESS_TOKEN_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    import importlib
    from marketing import (
        anthropic_client, budget, config, content, db, distribute, jobs,
        prompts, runs, schema, seed, strategy, tenant, worker,
    )
    from marketing.integrations import posthog as ph_int, resend as resend_int, x as x_int
    for mod in (config, db, schema, tenant, budget, runs, prompts,
                anthropic_client, content, seed, strategy,
                x_int, ph_int, resend_int, distribute, jobs, worker):
        importlib.reload(mod)
    return db_path


# ─── jobs CRUD ──────────────────────────────────────────────────────────


def test_enqueue_and_claim_one(tmp_db):
    from marketing import db, jobs, schema

    schema.ensure_schema()
    conn = db.connect()
    try:
        jid = jobs.enqueue(conn, kind="run_content", payload={"k": "v"})
        claimed = jobs.claim_one(conn)
        assert claimed is not None
        assert claimed.id == jid
        assert claimed.kind == "run_content"
        assert claimed.payload == {"k": "v"}
        assert claimed.attempts == 1

        # Second claim is None — the only job is running.
        assert jobs.claim_one(conn) is None
    finally:
        conn.close()


def test_claim_one_respects_run_at(tmp_db):
    from marketing import db, jobs, schema

    schema.ensure_schema()
    conn = db.connect()
    try:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        jobs.enqueue(conn, kind="run_content", run_at=future)
        assert jobs.claim_one(conn) is None
    finally:
        conn.close()


def test_claim_one_fifo_by_run_at(tmp_db):
    from marketing import db, jobs, schema

    schema.ensure_schema()
    conn = db.connect()
    try:
        earlier = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        later = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        j_later = jobs.enqueue(conn, kind="A", run_at=later)
        j_earlier = jobs.enqueue(conn, kind="B", run_at=earlier)
        claimed = jobs.claim_one(conn)
        assert claimed.id == j_earlier  # earlier run_at wins
    finally:
        conn.close()


def test_mark_done_and_failed_with_retry(tmp_db):
    from marketing import db, jobs, schema

    schema.ensure_schema()
    conn = db.connect()
    try:
        jid = jobs.enqueue(conn, kind="X", max_attempts=3)
        jobs.claim_one(conn)
        jobs.mark_failed(conn, jid, error="boom 1", backoff_seconds=0)
        row = conn.execute(
            "SELECT status, attempts, last_error FROM marketing_jobs WHERE id=?",
            (jid,),
        ).fetchone()
        # attempts=1, max=3 → requeued
        assert row["status"] == "queued"
        assert row["attempts"] == 1
        assert "boom 1" in row["last_error"]

        # Fail twice more — now terminal.
        jobs.claim_one(conn)
        jobs.mark_failed(conn, jid, error="boom 2", backoff_seconds=0)
        jobs.claim_one(conn)
        jobs.mark_failed(conn, jid, error="boom 3", backoff_seconds=0)
        row = conn.execute(
            "SELECT status, attempts FROM marketing_jobs WHERE id=?", (jid,)
        ).fetchone()
        assert row["status"] == "failed"
        assert row["attempts"] == 3
    finally:
        conn.close()


def test_mark_failed_with_future_run_at(tmp_db):
    """Retry pushes run_at into the future by backoff_seconds."""
    from marketing import db, jobs, schema

    schema.ensure_schema()
    conn = db.connect()
    try:
        jid = jobs.enqueue(conn, kind="X", max_attempts=3)
        jobs.claim_one(conn)
        jobs.mark_failed(conn, jid, error="x", backoff_seconds=120)
        row = conn.execute(
            "SELECT run_at FROM marketing_jobs WHERE id=?", (jid,)
        ).fetchone()
        run_at = datetime.fromisoformat(row["run_at"])
        assert run_at > datetime.now(timezone.utc)
    finally:
        conn.close()


# ─── worker tick + handlers ─────────────────────────────────────────────


def test_worker_tick_empty_returns_none(tmp_db):
    from marketing import db, schema, worker

    schema.ensure_schema()
    conn = db.connect()
    try:
        assert worker.tick(conn) is None
    finally:
        conn.close()


def test_worker_tick_unknown_kind_marks_failed(tmp_db):
    from marketing import db, jobs, schema, worker

    schema.ensure_schema()
    conn = db.connect()
    try:
        jid = jobs.enqueue(conn, kind="bogus_handler", max_attempts=1)
        worker.tick(conn)
        row = conn.execute(
            "SELECT status, last_error FROM marketing_jobs WHERE id=?", (jid,)
        ).fetchone()
        assert row["status"] == "failed"
        assert "no handler registered" in row["last_error"]
    finally:
        conn.close()


def test_worker_tick_dispatches_known_kind(tmp_db, monkeypatch):
    """Patch HANDLERS so we can verify the dispatch without invoking
    the real content agent (which needs an Anthropic key)."""
    from marketing import db, jobs, schema, worker

    calls: list = []

    def fake_handler(conn, job):
        calls.append((job.id, job.kind, job.payload))

    monkeypatch.setitem(worker.HANDLERS, "smoke", fake_handler)

    schema.ensure_schema()
    conn = db.connect()
    try:
        jid = jobs.enqueue(conn, kind="smoke", payload={"hi": "there"})
        ran = worker.tick(conn)
        assert ran is not None
        assert calls == [(jid, "smoke", {"hi": "there"})]
        row = conn.execute(
            "SELECT status FROM marketing_jobs WHERE id=?", (jid,)
        ).fetchone()
        assert row["status"] == "done"
    finally:
        conn.close()


def test_cron_seed_is_idempotent(tmp_db):
    from marketing import db, schema, worker

    schema.ensure_schema()
    conn = db.connect()
    try:
        first = worker.seed_cron(conn)
        second = worker.seed_cron(conn)
        # First call enqueues N seeds; second call sees them as pending
        # and skips.
        assert len(first) == len(worker.CRON_SEEDS)
        assert second == []
    finally:
        conn.close()


def test_cron_handler_reschedules_self(tmp_db, monkeypatch):
    """`daily_content_cron` should enqueue a `run_content` job AND
    re-enqueue itself with a future run_at."""
    from marketing import db, jobs, schema, worker

    # Patch `run_content` handler so the dispatch path doesn't actually
    # call the LLM; we just want to verify the cron handler enqueues.
    def fake_run(conn, job):
        return None

    monkeypatch.setitem(worker.HANDLERS, "run_content", fake_run)

    schema.ensure_schema()
    conn = db.connect()
    try:
        # Seed the cron job and immediately fire it.
        worker.seed_cron(conn)
        # Override the cron seed's run_at to "now" so claim_one picks it.
        conn.execute(
            "UPDATE marketing_jobs SET run_at=? WHERE kind LIKE '%_cron'",
            (datetime.now(timezone.utc).isoformat(),),
        )
        # Pick the daily_content_cron one specifically (claim_one returns
        # by run_at order; both seeds may match now). Loop until we see it.
        for _ in range(3):
            j = worker.tick(conn)
            if j and j.kind == "daily_content_cron":
                break
        else:
            pytest.fail("daily_content_cron never ran")

        # After firing: there should be a queued `run_content` job AND a
        # queued (future) `daily_content_cron` job.
        rows = conn.execute(
            "SELECT kind, status, run_at FROM marketing_jobs WHERE status='queued'"
        ).fetchall()
        kinds = {r["kind"] for r in rows}
        assert "run_content" in kinds
        assert "daily_content_cron" in kinds
    finally:
        conn.close()


# ─── Resend email distribution ──────────────────────────────────────────


def test_distribute_email_dry_run(tmp_db):
    from marketing import content as content_mod, db, distribute, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        iid = content_mod.insert_draft(
            conn, tenant_id=tid, source_run_id=None,
            draft=content_mod.ContentDraft(
                channel="email",
                body="Hi friend, here's some content.",
                pillar="P", angle_hook="A",
                metadata={"subject": "Hello", "preview": "p", "cta": "Sign up →"},
            ),
        )
        content_mod.approve(conn, tenant_id=tid, item_id=iid)

        result = distribute.distribute_item(
            conn, tenant_id=tid, item_id=iid,
            email_to="founder@hatchik.com", dry_run=True,
        )
        assert result["channel"] == "email"
        assert result["dry_run"] is True

        d = conn.execute(
            "SELECT provider FROM marketing_distributions WHERE content_queue_id=?",
            (iid,),
        ).fetchone()
        assert d["provider"] == "resend"
    finally:
        conn.close()


def test_distribute_email_calls_resend(tmp_db, monkeypatch):
    from marketing import content as content_mod, db, distribute, schema, seed
    from marketing.integrations import resend as resend_int

    sent: list[dict] = []

    def fake_send_email(**kwargs):
        sent.append(kwargs)
        return {"id": "rs_abc123"}

    monkeypatch.setattr(resend_int, "send_email", fake_send_email)

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        iid = content_mod.insert_draft(
            conn, tenant_id=tid, source_run_id=None,
            draft=content_mod.ContentDraft(
                channel="email",
                body="Hello, content here.",
                pillar="P", angle_hook="A",
                metadata={"subject": "Greetings", "preview": "p", "cta": "Go"},
            ),
        )
        content_mod.approve(conn, tenant_id=tid, item_id=iid)

        result = distribute.distribute_item(
            conn, tenant_id=tid, item_id=iid,
            email_to="founder@hatchik.com",
        )
        assert sent and sent[0]["subject"] == "Greetings"
        assert result["external_ids"] == ["rs_abc123"]
    finally:
        conn.close()


def test_distribute_email_requires_recipient(tmp_db):
    from marketing import content as content_mod, db, distribute, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        iid = content_mod.insert_draft(
            conn, tenant_id=tid, source_run_id=None,
            draft=content_mod.ContentDraft(
                channel="email",
                body="An email body here.",
                pillar="P", angle_hook="A",
                metadata={"subject": "S", "preview": "p", "cta": "Go"},
            ),
        )
        content_mod.approve(conn, tenant_id=tid, item_id=iid)
        with pytest.raises(distribute.DistributionError):
            distribute.distribute_item(conn, tenant_id=tid, item_id=iid, dry_run=True)
    finally:
        conn.close()
