"""Per-tenant daily spend cap.

Enforced before every LLM call. The check is purely backward-looking
(sum of `cost_usd` from `marketing_agent_runs` in the last 24h for the
tenant). Pre-call cost estimation is intentionally not attempted — the
trade-off is that a single rogue call can push spend past the cap by
its output cost, but the next call is then refused. The cap is a
safety net, not a precise meter.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


class OverBudget(Exception):
    """Raised when the tenant's rolling-24h spend has hit its cap."""


def spend_last_24h(conn: sqlite3.Connection, tenant_id: int) -> float:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    row = conn.execute(
        """
        SELECT COALESCE(SUM(cost_usd), 0.0) AS total
        FROM marketing_agent_runs
        WHERE tenant_id = ?
          AND started_at >= ?
          AND cost_usd IS NOT NULL
        """,
        (tenant_id, cutoff),
    ).fetchone()
    return float(row["total"])


def assert_within_cap(conn: sqlite3.Connection, tenant_id: int, cap_usd: float) -> None:
    spent = spend_last_24h(conn, tenant_id)
    if spent >= cap_usd:
        raise OverBudget(
            f"Tenant {tenant_id}: ${spent:.4f} spent in last 24h, cap ${cap_usd:.2f}"
        )
