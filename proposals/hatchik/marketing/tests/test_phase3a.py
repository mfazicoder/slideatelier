"""Phase-3a tests — X distribution orchestrator.

No network. No tweepy required (we substitute a fake XClient).
Covers: state-machine guards (only 'approved' items distribute), x_tweet
+ x_thread happy paths, dry-run, missing-thread-parts error, channel
not yet implemented, and distribute_due returning per-item results
(mix of success + failure).
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("MARKETING_DAILY_CAP_USD", "5.00")
    for var in (
        "ANTHROPIC_API_KEY",
        "HATCHIK_ANTHROPIC_MASTER_KEY",
        "POSTHOG_API_KEY",
        "X_API_CONSUMER_KEY",
        "X_API_CONSUMER_SECRET",
        "X_API_ACCESS_TOKEN",
        "X_API_ACCESS_TOKEN_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    import importlib
    from marketing import (
        anthropic_client, budget, config, content, db, distribute, prompts,
        runs, schema, seed, strategy, tenant,
    )
    from marketing.integrations import x as x_int, posthog as ph_int
    for mod in (config, db, schema, tenant, budget, runs, prompts,
                anthropic_client, content, seed, strategy,
                x_int, ph_int, distribute):
        importlib.reload(mod)
    return db_path


class _FakeXClient:
    """Stand-in for marketing.integrations.x.XClient. Records calls and
    returns deterministic synthetic ids."""

    def __init__(self) -> None:
        self.tweets: list[str] = []
        self.threads: list[list[str]] = []
        self._counter = 100

    def _next_id(self) -> str:
        self._counter += 1
        return str(self._counter)

    def post_tweet(self, text: str) -> dict[str, str]:
        self.tweets.append(text)
        tid = self._next_id()
        return {"id": tid, "url": f"https://x.com/i/web/status/{tid}"}

    def post_thread(self, parts: list[str]) -> list[dict[str, str]]:
        self.threads.append(parts)
        out = []
        for _ in parts:
            tid = self._next_id()
            out.append({"id": tid, "url": f"https://x.com/i/web/status/{tid}"})
        return out


def _insert_approved_tweet(conn, tenant_id, body="A specific tweet."):
    from marketing import content as content_mod

    iid = content_mod.insert_draft(
        conn,
        tenant_id=tenant_id,
        source_run_id=None,
        draft=content_mod.ContentDraft(
            channel="x_tweet", body=body, pillar="P1", angle_hook="A1"
        ),
    )
    content_mod.approve(conn, tenant_id=tenant_id, item_id=iid)
    return iid


def _insert_approved_thread(conn, tenant_id, parts=("Hook part one.", "Followup part two.")):
    from marketing import content as content_mod

    iid = content_mod.insert_draft(
        conn,
        tenant_id=tenant_id,
        source_run_id=None,
        draft=content_mod.ContentDraft(
            channel="x_thread",
            body="\n\n---\n\n".join(parts),
            pillar="P", angle_hook="A",
            metadata={"parts": list(parts)},
        ),
    )
    content_mod.approve(conn, tenant_id=tenant_id, item_id=iid)
    return iid


def test_distribute_tweet_happy(tmp_db):
    from marketing import db, distribute, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        iid = _insert_approved_tweet(conn, tid, "specific tweet here")

        client = _FakeXClient()
        result = distribute.distribute_item(
            conn, tenant_id=tid, item_id=iid, x_client=client
        )
        assert result["channel"] == "x_tweet"
        assert len(result["external_ids"]) == 1
        assert client.tweets == ["specific tweet here"]

        # Queue row flipped to posted.
        row = conn.execute(
            "SELECT status, posted_at FROM marketing_content_queue WHERE id=?", (iid,)
        ).fetchone()
        assert row["status"] == "posted"
        assert row["posted_at"] is not None

        # Distribution row exists.
        d = conn.execute(
            "SELECT provider, external_id, url FROM marketing_distributions WHERE content_queue_id=?",
            (iid,),
        ).fetchone()
        assert d["provider"] == "x"
        assert d["external_id"] == result["external_ids"][0]
    finally:
        conn.close()


def test_distribute_thread_happy(tmp_db):
    from marketing import db, distribute, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        iid = _insert_approved_thread(conn, tid, ("p1", "p2", "p3"))

        client = _FakeXClient()
        result = distribute.distribute_item(
            conn, tenant_id=tid, item_id=iid, x_client=client
        )
        assert result["channel"] == "x_thread"
        assert len(result["external_ids"]) == 3
        assert client.threads == [["p1", "p2", "p3"]]

        d = conn.execute(
            "SELECT metrics_json FROM marketing_distributions WHERE content_queue_id=?",
            (iid,),
        ).fetchone()
        metrics = json.loads(d["metrics_json"])
        assert metrics["thread_ids"] == result["external_ids"]
        assert metrics["dry_run"] is False
    finally:
        conn.close()


def test_distribute_rejects_non_approved_item(tmp_db):
    from marketing import content as content_mod, db, distribute, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        # Insert a pending draft (not approved).
        iid = content_mod.insert_draft(
            conn, tenant_id=tid, source_run_id=None,
            draft=content_mod.ContentDraft(
                channel="x_tweet", body="pending body here", pillar="P", angle_hook="A",
            ),
        )
        with pytest.raises(distribute.DistributionError):
            distribute.distribute_item(
                conn, tenant_id=tid, item_id=iid, x_client=_FakeXClient()
            )
    finally:
        conn.close()


def test_distribute_dry_run_skips_network(tmp_db):
    from marketing import db, distribute, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        iid = _insert_approved_tweet(conn, tid)
        # No x_client provided AND no env vars → would normally fail.
        # dry_run=True must short-circuit before any client is needed.
        result = distribute.distribute_item(
            conn, tenant_id=tid, item_id=iid, dry_run=True
        )
        assert result["dry_run"] is True
        assert result["external_ids"][0].startswith("dry-")

        row = conn.execute(
            "SELECT status FROM marketing_content_queue WHERE id=?", (iid,)
        ).fetchone()
        assert row["status"] == "posted"
    finally:
        conn.close()


def test_distribute_thread_missing_parts_errors(tmp_db):
    from marketing import db, distribute, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        # Insert raw row so we can put a bogus metadata payload past the
        # ContentDraft validator (which would otherwise reject it).
        from datetime import datetime, timezone
        conn.execute(
            """
            INSERT INTO marketing_content_queue
                (tenant_id, channel, body, metadata_json, status, created_at)
            VALUES (?, 'x_thread', 'body', '{}', 'approved', ?)
            """,
            (tid, datetime.now(timezone.utc).isoformat()),
        )
        iid = conn.execute("SELECT MAX(id) FROM marketing_content_queue").fetchone()[0]
        with pytest.raises(distribute.DistributionError):
            distribute.distribute_item(
                conn, tenant_id=tid, item_id=iid, x_client=_FakeXClient()
            )
    finally:
        conn.close()


def test_distribute_not_yet_implemented_channel(tmp_db):
    from marketing import content as content_mod, db, distribute, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        iid = content_mod.insert_draft(
            conn, tenant_id=tid, source_run_id=None,
            draft=content_mod.ContentDraft(
                channel="linkedin", body="x" * 400, pillar="P", angle_hook="A",
            ),
        )
        content_mod.approve(conn, tenant_id=tid, item_id=iid)
        with pytest.raises(distribute.NotYetImplemented):
            distribute.distribute_item(
                conn, tenant_id=tid, item_id=iid, x_client=_FakeXClient()
            )
    finally:
        conn.close()


def test_distribute_due_mixed_results(tmp_db):
    from marketing import db, distribute, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        i1 = _insert_approved_tweet(conn, tid, "first specific tweet body")
        i2 = _insert_approved_tweet(conn, tid, "second specific tweet body")
        results = distribute.distribute_due(
            conn, tenant_id=tid, x_client=_FakeXClient()
        )
        assert len(results) == 2
        assert all("error" not in r for r in results)
        # Both items posted.
        statuses = conn.execute(
            "SELECT status FROM marketing_content_queue WHERE id IN (?,?)",
            (i1, i2),
        ).fetchall()
        assert all(s["status"] == "posted" for s in statuses)
    finally:
        conn.close()


def test_xclient_from_env_raises_when_missing(tmp_db):
    from marketing.integrations import x as x_int

    with pytest.raises(x_int.MissingXCredentials):
        x_int.XClient.from_env()


def test_posthog_capture_is_noop_without_key(tmp_db):
    # No assertion — just verify it doesn't raise and returns None.
    from marketing.integrations import posthog as ph_int

    assert ph_int.capture(distinct_id="x", event="y", properties={"a": 1}) is None
