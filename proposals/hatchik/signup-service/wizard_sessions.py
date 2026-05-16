"""
wizard_sessions.py — server-side state for the conversational MCP signup.

The MCP signup flow (proposals/hatchik/mcp-signup-flow.md) is a series of
turns: the customer's AI calls start_signup → suggest_domains →
set_choices → quote → checkout → status → complete. Each turn carries
the same session_id; the backend keeps the cumulative state here.

Both the MCP path AND any future "save-and-resume" web wizard can sit on
top of this. The single-shot /api/signup endpoint stays as the synchronous
path for customers who don't want a session — these wizard sessions
eventually fall into the same create_signup() pipeline once 'complete' is
called, so the provisioning logic is shared.

Schema lives in signups.db (already mounted on the orchestrator host).

States:
    new           — created, no choices yet
    in_progress   — at least one set_choices call
    awaiting_pay  — checkout link issued, waiting for Paddle webhook
    provisioning  — payment received, signup row created, infra spinning up
    ready         — signup live, api_key available via complete()
    completed     — api_key handed back to MCP; session retired
    expired       — TTL elapsed without payment
    cancelled     — customer abandoned

Sessions expire after WIZARD_SESSION_TTL_HOURS hours (default 24) for
abandoned sessions and 90 days for completed ones (audit trail).
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import string
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
SESSION_TTL = timedelta(hours=int(os.environ.get("HATCHIK_WIZARD_TTL_HOURS", "24")))

# Pricing constants — single source of truth, matches PRODUCT_OFFERING + index.html
LAUNCH_SETUP_PENCE = 8900            # £89 once
LAUNCH_MONTHLY_ANNUAL_PENCE = 1400   # £14/mo on annual prepay (£168/yr)
LAUNCH_MONTHLY_ROLLING_PENCE = 1700  # £17/mo rolling
GROWTH_MONTHLY_PENCE = 3900          # £39/mo


SessionStatus = Literal[
    "new", "in_progress", "awaiting_pay", "provisioning",
    "ready", "completed", "expired", "cancelled",
]


# ─── Schema ───────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS wizard_sessions (
    id              TEXT PRIMARY KEY,           -- ws_<24 url-safe chars>
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    status          TEXT NOT NULL,
    choices_json    TEXT NOT NULL DEFAULT '{}',
    signup_id       INTEGER,                    -- set when status >= provisioning
    paddle_txn_id   TEXT,                       -- set when checkout completes
    install_token   TEXT UNIQUE,                -- one-time for complete()
    install_token_used_at TEXT
);
CREATE INDEX IF NOT EXISTS wizard_sessions_status_idx ON wizard_sessions(status, updated_at);
CREATE INDEX IF NOT EXISTS wizard_sessions_signup_idx ON wizard_sessions(signup_id);
CREATE INDEX IF NOT EXISTS wizard_sessions_paddle_idx ON wizard_sessions(paddle_txn_id);
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


# ─── Data shape ───────────────────────────────────────────────────────────
@dataclass
class WizardSession:
    id: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    status: SessionStatus
    choices: dict[str, Any] = field(default_factory=dict)
    signup_id: int | None = None
    paddle_txn_id: str | None = None
    install_token: str | None = None
    install_token_used_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Render datetimes as ISO strings for JSON consumers.
        for k in ("created_at", "updated_at", "expires_at", "install_token_used_at"):
            if d[k] is not None:
                d[k] = d[k].isoformat() if isinstance(d[k], datetime) else d[k]
        return d


def _gen_id() -> str:
    """ws_<24-char> — short enough to paste, opaque enough to be unguessable."""
    alphabet = string.ascii_letters + string.digits
    return "ws_" + "".join(secrets.choice(alphabet) for _ in range(24))


def _gen_install_token() -> str:
    """it_<32-char> — high entropy one-time token."""
    alphabet = string.ascii_letters + string.digits
    return "it_" + "".join(secrets.choice(alphabet) for _ in range(32))


def _parse(row: sqlite3.Row) -> WizardSession:
    return WizardSession(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
        status=row["status"],
        choices=json.loads(row["choices_json"] or "{}"),
        signup_id=row["signup_id"],
        paddle_txn_id=row["paddle_txn_id"],
        install_token=row["install_token"],
        install_token_used_at=(
            datetime.fromisoformat(row["install_token_used_at"])
            if row["install_token_used_at"] else None
        ),
    )


# ─── Public API ───────────────────────────────────────────────────────────
def create(initial_choices: dict[str, Any] | None = None) -> WizardSession:
    now = datetime.now(timezone.utc)
    session = WizardSession(
        id=_gen_id(),
        created_at=now,
        updated_at=now,
        expires_at=now + SESSION_TTL,
        status="new",
        choices=initial_choices or {},
    )
    with _conn() as db:
        db.execute(
            """INSERT INTO wizard_sessions
                 (id, created_at, updated_at, expires_at, status, choices_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session.id, session.created_at.isoformat(),
             session.updated_at.isoformat(), session.expires_at.isoformat(),
             session.status, json.dumps(session.choices)),
        )
    return session


def get(session_id: str) -> WizardSession | None:
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM wizard_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    if not row:
        return None
    s = _parse(row)
    # Lazy expiry: if past expires_at and still abandoned, flip to 'expired'.
    if (
        s.status in ("new", "in_progress", "awaiting_pay")
        and datetime.now(timezone.utc) > s.expires_at
    ):
        _set_status(s.id, "expired")
        s.status = "expired"
    return s


def update_choices(session_id: str, patch: dict[str, Any]) -> WizardSession | None:
    s = get(session_id)
    if not s:
        return None
    if s.status not in ("new", "in_progress"):
        return s  # locked once payment in flight
    merged = {**s.choices, **patch}
    now = datetime.now(timezone.utc)
    with _conn() as db:
        db.execute(
            """UPDATE wizard_sessions
                  SET choices_json = ?, updated_at = ?, status = ?
                WHERE id = ?""",
            (json.dumps(merged), now.isoformat(), "in_progress", session_id),
        )
    s.choices = merged
    s.updated_at = now
    s.status = "in_progress"
    return s


def _set_status(session_id: str, status: SessionStatus,
                **extra: Any) -> None:
    now = datetime.now(timezone.utc).isoformat()
    fields = ["status = ?", "updated_at = ?"]
    values: list[Any] = [status, now]
    for k, v in extra.items():
        fields.append(f"{k} = ?")
        values.append(v)
    values.append(session_id)
    with _conn() as db:
        db.execute(
            f"UPDATE wizard_sessions SET {', '.join(fields)} WHERE id = ?",
            values,
        )


def mark_awaiting_pay(session_id: str, paddle_txn_id: str | None = None) -> None:
    _set_status(session_id, "awaiting_pay", paddle_txn_id=paddle_txn_id)


def mark_provisioning(session_id: str, signup_id: int,
                      paddle_txn_id: str | None = None) -> str:
    """Promote a paid session to provisioning. Returns the install_token."""
    install_token = _gen_install_token()
    extras: dict[str, Any] = {
        "signup_id": signup_id,
        "install_token": install_token,
    }
    if paddle_txn_id:
        extras["paddle_txn_id"] = paddle_txn_id
    _set_status(session_id, "provisioning", **extras)
    return install_token


def mark_ready(session_id: str) -> None:
    _set_status(session_id, "ready")


def mark_completed(session_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    _set_status(session_id, "completed", install_token_used_at=now)


def by_paddle_txn(paddle_txn_id: str) -> WizardSession | None:
    """Resolve a session by the Paddle transaction id (used in webhook)."""
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM wizard_sessions WHERE paddle_txn_id = ?",
            (paddle_txn_id,),
        ).fetchone()
    return _parse(row) if row else None


def by_signup(signup_id: int) -> WizardSession | None:
    """Resolve the wizard session that produced this signup, if any."""
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM wizard_sessions WHERE signup_id = ? ORDER BY id DESC LIMIT 1",
            (signup_id,),
        ).fetchone()
    return _parse(row) if row else None


def cancel(session_id: str) -> WizardSession | None:
    s = get(session_id)
    if not s:
        return None
    if s.status in ("ready", "completed", "expired", "cancelled"):
        return s
    _set_status(session_id, "cancelled")
    s.status = "cancelled"
    return s


# ─── Quote ────────────────────────────────────────────────────────────────
@dataclass
class Quote:
    tier: Literal["sandbox", "launch", "growth"]
    setup_pence: int
    monthly_pence: int
    monthly_billing_cycle: Literal["annual", "rolling"]
    domain_passthrough_pence: int
    domain_passthrough_label: str
    breakdown: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        def gbp(p: int) -> str:
            return f"£{p/100:.2f}"
        return {
            **asdict(self),
            "setup_display": gbp(self.setup_pence),
            "monthly_display": gbp(self.monthly_pence),
            "domain_passthrough_display": gbp(self.domain_passthrough_pence),
            "year_one_display": gbp(self.setup_pence + self.monthly_pence * 12 + self.domain_passthrough_pence),
        }


def compute_quote(choices: dict[str, Any]) -> Quote:
    """Compute pricing given a session's current choices."""
    tier = (choices.get("tier") or "sandbox").lower()
    cycle = (choices.get("billing_cycle") or "annual").lower()

    setup = 0
    monthly = 0
    breakdown: list[dict[str, Any]] = []

    if tier == "launch":
        setup = LAUNCH_SETUP_PENCE
        monthly = LAUNCH_MONTHLY_ANNUAL_PENCE if cycle == "annual" else LAUNCH_MONTHLY_ROLLING_PENCE
        breakdown.append({"label": "Launch setup", "pence": setup, "kind": "one-off"})
        breakdown.append({
            "label": f"Launch monthly ({cycle} billing)",
            "pence": monthly, "kind": "recurring-monthly",
        })
    elif tier == "growth":
        # Direct-to-Growth is unusual but possible.
        monthly = GROWTH_MONTHLY_PENCE
        breakdown.append({
            "label": "Growth monthly",
            "pence": monthly, "kind": "recurring-monthly",
        })
    else:
        # Sandbox tier = free, no quote needed.
        breakdown.append({"label": "Sandbox tier", "pence": 0, "kind": "free"})

    # Domain passthrough — premium TLDs cost extra beyond the £14 ceiling.
    passthrough_pence = 0
    passthrough_label = ""
    domain = (choices.get("domain") or "").lower()
    if domain and tier in ("launch", "growth"):
        try:
            from domains import passthrough_info  # noqa: PLC0415
            info = passthrough_info(domain)
            if info:
                _tld, extra_gbp, label = info
                passthrough_pence = extra_gbp * 100
                passthrough_label = label
                if passthrough_pence > 0:
                    breakdown.append({
                        "label": f"Domain passthrough ({domain})",
                        "pence": passthrough_pence,
                        "kind": "one-off",
                    })
        except Exception:
            # Domain pricing helper isn't critical for the quote; if it
            # errors we just show zero passthrough.
            pass

    return Quote(
        tier=tier,  # type: ignore[arg-type]
        setup_pence=setup,
        monthly_pence=monthly,
        monthly_billing_cycle=cycle,  # type: ignore[arg-type]
        domain_passthrough_pence=passthrough_pence,
        domain_passthrough_label=passthrough_label,
        breakdown=breakdown,
    )


# ─── Validation ───────────────────────────────────────────────────────────
def is_ready_for_checkout(choices: dict[str, Any]) -> tuple[bool, str]:
    """Are the choices complete enough to issue a checkout link?"""
    if not choices.get("email"):
        return False, "Need the customer's email — call set_choices({\"email\": …}) first."
    if not choices.get("product_name"):
        return False, "Need a product name — call set_choices({\"product_name\": …})."
    tier = (choices.get("tier") or "sandbox").lower()
    if tier not in ("sandbox", "launch", "growth"):
        return False, f"Unknown tier {tier!r}."
    if tier in ("launch", "growth"):
        if not choices.get("domain"):
            return False, "Launch/Growth tier needs a domain — call suggest_domains and then set_choices({\"domain\": …})."
        if not choices.get("region"):
            return False, "Launch/Growth tier needs a region — call set_choices({\"region\": …})."
    return True, ""
