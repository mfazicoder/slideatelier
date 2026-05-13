"""
Cohort-funnel metrics for Hatchik signups.

Replaces the assumed numbers in MARKETING_PLAN.md §7:
    1. Sandbox→Launch conversion rate (assumed 5-10%)
    2. Launch tier annual churn (assumed 30%)
    3. Launch→Growth upgrade time (assumed month 12)

By signup #50 we want ground-truth on each, so we group signups into
weekly/monthly cohorts and follow them forward via the tier_transitions
table (initial signup → upgrade → churn/archive). The registry feeds
the "current tier" calculation for distribution today.

Heavy SQL lives here so main.py stays small. Functions are pure: take a
``sqlite3.Connection`` (so tests can hand in an in-memory DB) and
return JSON-serialisable dicts.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Literal


# Tiers that count as "still alive" — anything not in this set is treated
# as churned/archived for the live counters.
LIVE_TIERS = {"sandbox", "launch", "growth"}
DEAD_TIERS = {"cancelled", "archived"}


# ─── Helpers ─────────────────────────────────────────────────────────────
def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp from SQLite TEXT. Tolerant of trailing 'Z'."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _cohort_label(dt: datetime, granularity: Literal["week", "month"]) -> tuple[str, str]:
    """Return (label, cohort_start_date) for a given timestamp + granularity.

    Week buckets follow ISO-8601 ("2026-W19"). Month buckets are "YYYY-MM".
    Start date is always returned as YYYY-MM-DD.
    """
    if granularity == "week":
        iso_year, iso_week, _ = dt.isocalendar()
        label = f"{iso_year:04d}-W{iso_week:02d}"
        # ISO week start = Monday of that ISO week.
        start = datetime.fromisocalendar(iso_year, iso_week, 1)
        return label, start.date().isoformat()
    # month
    label = f"{dt.year:04d}-{dt.month:02d}"
    start = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
    return label, start.date().isoformat()


def _current_tier(
    signup_id: int,
    initial_tier: str,
    transitions_by_signup: dict[int, list[sqlite3.Row]],
) -> str:
    """Latest non-initial transition wins; fall back to initial tier."""
    rows = transitions_by_signup.get(signup_id, [])
    for row in reversed(rows):
        if row["from_tier"] is None:
            continue
        return row["to_tier"]
    return initial_tier


def _first_transition_to(
    signup_id: int,
    target: str,
    transitions_by_signup: dict[int, list[sqlite3.Row]],
) -> datetime | None:
    """First time the cohort member's tier flipped to ``target``."""
    for row in transitions_by_signup.get(signup_id, []):
        if row["to_tier"] == target and row["from_tier"] is not None:
            return _parse_iso(row["occurred_at"])
    return None


def _first_churn(
    signup_id: int,
    transitions_by_signup: dict[int, list[sqlite3.Row]],
) -> datetime | None:
    for row in transitions_by_signup.get(signup_id, []):
        if row["to_tier"] in DEAD_TIERS:
            return _parse_iso(row["occurred_at"])
    return None


def _mean_days(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _load_transitions(conn: sqlite3.Connection) -> dict[int, list[sqlite3.Row]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT signup_id, from_tier, to_tier, occurred_at, paddle_event_id, notes "
        "FROM tier_transitions ORDER BY signup_id, occurred_at"
    ).fetchall()
    out: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        out.setdefault(r["signup_id"], []).append(r)
    return out


def _load_signups(
    conn: sqlite3.Connection,
    since: str | None = None,
) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    sql = "SELECT id, created_at, tier FROM signups"
    params: tuple[Any, ...] = ()
    if since:
        sql += " WHERE created_at >= ?"
        params = (since,)
    sql += " ORDER BY id"
    return conn.execute(sql, params).fetchall()


# ─── Public API ──────────────────────────────────────────────────────────
def compute_cohorts(
    conn: sqlite3.Connection,
    granularity: Literal["week", "month"] = "week",
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Group signups by created_at into weekly or monthly cohorts.

    Returns one dict per cohort with totals, conversion counts, and mean
    days-to-event timings. Empty database → empty list (never raises).
    """
    signups = _load_signups(conn, since=since)
    if not signups:
        return []
    transitions = _load_transitions(conn)

    buckets: dict[str, dict[str, Any]] = {}
    for s in signups:
        signup_id = s["id"]
        created = _parse_iso(s["created_at"])
        label, start = _cohort_label(created, granularity)
        bucket = buckets.setdefault(
            label,
            {
                "cohort_label": label,
                "cohort_start_date": start,
                "_signups": [],
                "_days_to_launch": [],
                "_days_to_growth": [],
                "_days_to_churn": [],
                "_churned_within_30d": 0,
                "_churned_within_90d": 0,
                "_converted_launch_30d": 0,
                "_converted_growth_90d": 0,
                "signups_by_initial_tier": {"sandbox": 0, "launch": 0, "growth": 0},
                "total_signups": 0,
                "currently_live": 0,
            },
        )
        bucket["_signups"].append(signup_id)
        bucket["total_signups"] += 1
        initial = s["tier"] if s["tier"] in bucket["signups_by_initial_tier"] else "sandbox"
        bucket["signups_by_initial_tier"][initial] += 1

        current = _current_tier(signup_id, s["tier"], transitions)
        if current in LIVE_TIERS:
            bucket["currently_live"] += 1

        launch_at = _first_transition_to(signup_id, "launch", transitions)
        if launch_at is not None:
            days = (launch_at - created).total_seconds() / 86400.0
            bucket["_days_to_launch"].append(days)
            if days <= 30:
                bucket["_converted_launch_30d"] += 1

        growth_at = _first_transition_to(signup_id, "growth", transitions)
        if growth_at is not None:
            days = (growth_at - created).total_seconds() / 86400.0
            bucket["_days_to_growth"].append(days)
            if days <= 90:
                bucket["_converted_growth_90d"] += 1

        churn_at = _first_churn(signup_id, transitions)
        if churn_at is not None:
            days = (churn_at - created).total_seconds() / 86400.0
            bucket["_days_to_churn"].append(days)
            if days <= 30:
                bucket["_churned_within_30d"] += 1
            if days <= 90:
                bucket["_churned_within_90d"] += 1

    out: list[dict[str, Any]] = []
    for bucket in sorted(buckets.values(), key=lambda b: b["cohort_start_date"]):
        total = bucket["total_signups"] or 1
        out.append(
            {
                "cohort_label": bucket["cohort_label"],
                "cohort_start_date": bucket["cohort_start_date"],
                "total_signups": bucket["total_signups"],
                "signups_by_initial_tier": bucket["signups_by_initial_tier"],
                "currently_live": bucket["currently_live"],
                "converted_to_launch_within_30d": bucket["_converted_launch_30d"],
                "converted_to_growth_within_90d": bucket["_converted_growth_90d"],
                "mean_days_to_launch_upgrade": _mean_days(bucket["_days_to_launch"]),
                "mean_days_to_growth_upgrade": _mean_days(bucket["_days_to_growth"]),
                "mean_days_to_churn": _mean_days(bucket["_days_to_churn"]),
                "churn_rate_30d": round(100 * bucket["_churned_within_30d"] / total, 2),
                "churn_rate_90d": round(100 * bucket["_churned_within_90d"] / total, 2),
            }
        )
    return out


def compute_funnel_rollup(
    conn: sqlite3.Connection,
    since: str | None = None,
) -> dict[str, Any]:
    """All-time funnel rates across every cohort.

    sandbox→launch %: of signups whose initial tier was 'sandbox', the
    fraction that ever transitioned to 'launch'. Equivalent definitions
    used for launch→growth and overall churn.
    """
    signups = _load_signups(conn, since=since)
    if not signups:
        return {
            "total_signups": 0,
            "total_active": 0,
            "total_upgraded_to_launch": 0,
            "total_upgraded_to_growth": 0,
            "total_churned": 0,
            "sandbox_to_launch_pct": None,
            "launch_to_growth_pct": None,
            "overall_churn_pct": None,
        }
    transitions = _load_transitions(conn)

    total = len(signups)
    sandbox_start = 0
    launch_start_or_upgraded = 0
    upgraded_to_launch = 0
    upgraded_to_growth = 0
    churned = 0
    active = 0

    for s in signups:
        sid = s["id"]
        initial = s["tier"]
        if initial == "sandbox":
            sandbox_start += 1
        ever_launch = _first_transition_to(sid, "launch", transitions) is not None
        ever_growth = _first_transition_to(sid, "growth", transitions) is not None
        if ever_launch:
            upgraded_to_launch += 1
        if ever_growth:
            upgraded_to_growth += 1
        if initial in ("launch", "growth") or ever_launch:
            launch_start_or_upgraded += 1
        churn_at = _first_churn(sid, transitions)
        if churn_at is not None:
            churned += 1
        current = _current_tier(sid, initial, transitions)
        if current in LIVE_TIERS:
            active += 1

    def pct(num: int, den: int) -> float | None:
        return round(100 * num / den, 2) if den else None

    return {
        "total_signups": total,
        "total_active": active,
        "total_upgraded_to_launch": upgraded_to_launch,
        "total_upgraded_to_growth": upgraded_to_growth,
        "total_churned": churned,
        "sandbox_to_launch_pct": pct(upgraded_to_launch, sandbox_start),
        "launch_to_growth_pct": pct(upgraded_to_growth, launch_start_or_upgraded),
        "overall_churn_pct": pct(churned, total),
    }


def compute_tier_distribution_today(
    conn: sqlite3.Connection,
    registry: dict[str, Any],
    since: str | None = None,
) -> dict[str, Any]:
    """Counts by current tier of all live tenants.

    Uses the registry to filter to tenants that actually exist on the
    sandbox host (status not in {decommissioned, archived}); the current
    tier is read from the latest tier_transitions row, falling back to
    the signup's initial tier when no upgrade has happened.
    """
    signups = _load_signups(conn, since=since)
    transitions = _load_transitions(conn)

    registry_tenants = registry.get("tenants", {}) if registry else {}
    live_signup_ids: set[int] = set()
    for tenant in registry_tenants.values():
        if tenant.get("status") in {"decommissioned", "archived"}:
            continue
        sid = tenant.get("signup_id")
        if sid:
            live_signup_ids.add(int(sid))

    by_tier = {"sandbox": 0, "launch": 0, "growth": 0}
    total_live = 0
    for s in signups:
        if s["id"] not in live_signup_ids:
            continue
        current = _current_tier(s["id"], s["tier"], transitions)
        if current in DEAD_TIERS:
            continue
        by_tier[current] = by_tier.get(current, 0) + 1
        total_live += 1
    return {"total_live": total_live, "by_tier": by_tier}


__all__ = [
    "compute_cohorts",
    "compute_funnel_rollup",
    "compute_tier_distribution_today",
]
