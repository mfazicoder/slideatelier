"""Phase-4 tests — analytics aggregation, AnalysisReport schema,
analyze agent end-to-end with mocked LLM (auto-bumps strategy).

No network, no tweepy required.
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
        analysis, analytics, anthropic_client, budget, config, content, db,
        distribute, jobs, prompts, runs, schema, seed, strategy, tenant, worker,
    )
    from marketing.integrations import posthog as ph_int, resend as resend_int, x as x_int
    for mod in (config, db, schema, tenant, budget, runs, prompts,
                anthropic_client, content, seed, strategy,
                x_int, ph_int, resend_int, distribute, jobs, worker,
                analytics, analysis):
        importlib.reload(mod)
    return db_path


def _valid_strategy_payload(angles_per_pillar: int = 8) -> dict:
    rotation = ["x_tweet", "x_thread", "linkedin", "blog", "email"]
    return {
        "icp": {
            "primary": "Solo founders shipping their first AI-tool-built SaaS",
            "company_type": "solo founder",
            "team_size": "1",
            "stage": "pre-revenue",
            "geo": "global",
            "pain_points": ["wiring takes a weekend"],
            "buying_triggers": ["first Stripe webhook works"],
            "excludes": ["FAANG engineers who self-host"],
        },
        "sub_personas": [
            {"name": "Consultant", "role": "indie",
             "context": "side SaaS", "objection": "wait for sale", "hook": "free Sandbox"},
            {"name": "Builder", "role": "non-eng",
             "context": "stuck on infra", "objection": "lock-in", "hook": "owns repo"},
        ],
        "voice": {
            "tone_attributes": ["concrete", "unhedged"],
            "do": ["Use numbers"],
            "dont": ["Em-dashes"],
            "example_phrases": ["£89 once"],
        },
        "pillars": [
            {
                "name": f"Pillar {i}",
                "description": f"Pillar {i} desc",
                "why_it_matters": f"why {i}",
                "angles": [
                    {
                        "hook": f"P{i} angle {j} — specific scenario goes here",
                        "format_hint": rotation[(i + j) % len(rotation)],
                    }
                    for j in range(angles_per_pillar)
                ],
            }
            for i in range(1, 6)
        ],
    }


def _valid_analysis_payload() -> dict:
    return {
        "summary": {
            "posts_distributed": 5,
            "channels_breakdown": {"x_tweet": 3, "x_thread": 2},
            "engagement_total": 120,
            "engagement_per_post": {"median": 20, "p90": 50, "max": 60},
            "best_post_id": 3,
            "worst_post_id": 1,
            "pillar_performance": {"Pillar 1": {"posts": 2, "engagement": 40}},
        },
        "winners": [
            {"distribution_id": 3, "pillar": "Pillar 2",
             "what": "Question-led hook, posted at 9am UTC",
             "lesson": "Question hooks beat stat hooks for this audience"}
        ],
        "losers": [
            {"distribution_id": 1, "pillar": "Pillar 5",
             "what": "Too generic 'here's what I learned' opener",
             "lesson": "Drop the throat-clearing opener"}
        ],
        "hypotheses": [
            "Threads opened with a question outperform threads opened with a stat",
            "Posts at 9am UTC beat posts at 5pm UTC",
        ],
        "strategy_changes": {
            "voice_do_additions": ["Open threads with a question"],
            "voice_dont_additions": ["Open with 'here's what I learned'"],
            "pillars_to_amplify": ["Pillar 2"],
            "pillars_to_deprecate": [],
            "new_angles_per_pillar": {"Pillar 2": ["A new specific angle"]},
            "icp_refinements": ["ICP skews to consultants more than indie hackers"],
        },
        "updated_strategy": _valid_strategy_payload(),
    }


# ─── AnalysisReport schema ──────────────────────────────────────────────


def test_analysis_parse_happy(tmp_db):
    from marketing import analysis

    r = analysis.parse(json.dumps(_valid_analysis_payload()))
    assert len(r.winners) == 1
    assert len(r.losers) == 1
    assert len(r.hypotheses) == 2
    assert len(r.updated_strategy.pillars) == 5


def test_analysis_parse_strips_fences(tmp_db):
    from marketing import analysis

    raw = "```json\n" + json.dumps(_valid_analysis_payload()) + "\n```"
    r = analysis.parse(raw)
    assert r.strategy_changes.pillars_to_amplify == ["Pillar 2"]


def test_analysis_parse_rejects_invalid_json(tmp_db):
    from marketing import analysis

    with pytest.raises(analysis.AnalysisParseError):
        analysis.parse("{not: valid")


def test_analysis_parse_rejects_bad_updated_strategy(tmp_db):
    from marketing import analysis

    payload = _valid_analysis_payload()
    payload["updated_strategy"]["pillars"] = payload["updated_strategy"]["pillars"][:2]
    with pytest.raises(analysis.AnalysisParseError):
        analysis.parse(json.dumps(payload))


# ─── analytics aggregation ──────────────────────────────────────────────


def test_recent_distributions_filters_by_window(tmp_db):
    from marketing import analytics, content, db, distribute, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        iid = content.insert_draft(
            conn, tenant_id=tid, source_run_id=None,
            draft=content.ContentDraft(
                channel="x_tweet", body="Some specific tweet body.",
                pillar="P", angle_hook="A",
            ),
        )
        content.approve(conn, tenant_id=tid, item_id=iid)
        distribute.distribute_item(conn, tenant_id=tid, item_id=iid, dry_run=True)

        # The dry-run row's external_id starts with 'dry-', and recent
        # distributions includes everything regardless — that's
        # intentional, we want the agent to see them in analysis.
        rows = analytics.recent_distributions_with_metrics(
            conn, tenant_id=tid, days=7
        )
        assert len(rows) == 1
        assert rows[0]["channel"] == "x_tweet"
        assert rows[0]["pillar"] == "P"
        assert rows[0]["angle_hook"] == "A"

        # Old distribution — out of window.
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn.execute(
            "UPDATE marketing_distributions SET posted_at=?", (old,)
        )
        rows = analytics.recent_distributions_with_metrics(
            conn, tenant_id=tid, days=7
        )
        assert rows == []
    finally:
        conn.close()


def test_recent_listening_signals(tmp_db):
    from marketing import analytics, db, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO marketing_listening_signals
                (tenant_id, source, query, raw_json, sentiment, captured_at)
            VALUES (?, 'x_search', 'hatchik', '{}', 'pos', ?)
            """,
            (tid, now),
        )
        signals = analytics.recent_listening_signals(conn, tenant_id=tid, days=7)
        assert len(signals) == 1
        assert signals[0]["source"] == "x_search"
        assert signals[0]["sentiment"] == "pos"
    finally:
        conn.close()


# ─── refresh_x_metrics with fake tweepy client ──────────────────────────


def test_refresh_x_metrics_skips_dry_run_rows(tmp_db):
    from marketing import analytics, content, db, distribute, schema, seed
    from marketing.integrations import x as x_int

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        iid = content.insert_draft(
            conn, tenant_id=tid, source_run_id=None,
            draft=content.ContentDraft(
                channel="x_tweet", body="A specific tweet body.",
                pillar="P", angle_hook="A",
            ),
        )
        content.approve(conn, tenant_id=tid, item_id=iid)
        distribute.distribute_item(conn, tenant_id=tid, item_id=iid, dry_run=True)

        # All rows have external_id like 'dry-…' — filter excludes them.
        # No x_client needed, no tweepy reached.
        stats = analytics.refresh_x_metrics(conn, tenant_id=tid)
        assert stats == {"refreshed": 0, "errors": 0, "skipped_dry": 0}
    finally:
        conn.close()


def test_refresh_x_metrics_updates_real_rows(tmp_db):
    """Inject a fake distribution with a non-dry external_id, then
    monkeypatch XClient._tweepy to return a fake client with public
    metrics."""
    from datetime import datetime, timezone
    from marketing import analytics, db, schema, seed
    from marketing.integrations import x as x_int

    class _FakeTweet:
        def __init__(self, m):
            self.public_metrics = m

    class _FakeResp:
        def __init__(self, m):
            self.data = _FakeTweet(m)

    class _FakeTweepyClient:
        def get_tweet(self, tweet_id, tweet_fields=None):
            return _FakeResp({"like_count": 12, "reply_count": 3, "retweet_count": 1})

    class _FakeXClient:
        def _tweepy(self):
            return _FakeTweepyClient()

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        # Insert a content_queue row + distribution row manually so we
        # can give it a "real" external_id.
        conn.execute(
            """
            INSERT INTO marketing_content_queue
                (tenant_id, channel, body, status, created_at)
            VALUES (?, 'x_tweet', 'body', 'posted', ?)
            """,
            (tid, datetime.now(timezone.utc).isoformat()),
        )
        qid = conn.execute("SELECT MAX(id) FROM marketing_content_queue").fetchone()[0]
        conn.execute(
            """
            INSERT INTO marketing_distributions
                (tenant_id, content_queue_id, provider, external_id, url, posted_at, metrics_json)
            VALUES (?, ?, 'x', '999', 'https://x.com/i/web/status/999', ?, '{}')
            """,
            (tid, qid, datetime.now(timezone.utc).isoformat()),
        )

        stats = analytics.refresh_x_metrics(conn, tenant_id=tid, x_client=_FakeXClient())
        assert stats["refreshed"] == 1
        assert stats["errors"] == 0

        row = conn.execute(
            "SELECT metrics_json, last_metrics_fetched_at FROM marketing_distributions WHERE external_id='999'"
        ).fetchone()
        m = json.loads(row["metrics_json"])
        assert m["x_public_metrics"]["like_count"] == 12
        assert row["last_metrics_fetched_at"] is not None
    finally:
        conn.close()


# ─── analyze agent end-to-end (mocked LLM) ──────────────────────────────


def test_analyze_agent_auto_promotes_strategy(tmp_db, monkeypatch):
    from marketing import anthropic_client, db, schema, seed, strategy as strategy_mod
    from marketing.agents import analyze as analyze_agent

    schema.ensure_schema()
    conn = db.connect()
    try:
        seed.seed_hatchik_tenant(conn)
        s = strategy_mod.parse(json.dumps(_valid_strategy_payload()))
        strategy_mod.save(conn, tenant_id=1, strategy=s, source_run_id=None)
    finally:
        conn.close()

    payload = _valid_analysis_payload()

    def fake_complete(conn, **kwargs):
        from marketing import runs as runs_mod
        run_id = runs_mod.start_run(
            conn, tenant_id=kwargs["tenant_id"], layer=kwargs["layer"],
            model=kwargs["model"], input_payload={"system": "(mocked)"},
            prompt_name=kwargs.get("prompt_name"), prompt_version=kwargs.get("prompt_version"),
        )
        runs_mod.finish_run(
            conn, run_id, status="success",
            output_payload={"text": json.dumps(payload)},
            tokens_in=5000, tokens_out=3000, cost_usd=0.30,
        )
        return {
            "run_id": run_id, "text": json.dumps(payload),
            "tokens_in": 5000, "tokens_out": 3000,
            "cache_read": 4000, "cache_creation": 0, "cost_usd": 0.30,
        }

    monkeypatch.setattr(anthropic_client, "complete", fake_complete)

    result = analyze_agent.run(tenant_slug="hatchik")
    assert result["prior_strategy_version"] == 1
    assert result["new_strategy_version"] == 2

    # Verify the strategy bumped.
    conn = db.connect()
    try:
        current = strategy_mod.current(conn, result["tenant_id"])
        assert current is not None
        ver, _ = current
        assert ver == 2
    finally:
        conn.close()


def test_analyze_agent_no_auto_promote(tmp_db, monkeypatch):
    from marketing import anthropic_client, db, schema, seed, strategy as strategy_mod
    from marketing.agents import analyze as analyze_agent

    schema.ensure_schema()
    conn = db.connect()
    try:
        seed.seed_hatchik_tenant(conn)
        s = strategy_mod.parse(json.dumps(_valid_strategy_payload()))
        strategy_mod.save(conn, tenant_id=1, strategy=s, source_run_id=None)
    finally:
        conn.close()

    def fake_complete(conn, **kwargs):
        from marketing import runs as runs_mod
        rid = runs_mod.start_run(
            conn, tenant_id=kwargs["tenant_id"], layer=kwargs["layer"],
            model=kwargs["model"], input_payload={},
        )
        runs_mod.finish_run(
            conn, rid, status="success",
            output_payload={"text": json.dumps(_valid_analysis_payload())},
            tokens_in=100, tokens_out=100, cost_usd=0.01,
        )
        return {"run_id": rid, "text": json.dumps(_valid_analysis_payload()),
                "tokens_in": 100, "tokens_out": 100, "cost_usd": 0.01,
                "cache_read": 0, "cache_creation": 0}

    monkeypatch.setattr(anthropic_client, "complete", fake_complete)

    result = analyze_agent.run(tenant_slug="hatchik", auto_promote_strategy=False)
    assert result["new_strategy_version"] is None
    conn = db.connect()
    try:
        current = strategy_mod.current(conn, result["tenant_id"])
        ver, _ = current
        assert ver == 1  # not bumped
    finally:
        conn.close()


def test_analyze_agent_no_strategy_raises(tmp_db):
    from marketing import db, schema, seed
    from marketing.agents import analyze as analyze_agent

    schema.ensure_schema()
    conn = db.connect()
    try:
        seed.seed_hatchik_tenant(conn)
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="no current strategy"):
        analyze_agent.run(tenant_slug="hatchik")


def test_analysis_latest_report_round_trips(tmp_db, monkeypatch):
    from marketing import analysis, anthropic_client, db, schema, seed, strategy as strategy_mod
    from marketing.agents import analyze as analyze_agent

    schema.ensure_schema()
    conn = db.connect()
    try:
        seed.seed_hatchik_tenant(conn)
        s = strategy_mod.parse(json.dumps(_valid_strategy_payload()))
        strategy_mod.save(conn, tenant_id=1, strategy=s, source_run_id=None)
    finally:
        conn.close()

    payload = _valid_analysis_payload()

    def fake_complete(conn, **kwargs):
        from marketing import runs as runs_mod
        rid = runs_mod.start_run(
            conn, tenant_id=kwargs["tenant_id"], layer=kwargs["layer"],
            model=kwargs["model"], input_payload={},
        )
        runs_mod.finish_run(
            conn, rid, status="success",
            output_payload={"text": json.dumps(payload)},
            tokens_in=100, tokens_out=100, cost_usd=0.01,
        )
        return {"run_id": rid, "text": json.dumps(payload),
                "tokens_in": 100, "tokens_out": 100, "cost_usd": 0.01,
                "cache_read": 0, "cache_creation": 0}

    monkeypatch.setattr(anthropic_client, "complete", fake_complete)
    res = analyze_agent.run(tenant_slug="hatchik")

    conn = db.connect()
    try:
        out = analysis.latest_report(conn, tenant_id=res["tenant_id"])
        assert out is not None
        run_id, report = out
        assert run_id == res["run_id"]
        assert len(report.winners) == 1
    finally:
        conn.close()
