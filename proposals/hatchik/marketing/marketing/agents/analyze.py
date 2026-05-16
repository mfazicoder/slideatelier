"""Layer 4 — Listening & Adjustment.

Weekly (Sunday night per the brief). Reads:
  - current strategy
  - distributions from the last 7 days w/ metrics
  - listening_signals from the last 7 days
  - pending + rejected queue items (rejections include the reason)

Sends to Opus 4.7 with prompt caching on the system + strategy block.
Parses the response into an `AnalysisReport`. Saves the report's
`updated_strategy` as a new strategy version (auto-promotes); leaves
the full report in `marketing_agent_runs.output_json` for
`analysis show` to pull back out later.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import (
    analysis,
    analytics,
    anthropic_client,
    db,
    prompts,
    schema,
    strategy,
    tenant,
)


def _pending_queue(conn, *, tenant_id: int, limit: int = 30) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, channel, body, metadata_json, created_at
        FROM marketing_content_queue
        WHERE tenant_id = ? AND status = 'pending'
        ORDER BY id DESC
        LIMIT ?
        """,
        (tenant_id, limit),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "channel": r["channel"],
            "body_excerpt": r["body"][:200],
            "pillar": (json.loads(r["metadata_json"]) or {}).get("pillar"),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def _recent_rejections(conn, *, tenant_id: int, days: int = 7, limit: int = 30) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT id, channel, body, metadata_json, rejection_reason, created_at
        FROM marketing_content_queue
        WHERE tenant_id = ? AND status = 'rejected' AND created_at >= ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (tenant_id, since, limit),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "channel": r["channel"],
            "body_excerpt": r["body"][:200],
            "pillar": (json.loads(r["metadata_json"]) or {}).get("pillar"),
            "rejection_reason": r["rejection_reason"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def run(
    tenant_slug: str = "hatchik",
    *,
    days: int = 7,
    auto_promote_strategy: bool = True,
) -> dict[str, Any]:
    """Run one analysis pass. Returns a summary dict including the new
    strategy version (if promoted) and key numbers from the report."""
    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        t = tenant.get_by_slug(conn, tenant_slug)

        current = strategy.current(conn, t.id)
        if current is None:
            raise RuntimeError(
                f"Tenant {t.slug!r} has no current strategy. "
                "Run `marketing.cli run persona` first."
            )
        current_version, current_strat = current

        distributions = analytics.recent_distributions_with_metrics(
            conn, tenant_id=t.id, days=days
        )
        signals = analytics.recent_listening_signals(
            conn, tenant_id=t.id, days=days
        )
        pending = _pending_queue(conn, tenant_id=t.id)
        rejections = _recent_rejections(conn, tenant_id=t.id, days=days)

        prompt = prompts.load("analyze")
        prompts.mirror_to_db(conn, prompt)

        system_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": prompt.body, "cache_control": {"type": "ephemeral"}}
        ]
        cached_block = {
            "type": "text",
            "text": (
                "<current_strategy>\n"
                + current_strat.model_dump_json(indent=2)
                + "\n</current_strategy>"
            ),
            "cache_control": {"type": "ephemeral"},
        }
        live_block = {
            "type": "text",
            "text": (
                f"<distributions>\n{json.dumps(distributions, ensure_ascii=False, indent=2)}\n</distributions>\n\n"
                f"<signals>\n{json.dumps(signals, ensure_ascii=False, indent=2)}\n</signals>\n\n"
                f"<pending_queue>\n{json.dumps(pending, ensure_ascii=False, indent=2)}\n</pending_queue>\n\n"
                f"<rejected>\n{json.dumps(rejections, ensure_ascii=False, indent=2)}\n</rejected>\n\n"
                "Produce the JSON analysis now."
            ),
        }

        result = anthropic_client.complete(
            conn,
            tenant_id=t.id,
            tenant_cap_usd=t.spend_cap_daily_usd,
            layer="analyze",
            model=prompt.model,
            system=system_blocks,
            user_message=[cached_block, live_block],
            max_tokens=int(prompt.params.get("max_tokens", 8000)),
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        )

        report = analysis.parse(result["text"])

        new_version: int | None = None
        new_strategy_id: int | None = None
        if auto_promote_strategy:
            new_strategy_id = strategy.save(
                conn,
                tenant_id=t.id,
                strategy=report.updated_strategy,
                source_run_id=result["run_id"],
            )
            ver_row = conn.execute(
                "SELECT version FROM marketing_strategies WHERE id = ?",
                (new_strategy_id,),
            ).fetchone()
            new_version = int(ver_row["version"])

        return {
            "run_id": result["run_id"],
            "tenant_id": t.id,
            "tenant_slug": t.slug,
            "prior_strategy_version": current_version,
            "new_strategy_version": new_version,
            "new_strategy_id": new_strategy_id,
            "posts_analyzed": len(distributions),
            "signals_analyzed": len(signals),
            "winners": len(report.winners),
            "losers": len(report.losers),
            "hypotheses": len(report.hypotheses),
            "tokens_in": result["tokens_in"],
            "tokens_out": result["tokens_out"],
            "cache_read": result.get("cache_read", 0),
            "cache_creation": result.get("cache_creation", 0),
            "cost_usd": result["cost_usd"],
        }
    finally:
        conn.close()
