"""Phase-1 tests — Strategy schema, save/current versioning, and an
end-to-end persona dry-run with a mocked Anthropic call.

No network; no Anthropic key required.
"""

from __future__ import annotations

import json

import pytest


# Use the same tmp_db fixture from test_phase0.py via a local copy — pytest
# doesn't auto-share fixtures across files unless we set up conftest.py.
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("MARKETING_DAILY_CAP_USD", "5.00")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HATCHIK_ANTHROPIC_MASTER_KEY", raising=False)

    import importlib

    from marketing import (
        anthropic_client,
        budget,
        config,
        db,
        prompts,
        runs,
        schema,
        seed,
        strategy,
        tenant,
    )
    for mod in (config, db, schema, tenant, budget, runs, prompts, anthropic_client, seed, strategy):
        importlib.reload(mod)

    return db_path


def _valid_strategy_payload() -> dict:
    return {
        "icp": {
            "primary": "Solo founders shipping their first AI-tool-built SaaS",
            "company_type": "solo founder",
            "team_size": "1",
            "stage": "pre-revenue",
            "geo": "global",
            "pain_points": [
                "Wiring auth + payments + email takes a weekend",
                "Don't know which deploy stack will survive scale",
            ],
            "buying_triggers": ["First time a Stripe webhook works"],
            "excludes": ["Software engineers at FAANG — they self-host"],
        },
        "sub_personas": [
            {
                "name": "The Consultant Who Codes",
                "role": "Independent consultant",
                "context": "Has billable work; building a side SaaS at night",
                "objection": "Won't pay £14/mo until first paying customer",
                "hook": "Free Sandbox until you charge a real card",
            },
            {
                "name": "The First-Time Builder",
                "role": "Non-engineer using Cursor for the first time",
                "context": "Stuck on the infra wiring, not the product",
                "objection": "Worried they'll be locked in",
                "hook": "Customer owns the repo + VPS, not us",
            },
        ],
        "voice": {
            "tone_attributes": ["matter-of-fact", "concrete", "unhedged"],
            "do": ["Use numbers when you have them", "Name competitors by name"],
            "dont": ["Em-dashes", "Buzzwords like 'empower'"],
            "example_phrases": ["£89 once, £14/mo from month two"],
        },
        "pillars": [
            {
                "name": f"Pillar {i}",
                "description": f"What pillar {i} covers",
                "why_it_matters": f"Why audience cares about {i}",
                "angles": [
                    {"hook": f"Pillar {i} angle hook number {j}", "format_hint": "x_tweet"}
                    for j in range(8)
                ],
            }
            for i in range(1, 6)
        ],
    }


def test_strategy_parse_happy(tmp_db):
    from marketing import strategy

    raw = json.dumps(_valid_strategy_payload())
    strat = strategy.parse(raw)
    assert len(strat.pillars) == 5
    assert len(strat.sub_personas) == 2
    assert all(len(p.angles) == 8 for p in strat.pillars)


def test_strategy_parse_strips_code_fences(tmp_db):
    from marketing import strategy

    raw = "```json\n" + json.dumps(_valid_strategy_payload()) + "\n```"
    strat = strategy.parse(raw)
    assert strat.icp.primary.startswith("Solo founders")


def test_strategy_parse_rejects_invalid_json(tmp_db):
    from marketing import strategy

    with pytest.raises(strategy.StrategyParseError):
        strategy.parse("not json at all {{{")


def test_strategy_parse_rejects_too_few_pillars(tmp_db):
    from marketing import strategy

    payload = _valid_strategy_payload()
    payload["pillars"] = payload["pillars"][:3]  # only 3
    with pytest.raises(strategy.StrategyParseError):
        strategy.parse(json.dumps(payload))


def test_strategy_parse_rejects_too_few_angles(tmp_db):
    from marketing import strategy

    payload = _valid_strategy_payload()
    payload["pillars"][0]["angles"] = payload["pillars"][0]["angles"][:5]
    with pytest.raises(strategy.StrategyParseError):
        strategy.parse(json.dumps(payload))


def test_strategy_save_versions_and_flips_is_current(tmp_db):
    from marketing import db, schema, seed, strategy

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        strat = strategy.parse(json.dumps(_valid_strategy_payload()))

        id1 = strategy.save(conn, tenant_id=tid, strategy=strat, source_run_id=None)
        id2 = strategy.save(conn, tenant_id=tid, strategy=strat, source_run_id=None)

        v1 = conn.execute("SELECT version, is_current FROM marketing_strategies WHERE id = ?", (id1,)).fetchone()
        v2 = conn.execute("SELECT version, is_current FROM marketing_strategies WHERE id = ?", (id2,)).fetchone()

        assert v1["version"] == 1
        assert v2["version"] == 2
        assert v1["is_current"] == 0
        assert v2["is_current"] == 1

        # Only the latest comes back from current().
        version, _ = strategy.current(conn, tid)
        assert version == 2
    finally:
        conn.close()


def test_strategy_current_none_when_empty(tmp_db):
    from marketing import db, schema, seed, strategy

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        assert strategy.current(conn, tid) is None
    finally:
        conn.close()


def test_persona_agent_end_to_end_mocked(tmp_db, monkeypatch):
    """End-to-end: persona reads docs + seeds, calls (mocked) Opus,
    parses output, saves a strategy version. No network."""
    from marketing import anthropic_client, db, schema, seed, strategy
    from marketing.agents import persona

    schema.ensure_schema()
    conn = db.connect()
    try:
        seed.seed_hatchik_tenant(conn)
    finally:
        conn.close()

    payload = _valid_strategy_payload()

    def fake_complete(conn, **kwargs):
        from marketing import runs as runs_mod

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
            conn,
            run_id,
            status="success",
            output_payload={"text": json.dumps(payload)},
            tokens_in=1234,
            tokens_out=2345,
            cost_usd=0.0123,
        )
        return {
            "run_id": run_id,
            "text": json.dumps(payload),
            "tokens_in": 1234,
            "tokens_out": 2345,
            "cache_creation": 800,
            "cache_read": 0,
            "cost_usd": 0.0123,
        }

    monkeypatch.setattr(anthropic_client, "complete", fake_complete)

    result = persona.run(tenant_slug="hatchik")
    assert result["version"] == 1
    assert result["pillars"] == 5
    assert result["sub_personas"] == 2
    assert result["total_angles"] == 40

    # Verify the strategy is in the DB and marked current.
    conn = db.connect()
    try:
        cur = strategy.current(conn, result["tenant_id"])
        assert cur is not None
        version, strat = cur
        assert version == 1
        assert strat.pillars[0].name == "Pillar 1"
    finally:
        conn.close()
