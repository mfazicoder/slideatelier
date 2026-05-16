"""Per-tenant API-key vault.

Stores Fernet-encrypted secrets in `marketing_tenant_api_keys.ciphertext`.
The master key lives in env (`MARKETING_MASTER_ENCRYPTION_KEY`) — a 32-
byte url-safe base64 string. Generate one with `secrets.generate_key()`
or `python -m marketing.cli secrets generate-key`.

Tenant 1 (the founder tenant) can fall back to env vars — no keys
table row needed. Tenant N>1 (productized customer tenants) MUST
have a row; env fallback is denied to prevent cross-tenant leaks.

Lazy `cryptography` import so the base package installs without it.
Install via `pip install -e ".[multitenant]"`.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Iterable


_FOUNDER_TENANT_ID = 1


class MissingMasterKey(Exception):
    pass


class MissingTenantKey(Exception):
    pass


class MissingCryptoLib(Exception):
    pass


# ─── master key ────────────────────────────────────────────────────────


def _fernet():
    """Lazy load the cryptography lib + master key."""
    try:
        from cryptography.fernet import Fernet  # noqa: WPS433
    except ImportError as exc:
        raise MissingCryptoLib(
            "cryptography not installed. Run "
            '`pip install -e ".[multitenant]"` inside marketing/.'
        ) from exc
    master = os.environ.get("MARKETING_MASTER_ENCRYPTION_KEY", "")
    if not master:
        raise MissingMasterKey(
            "MARKETING_MASTER_ENCRYPTION_KEY env var not set. "
            "Generate one with `python -m marketing.cli secrets generate-key`."
        )
    return Fernet(master.encode("utf-8"))


def generate_key() -> str:
    """Return a fresh Fernet key as a UTF-8 string. Print it once;
    store it in /etc/hatchik/signup.env as MARKETING_MASTER_ENCRYPTION_KEY.
    Rotating the master key requires re-encrypting every row in
    marketing_tenant_api_keys — not implemented in v1."""
    try:
        from cryptography.fernet import Fernet  # noqa: WPS433
    except ImportError as exc:
        raise MissingCryptoLib(
            "cryptography not installed. Run "
            '`pip install -e ".[multitenant]"` inside marketing/.'
        ) from exc
    return Fernet.generate_key().decode("utf-8")


# ─── encrypt / decrypt ─────────────────────────────────────────────────


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode("utf-8")


# ─── tenant_api_keys CRUD ──────────────────────────────────────────────


def set_key(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    provider: str,
    plaintext: str,
) -> None:
    """Upsert an encrypted key for a tenant/provider pair."""
    ct = encrypt(plaintext)
    now = datetime.now(timezone.utc).isoformat()
    # INSERT OR REPLACE re-uses the (tenant_id, provider) PRIMARY KEY.
    conn.execute(
        """
        INSERT OR REPLACE INTO marketing_tenant_api_keys
            (tenant_id, provider, ciphertext, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (tenant_id, provider, ct, now),
    )


def get_key(
    conn: sqlite3.Connection, *, tenant_id: int, provider: str
) -> str | None:
    """Return the decrypted secret, or None if not stored."""
    row = conn.execute(
        "SELECT ciphertext FROM marketing_tenant_api_keys WHERE tenant_id=? AND provider=?",
        (tenant_id, provider),
    ).fetchone()
    if row is None:
        return None
    return decrypt(row["ciphertext"])


def delete_key(
    conn: sqlite3.Connection, *, tenant_id: int, provider: str
) -> bool:
    cur = conn.execute(
        "DELETE FROM marketing_tenant_api_keys WHERE tenant_id=? AND provider=?",
        (tenant_id, provider),
    )
    return cur.rowcount > 0


def list_providers(
    conn: sqlite3.Connection, *, tenant_id: int
) -> list[dict[str, str]]:
    """Return [{provider, created_at}, …] — never the plaintext."""
    rows = conn.execute(
        "SELECT provider, created_at FROM marketing_tenant_api_keys WHERE tenant_id=? ORDER BY provider",
        (tenant_id,),
    ).fetchall()
    return [{"provider": r["provider"], "created_at": r["created_at"]} for r in rows]


# ─── per-tenant resolution with env fallback (founder only) ────────────


def resolve_key(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    provider: str,
    env_keys: Iterable[str] = (),
) -> str:
    """Look up an API key for a tenant. Order:
      1. marketing_tenant_api_keys row → decrypt + return.
      2. (only for the founder tenant id=1) check env_keys in order.
    Raises MissingTenantKey if nothing resolves.

    For tenant N>1 we *never* fall back to env — that would mean a
    customer tenant unintentionally uses Hatchik's master keys."""
    db_value = get_key(conn, tenant_id=tenant_id, provider=provider)
    if db_value is not None:
        return db_value
    if tenant_id == _FOUNDER_TENANT_ID:
        for env_key in env_keys:
            v = os.environ.get(env_key)
            if v:
                return v
    raise MissingTenantKey(
        f"no key for tenant {tenant_id}, provider {provider!r}. "
        "Set via `marketing.cli tenant key set …` or populate env (founder tenant only)."
    )


def materialize_env_for_tenant(
    conn: sqlite3.Connection, *, tenant_id: int, env_map: dict[str, str]
) -> dict[str, str]:
    """For agents that read keys via env (XClient.from_env, etc.),
    populate os.environ with the tenant's keys for the duration of a
    run. Returns the prior env values so the caller can restore them.

    `env_map` maps env var name → provider name in the tenant_api_keys
    table. e.g. {"X_API_CONSUMER_KEY": "x_consumer_key", …}."""
    prior: dict[str, str] = {}
    for env_var, provider in env_map.items():
        value = get_key(conn, tenant_id=tenant_id, provider=provider)
        if value is None:
            continue
        if env_var in os.environ:
            prior[env_var] = os.environ[env_var]
        os.environ[env_var] = value
    return prior


def restore_env(prior: dict[str, str], cleared: Iterable[str]) -> None:
    """Counterpart to materialize_env_for_tenant. Restores prior values
    and clears anything we set."""
    for env_var in cleared:
        if env_var in prior:
            os.environ[env_var] = prior[env_var]
        else:
            os.environ.pop(env_var, None)
