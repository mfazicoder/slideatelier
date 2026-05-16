"""Tenant resolution helpers.

SQLite has no RLS — every tenant-scoped query takes tenant_id
explicitly. These helpers turn a slug into an id and back, and reject
unknown tenants loudly so bugs can't silently leak across tenants.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Tenant:
    id: int
    slug: str
    signup_id: int | None
    product_url: str | None
    status: str
    spend_cap_daily_usd: float


def get_by_slug(conn: sqlite3.Connection, slug: str) -> Tenant:
    row = conn.execute(
        """
        SELECT id, slug, signup_id, product_url, status, spend_cap_daily_usd
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
    )
