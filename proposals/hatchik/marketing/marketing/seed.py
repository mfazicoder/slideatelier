"""Idempotent seed: ensure the 'hatchik' marketing tenant exists."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from . import config, db, schema


HATCHIK_SETTINGS: dict = {
    "channels": {
        "x": {"enabled": True},
        "linkedin": {"enabled": True},
        "blog": {"enabled": True},
        "email": {"enabled": True},
        "reddit": {"enabled": True, "draft_only": True},
        "discord": {"enabled": True, "draft_only": True},
    },
    # Inputs into Layer 1 (persona agent). Alternatives the audience
    # considers — not direct competitors necessarily. Editable any time
    # by updating marketing_tenants.settings_json.
    "competitors": [
        {"name": "Lovable",     "url": "https://lovable.dev",   "note": "AI-first full-stack app generator"},
        {"name": "Bolt.new",    "url": "https://bolt.new",       "note": "StackBlitz AI app builder"},
        {"name": "Replit Agent","url": "https://replit.com",     "note": "AI dev environment + hosting"},
        {"name": "ShipFast",    "url": "https://shipfa.st",      "note": "SaaS boilerplate sold as a zip"},
        {"name": "Vercel",      "url": "https://vercel.com",     "note": "Frontend deploy; no business layer (auth/payments/mail)"},
    ],
    # Hatchik-specific source docs the persona agent reads. Paths are
    # relative to proposals/hatchik/ so this works in any worktree.
    "product_docs": [
        "PRODUCT_OFFERING.md",
        "MARKETING_PLAN.md",
    ],
}


def seed_hatchik_tenant(conn: sqlite3.Connection) -> int:
    """Insert or fetch the Hatchik tenant. Returns its id."""
    existing = conn.execute(
        "SELECT id FROM marketing_tenants WHERE slug = ?", ("hatchik",)
    ).fetchone()
    if existing is not None:
        return existing["id"]
    cur = conn.execute(
        """
        INSERT INTO marketing_tenants
            (slug, signup_id, product_url, status, spend_cap_daily_usd,
             settings_json, created_at)
        VALUES ('hatchik', NULL, 'https://hatchik.com', 'active', ?, ?, ?)
        """,
        (
            config.DAILY_CAP_USD,
            json.dumps(HATCHIK_SETTINGS, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def main() -> None:
    conn = db.connect()
    try:
        schema.ensure_schema(conn)
        tid = seed_hatchik_tenant(conn)
        print(f"hatchik tenant id={tid} (db={config.DB_PATH})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
