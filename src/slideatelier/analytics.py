"""Sprint Q — slide analytics persistence + aggregation.

Stores anonymous viewer events for published Web Decks in a SQLite database
shared with the auth/landing tables (`${SLIDEATELIER_OUTPUT_DIR}/atelier.db`).
We never co-create or migrate other modules' tables — every statement here is
scoped to `slide_events` and uses CREATE TABLE IF NOT EXISTS so parallel agents
can boot the same file without clobbering us.

Privacy contract (must be honoured wherever this module is touched):
    * No IP addresses, names, emails, phone numbers or other PII are stored.
    * `anon_session` is a client-generated UUID4 stored in localStorage; it is
      opaque to the server and rotates whenever the user clears storage.
    * `extras_json` is scrubbed by the beacon JS *before* it leaves the browser
      AND defensively re-scrubbed here in case a malicious client bypasses the
      JS layer. See `_scrub_extras`.
    * Payloads larger than 2KB are rejected outright (anti-flood + anti-PII).

Why a hand-rolled sqlite wrapper instead of the AuthDB class?
    * AuthDB lives in `auth/db.py` which this sprint is forbidden to touch.
    * Sharing a connection across modules invites lock contention; a separate
      short-lived connection per call is fine for the volumes analytics will
      ever see (single-tenant, single-worker FastAPI).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Constants — kept module-level so tests can monkeypatch them cheaply.
# ---------------------------------------------------------------------------

VALID_EVENTS: frozenset[str] = frozenset(
    {"slide_enter", "slide_exit", "cta_click", "share"}
)
MAX_PAYLOAD_BYTES = 2048
ANON_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DECK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Lightweight PII scrubbers. These run after the beacon JS has already
# stripped, so we treat them as defence-in-depth: drop the field entirely
# rather than redacting in place — keeps the audit story simple.
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\s.-]?){7,15}\d(?!\d)")
# Crude street-address sniffer — "123 Main St", "456 Elm Avenue" etc.
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z][A-Za-z .'-]{2,}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# DB plumbing
# ---------------------------------------------------------------------------

_init_lock = threading.Lock()
_initialised: set[str] = set()


def db_path(output_dir: Path | None = None) -> Path:
    """Resolve the analytics DB path (re-reads env each call so tests work)."""
    if output_dir is None:
        output_dir = Path(os.getenv("SLIDEATELIER_OUTPUT_DIR", "./output"))
    return output_dir / "atelier.db"


def _connect(output_dir: Path | None = None) -> sqlite3.Connection:
    p = db_path(output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    # Foreign keys aren't used here, but match the auth module's pragma so
    # that opening the DB doesn't shift global state.
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn, str(p))
    return conn


def _ensure_schema(conn: sqlite3.Connection, key: str) -> None:
    """Create our table if it isn't there. Idempotent + cheap so we don't
    bother caching beyond a process-local set."""
    with _init_lock:
        if key in _initialised:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS slide_events (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              deck_id       TEXT    NOT NULL,
              slug          TEXT    NOT NULL,
              slide_idx     INTEGER NOT NULL,
              event         TEXT    NOT NULL,
              ts            REAL    NOT NULL,
              anon_session  TEXT    NOT NULL,
              duration_ms   INTEGER,
              extras_json   TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_slide_events_deck
              ON slide_events(deck_id);
            CREATE INDEX IF NOT EXISTS idx_slide_events_slug
              ON slide_events(slug);
            CREATE INDEX IF NOT EXISTS idx_slide_events_event
              ON slide_events(event);
            """
        )
        conn.commit()
        _initialised.add(key)


# ---------------------------------------------------------------------------
# Validation + ingestion
# ---------------------------------------------------------------------------


class AnalyticsValidationError(ValueError):
    """Raised when an inbound event payload fails validation. Routes turn this
    into a 400 — never leak the actual reason to clients (could help an
    attacker find an unblocked field), but we do log internally."""


def _scrub_extras(extras: Any) -> dict[str, Any]:
    """Recursively drop dict values that match obvious PII patterns. Lists
    of strings keep non-PII items; nested dicts get the same treatment."""
    if not isinstance(extras, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in extras.items():
        if not isinstance(k, str):
            continue
        # Reject suspicious-looking keys outright. Defence in depth — the JS
        # layer is supposed to have done this already.
        kl = k.lower()
        if any(
            tag in kl
            for tag in (
                "email", "phone", "address", "name", "ip", "user_agent",
                "password", "ssn", "dob", "credit",
            )
        ):
            continue
        if isinstance(v, str):
            if _looks_pii(v):
                continue
            out[k] = v[:512]  # cap value length
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list):
            out[k] = [
                item for item in v
                if isinstance(item, (int, float, bool)) or
                (isinstance(item, str) and not _looks_pii(item))
            ][:32]
        elif isinstance(v, dict):
            out[k] = _scrub_extras(v)
        # else: silently drop — only JSON-safe primitives are persisted.
    return out


def _looks_pii(s: str) -> bool:
    if not isinstance(s, str):
        return False
    if _EMAIL_RE.search(s):
        return True
    if _ADDRESS_RE.search(s):
        return True
    # Phone heuristic is noisy on UUIDs etc, so guard with a digit-density
    # check first.
    digits = sum(c.isdigit() for c in s)
    if digits >= 7 and _PHONE_RE.search(s):
        return True
    return False


def validate_payload(raw: bytes | str | dict[str, Any]) -> dict[str, Any]:
    """Parse + validate an event POST body. Returns a normalised dict ready
    for `record_event`. Raises AnalyticsValidationError on any issue."""
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise AnalyticsValidationError("payload too large")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise AnalyticsValidationError(f"bad json: {e}") from e
    elif isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise AnalyticsValidationError("payload too large")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AnalyticsValidationError(f"bad json: {e}") from e
    elif isinstance(raw, dict):
        data = raw
    else:
        raise AnalyticsValidationError("unsupported payload type")

    if not isinstance(data, dict):
        raise AnalyticsValidationError("payload must be a JSON object")

    event = data.get("event")
    if event not in VALID_EVENTS:
        raise AnalyticsValidationError("unknown event type")

    deck_id = str(data.get("deck_id", "")).strip()
    if not DECK_ID_RE.match(deck_id):
        raise AnalyticsValidationError("bad deck_id")

    slug = str(data.get("slug", "")).strip()
    if not SLUG_RE.match(slug):
        raise AnalyticsValidationError("bad slug")

    slide_id = data.get("slide_id", data.get("slide_idx", 0))
    try:
        slide_idx = int(slide_id)
    except (TypeError, ValueError) as e:
        raise AnalyticsValidationError("bad slide_id") from e
    if slide_idx < 0 or slide_idx > 1000:
        raise AnalyticsValidationError("slide_id out of range")

    ts = data.get("ts")
    try:
        ts_f = float(ts) if ts is not None else time.time()
    except (TypeError, ValueError) as e:
        raise AnalyticsValidationError("bad ts") from e

    anon_session = str(data.get("anon_session", "")).strip()
    if not ANON_SESSION_RE.match(anon_session):
        raise AnalyticsValidationError("bad anon_session")

    duration_ms = data.get("duration_ms")
    if duration_ms is not None:
        try:
            duration_ms = int(duration_ms)
        except (TypeError, ValueError) as e:
            raise AnalyticsValidationError("bad duration_ms") from e
        if duration_ms < 0 or duration_ms > 24 * 3600 * 1000:
            raise AnalyticsValidationError("duration out of range")

    extras = data.get("extras")
    extras_clean = _scrub_extras(extras) if extras is not None else {}
    # Cap referrer specifically — it's the only field we capture by spec but
    # it can be a long URL with embedded query params. Scheme+host only.
    if "referrer" in extras_clean and isinstance(extras_clean["referrer"], str):
        extras_clean["referrer"] = _safe_referrer(extras_clean["referrer"])

    return {
        "deck_id": deck_id,
        "slug": slug,
        "slide_idx": slide_idx,
        "event": event,
        "ts": ts_f,
        "anon_session": anon_session,
        "duration_ms": duration_ms,
        "extras": extras_clean,
    }


def _safe_referrer(url: str) -> str:
    """Strip path + query from a referrer URL. We only want the origin so
    the dashboard can show 'top sources' without leaking page-level intent."""
    if not url:
        return ""
    # Naive scheme://host extraction — full urlparse would also work but this
    # avoids accidentally surfacing username:password@ artefacts.
    m = re.match(r"^(https?://[^/?#]+)", url, re.IGNORECASE)
    return m.group(1)[:120] if m else ""


def record_event(payload: dict[str, Any], output_dir: Path | None = None) -> int:
    """Insert a validated payload. Returns rowid."""
    extras_json = json.dumps(payload.get("extras") or {}, separators=(",", ":"))
    conn = _connect(output_dir)
    try:
        cur = conn.execute(
            "INSERT INTO slide_events"
            "(deck_id, slug, slide_idx, event, ts, anon_session, duration_ms, extras_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload["deck_id"],
                payload["slug"],
                payload["slide_idx"],
                payload["event"],
                payload["ts"],
                payload["anon_session"],
                payload.get("duration_ms"),
                extras_json,
            ),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Aggregation helpers — feed the dashboard. All return plain dicts/lists so
# the Jinja template stays trivially testable.
# ---------------------------------------------------------------------------


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return s[k]


def deck_funnel(deck_id: str, output_dir: Path | None = None) -> list[dict[str, Any]]:
    """Per-slide unique-session retention. Returns a list ordered by slide_idx
    with `{slide_idx, unique_sessions, retention}` (retention relative to slide 0)."""
    conn = _connect(output_dir)
    try:
        rows = conn.execute(
            "SELECT slide_idx, COUNT(DISTINCT anon_session) AS sessions "
            "FROM slide_events WHERE deck_id = ? AND event = 'slide_enter' "
            "GROUP BY slide_idx ORDER BY slide_idx",
            (deck_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []
    base = rows[0]["sessions"] or 1
    out = []
    for r in rows:
        sessions = int(r["sessions"] or 0)
        out.append({
            "slide_idx": int(r["slide_idx"]),
            "unique_sessions": sessions,
            "retention": (sessions / base) if base else 0.0,
        })
    return out


def slide_dwell(deck_id: str, output_dir: Path | None = None) -> list[dict[str, Any]]:
    """Median + p90 dwell time per slide, in ms. Source: slide_exit events
    that carry duration_ms."""
    conn = _connect(output_dir)
    try:
        rows = conn.execute(
            "SELECT slide_idx, duration_ms FROM slide_events "
            "WHERE deck_id = ? AND event = 'slide_exit' AND duration_ms IS NOT NULL "
            "ORDER BY slide_idx",
            (deck_id,),
        ).fetchall()
    finally:
        conn.close()
    by_slide: dict[int, list[int]] = {}
    for r in rows:
        by_slide.setdefault(int(r["slide_idx"]), []).append(int(r["duration_ms"]))
    out = []
    for slide_idx, vals in sorted(by_slide.items()):
        out.append({
            "slide_idx": slide_idx,
            "samples": len(vals),
            "median_ms": _percentile(vals, 50),
            "p90_ms": _percentile(vals, 90),
        })
    return out


def cta_ctr(deck_id: str, output_dir: Path | None = None) -> list[dict[str, Any]]:
    """Click-through rate per slide. CTR = clicks / unique slide_enter sessions."""
    conn = _connect(output_dir)
    try:
        click_rows = conn.execute(
            "SELECT slide_idx, COUNT(*) AS clicks "
            "FROM slide_events WHERE deck_id = ? AND event = 'cta_click' "
            "GROUP BY slide_idx",
            (deck_id,),
        ).fetchall()
        enter_rows = conn.execute(
            "SELECT slide_idx, COUNT(DISTINCT anon_session) AS viewers "
            "FROM slide_events WHERE deck_id = ? AND event = 'slide_enter' "
            "GROUP BY slide_idx",
            (deck_id,),
        ).fetchall()
    finally:
        conn.close()
    viewers = {int(r["slide_idx"]): int(r["viewers"]) for r in enter_rows}
    out = []
    for r in click_rows:
        idx = int(r["slide_idx"])
        clicks = int(r["clicks"])
        v = viewers.get(idx, 0)
        out.append({
            "slide_idx": idx,
            "clicks": clicks,
            "viewers": v,
            "ctr": (clicks / v) if v else 0.0,
        })
    out.sort(key=lambda d: d["slide_idx"])
    return out


def top_referrers(
    deck_id: str, limit: int = 10, output_dir: Path | None = None
) -> list[dict[str, Any]]:
    """Top sources by event volume — pulled from extras_json.referrer."""
    conn = _connect(output_dir)
    try:
        rows = conn.execute(
            "SELECT extras_json FROM slide_events "
            "WHERE deck_id = ? AND event = 'slide_enter' AND extras_json IS NOT NULL",
            (deck_id,),
        ).fetchall()
    finally:
        conn.close()
    counts: dict[str, int] = {}
    for r in rows:
        try:
            extras = json.loads(r["extras_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        ref = extras.get("referrer") if isinstance(extras, dict) else None
        if not ref or not isinstance(ref, str):
            continue
        counts[ref] = counts.get(ref, 0) + 1
    return [
        {"referrer": ref, "count": n}
        for ref, n in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    ]


def total_sessions(deck_id: str, output_dir: Path | None = None) -> int:
    conn = _connect(output_dir)
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT anon_session) AS n FROM slide_events "
            "WHERE deck_id = ?",
            (deck_id,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"]) if row else 0


def all_events_for(
    deck_id: str, output_dir: Path | None = None
) -> Iterable[sqlite3.Row]:
    """Test helper — returns every row for a deck."""
    conn = _connect(output_dir)
    try:
        return list(conn.execute(
            "SELECT * FROM slide_events WHERE deck_id = ? ORDER BY id",
            (deck_id,),
        ).fetchall())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Per-deck settings (privacy opt-out etc.) — JSON file under the workflow dir.
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS: dict[str, Any] = {
    "analytics_enabled": True,
}


def settings_path(workflow_dir: Path) -> Path:
    return workflow_dir / "analytics_settings.json"


def load_settings(workflow_dir: Path) -> dict[str, Any]:
    p = settings_path(workflow_dir)
    if not p.exists():
        return dict(_DEFAULT_SETTINGS)
    try:
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            return dict(_DEFAULT_SETTINGS)
        merged = dict(_DEFAULT_SETTINGS)
        merged.update({k: v for k, v in data.items() if k in _DEFAULT_SETTINGS})
        return merged
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT_SETTINGS)


def save_settings(workflow_dir: Path, settings: dict[str, Any]) -> dict[str, Any]:
    """Persist the subset of supported settings; returns the merged result."""
    current = load_settings(workflow_dir)
    for k, v in settings.items():
        if k in _DEFAULT_SETTINGS:
            current[k] = bool(v) if isinstance(_DEFAULT_SETTINGS[k], bool) else v
    workflow_dir.mkdir(parents=True, exist_ok=True)
    settings_path(workflow_dir).write_text(json.dumps(current, indent=2, sort_keys=True))
    return current


def new_anon_session() -> str:
    """Server-side generator — only used as a fallback if the client misses
    its own UUID. The real one is created in the beacon JS."""
    return uuid.uuid4().hex
