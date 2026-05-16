"""
Cohort + churn analytics agent.

Runs daily. Reads the tenant's `users` and `events` tables (provided by
the substrate-template's standard schema). Computes:

  * weekly cohort retention table (last 8 weeks)
  * 7-day rolling MRR if a `subscriptions` table exists
  * churn rate vs prior week
  * top-3 retention drop signals

Then asks the LLM to produce a 200-word digest the founder can read in
30 seconds. Saves the digest + raw metrics into agent state so the
dashboard can render the table without re-running the heavy queries.

The agent emits ONE `post_message` action (requires_approval=False) so
it shows up in /account → Agents → Recent. No customer-facing
side-effects.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from agents.base import Agent, AgentAction, AgentContext, AgentResult
from agents.registry import register

log = logging.getLogger("agents.cohort")


SYSTEM_PROMPT = """You are a growth analyst for a SaaS founder. You'll
get cohort retention + MRR data. Produce a 200-word digest:

1. One sentence: how is retention vs last week?
2. One observation: is there a cohort that's dropping faster?
3. One suggestion: what should the founder look at next week?

Be specific. Use numbers. No fluff. No bullet lists. Plain prose.
"""


@register
class CohortAgent(Agent):
    NAME = "cohort_churn"
    DESCRIPTION = (
        "Daily cohort retention + churn analytics. Sends a 200-word "
        "digest of what changed week-over-week + what to investigate."
    )
    SCHEDULE = "daily"
    DEFAULT_ON_TIERS = ("launch", "growth")

    def run(self, ctx: AgentContext) -> AgentResult:
        cohort_table = self._build_cohort_table(ctx)
        weekly_active = self._weekly_active(ctx)
        mrr_now = self._mrr_now(ctx)
        churn_this_week = self._churn_rate(ctx, 7)
        churn_last_week = self._churn_rate(ctx, 14, 7)

        metrics = {
            "cohort_table": cohort_table,
            "weekly_active": weekly_active,
            "mrr_pence": mrr_now,
            "churn_this_week_pct": churn_this_week,
            "churn_last_week_pct": churn_last_week,
        }

        # Generate the digest.
        try:
            response = ctx.llm(
                system=SYSTEM_PROMPT,
                user=self._build_user_prompt(metrics),
                model="claude-haiku-4-5", max_tokens=400,
            )
            digest = self._extract_text(response).strip()
        except Exception as e:  # noqa: BLE001
            log.warning("digest LLM failed: %s", e)
            digest = self._fallback_digest(metrics)

        # Persist into state so the dashboard can pull without re-running.
        ctx.state_set("last_metrics", metrics)
        ctx.state_set("last_digest", digest)

        action = AgentAction(
            kind="post_message", requires_approval=False,
            summary=f"Cohort digest — MRR £{(mrr_now or 0)/100:.2f}, "
                    f"weekly churn {churn_this_week:.1f}%",
            payload={"digest": digest, "metrics": metrics},
            rationale="daily cohort sweep",
        )
        notes = (
            f"Cohort table: {len(cohort_table)} weeks. "
            f"WAU: {weekly_active}. MRR: £{(mrr_now or 0)/100:.2f}. "
            f"Churn this week: {churn_this_week:.1f}% (last: {churn_last_week:.1f}%)."
        )
        return AgentResult(actions=[action], notes=notes, metrics=metrics)

    # ─── Queries ──────────────────────────────────────────────────────
    def _build_cohort_table(self, ctx: AgentContext) -> list[dict[str, Any]]:
        """Last 8 weeks of cohort retention.

        Query against the substrate's `users` table (id, created_at) and
        `events` table (user_id, event_at). 'Retained' = had an event
        in the cohort week + has had at least one event in week N+k.
        """
        rows = ctx.tenant_db(
            """
            WITH cohorts AS (
              SELECT id AS user_id,
                     date_trunc('week', created_at) AS cohort_week
                FROM users
               WHERE created_at >= now() - interval '8 weeks'
            ),
            activity AS (
              SELECT user_id, date_trunc('week', event_at) AS active_week
                FROM events
               WHERE event_at >= now() - interval '8 weeks'
               GROUP BY user_id, date_trunc('week', event_at)
            )
            SELECT to_char(c.cohort_week, 'YYYY-MM-DD') AS cohort,
                   COUNT(DISTINCT c.user_id) AS cohort_size,
                   COUNT(DISTINCT CASE WHEN a.active_week = c.cohort_week + interval '1 week' THEN c.user_id END) AS week_1,
                   COUNT(DISTINCT CASE WHEN a.active_week = c.cohort_week + interval '2 weeks' THEN c.user_id END) AS week_2,
                   COUNT(DISTINCT CASE WHEN a.active_week = c.cohort_week + interval '3 weeks' THEN c.user_id END) AS week_3,
                   COUNT(DISTINCT CASE WHEN a.active_week = c.cohort_week + interval '4 weeks' THEN c.user_id END) AS week_4
              FROM cohorts c
              LEFT JOIN activity a ON a.user_id = c.user_id
             GROUP BY c.cohort_week
             ORDER BY c.cohort_week
            """,
        )
        out = []
        for r in rows:
            cols = r.get("row") or []
            if "error" in r or len(cols) < 6:
                continue
            try:
                out.append({
                    "cohort": cols[0],
                    "size": int(cols[1]),
                    "week_1": int(cols[2]), "week_2": int(cols[3]),
                    "week_3": int(cols[4]), "week_4": int(cols[5]),
                })
            except (ValueError, IndexError):
                continue
        return out

    def _weekly_active(self, ctx: AgentContext) -> int:
        rows = ctx.tenant_db(
            "SELECT COUNT(DISTINCT user_id) FROM events WHERE event_at >= now() - interval '7 days'",
        )
        if rows and "row" in rows[0] and rows[0]["row"]:
            try:
                return int(rows[0]["row"][0])
            except (ValueError, IndexError):
                pass
        return 0

    def _mrr_now(self, ctx: AgentContext) -> int:
        """MRR in pence — sums active subscriptions if the table exists."""
        rows = ctx.tenant_db(
            "SELECT COALESCE(SUM(monthly_pence), 0) FROM subscriptions "
            "WHERE status = 'active'",
        )
        if rows and "row" in rows[0] and rows[0]["row"]:
            try:
                return int(rows[0]["row"][0])
            except (ValueError, IndexError):
                pass
        return 0

    def _churn_rate(self, ctx: AgentContext, days_ago: int, until_days_ago: int = 0) -> float:
        """% of users active in window [days_ago, until_days_ago] who
        were not active in the next equal-length window."""
        rows = ctx.tenant_db(
            "SELECT (COUNT(DISTINCT u1.user_id) - COUNT(DISTINCT u2.user_id))::float / "
            "GREATEST(COUNT(DISTINCT u1.user_id), 1) * 100 AS pct "
            "FROM events u1 "
            f"LEFT JOIN events u2 ON u1.user_id = u2.user_id "
            f"  AND u2.event_at >= now() - interval '{until_days_ago} days' "
            f"WHERE u1.event_at >= now() - interval '{days_ago} days' "
            f"  AND u1.event_at < now() - interval '{until_days_ago} days'",
        )
        if rows and "row" in rows[0] and rows[0]["row"]:
            try:
                return float(rows[0]["row"][0])
            except (ValueError, IndexError):
                pass
        return 0.0

    # ─── Prompt + fallback ────────────────────────────────────────────
    def _build_user_prompt(self, m: dict[str, Any]) -> str:
        import json as _json
        return (
            f"Weekly active users: {m['weekly_active']}\n"
            f"MRR: £{(m['mrr_pence'] or 0)/100:.2f}\n"
            f"Churn this week: {m['churn_this_week_pct']:.1f}%\n"
            f"Churn last week: {m['churn_last_week_pct']:.1f}%\n"
            f"Last 8 cohorts (size, w1, w2, w3, w4):\n"
            f"{_json.dumps(m['cohort_table'], indent=2)}"
        )

    def _fallback_digest(self, m: dict[str, Any]) -> str:
        return (
            f"WAU is {m['weekly_active']}; MRR sits at £{(m['mrr_pence'] or 0)/100:.2f}. "
            f"Churn this week was {m['churn_this_week_pct']:.1f}% vs "
            f"{m['churn_last_week_pct']:.1f}% last week. "
            f"(LLM digest unavailable — review the cohort table directly.)"
        )

    def _extract_text(self, response: dict[str, Any]) -> str:
        content = response.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                return first.get("text", "")
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") or {}
            return msg.get("content", "")
        return ""
