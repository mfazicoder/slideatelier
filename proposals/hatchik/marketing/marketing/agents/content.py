"""Layer 2 — Content Generation Agent.

Cadence: daily (in Phase 3 the scheduler enqueues `marketing_jobs`
rows that fire this agent). Reads the tenant's current strategy,
picks N angles per channel (deduped within the batch, prefers
angle.format_hint matches), and calls Sonnet 4.6 once per item.
Each draft is parsed and inserted into `marketing_content_queue`
with status='pending'.

Prompt caching: the system prompt and the strategy snapshot are both
cached. With a batch of 5 items, only the first call pays full input
cost — the rest read from cache (~10% of input rate) until the 5-min
TTL expires.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .. import anthropic_client, content, db, prompts, schema, strategy, tenant
from ..strategy import Angle, Pillar, Strategy


# Default daily batch shape. Email is triggered-only (left at 0 here);
# blog runs 3x/week — caller decides whether today is a blog day.
@dataclass
class BatchPlan:
    x_tweet: int = 3
    x_thread: int = 1
    linkedin: int = 1
    blog: int = 0
    email: int = 0

    def slots(self) -> list[tuple[str, int]]:
        return [
            ("x_tweet", self.x_tweet),
            ("x_thread", self.x_thread),
            ("linkedin", self.linkedin),
            ("blog", self.blog),
            ("email", self.email),
        ]


@dataclass
class _Pick:
    channel: str
    pillar: Pillar
    angle: Angle


def pick_angles(
    strat: Strategy, plan: BatchPlan, *, seed: int | None = None
) -> list[_Pick]:
    """Group angles by format_hint, then fill each channel slot.
    Within a batch, no angle is used twice. If the format pool runs
    out, the slot is filled from the global remaining pool."""
    rng = random.Random(seed)
    pool: dict[str, list[tuple[Pillar, Angle]]] = defaultdict(list)
    for pillar in strat.pillars:
        for angle in pillar.angles:
            pool[angle.format_hint].append((pillar, angle))
    for fmt in pool:
        rng.shuffle(pool[fmt])

    used: set[tuple[str, str]] = set()
    selections: list[_Pick] = []

    def _take(channel: str, candidates: list[tuple[Pillar, Angle]]) -> _Pick | None:
        while candidates:
            pillar, angle = candidates.pop()
            key = (pillar.name, angle.hook)
            if key in used:
                continue
            used.add(key)
            return _Pick(channel=channel, pillar=pillar, angle=angle)
        return None

    for channel, n in plan.slots():
        if n <= 0:
            continue
        # 1. Prefer angles whose format_hint matches the channel.
        primary_pool = pool.get(channel, [])
        for _ in range(n):
            pick = _take(channel, primary_pool)
            if pick is not None:
                selections.append(pick)
            else:
                # 2. Cross-format fallback — pull from any remaining pool.
                fallback = [pa for fmt, lst in pool.items() for pa in lst
                            if (pa[0].name, pa[1].hook) not in used]
                rng.shuffle(fallback)
                pick = _take(channel, fallback)
                if pick is not None:
                    selections.append(pick)
                    # Remove the picked tuple from its origin pool too,
                    # so it isn't offered again.
                    for fmt, lst in pool.items():
                        pool[fmt] = [
                            x for x in lst
                            if (x[0].name, x[1].hook) != (pick.pillar.name, pick.angle.hook)
                        ]
    return selections


def _format_strategy(strat: Strategy) -> str:
    """Compact, deterministic rendering of the strategy for prompt input."""
    pillars_text = "\n".join(
        f"- {p.name}: {p.description} (why: {p.why_it_matters})"
        for p in strat.pillars
    )
    subs_text = "\n".join(
        f"- {sp.name} ({sp.role}): {sp.context} — objection: {sp.objection} — hook: {sp.hook}"
        for sp in strat.sub_personas
    )
    return (
        f"## ICP\n{strat.icp.primary}\n"
        f"Pain: {'; '.join(strat.icp.pain_points)}\n"
        f"Excludes: {'; '.join(strat.icp.excludes) or '(none specified)'}\n\n"
        f"## Sub-personas\n{subs_text}\n\n"
        f"## Voice\n"
        f"Tone: {', '.join(strat.voice.tone_attributes)}\n"
        f"Do: {'; '.join(strat.voice.do)}\n"
        f"Dont: {'; '.join(strat.voice.dont)}\n\n"
        f"## Pillars\n{pillars_text}\n"
    )


def run(
    tenant_slug: str = "hatchik",
    *,
    plan: BatchPlan | None = None,
    signals: str = "",
    seed: int | None = None,
) -> dict[str, Any]:
    """Generate one daily batch. Returns a summary of what was queued."""
    plan = plan or BatchPlan()
    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        t = tenant.get_by_slug(conn, tenant_slug)

        current = strategy.current(conn, t.id)
        if current is None:
            raise RuntimeError(
                f"Tenant {t.slug!r} has no current strategy. Run "
                "`marketing.cli run persona` first."
            )
        version, strat = current

        prompt = prompts.load("content")
        prompts.mirror_to_db(conn, prompt)

        picks = pick_angles(strat, plan, seed=seed)
        if not picks:
            return {"tenant_slug": t.slug, "items_queued": 0, "items": [], "cost_usd": 0.0}

        strategy_block_text = _format_strategy(strat)
        system_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": prompt.body, "cache_control": {"type": "ephemeral"}}
        ]

        queued: list[dict[str, Any]] = []
        total_cost = 0.0
        total_in = 0
        total_out = 0
        total_cache_read = 0
        total_cache_creation = 0
        errors: list[dict[str, Any]] = []

        for pick in picks:
            user_content = [
                {
                    "type": "text",
                    "text": (
                        f"<strategy>\n{strategy_block_text}\n</strategy>"
                    ),
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": (
                        f"<pillar>{pick.pillar.name}</pillar>\n"
                        f"<angle>{pick.angle.hook}</angle>\n"
                        f"<channel>{pick.channel}</channel>\n"
                        f"<signals>{signals or '(none yet)'}</signals>\n\n"
                        "Produce the JSON now."
                    ),
                },
            ]

            try:
                result = anthropic_client.complete(
                    conn,
                    tenant_id=t.id,
                    tenant_cap_usd=t.spend_cap_daily_usd,
                    layer="content",
                    model=prompt.model,
                    system=system_blocks,
                    user_message=user_content,
                    max_tokens=int(prompt.params.get("max_tokens", 1500)),
                    prompt_name=prompt.name,
                    prompt_version=prompt.version,
                )
            except Exception as exc:
                errors.append({
                    "channel": pick.channel,
                    "pillar": pick.pillar.name,
                    "angle": pick.angle.hook,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue

            total_cost += result["cost_usd"]
            total_in += result["tokens_in"]
            total_out += result["tokens_out"]
            total_cache_read += result.get("cache_read", 0)
            total_cache_creation += result.get("cache_creation", 0)

            try:
                draft = content.parse_draft(result["text"])
            except content.DraftParseError as exc:
                errors.append({
                    "channel": pick.channel,
                    "pillar": pick.pillar.name,
                    "angle": pick.angle.hook,
                    "run_id": result["run_id"],
                    "error": f"parse: {exc}",
                })
                continue

            # The LLM may have set channel/pillar/angle slightly
            # differently — trust the picked values for the queue row.
            if draft.channel != pick.channel:
                # Coerce — keep the channel we asked for; metadata gets
                # rebuilt by ContentDraft.validate via re-construct.
                try:
                    draft = content.ContentDraft.model_validate({
                        **draft.model_dump(),
                        "channel": pick.channel,
                    })
                except Exception as exc:
                    errors.append({
                        "channel": pick.channel,
                        "run_id": result["run_id"],
                        "error": f"channel-coerce: {exc}",
                    })
                    continue

            item_id = content.insert_draft(
                conn,
                tenant_id=t.id,
                draft=draft,
                source_run_id=result["run_id"],
            )
            queued.append({
                "item_id": item_id,
                "run_id": result["run_id"],
                "channel": pick.channel,
                "pillar": pick.pillar.name,
                "angle": pick.angle.hook,
                "cost_usd": result["cost_usd"],
            })

        return {
            "tenant_id": t.id,
            "tenant_slug": t.slug,
            "strategy_version": version,
            "items_planned": len(picks),
            "items_queued": len(queued),
            "items": queued,
            "errors": errors,
            "tokens_in": total_in,
            "tokens_out": total_out,
            "cache_read": total_cache_read,
            "cache_creation": total_cache_creation,
            "cost_usd": total_cost,
        }
    finally:
        conn.close()
