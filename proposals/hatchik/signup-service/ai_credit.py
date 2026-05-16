"""
ai_credit.py — track Hatchik AI passthrough spend per tenant.

Customer-facing promise: "£0.50 AI credit on Sandbox (one-off taster),
£3/month included on Launch, £10/month on Growth. Beyond that, your AI
usage is passed through at cost + a small margin and shown as a single
line on your invoice."

This module owns:

  1. The data model — ``ai_usage`` and ``ai_credit_balance`` tables in
     signups.db.
  2. The accountancy — record_event() drops a token-usage event;
     get_balance() aggregates it into a balance for the account.html UI.
  3. The tier defaults — credit_for_tier() returns the monthly allowance
     in pence for each tier (single source of truth — DO NOT duplicate).

The actual proxy that mediates AI calls (intercept → forward to provider
→ meter response) is OUT OF SCOPE here. That belongs in a separate
``ai-proxy`` service that posts usage events into this module via
record_event(). For v1 we wire the data model and the read-side; the
proxy is wired in a follow-up.

For BYO-API-key customers (the default) we never see the AI traffic, so
their balance stays at the monthly allowance with zero spend. The
account.html UI shows "£3.00 / £3.00 unused — using your own API key".
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))

# Per-tier monthly allowance in pence — single source of truth.
# Spec §2: Sandbox £0.50 one-off, Launch £3/mo, Growth £10/mo.
TIER_CREDIT_PENCE = {
    "sandbox": 50,    # one-off, never renewed
    "launch":  300,   # /month
    "growth":  1000,  # /month
}

# Per-tier overage markup multiplier (cost × N).
# Spec §3.1: pass-through cost × 1.6 (40% net + Paddle fees baked in).
OVERAGE_MARKUP = float(os.environ.get("HATCHIK_AI_OVERAGE_MARKUP", "1.6"))


# ─── Data shape ───────────────────────────────────────────────────────────
@dataclass
class Balance:
    tier: str
    allowance_pence: int         # this month's allowance
    spent_pence: int             # this month's spend
    remaining_pence: int         # max(allowance - spent, 0)
    overage_pence: int           # max(spent - allowance, 0)
    period_start: datetime
    using_byo_key: bool


# ─── Schema ───────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signup_id       INTEGER NOT NULL,
    slug            TEXT    NOT NULL,
    occurred_at     TEXT    NOT NULL,             -- ISO-8601 UTC
    provider        TEXT    NOT NULL,             -- 'anthropic' | 'openai' | …
    model           TEXT    NOT NULL,             -- 'claude-opus-4-7' etc.
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cost_pence      INTEGER NOT NULL,             -- raw provider cost
    billed_pence    INTEGER NOT NULL,             -- cost_pence × markup
    request_id      TEXT,                         -- provider request id (for dedup)
    UNIQUE(provider, request_id)                  -- dedup retries
);
CREATE INDEX IF NOT EXISTS ai_usage_signup_idx ON ai_usage(signup_id, occurred_at);

CREATE TABLE IF NOT EXISTS ai_credit_byo_key_flag (
    signup_id       INTEGER PRIMARY KEY,
    using_byo       INTEGER NOT NULL DEFAULT 0,   -- 1 = using own API key (no passthrough)
    set_at          TEXT    NOT NULL
);
"""


def init_schema(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(DB_PATH)
    try:
        db.row_factory = sqlite3.Row
        yield db
        db.commit()
    finally:
        db.close()


# ─── Tier helpers ─────────────────────────────────────────────────────────
def credit_for_tier(tier: str) -> int:
    """Monthly allowance in pence for the given tier."""
    return TIER_CREDIT_PENCE.get(tier.lower(), 0)


def _period_start(now: datetime | None = None) -> datetime:
    """First day of the current calendar month, UTC."""
    n = now or datetime.now(timezone.utc)
    return datetime(n.year, n.month, 1, tzinfo=timezone.utc)


# ─── Write side ───────────────────────────────────────────────────────────
def record_event(
    signup_id: int, slug: str, *, provider: str, model: str,
    tokens_in: int, tokens_out: int, cost_pence: int,
    request_id: str | None = None, when: datetime | None = None,
) -> int | None:
    """Record one AI passthrough call. Returns row id or None if duplicate.

    Called by the AI proxy service (out of scope here) when it forwards
    a customer's call to Anthropic/OpenAI. The proxy computes cost_pence
    from the provider's response (input + output tokens × model rate).
    """
    when = when or datetime.now(timezone.utc)
    billed = int(round(cost_pence * OVERAGE_MARKUP))
    try:
        with _conn() as db:
            init_schema(db)
            cur = db.execute(
                """INSERT INTO ai_usage
                   (signup_id, slug, occurred_at, provider, model,
                    tokens_in, tokens_out, cost_pence, billed_pence, request_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (signup_id, slug, when.isoformat(), provider, model,
                 tokens_in, tokens_out, cost_pence, billed, request_id),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        # Same provider request_id seen before — retry of an already-recorded call.
        return None


def set_byo_key_flag(signup_id: int, using_byo: bool) -> None:
    """Customer flipped between Hatchik passthrough and their own key.

    Called from /api/account/me?ai_key=byo  or  ?ai_key=hatchik.
    """
    with _conn() as db:
        init_schema(db)
        db.execute(
            """INSERT INTO ai_credit_byo_key_flag (signup_id, using_byo, set_at)
               VALUES (?, ?, ?)
               ON CONFLICT(signup_id) DO UPDATE SET
                   using_byo = excluded.using_byo, set_at = excluded.set_at""",
            (signup_id, 1 if using_byo else 0,
             datetime.now(timezone.utc).isoformat()),
        )


# ─── Read side (powers /api/account/ai-credit/{slug}) ─────────────────────
def get_balance(signup_id: int, tier: str) -> Balance:
    """Compute this calendar month's balance for the customer."""
    allowance = credit_for_tier(tier)
    period_start = _period_start()

    with _conn() as db:
        init_schema(db)
        cur = db.execute(
            """SELECT COALESCE(SUM(billed_pence), 0) AS spent
                 FROM ai_usage
                WHERE signup_id = ?
                  AND occurred_at >= ?""",
            (signup_id, period_start.isoformat()),
        )
        spent = int(cur.fetchone()["spent"] or 0)
        cur = db.execute(
            "SELECT using_byo FROM ai_credit_byo_key_flag WHERE signup_id = ?",
            (signup_id,),
        )
        row = cur.fetchone()
        using_byo = bool(row and row["using_byo"])

    remaining = max(0, allowance - spent)
    overage = max(0, spent - allowance)
    return Balance(
        tier=tier,
        allowance_pence=allowance,
        spent_pence=spent,
        remaining_pence=remaining,
        overage_pence=overage,
        period_start=period_start,
        using_byo_key=using_byo,
    )


def recent_events(signup_id: int, limit: int = 25) -> list[dict]:
    """Last N usage events, newest first. For 'recent activity' UI."""
    with _conn() as db:
        init_schema(db)
        cur = db.execute(
            """SELECT occurred_at, provider, model, tokens_in, tokens_out,
                      cost_pence, billed_pence
                 FROM ai_usage
                WHERE signup_id = ?
                ORDER BY id DESC LIMIT ?""",
            (signup_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]


# ─── HTTP shape (consumed by signup-service/main.py route) ────────────────
def to_json(balance: Balance) -> dict:
    """JSON payload for /api/account/ai-credit/{slug}."""
    def gbp(p: int) -> str:
        return f"£{p/100:.2f}"
    return {
        "tier": balance.tier,
        "allowance_pence": balance.allowance_pence,
        "spent_pence": balance.spent_pence,
        "remaining_pence": balance.remaining_pence,
        "overage_pence": balance.overage_pence,
        "period_start": balance.period_start.isoformat(),
        "using_byo_key": balance.using_byo_key,
        # Pre-formatted for the dashboard so we don't duplicate the £x.xx
        # math across HTML + JS + here.
        "allowance_display": gbp(balance.allowance_pence),
        "spent_display": gbp(balance.spent_pence),
        "remaining_display": gbp(balance.remaining_pence),
        "overage_display": gbp(balance.overage_pence),
    }
