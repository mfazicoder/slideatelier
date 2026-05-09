"""SQLite-backed user/session/deck store.

Schema (created idempotently at startup via init_schema):

    users
      id            INTEGER PRIMARY KEY AUTOINCREMENT
      email         TEXT    UNIQUE NOT NULL  (lower-cased on write)
      password_hash TEXT    NOT NULL
      created_at    TEXT    NOT NULL  (ISO-8601 UTC)

    sessions
      token         TEXT    PRIMARY KEY    (32 hex chars from secrets.token_hex)
      user_id       INTEGER NOT NULL  REFERENCES users(id)
      created_at    TEXT    NOT NULL
      expires_at    TEXT    NOT NULL

    decks
      job_id        TEXT    PRIMARY KEY
      owner_user_id INTEGER NOT NULL  REFERENCES users(id)
      slug          TEXT    UNIQUE             (NULL until first publish)
      created_at    TEXT    NOT NULL

The `system` user (id=0) is reserved for legacy decks that pre-date auth.
We INSERT OR IGNORE it on startup. Foreign keys are advisory only — sqlite3
honours them when PRAGMA foreign_keys=ON, which we set on every connection.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SYSTEM_USER_ID = 0
SESSION_TTL_DAYS = 30
SESSION_COOKIE_NAME = "atelier_session"

_now = lambda: datetime.now(timezone.utc)
_iso = lambda dt: dt.isoformat()


# ---------------------------------------------------------------------------
# Lightweight typed records — avoid pulling pydantic into the auth path so the
# DB module stays trivially importable from tests.
# ---------------------------------------------------------------------------

@dataclass
class User:
    id: int
    email: str
    password_hash: str
    created_at: str


@dataclass
class Deck:
    job_id: str
    owner_user_id: int
    slug: Optional[str]
    created_at: str


# ---------------------------------------------------------------------------
# AuthDB — thin wrapper around a single sqlite3.Connection. Each call opens a
# short-lived cursor; thread-safety achieved via a module-level lock since
# uvicorn's BackgroundTasks may run in a thread pool.
# ---------------------------------------------------------------------------

class AuthDB:
    """Single-file SQLite store. Safe for the single-worker FastAPI we ship."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False — we serialize via _lock and FastAPI may dispatch
        # request handlers across thread pool workers.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        self._init_schema()

    # ---- schema -------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                  id            INTEGER PRIMARY KEY AUTOINCREMENT,
                  email         TEXT    UNIQUE NOT NULL,
                  password_hash TEXT    NOT NULL,
                  created_at    TEXT    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                  token       TEXT    PRIMARY KEY,
                  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  created_at  TEXT    NOT NULL,
                  expires_at  TEXT    NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS decks (
                  job_id        TEXT    PRIMARY KEY,
                  owner_user_id INTEGER NOT NULL REFERENCES users(id),
                  slug          TEXT    UNIQUE,
                  created_at    TEXT    NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_decks_owner ON decks(owner_user_id);
                """
            )
            # Reserved system user. Placeholder password_hash that no real
            # user could match (bcrypt always starts with $2…); we only ever
            # reach this row through SYSTEM_USER_ID.
            self._conn.execute(
                "INSERT OR IGNORE INTO users(id, email, password_hash, created_at) "
                "VALUES (?, ?, ?, ?)",
                (SYSTEM_USER_ID, "system@localhost", "!system-no-login", _iso(_now())),
            )

    # ---- users --------------------------------------------------------------

    def create_user(self, email: str, password_hash: str) -> User:
        email = email.strip().lower()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO users(email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, password_hash, _iso(_now())),
            )
            uid = cur.lastrowid
        return self.get_user(uid)  # type: ignore[return-value]

    def get_user(self, user_id: int) -> Optional[User]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, password_hash, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return User(**row) if row else None

    def get_user_by_email(self, email: str) -> Optional[User]:
        email = (email or "").strip().lower()
        if not email:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT id, email, password_hash, created_at FROM users WHERE email = ?",
                (email,),
            ).fetchone()
        return User(**row) if row else None

    # ---- sessions -----------------------------------------------------------

    def create_session(self, user_id: int, ttl_days: int = SESSION_TTL_DAYS) -> str:
        token = secrets.token_hex(32)
        now = _now()
        expires = now + timedelta(days=ttl_days)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sessions(token, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token, user_id, _iso(now), _iso(expires)),
            )
        return token

    def get_user_for_session(self, token: str) -> Optional[User]:
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT u.id, u.email, u.password_hash, u.created_at "
                "FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token = ? AND s.expires_at > ?",
                (token, _iso(_now())),
            ).fetchone()
        return User(**row) if row else None

    def delete_session(self, token: str) -> None:
        if not token:
            return
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    # ---- decks --------------------------------------------------------------

    def record_deck(self, job_id: str, owner_user_id: int) -> None:
        """INSERT OR IGNORE — registering an already-known job is a no-op."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO decks(job_id, owner_user_id, slug, created_at) "
                "VALUES (?, ?, NULL, ?)",
                (job_id, owner_user_id, _iso(_now())),
            )

    def set_deck_slug(self, job_id: str, slug: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE decks SET slug = ? WHERE job_id = ?",
                (slug, job_id),
            )

    def get_deck(self, job_id: str) -> Optional[Deck]:
        with self._lock:
            row = self._conn.execute(
                "SELECT job_id, owner_user_id, slug, created_at FROM decks WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return Deck(**row) if row else None

    def get_deck_by_slug(self, slug: str) -> Optional[Deck]:
        if not slug:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT job_id, owner_user_id, slug, created_at FROM decks WHERE slug = ?",
                (slug,),
            ).fetchone()
        return Deck(**row) if row else None

    def list_decks_for_user(self, user_id: int) -> list[Deck]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT job_id, owner_user_id, slug, created_at FROM decks "
                "WHERE owner_user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [Deck(**r) for r in rows]


# ---------------------------------------------------------------------------
# Per-output-dir AuthDB cache. Tests monkeypatch SLIDEATELIER_OUTPUT_DIR per
# function, so we key the cache by absolute path; idempotent re-init is fine.
# ---------------------------------------------------------------------------

_db_cache: dict[str, AuthDB] = {}
_db_cache_lock = threading.Lock()


def get_db(output_dir: Path | None = None) -> AuthDB:
    """Return the AuthDB for the given output_dir, creating it if needed.

    When called with no argument we resolve $SLIDEATELIER_OUTPUT_DIR fresh — so
    monkeypatched test environments get a fresh DB per tmp_path.
    """
    if output_dir is None:
        output_dir = Path(os.getenv("SLIDEATELIER_OUTPUT_DIR", "./output")).resolve()
    else:
        output_dir = output_dir.resolve()
    db_path = output_dir / "atelier.db"
    key = str(db_path)
    with _db_cache_lock:
        db = _db_cache.get(key)
        if db is None:
            db = AuthDB(db_path)
            _db_cache[key] = db
    return db


def reset_db_cache() -> None:
    """Test helper — clear the cache so a fresh tmp_path gets a fresh DB."""
    with _db_cache_lock:
        for db in _db_cache.values():
            try:
                db._conn.close()  # noqa: SLF001
            except Exception:  # noqa: BLE001
                pass
        _db_cache.clear()


# ---------------------------------------------------------------------------
# Migration — seed legacy data on first start of the new schema.
# ---------------------------------------------------------------------------

def migrate_legacy_layout(output_dir: Path) -> dict:
    """Idempotently seed the SQLite decks table from pre-auth filesystem state.

    Walks output/workflow/* and output/web_slugs.json. Anything not yet present
    in `decks` is recorded under owner_user_id=SYSTEM_USER_ID. Files are NOT
    moved — the legacy flat layout keeps working through the anonymous code
    path, and we just add a database record so the slug → job_id lookup can
    move from JSON into SQL.

    Returns a small report dict: {"jobs_seeded": N, "slugs_seeded": M}.
    """
    db = get_db(output_dir)
    report = {"jobs_seeded": 0, "slugs_seeded": 0}

    workflow_root = output_dir / "workflow"
    if workflow_root.exists():
        for child in workflow_root.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            job_id = child.name
            existing = db.get_deck(job_id)
            if existing is None:
                db.record_deck(job_id, SYSTEM_USER_ID)
                report["jobs_seeded"] += 1

    legacy_index = output_dir / "web_slugs.json"
    if legacy_index.exists():
        try:
            import json
            data = json.loads(legacy_index.read_text())
            if isinstance(data, dict):
                for slug, job_id in data.items():
                    if not isinstance(slug, str) or not isinstance(job_id, str):
                        continue
                    deck = db.get_deck(job_id)
                    if deck is None:
                        db.record_deck(job_id, SYSTEM_USER_ID)
                    if deck is None or deck.slug != slug:
                        try:
                            db.set_deck_slug(job_id, slug)
                            report["slugs_seeded"] += 1
                        except sqlite3.IntegrityError:
                            # Slug collision (shouldn't happen in legacy data) — skip.
                            pass
        except Exception:  # noqa: BLE001
            pass

    return report
