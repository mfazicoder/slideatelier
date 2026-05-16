"""Metrics fetching.

For Phase 4 the only live source is X public_metrics (likes, replies,
retweets, impressions). Updates `marketing_distributions.metrics_json`
and stamps `last_metrics_fetched_at`. Used by the analyze agent to
read recent post performance.

Lazy tweepy import — install via `pip install -e ".[distribute]"`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .integrations import x as x_int


def refresh_x_metrics(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    max_age_hours: int = 24,
    x_client: x_int.XClient | None = None,
    limit: int = 50,
) -> dict[str, int]:
    """Pull fresh public_metrics for X distributions whose
    last_metrics_fetched_at is older than `max_age_hours`. Skips
    dry-run rows (external_id starting with 'dry-')."""
    threshold = (
        datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    ).isoformat()
    rows = conn.execute(
        """
        SELECT id, external_id, metrics_json, last_metrics_fetched_at
        FROM marketing_distributions
        WHERE tenant_id = ? AND provider = 'x'
          AND (last_metrics_fetched_at IS NULL OR last_metrics_fetched_at < ?)
          AND external_id IS NOT NULL
          AND external_id NOT LIKE 'dry-%'
        ORDER BY id DESC
        LIMIT ?
        """,
        (tenant_id, threshold, limit),
    ).fetchall()
    if not rows:
        return {"refreshed": 0, "errors": 0, "skipped_dry": 0}

    client = x_client or x_int.XClient.from_env()
    tweepy_client = client._tweepy()

    refreshed = 0
    errors = 0
    for row in rows:
        try:
            resp = tweepy_client.get_tweet(
                row["external_id"], tweet_fields=["public_metrics"]
            )
            metrics: dict[str, Any] = dict(resp.data.public_metrics or {})
        except Exception:
            errors += 1
            continue

        existing = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        existing["x_public_metrics"] = metrics
        conn.execute(
            """
            UPDATE marketing_distributions
            SET metrics_json = ?, last_metrics_fetched_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(existing, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
                row["id"],
            ),
        )
        refreshed += 1

    return {"refreshed": refreshed, "errors": errors}


def recent_distributions_with_metrics(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    days: int = 7,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return distributions from the last N days, joined with their
    queue body / pillar / angle and parsed metrics — flat dicts ready
    to feed into the analyze prompt."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT
            d.id AS distribution_id,
            d.posted_at,
            d.provider,
            d.external_id,
            d.url,
            d.metrics_json,
            q.id AS content_queue_id,
            q.channel,
            q.body,
            q.metadata_json
        FROM marketing_distributions d
        JOIN marketing_content_queue q ON q.id = d.content_queue_id
        WHERE d.tenant_id = ? AND d.posted_at >= ?
        ORDER BY d.posted_at DESC
        LIMIT ?
        """,
        (tenant_id, since, limit),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        meta = json.loads(r["metadata_json"] or "{}")
        metrics = json.loads(r["metrics_json"] or "{}")
        out.append(
            {
                "distribution_id": r["distribution_id"],
                "content_queue_id": r["content_queue_id"],
                "channel": r["channel"],
                "provider": r["provider"],
                "external_id": r["external_id"],
                "url": r["url"],
                "posted_at": r["posted_at"],
                "pillar": meta.get("pillar"),
                "angle_hook": meta.get("angle_hook"),
                "body_excerpt": r["body"][:400],
                "metrics": metrics,
            }
        )
    return out


def recent_listening_signals(
    conn: sqlite3.Connection, *, tenant_id: int, days: int = 7, limit: int = 200
) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT id, source, query, sentiment, captured_at, raw_json
        FROM marketing_listening_signals
        WHERE tenant_id = ? AND captured_at >= ?
        ORDER BY captured_at DESC
        LIMIT ?
        """,
        (tenant_id, since, limit),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "source": r["source"],
            "query": r["query"],
            "sentiment": r["sentiment"],
            "captured_at": r["captured_at"],
            "raw": json.loads(r["raw_json"]) if r["raw_json"] else None,
        }
        for r in rows
    ]
