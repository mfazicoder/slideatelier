"""Layer 1 — Persona & Strategy agent.

Cadence: monthly + on-demand. Reads the tenant's product docs +
competitor list from `marketing_tenants.settings_json`, sends them
through Opus 4.7, parses the JSON output into a `Strategy`, and saves
it as a new version in `marketing_strategies` (bumps `is_current`).

Prompt caching: the system prompt and the product/competitor blob both
get `cache_control: ephemeral` markers so re-runs within ~5 min cost
~10% of a cold run. Useful while iterating on the prompt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import anthropic_client, db, prompts, schema, strategy, tenant


_MARKETING_ROOT = Path(__file__).resolve().parents[2]
HATCHIK_ROOT = _MARKETING_ROOT.parent  # proposals/hatchik/


def _load_settings(conn, tenant_id: int) -> dict:
    row = conn.execute(
        "SELECT settings_json FROM marketing_tenants WHERE id = ?", (tenant_id,)
    ).fetchone()
    return json.loads(row["settings_json"])


def _load_docs(rel_paths: list[str], *, max_chars_total: int) -> str:
    """Read referenced docs from proposals/hatchik/, concat, hard-truncate."""
    pieces: list[str] = []
    budget = max_chars_total
    for rel in rel_paths:
        path = HATCHIK_ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if len(text) > budget:
            text = text[:budget] + "\n\n[...truncated]"
        pieces.append(f"## {rel}\n\n{text}")
        budget -= len(text)
        if budget <= 0:
            break
    return "\n\n---\n\n".join(pieces)


def _format_competitors(competitors: list[dict[str, Any]]) -> str:
    if not competitors:
        return "(none specified yet)"
    lines = []
    for c in competitors:
        name = c.get("name", "?")
        url = c.get("url", "")
        note = c.get("note", "")
        lines.append(f"- {name} ({url}): {note}".rstrip(" :"))
    return "\n".join(lines)


def run(
    tenant_slug: str = "hatchik",
    *,
    signals: str = "",
    max_doc_chars: int = 10_000,
) -> dict:
    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        t = tenant.get_by_slug(conn, tenant_slug)
        settings = _load_settings(conn, t.id)

        product_text = _load_docs(
            settings.get("product_docs", []), max_chars_total=max_doc_chars
        )
        competitors_text = _format_competitors(settings.get("competitors", []))

        prompt = prompts.load("persona")
        prompts.mirror_to_db(conn, prompt)

        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": prompt.body,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        cached_user_block = (
            f"<product>\n{product_text or '(no product docs found)'}\n</product>\n\n"
            f"<competitors>\n{competitors_text}\n</competitors>"
        )
        live_user_block = (
            f"<signals>\n{signals or '(none yet — pre-launch)'}\n</signals>\n\n"
            "Produce the strategy now."
        )
        user_content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": cached_user_block,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": live_user_block},
        ]

        result = anthropic_client.complete(
            conn,
            tenant_id=t.id,
            tenant_cap_usd=t.spend_cap_daily_usd,
            layer="persona",
            model=prompt.model,
            system=system_blocks,
            user_message=user_content,
            max_tokens=int(prompt.params.get("max_tokens", 4096)),
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        )

        # Parse output into a Strategy. If invalid, propagate to the
        # caller — the agent_runs row stays as 'success' (the LLM call
        # succeeded) and the parse error includes a path that points at
        # which field failed validation.
        strat = strategy.parse(result["text"])
        strategy_id = strategy.save(
            conn,
            tenant_id=t.id,
            strategy=strat,
            source_run_id=result["run_id"],
        )

        version_row = conn.execute(
            "SELECT version FROM marketing_strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()

        return {
            "run_id": result["run_id"],
            "strategy_id": strategy_id,
            "version": int(version_row["version"]),
            "tenant_id": t.id,
            "tenant_slug": t.slug,
            "pillars": len(strat.pillars),
            "sub_personas": len(strat.sub_personas),
            "total_angles": sum(len(p.angles) for p in strat.pillars),
            "tokens_in": result["tokens_in"],
            "tokens_out": result["tokens_out"],
            "cache_creation": result.get("cache_creation", 0),
            "cache_read": result.get("cache_read", 0),
            "cost_usd": result["cost_usd"],
        }
    finally:
        conn.close()
