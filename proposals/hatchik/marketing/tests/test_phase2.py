"""Phase-2 tests — content schemas, angle picker, queue state
transitions, and a mocked content-agent end-to-end.

No network, no Anthropic key required.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("MARKETING_DAILY_CAP_USD", "5.00")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HATCHIK_ANTHROPIC_MASTER_KEY", raising=False)

    import importlib
    from marketing import (
        anthropic_client, budget, config, content, db, prompts,
        runs, schema, seed, strategy, tenant,
    )
    for mod in (config, db, schema, tenant, budget, runs, prompts,
                anthropic_client, content, seed, strategy):
        importlib.reload(mod)
    return db_path


def _valid_strategy_payload(angles_per_pillar: int = 9) -> dict:
    # Distribute format_hints so the picker has some matches and some misses.
    rotation = ["x_tweet", "x_thread", "linkedin", "blog", "email"]
    return {
        "icp": {
            "primary": "Solo founders shipping their first AI-tool-built SaaS",
            "company_type": "solo founder",
            "team_size": "1",
            "stage": "pre-revenue",
            "geo": "global",
            "pain_points": ["wiring infra takes a weekend"],
            "buying_triggers": ["first Stripe webhook works"],
            "excludes": ["FAANG engineers who self-host"],
        },
        "sub_personas": [
            {"name": "Consultant", "role": "Indie consultant",
             "context": "Side SaaS at night", "objection": "Won't pay until first sale",
             "hook": "Free Sandbox until charging"},
            {"name": "First-time builder", "role": "Non-eng using Cursor",
             "context": "Stuck on infra", "objection": "Lock-in fear",
             "hook": "Customer owns repo + VPS"},
        ],
        "voice": {
            "tone_attributes": ["concrete", "unhedged"],
            "do": ["Use numbers", "Name competitors"],
            "dont": ["Em-dashes", "Buzzwords"],
            "example_phrases": ["£89 once, £14/mo from month two"],
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


# ─── ContentDraft schema tests ──────────────────────────────────────────


def test_draft_x_tweet_validates(tmp_db):
    from marketing import content

    d = content.ContentDraft(
        channel="x_tweet",
        body="A short tweet that's under 280 chars.",
        pillar="Pillar 1",
        angle_hook="An angle",
    )
    assert d.channel == "x_tweet"


def test_draft_x_tweet_rejects_too_long_body(tmp_db):
    from marketing import content

    with pytest.raises(Exception):
        content.ContentDraft(
            channel="x_tweet",
            body="x" * 300,
            pillar="Pillar 1",
            angle_hook="Angle",
        )


def test_draft_x_thread_requires_parts(tmp_db):
    from marketing import content

    d = content.ContentDraft(
        channel="x_thread",
        body="part1\n\n---\n\npart2",
        pillar="Pillar 1",
        angle_hook="Angle",
        metadata={"parts": ["part1", "part2"]},
    )
    assert d.metadata["parts"] == ["part1", "part2"]

    with pytest.raises(Exception):
        content.ContentDraft(
            channel="x_thread", body="x", pillar="p", angle_hook="a",
            metadata={"parts": ["only one"]},
        )


def test_draft_blog_requires_full_outline_metadata(tmp_db):
    from marketing import content

    md = {
        "title": "A working title for the blog post",
        "hook": "An opening hook sentence.",
        "sections": [
            {"heading": "Section 1", "bullets": ["b1", "b2"]},
            {"heading": "Section 2", "bullets": ["b1", "b2"]},
            {"heading": "Section 3", "bullets": ["b1", "b2"]},
        ],
        "key_takeaway": "The single takeaway.",
    }
    content.ContentDraft(
        channel="blog",
        body="## Section 1\n- b1\n- b2",
        pillar="p", angle_hook="a", metadata=md,
    )

    bad = dict(md)
    bad["sections"] = bad["sections"][:2]  # too few
    with pytest.raises(Exception):
        content.ContentDraft(
            channel="blog", body="x", pillar="p", angle_hook="a", metadata=bad
        )


def test_parse_draft_strips_fences(tmp_db):
    from marketing import content

    raw = "```json\n" + json.dumps({
        "channel": "x_tweet",
        "body": "A tweet.",
        "pillar": "P", "angle_hook": "A",
    }) + "\n```"
    d = content.parse_draft(raw)
    assert d.body == "A tweet."


# ─── angle picker tests ─────────────────────────────────────────────────


def test_pick_angles_dedupes_and_fills(tmp_db):
    from marketing import strategy
    from marketing.agents.content import BatchPlan, pick_angles

    strat = strategy.parse(json.dumps(_valid_strategy_payload()))
    plan = BatchPlan(x_tweet=3, x_thread=1, linkedin=1, blog=1, email=0)
    picks = pick_angles(strat, plan, seed=42)

    assert len(picks) == 6
    keys = {(p.pillar.name, p.angle.hook) for p in picks}
    assert len(keys) == 6  # no repeats


def test_pick_angles_prefers_matching_format(tmp_db):
    from marketing import strategy
    from marketing.agents.content import BatchPlan, pick_angles

    strat = strategy.parse(json.dumps(_valid_strategy_payload()))
    plan = BatchPlan(x_tweet=3, x_thread=0, linkedin=0, blog=0, email=0)
    picks = pick_angles(strat, plan, seed=1)
    # Every angle in the pool labeled x_tweet should be eligible; with
    # 5 pillars × 9 angles ≈ 9 x_tweet-tagged angles available, 3
    # picks all match.
    assert all(p.angle.format_hint == "x_tweet" for p in picks)


def test_pick_angles_falls_back_when_pool_empty(tmp_db):
    from marketing import strategy
    from marketing.agents.content import BatchPlan, pick_angles

    payload = _valid_strategy_payload()
    # Force every angle to format_hint=blog so there are no x_tweet matches.
    for pillar in payload["pillars"]:
        for angle in pillar["angles"]:
            angle["format_hint"] = "blog"
    strat = strategy.parse(json.dumps(payload))

    plan = BatchPlan(x_tweet=2, x_thread=0, linkedin=0, blog=0, email=0)
    picks = pick_angles(strat, plan, seed=7)
    assert len(picks) == 2
    assert all(p.channel == "x_tweet" for p in picks)
    # The picked angles are blog-tagged because there were no x_tweet
    # ones to honor — fallback worked.
    assert all(p.angle.format_hint == "blog" for p in picks)


# ─── queue state transitions ────────────────────────────────────────────


def test_queue_insert_approve_reject(tmp_db):
    from marketing import content, db, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        d = content.ContentDraft(
            channel="x_tweet", body="A tweet.", pillar="P1", angle_hook="A1",
        )
        item_id = content.insert_draft(conn, tenant_id=tid, draft=d, source_run_id=None)

        # Approve once succeeds.
        assert content.approve(conn, tenant_id=tid, item_id=item_id) is True
        # Second approve is a no-op (already approved, not pending).
        assert content.approve(conn, tenant_id=tid, item_id=item_id) is False
        # Reject after approve is also a no-op (state machine guard).
        assert content.reject(conn, tenant_id=tid, item_id=item_id, reason="x") is False
    finally:
        conn.close()


def test_queue_reject_writes_reason(tmp_db):
    from marketing import content, db, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        d = content.ContentDraft(
            channel="x_tweet", body="Another tweet.", pillar="P", angle_hook="A",
        )
        iid = content.insert_draft(conn, tenant_id=tid, draft=d, source_run_id=None)

        assert content.reject(conn, tenant_id=tid, item_id=iid, reason="too generic") is True
        row = content.get_item(conn, tenant_id=tid, item_id=iid)
        assert row["status"] == "rejected"
        assert row["rejection_reason"] == "too generic"
    finally:
        conn.close()


def test_queue_stats_aggregates(tmp_db):
    from marketing import content, db, schema, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        for i in range(4):
            content.insert_draft(
                conn, tenant_id=tid, source_run_id=None,
                draft=content.ContentDraft(
                    channel="x_tweet", body=f"t{i} content", pillar="P", angle_hook="A",
                ),
            )
        # Approve 2, reject 1, leave 1 pending.
        rows = content.list_queue(conn, tenant_id=tid)
        content.approve(conn, tenant_id=tid, item_id=rows[0]["id"])
        content.approve(conn, tenant_id=tid, item_id=rows[1]["id"])
        content.reject(conn, tenant_id=tid, item_id=rows[2]["id"], reason="x")

        stats = content.queue_stats(conn, tenant_id=tid)
        assert stats["approved"] == 2
        assert stats["rejected"] == 1
        assert stats["pending"] == 1
    finally:
        conn.close()


# ─── content agent end-to-end (mocked LLM) ─────────────────────────────


def test_content_agent_end_to_end_mocked(tmp_db, monkeypatch):
    from marketing import anthropic_client, db, schema, seed, strategy
    from marketing.agents import content as content_agent

    schema.ensure_schema()
    conn = db.connect()
    try:
        seed.seed_hatchik_tenant(conn)
        strat = strategy.parse(json.dumps(_valid_strategy_payload()))
        strategy.save(conn, tenant_id=1, strategy=strat, source_run_id=None)
    finally:
        conn.close()

    # Mock anthropic_client.complete to return a valid draft per call,
    # parametrized by the channel found in the user message.
    def fake_complete(conn, **kwargs):
        from marketing import runs as runs_mod

        # Extract channel from the user_message structure passed in.
        last_block = kwargs["user_message"][-1]["text"]
        # crude: look for <channel>X</channel>
        chan = last_block.split("<channel>")[1].split("</channel>")[0].strip()

        if chan == "x_tweet":
            payload = {"channel": "x_tweet", "body": "A specific tweet.",
                       "pillar": "P1", "angle_hook": "A"}
        elif chan == "x_thread":
            payload = {"channel": "x_thread",
                       "body": "part1\n\n---\n\npart2",
                       "pillar": "P", "angle_hook": "A",
                       "metadata": {"parts": ["part1", "part2"]}}
        elif chan == "linkedin":
            payload = {"channel": "linkedin",
                       "body": "L" * 500,
                       "pillar": "P", "angle_hook": "A"}
        elif chan == "blog":
            payload = {"channel": "blog",
                       "body": "## a\n- b1\n- b2",
                       "pillar": "P", "angle_hook": "A",
                       "metadata": {
                           "title": "A working title here",
                           "hook": "hook sentence",
                           "sections": [
                               {"heading": "h1", "bullets": ["b1", "b2"]},
                               {"heading": "h2", "bullets": ["b1", "b2"]},
                               {"heading": "h3", "bullets": ["b1", "b2"]},
                           ],
                           "key_takeaway": "the takeaway",
                       }}
        else:
            payload = {"channel": chan, "body": "x" * 16,
                       "pillar": "P", "angle_hook": "A",
                       "metadata": {"subject": "S", "preview": "P", "cta": "Go"}}

        run_id = runs_mod.start_run(
            conn,
            tenant_id=kwargs["tenant_id"],
            layer=kwargs["layer"],
            model=kwargs["model"],
            input_payload={"system": "(mocked)"},
            prompt_name=kwargs.get("prompt_name"),
            prompt_version=kwargs.get("prompt_version"),
        )
        runs_mod.finish_run(
            conn, run_id, status="success",
            output_payload={"text": json.dumps(payload)},
            tokens_in=200, tokens_out=400, cost_usd=0.008,
        )
        return {
            "run_id": run_id,
            "text": json.dumps(payload),
            "tokens_in": 200, "tokens_out": 400,
            "cache_creation": 1500 if run_id == 1 else 0,
            "cache_read": 0 if run_id == 1 else 1500,
            "cost_usd": 0.008,
        }

    monkeypatch.setattr(anthropic_client, "complete", fake_complete)

    plan = content_agent.BatchPlan(x_tweet=2, x_thread=1, linkedin=1, blog=1, email=0)
    result = content_agent.run(tenant_slug="hatchik", plan=plan, seed=11)
    assert result["items_queued"] == 5
    assert result["items_planned"] == 5
    assert not result["errors"]

    # Items present in queue with status='pending'.
    from marketing import content as content_mod
    conn = db.connect()
    try:
        rows = content_mod.list_queue(conn, tenant_id=result["tenant_id"], status="pending")
        assert len(rows) == 5
    finally:
        conn.close()
