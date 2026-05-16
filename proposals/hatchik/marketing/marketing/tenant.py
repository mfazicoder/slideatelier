"""Tenant resolution + multi-tenant CRUD helpers.

SQLite has no RLS — every tenant-scoped query takes tenant_id
explicitly. These helpers turn a slug into an id and back, reject
unknown/inactive tenants loudly, and provide the CRUD surface the
Phase 5 onboarding flow uses (create, list, bind to a signup row,
update settings).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Tenant:
    id: int
    slug: str
    signup_id: int | None
    product_url: str | None
    status: str
    spend_cap_daily_usd: float
    settings: dict | None = None


def get_by_slug(conn: sqlite3.Connection, slug: str) -> Tenant:
    row = conn.execute(
        """
        SELECT id, slug, signup_id, product_url, status, spend_cap_daily_usd,
               settings_json
        FROM marketing_tenants
        WHERE slug = ?
        """,
        (slug,),
    ).fetchone()
    if row is None:
        raise LookupError(f"No marketing tenant with slug {slug!r}")
    if row["status"] != "active":
        raise PermissionError(f"Tenant {slug!r} is {row['status']}, not active")
    return Tenant(
        id=row["id"],
        slug=row["slug"],
        signup_id=row["signup_id"],
        product_url=row["product_url"],
        status=row["status"],
        spend_cap_daily_usd=row["spend_cap_daily_usd"],
        settings=json.loads(row["settings_json"]) if row["settings_json"] else {},
    )


def get_by_id(conn: sqlite3.Connection, tenant_id: int) -> Tenant:
    row = conn.execute(
        """
        SELECT id, slug, signup_id, product_url, status, spend_cap_daily_usd,
               settings_json
        FROM marketing_tenants
        WHERE id = ?
        """,
        (tenant_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"No marketing tenant with id {tenant_id}")
    return Tenant(
        id=row["id"],
        slug=row["slug"],
        signup_id=row["signup_id"],
        product_url=row["product_url"],
        status=row["status"],
        spend_cap_daily_usd=row["spend_cap_daily_usd"],
        settings=json.loads(row["settings_json"]) if row["settings_json"] else {},
    )


def list_all(conn: sqlite3.Connection) -> list[Tenant]:
    rows = conn.execute(
        """
        SELECT id, slug, signup_id, product_url, status, spend_cap_daily_usd, settings_json
        FROM marketing_tenants
        ORDER BY id
        """
    ).fetchall()
    return [
        Tenant(
            id=r["id"],
            slug=r["slug"],
            signup_id=r["signup_id"],
            product_url=r["product_url"],
            status=r["status"],
            spend_cap_daily_usd=r["spend_cap_daily_usd"],
            settings=json.loads(r["settings_json"]) if r["settings_json"] else {},
        )
        for r in rows
    ]


def create(
    conn: sqlite3.Connection,
    *,
    slug: str,
    signup_id: int | None = None,
    product_url: str | None = None,
    spend_cap_daily_usd: float = 5.0,
    settings: dict | None = None,
) -> int:
    """Insert a new tenant. Raises sqlite3.IntegrityError on duplicate slug."""
    cur = conn.execute(
        """
        INSERT INTO marketing_tenants
            (slug, signup_id, product_url, status, spend_cap_daily_usd,
             settings_json, created_at)
        VALUES (?, ?, ?, 'active', ?, ?, ?)
        """,
        (
            slug,
            signup_id,
            product_url,
            spend_cap_daily_usd,
            json.dumps(settings or {}, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def bind_signup(
    conn: sqlite3.Connection, *, tenant_id: int, signup_id: int
) -> None:
    """Attach a signups(id) value to a tenant — used when productizing
    Autonomous Growth to a customer who already exists in the substrate
    signups table."""
    conn.execute(
        "UPDATE marketing_tenants SET signup_id = ? WHERE id = ?",
        (signup_id, tenant_id),
    )


def set_status(
    conn: sqlite3.Connection, *, tenant_id: int, status: str
) -> None:
    if status not in ("active", "paused", "archived"):
        raise ValueError(f"bad status {status!r}")
    conn.execute(
        "UPDATE marketing_tenants SET status = ? WHERE id = ?",
        (status, tenant_id),
    )


def update_settings(
    conn: sqlite3.Connection, *, tenant_id: int, settings: dict
) -> None:
    conn.execute(
        "UPDATE marketing_tenants SET settings_json = ? WHERE id = ?",
        (json.dumps(settings, ensure_ascii=False), tenant_id),
    )
