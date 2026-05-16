"""Distribution orchestrator (Phase 3a — X only).

Pulls approved items from `marketing_content_queue`, posts via the
X API, records the outcome in `marketing_distributions`, flips the
queue row to `posted`, and fires a PostHog event. Tenant-scoped.

Channels covered here:
  - x_tweet:  one POST /2/tweets call
  - x_thread: N POST /2/tweets calls chained via in_reply_to_tweet_id

Other channels (linkedin, blog, email) raise NotYetImplemented for now
— Phase 3b will add Resend for email; linkedin + blog stay manual
until the user wires those targets.

Dry-run mode skips the network call entirely and returns synthetic
ids so the rest of the pipeline (DB writes, state flips, tracking) can
be exercised end-to-end before real X credentials are in place.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import content as content_mod
from .integrations import posthog as posthog_int
from .integrations import x as x_int


class DistributionError(Exception):
    pass


class NotYetImplemented(DistributionError):
    """Channel is queued but no distribution integration exists yet."""


def distribute_item(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    item_id: int,
    x_client: x_int.XClient | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Distribute one approved item. Returns a result dict including
    distribution_id, the posted external ids, and the URL of the first
    tweet (for threads, this is the head of the thread)."""
    row = content_mod.get_item(conn, tenant_id=tenant_id, item_id=item_id)
    if row is None:
        raise DistributionError(f"no item id={item_id} for tenant {tenant_id}")
    if row["status"] != "approved":
        raise DistributionError(
            f"item #{item_id} is {row['status']!r}, not 'approved'"
        )

    channel = row["channel"]
    body = row["body"]
    meta: dict[str, Any] = json.loads(row["metadata_json"])

    if channel == "x_tweet":
        if dry_run:
            posted = [{"id": f"dry-{item_id}", "url": "(dry-run)"}]
        else:
            client = x_client or x_int.XClient.from_env()
            posted = [client.post_tweet(body)]

    elif channel == "x_thread":
        parts = meta.get("parts") or []
        if not parts:
            raise DistributionError(
                f"item #{item_id} is x_thread but metadata.parts is empty"
            )
        if dry_run:
            posted = [
                {"id": f"dry-{item_id}-{i}", "url": "(dry-run)"} for i in range(len(parts))
            ]
        else:
            client = x_client or x_int.XClient.from_env()
            posted = client.post_thread(parts)

    elif channel in ("linkedin", "blog", "email", "reddit_draft", "discord_draft"):
        raise NotYetImplemented(
            f"channel {channel!r} not auto-posted in Phase 3a (X only). "
            f"Reddit/Discord remain draft-only by design — copy/paste manually."
        )
    else:
        raise DistributionError(f"unknown channel {channel!r}")

    primary = posted[0]
    now = datetime.now(timezone.utc).isoformat()

    cur = conn.execute(
        """
        INSERT INTO marketing_distributions
            (tenant_id, content_queue_id, provider, external_id, url, posted_at,
             metrics_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant_id,
            item_id,
            "x",
            primary["id"],
            primary["url"],
            now,
            json.dumps(
                {
                    "thread_ids": [p["id"] for p in posted],
                    "dry_run": dry_run,
                },
                ensure_ascii=False,
            ),
        ),
    )
    distribution_id = cur.lastrowid

    conn.execute(
        """
        UPDATE marketing_content_queue
        SET status = 'posted', posted_at = ?
        WHERE id = ? AND tenant_id = ?
        """,
        (now, item_id, tenant_id),
    )

    posthog_int.capture(
        distinct_id=f"hatchik-marketing-tenant-{tenant_id}",
        event="marketing_post_sent",
        properties={
            "channel": channel,
            "item_id": item_id,
            "primary_external_id": primary["id"],
            "thread_len": len(posted),
            "dry_run": dry_run,
        },
    )

    return {
        "distribution_id": distribution_id,
        "item_id": item_id,
        "tenant_id": tenant_id,
        "channel": channel,
        "primary_url": primary["url"],
        "external_ids": [p["id"] for p in posted],
        "dry_run": dry_run,
    }


def distribute_due(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    limit: int = 10,
    x_client: x_int.XClient | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Distribute every currently-approved item (up to `limit`).

    For Phase 3a we don't yet honor `scheduled_for` — that lands with
    the Phase 3b scheduler. For now, all approved items are due."""
    rows = content_mod.list_queue(
        conn, tenant_id=tenant_id, status="approved", limit=limit
    )
    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            results.append(
                distribute_item(
                    conn,
                    tenant_id=tenant_id,
                    item_id=row["id"],
                    x_client=x_client,
                    dry_run=dry_run,
                )
            )
        except (DistributionError, x_int.MissingXCredentials, RuntimeError) as exc:
            results.append(
                {
                    "item_id": row["id"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return results


def list_distributions(
    conn: sqlite3.Connection, *, tenant_id: int, limit: int = 20
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, content_queue_id, provider, external_id, url, posted_at,
               last_metrics_fetched_at, metrics_json
        FROM marketing_distributions
        WHERE tenant_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (tenant_id, limit),
    ).fetchall()
