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
