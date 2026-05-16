"""Phase-5 tests — Fernet vault, tenant CRUD, multi-tenant isolation.

Requires the [multitenant] extra (cryptography). Tests are skipped
when it's unavailable.

No network.
"""

from __future__ import annotations

import json

import pytest


cryptography = pytest.importorskip("cryptography")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("HATCHIK_SIGNUP_DB", str(db_path))
    monkeypatch.setenv("MARKETING_DAILY_CAP_USD", "5.00")
    # Provide a fixed master key so encrypt/decrypt is reproducible.
    from cryptography.fernet import Fernet
    monkeypatch.setenv("MARKETING_MASTER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    for var in (
        "ANTHROPIC_API_KEY", "HATCHIK_ANTHROPIC_MASTER_KEY",
        "POSTHOG_API_KEY", "RESEND_API_KEY",
        "X_API_CONSUMER_KEY", "X_API_CONSUMER_SECRET",
        "X_API_ACCESS_TOKEN", "X_API_ACCESS_TOKEN_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    import importlib
    from marketing import (
        analysis, analytics, anthropic_client, budget, config, content, db,
        distribute, jobs, prompts, runs, schema, secrets, seed, strategy, tenant,
        worker,
    )
    from marketing.integrations import posthog as ph_int, resend as resend_int, x as x_int
    for mod in (config, db, schema, tenant, budget, runs, prompts,
                anthropic_client, content, seed, strategy,
                x_int, ph_int, resend_int, distribute, jobs, worker,
                analytics, analysis, secrets):
        importlib.reload(mod)
    return db_path


# ─── encrypt/decrypt ────────────────────────────────────────────────────


def test_encrypt_decrypt_round_trip(tmp_db):
    from marketing import secrets

    ct = secrets.encrypt("hello secret")
    assert isinstance(ct, (bytes, bytearray))
    assert b"hello secret" not in ct  # encrypted, not plaintext
    assert secrets.decrypt(ct) == "hello secret"


def test_missing_master_key_raises(tmp_db, monkeypatch):
    from marketing import secrets

    monkeypatch.delenv("MARKETING_MASTER_ENCRYPTION_KEY", raising=False)
    with pytest.raises(secrets.MissingMasterKey):
        secrets.encrypt("anything")


def test_generate_key_returns_valid_fernet(tmp_db):
    from cryptography.fernet import Fernet
    from marketing import secrets

    key = secrets.generate_key()
    # Round-trip via a Fernet built from the key.
    f = Fernet(key.encode("utf-8"))
    assert f.decrypt(f.encrypt(b"x")) == b"x"


# ─── tenant_api_keys CRUD ──────────────────────────────────────────────


def test_set_get_delete_key(tmp_db):
    from marketing import db, schema, secrets, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        secrets.set_key(conn, tenant_id=tid, provider="anthropic", plaintext="sk-test-123")
        assert secrets.get_key(conn, tenant_id=tid, provider="anthropic") == "sk-test-123"

        providers = secrets.list_providers(conn, tenant_id=tid)
        assert providers == [{"provider": "anthropic", "created_at": providers[0]["created_at"]}]

        assert secrets.delete_key(conn, tenant_id=tid, provider="anthropic") is True
        assert secrets.get_key(conn, tenant_id=tid, provider="anthropic") is None
        # Idempotent delete.
        assert secrets.delete_key(conn, tenant_id=tid, provider="anthropic") is False
    finally:
        conn.close()


def test_set_key_replaces_existing(tmp_db):
    from marketing import db, schema, secrets, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        secrets.set_key(conn, tenant_id=tid, provider="x_consumer_key", plaintext="old")
        secrets.set_key(conn, tenant_id=tid, provider="x_consumer_key", plaintext="new")
        assert secrets.get_key(conn, tenant_id=tid, provider="x_consumer_key") == "new"
        rows = secrets.list_providers(conn, tenant_id=tid)
        assert len(rows) == 1
    finally:
        conn.close()


# ─── resolve_key with env fallback (founder only) ──────────────────────


def test_resolve_key_db_hit(tmp_db):
    from marketing import db, schema, secrets, seed

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        secrets.set_key(conn, tenant_id=tid, provider="anthropic", plaintext="from-db")
        v = secrets.resolve_key(
            conn, tenant_id=tid, provider="anthropic",
            env_keys=["HATCHIK_ANTHROPIC_MASTER_KEY"],
        )
        assert v == "from-db"
    finally:
        conn.close()


def test_resolve_key_env_fallback_for_founder(tmp_db, monkeypatch):
    from marketing import db, schema, secrets, seed

    monkeypatch.setenv("HATCHIK_ANTHROPIC_MASTER_KEY", "from-env")
    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = seed.seed_hatchik_tenant(conn)
        assert tid == 1  # founder tenant
        # No DB row → env fallback resolves.
        v = secrets.resolve_key(
            conn, tenant_id=tid, provider="anthropic",
            env_keys=["HATCHIK_ANTHROPIC_MASTER_KEY"],
        )
        assert v == "from-env"
    finally:
        conn.close()


def test_resolve_key_env_fallback_denied_for_customer_tenant(tmp_db, monkeypatch):
    """Tenant N>1 must never inherit Hatchik's env-supplied master keys."""
    from marketing import db, schema, secrets, seed, tenant

    monkeypatch.setenv("HATCHIK_ANTHROPIC_MASTER_KEY", "from-env")
    schema.ensure_schema()
    conn = db.connect()
    try:
        seed.seed_hatchik_tenant(conn)  # id=1
        customer_tid = tenant.create(conn, slug="customer-a")
        assert customer_tid != 1
        with pytest.raises(secrets.MissingTenantKey):
            secrets.resolve_key(
                conn, tenant_id=customer_tid, provider="anthropic",
                env_keys=["HATCHIK_ANTHROPIC_MASTER_KEY"],
            )
    finally:
        conn.close()


# ─── tenant CRUD ───────────────────────────────────────────────────────


def test_tenant_create_and_get(tmp_db):
    from marketing import db, schema, tenant

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = tenant.create(
            conn,
            slug="customer-b",
            signup_id=42,
            product_url="https://customer-b.example",
            spend_cap_daily_usd=3.50,
            settings={"channels": {"x": {"enabled": True}}},
        )
        t = tenant.get_by_slug(conn, "customer-b")
        assert t.id == tid
        assert t.signup_id == 42
        assert t.product_url == "https://customer-b.example"
        assert t.spend_cap_daily_usd == 3.50
        assert t.settings["channels"]["x"]["enabled"] is True

        # Lookup by id round-trips too.
        t2 = tenant.get_by_id(conn, tid)
        assert t2.slug == "customer-b"
    finally:
        conn.close()


def test_tenant_list_returns_all(tmp_db):
    from marketing import db, schema, seed, tenant

    schema.ensure_schema()
    conn = db.connect()
    try:
        seed.seed_hatchik_tenant(conn)
        tenant.create(conn, slug="customer-c")
        tenant.create(conn, slug="customer-d")
        rows = tenant.list_all(conn)
        slugs = [r.slug for r in rows]
        assert slugs == ["hatchik", "customer-c", "customer-d"]
    finally:
        conn.close()


def test_tenant_unique_slug(tmp_db):
    import sqlite3
    from marketing import db, schema, tenant

    schema.ensure_schema()
    conn = db.connect()
    try:
        tenant.create(conn, slug="dupe")
        with pytest.raises(sqlite3.IntegrityError):
            tenant.create(conn, slug="dupe")
    finally:
        conn.close()


def test_tenant_bind_signup(tmp_db):
    from marketing import db, schema, tenant

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = tenant.create(conn, slug="will-bind")
        t = tenant.get_by_id(conn, tid)
        assert t.signup_id is None
        tenant.bind_signup(conn, tenant_id=tid, signup_id=99)
        assert tenant.get_by_id(conn, tid).signup_id == 99
    finally:
        conn.close()


def test_tenant_set_status_archives(tmp_db):
    from marketing import db, schema, tenant

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = tenant.create(conn, slug="goes-away")
        tenant.set_status(conn, tenant_id=tid, status="archived")
        with pytest.raises(PermissionError, match="archived"):
            tenant.get_by_slug(conn, "goes-away")
    finally:
        conn.close()


# ─── cross-tenant isolation for keys ───────────────────────────────────


def test_keys_are_tenant_scoped(tmp_db):
    from marketing import db, schema, secrets, seed, tenant

    schema.ensure_schema()
    conn = db.connect()
    try:
        founder = seed.seed_hatchik_tenant(conn)
        customer = tenant.create(conn, slug="customer-e")
        secrets.set_key(conn, tenant_id=founder, provider="anthropic", plaintext="founder-key")
        secrets.set_key(conn, tenant_id=customer, provider="anthropic", plaintext="customer-key")
        assert secrets.get_key(conn, tenant_id=founder, provider="anthropic") == "founder-key"
        assert secrets.get_key(conn, tenant_id=customer, provider="anthropic") == "customer-key"
    finally:
        conn.close()


# ─── materialize_env / restore_env ─────────────────────────────────────


def test_materialize_env_for_tenant(tmp_db, monkeypatch):
    import os
    from marketing import db, schema, secrets, tenant

    schema.ensure_schema()
    conn = db.connect()
    try:
        tid = tenant.create(conn, slug="customer-with-x")
        secrets.set_key(conn, tenant_id=tid, provider="x_consumer_key", plaintext="ck-tenant")

        # Set a pre-existing env value so we verify restore_env returns it.
        monkeypatch.setenv("X_API_CONSUMER_KEY", "ck-before")
        prior = secrets.materialize_env_for_tenant(
            conn, tenant_id=tid,
            env_map={"X_API_CONSUMER_KEY": "x_consumer_key"},
        )
        assert os.environ["X_API_CONSUMER_KEY"] == "ck-tenant"
        assert prior == {"X_API_CONSUMER_KEY": "ck-before"}

        secrets.restore_env(prior, cleared={"X_API_CONSUMER_KEY"})
        assert os.environ["X_API_CONSUMER_KEY"] == "ck-before"
    finally:
        conn.close()
