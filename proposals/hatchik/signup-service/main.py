"""
Hatchik signup endpoint — concierge-MVP backend.

Accepts a signup from hatchik.com's signup form, logs it to SQLite,
emails the founder (hello@hatchik.com) with the signup details, and
responds to the customer's browser.

Deploy on the same VPS as the marketing site (Infomaniak shared
host). systemd manages the process; Caddy reverse-proxies
/api/signup to localhost:8090.

This is a stopgap. When the wizard ships, the provisioning worker
takes over and this service is retired.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator

# ─── Config ──────────────────────────────────────────────────────────────
DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FOUNDER_EMAIL = os.environ.get("HATCHIK_FOUNDER_EMAIL", "hello@hatchik.com")
FROM_EMAIL = os.environ.get("HATCHIK_FROM_EMAIL", "onboarding@resend.dev")
RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 10.0
ALLOWED_ORIGINS = os.environ.get(
    "HATCHIK_ALLOWED_ORIGINS", "https://hatchik.com,https://www.hatchik.com"
).split(",")
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 5

# Admin endpoints (POST /api/admin/* and DELETE /api/admin/*) require this
# shared secret in the X-Admin-Token header. Empty value disables admin API.
ADMIN_TOKEN = os.environ.get("HATCHIK_ADMIN_TOKEN", "")

# Self-serve deletion: customers receive an emailed link with a token,
# valid for this many hours.
DELETION_TOKEN_TTL_HOURS = 24

# Magic-link login tokens are single-use and short-lived.
LOGIN_TOKEN_TTL_MINUTES = 30
# Session cookies last this long after last activity.
SESSION_TTL_DAYS = 30
SESSION_COOKIE_NAME = "hatchik_session"

# Paddle config (Launch tier upgrade flow). When PADDLE_LAUNCH_PRICE_ID is
# set, /api/account/upgrade returns a hosted-checkout URL; otherwise the
# UI shows a "coming soon, Paddle approval pending" placeholder.
PADDLE_VENDOR = os.environ.get("PADDLE_VENDOR", "")
PADDLE_LAUNCH_PRICE_ID = os.environ.get("PADDLE_LAUNCH_PRICE_ID", "")
PADDLE_CHECKOUT_BASE = os.environ.get(
    "PADDLE_CHECKOUT_BASE", "https://buy.paddle.com/checkout"
)
PADDLE_BILLING_PORTAL_BASE = os.environ.get(
    "PADDLE_BILLING_PORTAL_BASE", "https://buyer.paddle.com"
)

# Path to decommission.py — subprocess'd for tear-down.
DECOMMISSION_SCRIPT = os.environ.get(
    "HATCHIK_DECOMMISSION_SCRIPT", "/opt/hatchik-orchestrator/decommission.py"
)

# Paddle Billing webhook config. Hatchik's selling entity is Omani, so we
# use Paddle as Merchant of Record (see PRODUCT_OFFERING.md §8.1) — Stripe
# does not support Omani entities.
PADDLE_WEBHOOK_SECRET = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
# Paddle signs payloads with a timestamp; reject events older than this to
# block replay attacks. Tolerance matches Stripe's default of 5 minutes.
PADDLE_SIGNATURE_TOLERANCE_SECONDS = 300

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("loftik-signup")

# ─── DB ──────────────────────────────────────────────────────────────────
def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signups (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT NOT NULL,
                email           TEXT NOT NULL,
                first_name      TEXT,
                product_name    TEXT,
                description     TEXT,
                tier            TEXT NOT NULL CHECK(tier IN ('sandbox', 'launch')),
                region          TEXT,
                domain_choice   TEXT,
                ip_address      TEXT,
                user_agent      TEXT,
                status          TEXT NOT NULL DEFAULT 'new'
            )
            """
        )
        # Additive migration for live DBs that pre-date first_name.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signups)").fetchall()}
        if "first_name" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN first_name TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limit (
                ip          TEXT NOT NULL,
                timestamp   REAL NOT NULL
            )
            """
        )
        # One-time migration: the never-deployed Stripe schema used
        # checkout_session_id / stripe_customer_id columns. Hatchik switched
        # to Paddle (MoR; see PRODUCT_OFFERING.md §8.1) before any payment
        # row was written, so dropping is safe — no data loss possible on a
        # fresh install, and the only deploy in flight is this rename.
        conn.execute("DROP TABLE IF EXISTS payments")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at               TEXT NOT NULL,
                paddle_transaction_id    TEXT UNIQUE,
                paddle_customer_id       TEXT,
                paddle_subscription_id   TEXT,
                customer_email           TEXT,
                currency                 TEXT,
                amount                   TEXT,
                status                   TEXT,
                raw_payload              TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id    TEXT PRIMARY KEY,
                event_type  TEXT NOT NULL,
                processed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deletion_tokens (
                token       TEXT PRIMARY KEY,
                email       TEXT NOT NULL,
                slug        TEXT NOT NULL,
                signup_id   INTEGER NOT NULL,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                consumed_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deletion_tokens_email ON deletion_tokens(email)")
        # Account-management auth: magic-link login + session cookies.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_tokens (
                token       TEXT PRIMARY KEY,
                email       TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                consumed_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id   TEXT PRIMARY KEY,
                email        TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                last_seen_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_login_tokens_email ON login_tokens(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signups_email ON signups(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signups_created ON signups(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rate_limit_ip ON rate_limit(ip)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_customer ON payments(paddle_customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_email ON payments(customer_email)")
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    init_db()
    log.info("Hatchik signup service started — DB at %s", DB_PATH)
    yield


# ─── App ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Hatchik Signup Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


# ─── Models ──────────────────────────────────────────────────────────────
class SignupRequest(BaseModel):
    model_config = {"populate_by_name": True}

    email: EmailStr
    first_name: str = Field(
        "",
        max_length=80,
        validation_alias=AliasChoices("first_name", "firstName", "name"),
    )
    product_name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(
        "",
        max_length=2000,
        validation_alias=AliasChoices("description", "idea", "product_idea"),
    )
    tier: Literal["sandbox", "launch"] = "sandbox"
    region: str | None = Field(None, max_length=40)
    domain_choice: str | None = Field(None, max_length=255)

    @field_validator("product_name", "description")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("first_name")
    @classmethod
    def clean_first_name(cls, v: str) -> str:
        # Take the first whitespace-separated token, capitalize it.
        # "  alice ross " → "Alice", "Mr. Bean" → "Mr." → "Mr." (still
        # better than firing the whole string into a greeting).
        token = v.strip().split()[0] if v.strip() else ""
        return token[:1].upper() + token[1:] if token else ""


class SignupResponse(BaseModel):
    ok: bool
    message: str


# ─── Rate limit ──────────────────────────────────────────────────────────
def check_rate_limit(ip: str) -> bool:
    """True if the request is allowed; False if rate-limited."""
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM rate_limit WHERE timestamp < ?", (cutoff,))
        cur = conn.execute(
            "SELECT COUNT(*) FROM rate_limit WHERE ip = ? AND timestamp >= ?",
            (ip, cutoff),
        )
        count = cur.fetchone()[0]
        if count >= RATE_LIMIT_MAX_REQUESTS:
            return False
        conn.execute("INSERT INTO rate_limit (ip, timestamp) VALUES (?, ?)", (ip, now))
        conn.commit()
    return True


# ─── Email ───────────────────────────────────────────────────────────────
def _html_escape(s: str) -> str:
    """Minimal HTML escape — keeps deps to stdlib + httpx."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


async def _resend_send(payload: dict[str, Any]) -> None:
    """POST to Resend; raise on HTTP error so the caller can log+swallow."""
    async with httpx.AsyncClient(timeout=RESEND_TIMEOUT_SECONDS) as client:
        r = await client.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()


async def send_founder_notification(
    req: SignupRequest, signup_id: int, ip: str = "unknown"
) -> None:
    """Email the founder with signup details (provisioning runs automatically for Sandbox)."""
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping founder notification")
        return

    subject = f"[Hatchik signup #{signup_id}] {req.tier.title()}: {req.product_name}"
    body = f"""\
New Hatchik signup #{signup_id}

  Email:       {req.email}
  First name:  {req.first_name or '(not provided)'}
  Tier:        {req.tier}
  Product:     {req.product_name}
  Region:      {req.region or 'not specified'}
  Domain:      {req.domain_choice or 'will be discussed'}
  IP:          {ip}

  Description:
  {req.description or '(none)'}

Sandbox tier: provisioning runs automatically — watch /var/log/hatchik/provision-{signup_id}.log
Launch tier: see FIRST_CUSTOMER_RUNBOOK.md for the manual flow.
"""

    try:
        await _resend_send({
            "from": FROM_EMAIL,
            "to": [FOUNDER_EMAIL],
            "reply_to": str(req.email),
            "subject": subject,
            "text": body,
        })
        log.info("Founder notification sent for signup #%s", signup_id)
    except Exception as e:  # noqa: BLE001 — never break signup on email failure
        log.error("Failed to send founder notification for #%s: %s", signup_id, e)


def _customer_email_bodies(req: SignupRequest) -> tuple[str, str]:
    """Render plaintext + HTML versions of the customer acknowledgement."""
    if req.tier == "launch":
        intro = f"Thanks for signing up for {req.product_name} on the Launch tier."
        next_step = (
            "Your Hatchik is being provisioned now. You'll get another email "
            "shortly with the link to log in and start building — usually "
            "within a few hours, sometimes faster if nothing needs your input."
        )
    else:
        intro = f"Thanks for signing up — and welcome to {req.product_name}."
        next_step = (
            "Your Hatchik sandbox is being provisioned now. You'll get "
            "another email in a few minutes with the link to log in and "
            "start building."
        )

    greeting = f"Hi {req.first_name}," if req.first_name else "Hi,"
    greeting_html = _html_escape(greeting)

    text = f"""\
{greeting}

{intro}

{next_step}

Further information can be found at https://hatchik.com/#faq if you need it.

— Hatchik

(This is an automated message — please don't reply.)
"""

    intro_html = _html_escape(intro)
    next_html = _html_escape(next_step)

    html = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Welcome to Hatchik</title>
</head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:8px;padding:32px;">
          <tr>
            <td style="font-size:16px;line-height:1.6;color:#1a1a1a;">
              <p style="margin:0 0 16px 0;">{greeting_html}</p>
              <p style="margin:0 0 16px 0;">{intro_html}</p>
              <p style="margin:0 0 16px 0;">{next_html}</p>
              <p style="margin:0 0 16px 0;">Further information can be found <a href="https://hatchik.com/#faq" style="color:#4f46e5;text-decoration:underline;">here</a> if you need it.</p>
              <p style="margin:24px 0 0 0;">&mdash; Hatchik</p>
              <p style="margin:24px 0 0 0;color:#888;font-size:12px;">This is an automated message &mdash; please don&rsquo;t reply.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    return text, html


async def send_customer_acknowledgement(req: SignupRequest) -> None:
    """Confirm-receipt email to the customer. Founder follows up personally."""
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping customer acknowledgement")
        return

    text_body, html_body = _customer_email_bodies(req)
    if req.tier == "launch":
        subject = f"Thanks for signing up — getting {req.product_name} built now"
    else:
        subject = f"Welcome to Hatchik — your sandbox for {req.product_name} is being set up"

    try:
        await _resend_send({
            "from": FROM_EMAIL,
            "to": [str(req.email)],
            "subject": subject,
            "text": text_body,
            "html": html_body,
        })
        log.info("Customer acknowledgement sent to %s", req.email)
    except Exception as e:  # noqa: BLE001 — never break signup on email failure
        log.error("Failed to send customer acknowledgement to %s: %s", req.email, e)


# ─── Paddle webhook ──────────────────────────────────────────────────────
def verify_paddle_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Verify a Paddle Billing webhook signature header per
    https://developer.paddle.com/webhooks/signature-verification.

    Header format: ``ts=<unix_ts>;h1=<sig>``. We compute HMAC-SHA256 over
    ``<ts>:<payload>`` with the notification secret key and compare against
    the ``h1`` signature in constant time. Replay tolerance enforced by
    ``PADDLE_SIGNATURE_TOLERANCE_SECONDS``.
    """
    if not sig_header or not secret:
        return False

    timestamp: str | None = None
    signatures: list[str] = []
    for part in sig_header.split(";"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "ts":
            timestamp = value
        elif key == "h1":
            signatures.append(value)

    if timestamp is None or not signatures:
        return False

    try:
        ts_int = int(timestamp)
    except ValueError:
        return False

    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - ts_int) > PADDLE_SIGNATURE_TOLERANCE_SECONDS:
        log.warning("Paddle webhook timestamp outside tolerance (drift=%ss)", now - ts_int)
        return False

    # Paddle signs `<ts>:<raw_body>` — note the colon separator (Stripe uses
    # a dot). The raw bytes must be the exact body received; any
    # re-serialization breaks the signature.
    signed_payload = f"{timestamp}:".encode() + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


def _event_already_processed(event_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)
        ).fetchone()
    return row is not None


def _mark_event_processed(event_id: str, event_type: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_events (event_id, event_type, processed_at) "
            "VALUES (?, ?, ?)",
            (event_id, event_type, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def _persist_paddle_transaction(transaction: dict[str, Any], raw_payload: bytes) -> None:
    """Record a completed Paddle transaction in the payments table.

    Paddle's ``transaction.completed`` event ``data`` object exposes
    ``id`` (transaction id), ``customer_id``, ``subscription_id``,
    ``status``, ``currency_code``, ``details.totals.grand_total``, and the
    payer's email under ``customer.email`` or ``billing_details.email``.
    Field names follow the Paddle Billing (v1) shape, not Paddle Classic.
    """
    details = transaction.get("details") or {}
    totals = details.get("totals") or {}
    customer = transaction.get("customer") or {}
    billing_details = transaction.get("billing_details") or {}
    email = (
        customer.get("email")
        or billing_details.get("email")
        or transaction.get("customer_email")
    )
    amount = (
        totals.get("grand_total")
        or totals.get("total")
        or transaction.get("amount")
    )
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO payments (
                created_at, paddle_transaction_id, paddle_customer_id,
                paddle_subscription_id, customer_email, currency, amount,
                status, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                transaction.get("id"),
                transaction.get("customer_id"),
                transaction.get("subscription_id"),
                email,
                transaction.get("currency_code") or transaction.get("currency"),
                str(amount) if amount is not None else None,
                transaction.get("status"),
                raw_payload.decode("utf-8", errors="replace"),
            ),
        )
        conn.commit()


async def notify_founder_payment_failure(transaction: dict[str, Any]) -> None:
    """Email the founder when a Paddle transaction payment fails — needs fast attention.

    Paddle's ``transaction.payment_failed`` event carries the transaction
    object; ``payments`` is a list of attempts, each with an
    ``error_code``. Customer email is in ``customer.email`` (or
    ``billing_details.email`` for guest checkout).
    """
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping payment-failure notification")
        return

    customer = transaction.get("customer") or {}
    billing_details = transaction.get("billing_details") or {}
    customer_email = (
        customer.get("email")
        or billing_details.get("email")
        or "(unknown)"
    )
    customer_id = transaction.get("customer_id") or "(unknown)"
    subscription_id = transaction.get("subscription_id") or "(none)"

    details = transaction.get("details") or {}
    totals = details.get("totals") or {}
    amount = totals.get("grand_total") or totals.get("total")
    currency = (transaction.get("currency_code") or transaction.get("currency") or "").upper()
    # Paddle returns amounts as minor-unit strings (e.g. "7900" = 79.00 GBP).
    try:
        amount_str = f"{int(amount) / 100:.2f} {currency}" if amount else "(unknown amount)"
    except (TypeError, ValueError):
        amount_str = f"{amount} {currency}".strip()

    payments_list = transaction.get("payments") or []
    last_error = "(no error code)"
    if payments_list:
        last = payments_list[-1] if isinstance(payments_list, list) else {}
        last_error = (
            last.get("error_code")
            or last.get("status")
            or last_error
        )

    body = f"""\
Paddle transaction payment FAILED.

  Customer email:        {customer_email}
  Paddle customer:       {customer_id}
  Paddle subscription:   {subscription_id}
  Transaction id:        {transaction.get('id') or '(unknown)'}
  Amount:                {amount_str}
  Last attempt error:    {last_error}
  Status:                {transaction.get('status') or '(unknown)'}

Action: check the Paddle dashboard, follow up with the customer if needed.
"""

    try:
        await _resend_send({
            "from": FROM_EMAIL,
            "to": [FOUNDER_EMAIL],
            "subject": f"[Hatchik] Payment failed — {customer_email}",
            "text": body,
        })
        log.info("Payment-failure notification sent for customer %s", customer_id)
    except Exception as e:  # noqa: BLE001 — never crash the webhook on email
        log.error("Failed to send payment-failure notification: %s", e)


# ─── Endpoints ───────────────────────────────────────────────────────────
@app.post("/api/signup", response_model=SignupResponse, status_code=201)
async def create_signup(req: SignupRequest, request: Request) -> SignupResponse:
    ip = (request.headers.get("CF-Connecting-IP")
          or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))

    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    user_agent = request.headers.get("User-Agent", "")
    created_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO signups (
                created_at, email, first_name, product_name, description, tier,
                region, domain_choice, ip_address, user_agent, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
            """,
            (
                created_at, str(req.email), req.first_name, req.product_name,
                req.description, req.tier, req.region, req.domain_choice,
                ip, user_agent,
            ),
        )
        signup_id = cur.lastrowid or 0
        conn.commit()

    log.info("New signup #%s: %s tier=%s", signup_id, req.email, req.tier)

    # Fire both notification emails. Failures are logged inside each helper
    # so they cannot break the signup — the DB insert above is the source of
    # truth and the customer has already received a 201 by the time these run.
    await send_founder_notification(req, signup_id, ip)
    await send_customer_acknowledgement(req)

    # If this is a Sandbox signup, trigger provisioning in the background.
    # We don't await it — provision.py is slow (substrate build + compose up
    # + healthcheck = ~60-90s) and the signup endpoint must return fast.
    # provision.py sends its own "your sandbox is ready" email once live.
    if req.tier == "sandbox":
        trigger_sandbox_provision(signup_id)

    return SignupResponse(
        ok=True,
        message="Thanks. We're setting your Hatchik up — check your email in a few minutes.",
    )


def trigger_sandbox_provision(signup_id: int) -> None:
    """Fire-and-forget provisioning. Logs errors but never raises."""
    import subprocess
    script = os.environ.get("HATCHIK_PROVISION_SCRIPT", "/opt/hatchik-orchestrator/provision.py")
    if not Path(script).exists():
        log.warning("provision script not found at %s — skipping (concierge MVP)", script)
        return
    try:
        # Detach completely from this process — provision.py runs to completion
        # even if uvicorn restarts. stdout/stderr go to a log file per signup.
        log_dir = Path("/var/log/hatchik")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"provision-{signup_id}.log"
        subprocess.Popen(
            [script, str(signup_id)],
            stdout=log_file.open("ab"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log.info("Provisioning kicked off for signup #%s → %s", signup_id, log_file)
    except Exception as e:  # noqa: BLE001
        log.error("Failed to kick off provisioning for #%s: %s", signup_id, e)


@app.get("/api/signup/stats")
async def stats() -> dict[str, int]:
    """Public stats — count of signups, no PII."""
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM signups").fetchone()[0]
        new = conn.execute("SELECT COUNT(*) FROM signups WHERE status = 'new'").fetchone()[0]
    return {"total": total, "new": new}


# ─── Account harness: create + delete ────────────────────────────────────
# Admin path: shared-secret header X-Admin-Token = HATCHIK_ADMIN_TOKEN.
# Self-serve path: customer hits POST /api/account/request-deletion with
# their email; service mails a one-time token; customer clicks the link;
# /api/account/confirm-deletion?token=... fires decommission.py.

def _require_admin(token: str | None) -> None:
    if not ADMIN_TOKEN or not token or not hmac.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="admin token required")


def _run_decommission(slug: str, hard: bool) -> dict[str, Any]:
    """Subprocess decommission.py and parse its JSON summary."""
    import subprocess
    if not Path(DECOMMISSION_SCRIPT).exists():
        raise HTTPException(status_code=500, detail="decommission.py not deployed")
    cmd = ["python3", DECOMMISSION_SCRIPT, slug, "--json", "--quiet"]
    if hard:
        cmd.append("--hard")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        log.error("decommission.py failed for %s: %s", slug, r.stderr)
        raise HTTPException(status_code=500, detail=f"decommission failed: {r.stderr.strip()[:200]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"slug": slug, "raw": r.stdout.strip()}


@app.delete("/api/admin/account/{slug}")
async def admin_delete_account(
    slug: str,
    hard: bool = False,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Admin: tear down a sandbox by slug."""
    _require_admin(x_admin_token)
    log.info("admin decommission requested: slug=%s hard=%s", slug, hard)
    return _run_decommission(slug, hard=hard)


@app.get("/api/admin/accounts")
async def admin_list_accounts(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Admin: list all signups + their tenant status from registry."""
    _require_admin(x_admin_token)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, created_at, email, first_name, product_name, tier, status "
            "FROM signups ORDER BY id DESC"
        ).fetchall()
    reg = _load_registry()
    tenants_by_signup: dict[int, dict[str, Any]] = {
        t.get("signup_id"): {"slug": slug, **t}
        for slug, t in reg.get("tenants", {}).items()
        if t.get("signup_id")
    }
    enriched = []
    for r in rows:
        d = dict(r)
        tenant = tenants_by_signup.get(r["id"])
        d["tenant"] = (
            {
                "slug": tenant["slug"],
                "url": tenant.get("url"),
                "status": tenant.get("status"),
                "port": tenant.get("port"),
            }
            if tenant else None
        )
        enriched.append(d)
    return {"signups": enriched}


class DeletionRequest(BaseModel):
    email: EmailStr


@app.post("/api/account/request-deletion", status_code=202)
async def request_deletion(req: DeletionRequest) -> dict[str, Any]:
    """Self-serve: customer asks to delete their sandbox.

    Service looks up signups for that email, generates a one-time token
    per slug, emails the customer a confirmation link. Always returns
    202 — never reveal whether the email matched (anti-enumeration).
    """
    email = str(req.email).lower().strip()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        signups = conn.execute(
            "SELECT id, first_name, product_name FROM signups "
            "WHERE LOWER(email) = ? AND status NOT IN ('deleted', 'cancelled')",
            (email,),
        ).fetchall()
    # Match each signup to its registry slug (if any).
    reg = _load_registry()
    tenants_by_signup = {t.get("signup_id"): (slug, t) for slug, t in reg.get("tenants", {}).items()}
    confirmable: list[tuple[int, str, str]] = []  # (signup_id, slug, product_name)
    for s in signups:
        match = tenants_by_signup.get(s["id"])
        if match is None:
            continue
        slug, tenant = match
        if tenant.get("status") == "decommissioned":
            continue
        confirmable.append((s["id"], slug, s["product_name"] or "Untitled"))

    if not confirmable:
        log.info("deletion request for %s — no live tenants found", email)
        return {"ok": True, "message": "If you have an active Hatchik sandbox, we've sent you a confirmation link."}

    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(hours=DELETION_TOKEN_TTL_HOURS)
    links: list[tuple[str, str]] = []  # (product_name, confirmation_url)
    with sqlite3.connect(DB_PATH) as conn:
        for signup_id, slug, product_name in confirmable:
            token = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO deletion_tokens (token, email, slug, signup_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (token, email, slug, signup_id, created_at.isoformat(), expires_at.isoformat()),
            )
            links.append((product_name, f"https://hatchik.com/api/account/confirm-deletion?token={token}"))
        conn.commit()

    await _send_deletion_confirmation_email(email, links, signups[0]["first_name"] or "")
    log.info("deletion confirmation sent to %s — %d sandbox(es)", email, len(links))
    return {"ok": True, "message": "If you have an active Hatchik sandbox, we've sent you a confirmation link."}


def _load_registry() -> dict[str, Any]:
    path = Path(os.environ.get("HATCHIK_TENANTS_DIR", "/opt/hatchik-tenants")) / "registry.json"
    if not path.exists():
        return {"tenants": {}}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"tenants": {}}


async def _send_deletion_confirmation_email(email: str, links: list[tuple[str, str]], first_name: str) -> None:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping deletion email to %s", email)
        return
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    rows_text = "\n".join(f"  • {name}: {url}" for name, url in links)
    rows_html = "".join(
        f'<li style="margin:0 0 8px 0;"><strong>{_html_escape(name)}</strong> &mdash; '
        f'<a href="{url}" style="color:#4f46e5;text-decoration:underline;">Confirm deletion</a></li>'
        for name, url in links
    )
    text = f"""{greeting}

We received a request to delete your Hatchik sandbox{'es' if len(links) > 1 else ''}.

To confirm, click the link below. Each link is single-use and expires in {DELETION_TOKEN_TTL_HOURS}h.

{rows_text}

If you didn't ask for this, ignore this email and nothing happens.

— Hatchik

(This is an automated message — please don't reply.)
"""
    html = f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Confirm sandbox deletion</title></head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:8px;padding:32px;">
<tr><td style="font-size:16px;line-height:1.6;color:#1a1a1a;">
<p style="margin:0 0 16px 0;">{_html_escape(greeting)}</p>
<p style="margin:0 0 16px 0;">We received a request to delete your Hatchik sandbox{'es' if len(links) > 1 else ''}. Click below to confirm. Each link is single-use and expires in {DELETION_TOKEN_TTL_HOURS}h.</p>
<ul style="margin:0 0 16px 0;padding-left:20px;">{rows_html}</ul>
<p style="margin:0 0 16px 0;color:#555;font-size:14px;">If you didn&rsquo;t ask for this, ignore this email and nothing happens.</p>
<p style="margin:24px 0 0 0;">&mdash; Hatchik</p>
<p style="margin:24px 0 0 0;color:#888;font-size:12px;">This is an automated message &mdash; please don&rsquo;t reply.</p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>
"""
    try:
        await _resend_send({
            "from": FROM_EMAIL,
            "to": [email],
            "subject": "Confirm your Hatchik sandbox deletion",
            "text": text,
            "html": html,
        })
    except Exception as e:  # noqa: BLE001
        log.error("Failed to send deletion confirmation to %s: %s", email, e)


@app.get("/api/account/confirm-deletion", response_class=PlainTextResponse)
async def confirm_deletion(token: str) -> str:
    """Self-serve: customer clicks the emailed link, tenant is torn down."""
    if not token or len(token) < 32:
        raise HTTPException(status_code=400, detail="invalid token")
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT email, slug, signup_id, expires_at, consumed_at "
            "FROM deletion_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="token not found")
        if row["consumed_at"]:
            raise HTTPException(status_code=410, detail="token already used")
        if row["expires_at"] < now:
            raise HTTPException(status_code=410, detail="token expired")
        # Mark consumed BEFORE running decommission so concurrent clicks
        # can't double-fire.
        conn.execute("UPDATE deletion_tokens SET consumed_at = ? WHERE token = ?", (now, token))
        conn.commit()

    log.info("self-serve deletion confirmed: slug=%s email=%s", row["slug"], row["email"])
    summary = _run_decommission(row["slug"], hard=True)
    return (
        f"Your '{row['slug']}' sandbox has been deleted.\n\n"
        f"Status: {summary.get('status', 'done')}\n"
        f"You can sign up again at https://hatchik.com whenever you want."
    )


# ─── Account management: magic-link auth + dashboard endpoints ───────────
# Customer flow: POST /api/account/login {email} → emailed magic link →
# GET /api/account/auth?token=... → session cookie set → redirect to
# /account. Protected endpoints read SESSION_COOKIE_NAME from the request
# cookies and look up the session in SQLite. Sign-out deletes the row.

from fastapi import Cookie, Response
from fastapi.responses import RedirectResponse, JSONResponse


class LoginRequest(BaseModel):
    email: EmailStr


def _resolve_session(session_id: str | None) -> dict[str, Any] | None:
    """Return {email, signup_id, ...} for a valid session cookie, else None."""
    if not session_id:
        return None
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT email, expires_at FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None or row["expires_at"] < now:
            return None
        conn.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        conn.commit()
    return {"email": row["email"]}


@app.post("/api/account/login", status_code=202)
async def request_login_link(req: LoginRequest) -> dict[str, Any]:
    """Self-serve: email a one-time sign-in link.

    Anti-enumeration: returns 202 whether or not the email matches a
    signup row.
    """
    email = str(req.email).lower().strip()
    with sqlite3.connect(DB_PATH) as conn:
        match = conn.execute(
            "SELECT 1 FROM signups WHERE LOWER(email) = ? LIMIT 1", (email,)
        ).fetchone()
    if not match:
        log.info("login link requested for unknown email %s", email)
        return {"ok": True, "message": "If that email matches an active Hatchik account, we've sent a sign-in link."}

    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(minutes=LOGIN_TOKEN_TTL_MINUTES)
    token = secrets.token_urlsafe(32)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO login_tokens (token, email, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, email, created_at.isoformat(), expires_at.isoformat()),
        )
        conn.commit()

    link = f"https://hatchik.com/api/account/auth?token={token}"
    await _send_login_email(email, link)
    log.info("login link emailed to %s", email)
    return {"ok": True, "message": "If that email matches an active Hatchik account, we've sent a sign-in link."}


async def _send_login_email(email: str, link: str) -> None:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping login email to %s", email)
        return
    text = f"""Hi,

Click the link below to sign in to your Hatchik account. It's
single-use and expires in {LOGIN_TOKEN_TTL_MINUTES} minutes.

{link}

If you didn't ask for this, ignore this email.

— Hatchik

(This is an automated message — please don't reply.)
"""
    html = f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Sign in to Hatchik</title></head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:8px;padding:32px;">
<tr><td style="font-size:16px;line-height:1.6;color:#1a1a1a;">
<p style="margin:0 0 16px 0;">Hi,</p>
<p style="margin:0 0 16px 0;">Click the button below to sign in to your Hatchik account. It&rsquo;s single-use and expires in {LOGIN_TOKEN_TTL_MINUTES} minutes.</p>
<p style="margin:24px 0;"><a href="{link}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;">Sign in to Hatchik</a></p>
<p style="margin:0 0 16px 0;color:#555;font-size:14px;">Or paste this URL into your browser: <a href="{link}" style="color:#4f46e5;text-decoration:underline;">{link}</a></p>
<p style="margin:0 0 16px 0;color:#555;font-size:14px;">If you didn&rsquo;t ask for this, ignore this email.</p>
<p style="margin:24px 0 0 0;">&mdash; Hatchik</p>
<p style="margin:24px 0 0 0;color:#888;font-size:12px;">This is an automated message &mdash; please don&rsquo;t reply.</p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>
"""
    try:
        await _resend_send({
            "from": FROM_EMAIL,
            "to": [email],
            "subject": "Sign in to Hatchik",
            "text": text,
            "html": html,
        })
    except Exception as e:  # noqa: BLE001
        log.error("Failed to send login email to %s: %s", email, e)


@app.get("/api/account/auth")
async def auth_callback(token: str) -> RedirectResponse:
    """Verify a magic-link token, create a session, set cookie, redirect."""
    if not token or len(token) < 32:
        raise HTTPException(status_code=400, detail="invalid token")
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT email, expires_at, consumed_at FROM login_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="token not found")
        if row["consumed_at"]:
            raise HTTPException(status_code=410, detail="token already used")
        if row["expires_at"] < now_iso:
            raise HTTPException(status_code=410, detail="token expired")
        conn.execute("UPDATE login_tokens SET consumed_at = ? WHERE token = ?", (now_iso, token))
        session_id = secrets.token_urlsafe(32)
        expires_at = (now + timedelta(days=SESSION_TTL_DAYS)).isoformat()
        conn.execute(
            "INSERT INTO sessions (session_id, email, created_at, expires_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, row["email"], now_iso, expires_at, now_iso),
        )
        conn.commit()

    resp = RedirectResponse(url="/account", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    log.info("session created for %s", row["email"])
    return resp


@app.post("/api/account/logout", status_code=204)
async def logout(
    response: Response,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Response:
    if hatchik_session:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (hatchik_session,))
            conn.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@app.get("/api/account/me")
async def get_me(
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    session = _resolve_session(hatchik_session)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    email = session["email"]
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        signup_rows = conn.execute(
            "SELECT id, first_name, product_name, tier, created_at, status "
            "FROM signups WHERE LOWER(email) = ? ORDER BY id DESC",
            (email,),
        ).fetchall()
    reg = _load_registry()
    tenants_by_signup = {
        t.get("signup_id"): {"slug": slug, **t}
        for slug, t in reg.get("tenants", {}).items()
        if t.get("signup_id")
    }
    sandboxes = []
    first_name = ""
    for r in signup_rows:
        if not first_name and r["first_name"]:
            first_name = r["first_name"]
        tenant = tenants_by_signup.get(r["id"])
        sandboxes.append({
            "signup_id": r["id"],
            "product_name": r["product_name"],
            "tier": r["tier"],
            "created_at": r["created_at"],
            "status": tenant.get("status") if tenant else r["status"],
            "url": tenant.get("url") if tenant else None,
            "slug": tenant.get("slug") if tenant else None,
        })
    return {"email": email, "first_name": first_name, "sandboxes": sandboxes}


class UpdateMeRequest(BaseModel):
    first_name: str | None = Field(None, max_length=80)

    @field_validator("first_name")
    @classmethod
    def clean_first_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        token = v.strip().split()[0] if v.strip() else ""
        return token[:1].upper() + token[1:] if token else ""


@app.patch("/api/account/me")
async def update_me(
    body: UpdateMeRequest,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    session = _resolve_session(hatchik_session)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    email = session["email"]
    if body.first_name is not None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE signups SET first_name = ? WHERE LOWER(email) = ?",
                (body.first_name, email),
            )
            conn.commit()
    return {"ok": True}


@app.get("/api/account/upgrade")
async def upgrade_info(
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    """Return upgrade info — Paddle checkout URL if configured, else 'coming soon'."""
    session = _resolve_session(hatchik_session)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    if not PADDLE_LAUNCH_PRICE_ID:
        return {
            "available": False,
            "reason": "Paddle approval pending — we'll email you when Launch tier opens.",
        }
    # Paddle Billing hosted checkout — passes customer_email so the
    # post-checkout webhook can match to the signup row.
    checkout_url = (
        f"{PADDLE_CHECKOUT_BASE}/{PADDLE_LAUNCH_PRICE_ID}"
        f"?customer_email={session['email']}"
    )
    return {"available": True, "checkout_url": checkout_url}


@app.get("/api/account/billing-portal")
async def billing_portal(
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    """Return a Paddle customer-portal URL for the signed-in customer.

    Only customers with at least one successful Launch-tier payment have
    a Paddle customer_id. Until then, this returns 404 and the UI hides
    the billing tab.
    """
    session = _resolve_session(hatchik_session)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT paddle_customer_id FROM payments "
            "WHERE LOWER(customer_email) = ? AND paddle_customer_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (session["email"],),
        ).fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="no Paddle customer record yet")
    return {"url": f"{PADDLE_BILLING_PORTAL_BASE}/customer/{row[0]}"}


@app.post("/api/paddle/webhook")
async def paddle_webhook(request: Request) -> dict[str, Any]:
    """Receive Paddle Billing webhook events.

    Verifies the Paddle signature, deduplicates by ``event_id``, and
    dispatches on ``event_type``. Unhandled event types still return 200 —
    Paddle retries on non-2xx, and we want to avoid retry storms for
    things we don't care about.

    Event names follow Paddle Billing (v1), not Paddle Classic: e.g.
    ``transaction.completed``, not ``checkout.session.completed``.
    """
    if not PADDLE_WEBHOOK_SECRET:
        # Hard-fail on misconfiguration rather than silently accepting
        # unauthenticated traffic. Logged at error so journalctl shows it.
        log.error("Paddle webhook hit but PADDLE_WEBHOOK_SECRET is not configured")
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    payload = await request.body()
    sig_header = request.headers.get("Paddle-Signature", "")

    if not verify_paddle_signature(payload, sig_header, PADDLE_WEBHOOK_SECRET):
        log.warning("Paddle webhook signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        log.error("Paddle webhook payload not valid JSON: %s", e)
        raise HTTPException(status_code=400, detail="Invalid payload") from e

    # Paddle Billing places ``event_id`` and ``event_type`` at the top
    # level; the resource itself is under ``data``.
    event_id = event.get("event_id")
    event_type = event.get("event_type", "")
    if not event_id or not event_type:
        log.error("Paddle webhook missing event_id/event_type fields")
        raise HTTPException(status_code=400, detail="Malformed event")

    # Idempotency: Paddle retries on any non-2xx and occasionally
    # double-fires. Same event id twice -> ack with no work.
    if _event_already_processed(event_id):
        log.info("Paddle event %s (%s) already processed — skipping", event_id, event_type)
        return {"received": True, "duplicate": True}

    data_object = event.get("data") or {}

    if event_type == "transaction.completed":
        log.info(
            "Paddle transaction.completed: txn=%s customer=%s subscription=%s status=%s currency=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("subscription_id"),
            data_object.get("status"),
            data_object.get("currency_code"),
        )
        _persist_paddle_transaction(data_object, payload)

    elif event_type == "subscription.created":
        log.info(
            "Paddle subscription.created: subscription=%s customer=%s status=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("status"),
        )

    elif event_type == "subscription.updated":
        log.info(
            "Paddle subscription.updated: subscription=%s customer=%s status=%s next_billed_at=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("status"),
            data_object.get("next_billed_at"),
        )

    elif event_type == "subscription.canceled":
        log.warning(
            "Paddle subscription.canceled (churn): subscription=%s customer=%s canceled_at=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("canceled_at"),
        )

    elif event_type == "transaction.payment_failed":
        log.warning(
            "Paddle transaction.payment_failed: txn=%s customer=%s status=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("status"),
        )
        await notify_founder_payment_failure(data_object)

    else:
        log.info("Paddle event %s (%s) — no handler, ack'd", event_id, event_type)

    _mark_event_processed(event_id, event_type)
    return {"received": True}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
