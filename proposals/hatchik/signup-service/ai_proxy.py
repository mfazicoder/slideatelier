"""
ai_proxy.py — Anthropic + OpenAI passthrough proxy.

Customer-facing surface: customers point their AI SDK at
``https://hatchik.com/v1`` instead of ``api.anthropic.com`` or
``api.openai.com``. They authenticate with a Hatchik token
(``hk_ai_<…>``); we forward to the real provider with the master key
held in env, meter the tokens consumed from the provider's response,
and post a usage event into ``ai_credit.record_event``.

The flow:

  1. Token resolution — look up ``hk_ai_<token>`` in ``ai_tokens``
     table, get signup_id + tier + cap_pence (optional per-token cap).
  2. Cap check — sum month-to-date spend; if (spent + estimated next
     call) exceeds the tier allowance + soft buffer + per-token cap,
     reject with 402 Payment Required.
  3. Forward — proxy the request body verbatim to the real provider,
     swapping the customer's Authorization for the master key. Preserve
     model, headers (except auth), and body shape.
  4. Meter — read tokens_in/tokens_out from the provider's response
     (Anthropic puts them in ``usage``; OpenAI puts them in ``usage``).
  5. Record — POST to ``ai_credit.record_event`` with request_id for
     dedup. The cost_pence we record is the raw provider cost;
     ai_credit applies the × 1.6 markup at billing time.
  6. Return — pass the provider response through unchanged.

Streaming: v1 returns the full response (no SSE proxying). The
Anthropic + OpenAI SDKs default to non-streaming, so this covers most
of the surface. Streaming is on the v2 roadmap.

Auth: the proxy accepts either ``x-api-key`` (Anthropic style) or
``Authorization: Bearer`` (OpenAI style). Customers pick the header
their SDK already sends.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import string
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

import httpx

log = logging.getLogger("ai_proxy")

DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
ANTHROPIC_MASTER_KEY = os.environ.get("HATCHIK_ANTHROPIC_MASTER_KEY", "")
OPENAI_MASTER_KEY = os.environ.get("HATCHIK_OPENAI_MASTER_KEY", "")
ANTHROPIC_BASE = os.environ.get("HATCHIK_ANTHROPIC_BASE", "https://api.anthropic.com")
OPENAI_BASE = os.environ.get("HATCHIK_OPENAI_BASE", "https://api.openai.com")
PROXY_TIMEOUT_S = float(os.environ.get("HATCHIK_AI_PROXY_TIMEOUT_S", "120"))

# Strict caps reject the call once the tier allowance is spent. Soft
# caps allow up to (allowance × OVERAGE_MULTIPLIER_CEILING) so customers
# don't get cliff-edge cut off in the middle of an AI feature ship.
# Operator can tune both via env.
OVERAGE_MULTIPLIER_CEILING = float(os.environ.get("HATCHIK_AI_OVERAGE_CEILING", "5.0"))
STRICT_MODE = os.environ.get("HATCHIK_AI_STRICT_CAP", "0") == "1"


# ─── Schema ───────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signup_id       INTEGER NOT NULL,
    label           TEXT NOT NULL,
    sha256          TEXT NOT NULL UNIQUE,         -- hash of hk_ai_<…>
    prefix          TEXT NOT NULL,                -- first 12 chars for display
    cap_pence       INTEGER,                       -- NULL = use tier default
    created_at      TEXT NOT NULL,
    last_used_at    TEXT,
    revoked_at      TEXT
);
CREATE INDEX IF NOT EXISTS ai_tokens_signup_idx ON ai_tokens(signup_id);
CREATE INDEX IF NOT EXISTS ai_tokens_sha_idx    ON ai_tokens(sha256);
"""


def init_schema(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    init_schema(db)
    try:
        yield db
        db.commit()
    finally:
        db.close()


# ─── Token management ────────────────────────────────────────────────────
@dataclass
class TokenInfo:
    signup_id: int
    label: str
    cap_pence: int | None


def _gen_token() -> str:
    alphabet = string.ascii_letters + string.digits
    return "hk_ai_" + "".join(secrets.choice(alphabet) for _ in range(40))


def _sha(raw: str) -> str:
    import hashlib
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(signup_id: int, label: str,
                cap_pence: int | None = None) -> tuple[str, dict[str, Any]]:
    """Mint a fresh hk_ai_* token. Returns (raw_token, row_metadata)."""
    raw = _gen_token()
    digest = _sha(raw)
    prefix = raw[:12]
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        cur = db.execute(
            """INSERT INTO ai_tokens
                 (signup_id, label, sha256, prefix, cap_pence, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (signup_id, label[:80], digest, prefix, cap_pence, now),
        )
    return raw, {
        "id": cur.lastrowid, "signup_id": signup_id, "label": label[:80],
        "prefix": prefix, "cap_pence": cap_pence, "created_at": now,
    }


def resolve_token(raw_token: str) -> TokenInfo | None:
    if not raw_token or not raw_token.startswith("hk_ai_"):
        return None
    digest = _sha(raw_token)
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        row = db.execute(
            """SELECT signup_id, label, cap_pence, revoked_at
                 FROM ai_tokens WHERE sha256 = ?""",
            (digest,),
        ).fetchone()
        if not row or row["revoked_at"]:
            return None
        # last_used_at is best-effort; non-critical for correctness.
        db.execute(
            "UPDATE ai_tokens SET last_used_at = ? WHERE sha256 = ?",
            (now, digest),
        )
    return TokenInfo(
        signup_id=row["signup_id"], label=row["label"],
        cap_pence=row["cap_pence"],
    )


def list_for_signup(signup_id: int) -> list[dict[str, Any]]:
    with _conn() as db:
        rows = db.execute(
            """SELECT id, label, prefix, cap_pence, created_at,
                      last_used_at, revoked_at
                 FROM ai_tokens
                WHERE signup_id = ?
                ORDER BY created_at DESC""",
            (signup_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_token(signup_id: int, token_id: int) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        cur = db.execute(
            """UPDATE ai_tokens SET revoked_at = ?
                WHERE id = ? AND signup_id = ? AND revoked_at IS NULL""",
            (now, token_id, signup_id),
        )
    return cur.rowcount > 0


# ─── Tier lookup (for cap check) ─────────────────────────────────────────
def _tier_of(signup_id: int) -> str:
    with _conn() as db:
        row = db.execute(
            "SELECT tier FROM signups WHERE id = ?", (signup_id,),
        ).fetchone()
    return (row[0] if row else "sandbox") or "sandbox"


def _is_within_cap(signup_id: int, tier: str,
                   token_cap_pence: int | None) -> tuple[bool, str]:
    """Returns (allowed, reason). The reason is "" when allowed."""
    import ai_credit  # noqa: PLC0415 — same dir
    balance = ai_credit.get_balance(signup_id, tier)
    spent = balance.spent_pence
    allowance = balance.allowance_pence
    # Soft cap: allowance × OVERAGE_MULTIPLIER_CEILING.
    soft_cap = int(allowance * OVERAGE_MULTIPLIER_CEILING)
    if token_cap_pence is not None:
        soft_cap = min(soft_cap, token_cap_pence)

    if STRICT_MODE and spent >= allowance:
        return False, (
            f"AI credit allowance of £{allowance/100:.2f} for the {tier} tier "
            f"has been used this month. Top up at /account or wait until "
            f"month rollover."
        )
    if spent >= soft_cap:
        return False, (
            f"AI passthrough cap reached for this token "
            f"(£{soft_cap/100:.2f}). Raise the cap at /account or use a "
            f"BYO key for this call."
        )
    return True, ""


# ─── Forward helpers ─────────────────────────────────────────────────────
def _extract_anthropic_usage(body: dict[str, Any]) -> tuple[int, int]:
    u = body.get("usage") or {}
    return int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))


def _extract_openai_usage(body: dict[str, Any]) -> tuple[int, int]:
    u = body.get("usage") or {}
    return int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))


async def proxy_anthropic_messages(
    raw_token: str, request_body: bytes, headers: dict[str, str],
) -> tuple[int, dict[str, str], bytes]:
    """Forward POST /v1/messages. Returns (status, headers, body) verbatim."""
    info = resolve_token(raw_token)
    if not info:
        return _err(401, "Invalid hk_ai_* token.")
    tier = _tier_of(info.signup_id)
    ok, reason = _is_within_cap(info.signup_id, tier, info.cap_pence)
    if not ok:
        return _err(402, reason)

    if not ANTHROPIC_MASTER_KEY:
        return _err(503, "Anthropic master key not configured on this host.")

    fwd_headers = {k: v for k, v in headers.items()
                   if k.lower() not in ("authorization", "x-api-key",
                                        "host", "content-length")}
    fwd_headers["x-api-key"] = ANTHROPIC_MASTER_KEY
    fwd_headers.setdefault("anthropic-version", "2023-06-01")
    fwd_headers["content-type"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{ANTHROPIC_BASE}/v1/messages",
                content=request_body, headers=fwd_headers,
            )
    except httpx.HTTPError as e:
        log.error("anthropic forward failed: %s", e)
        return _err(502, f"Upstream Anthropic error: {e}")

    body = resp.content
    if resp.headers.get("content-type", "").startswith("application/json"):
        try:
            parsed = json.loads(body)
            t_in, t_out = _extract_anthropic_usage(parsed)
            _record_usage(info.signup_id, info.label, "anthropic",
                          parsed.get("model", ""),
                          t_in, t_out, parsed.get("id"))
        except (ValueError, KeyError) as e:
            log.warning("could not parse anthropic response for metering: %s", e)
    out_headers = {k: v for k, v in resp.headers.items()
                   if k.lower() not in ("content-encoding", "transfer-encoding",
                                        "connection")}
    return resp.status_code, out_headers, body


async def proxy_openai_chat_completions(
    raw_token: str, request_body: bytes, headers: dict[str, str],
) -> tuple[int, dict[str, str], bytes]:
    """Forward POST /v1/chat/completions."""
    info = resolve_token(raw_token)
    if not info:
        return _err(401, "Invalid hk_ai_* token.")
    tier = _tier_of(info.signup_id)
    ok, reason = _is_within_cap(info.signup_id, tier, info.cap_pence)
    if not ok:
        return _err(402, reason)

    if not OPENAI_MASTER_KEY:
        return _err(503, "OpenAI master key not configured on this host.")

    fwd_headers = {k: v for k, v in headers.items()
                   if k.lower() not in ("authorization", "x-api-key",
                                        "host", "content-length")}
    fwd_headers["authorization"] = f"Bearer {OPENAI_MASTER_KEY}"
    fwd_headers["content-type"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{OPENAI_BASE}/v1/chat/completions",
                content=request_body, headers=fwd_headers,
            )
    except httpx.HTTPError as e:
        log.error("openai forward failed: %s", e)
        return _err(502, f"Upstream OpenAI error: {e}")

    body = resp.content
    if resp.headers.get("content-type", "").startswith("application/json"):
        try:
            parsed = json.loads(body)
            t_in, t_out = _extract_openai_usage(parsed)
            _record_usage(info.signup_id, info.label, "openai",
                          parsed.get("model", ""),
                          t_in, t_out, parsed.get("id"))
        except (ValueError, KeyError) as e:
            log.warning("could not parse openai response for metering: %s", e)
    out_headers = {k: v for k, v in resp.headers.items()
                   if k.lower() not in ("content-encoding", "transfer-encoding",
                                        "connection")}
    return resp.status_code, out_headers, body


def _record_usage(
    signup_id: int, token_label: str, provider: str, model: str,
    tokens_in: int, tokens_out: int, request_id: str | None,
) -> None:
    try:
        import ai_pricing  # noqa: PLC0415
        import ai_credit  # noqa: PLC0415
        cost = ai_pricing.cost_pence(
            provider, model, tokens_in, tokens_out  # type: ignore[arg-type]
        )
        ai_credit.record_event(
            signup_id=signup_id, slug=token_label[:60],
            provider=provider, model=model,
            tokens_in=tokens_in, tokens_out=tokens_out,
            cost_pence=cost, request_id=request_id,
        )
    except Exception as e:  # noqa: BLE001
        # Metering failure must not break the user-visible call.
        log.warning("ai_credit.record_event failed: %s", e)


def _err(status: int, message: str) -> tuple[int, dict[str, str], bytes]:
    payload = {"error": {"type": "hatchik_proxy", "message": message}}
    return status, {"content-type": "application/json"}, json.dumps(payload).encode()
