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
import sqlite3
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
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
    """Email the founder so they can start the manual provisioning."""
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping founder notification")
        return

    subject = f"[Hatchik signup #{signup_id}] {req.tier.title()}: {req.product_name}"
    body = f"""\
New Hatchik signup #{signup_id}

  Email:       {req.email}
  Tier:        {req.tier}
  Product:     {req.product_name}
  Region:      {req.region or 'not specified'}
  Domain:      {req.domain_choice or 'will be discussed'}
  IP:          {ip}

  Description:
  {req.description or '(none)'}

Next step: see FIRST_CUSTOMER_RUNBOOK.md
Reply directly to {req.email} to begin the white-glove onboarding.
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
    """Render plaintext + HTML versions of the customer acknowledgement.

    Tone & structure mirror WELCOME_EMAILS.md §1 (sandbox) / §2 (launch):
    British voice, founder-signed, white-glove framing.
    """
    if req.tier == "launch":
        intro = (
            f"Just got your signup for {req.product_name} on the Launch tier — "
            "thank you. I'm provisioning your Hatchik right now."
        )
        next_step = (
            "You'll get the full handover email within 24 hours — earlier if "
            "nothing breaks. If anything needs your input (domain choice, "
            "Google OAuth preferences, etc.) I'll ask in a separate email."
        )
    else:
        intro = (
            f"Got your signup — really like the sound of {req.product_name}."
        )
        next_step = (
            "I'm setting your Hatchik sandbox up now. You'll get another email "
            "from me within 24 hours with the link to log in and start building."
        )

    text = f"""\
Hi,

{intro}

{next_step}

A heads-up: Hatchik's brand new, which means for now I (the founder)
hand-provision each signup. The flow you see in the demo is what's
shipping over the next few weeks. Until then, you're getting the
white-glove version — feel free to ask me anything by replying to
this email.

Talk soon,
Hatchik
"""

    # HTML version — same copy, simple inline styles, mobile-friendly.
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
              <p style="margin:0 0 16px 0;">Hi,</p>
              <p style="margin:0 0 16px 0;">{intro_html}</p>
              <p style="margin:0 0 16px 0;">{next_html}</p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">
                A heads-up: Hatchik&rsquo;s brand new, which means for now I
                (the founder) hand-provision each signup. The flow you see
                in the demo is what&rsquo;s shipping over the next few weeks.
                Until then, you&rsquo;re getting the white-glove version &mdash;
                feel free to ask me anything by replying to this email.
              </p>
              <p style="margin:24px 0 0 0;">Talk soon,<br>Hatchik</p>
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
            "reply_to": FOUNDER_EMAIL,
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
                created_at, email, product_name, description, tier,
                region, domain_choice, ip_address, user_agent, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
            """,
            (
                created_at, str(req.email), req.product_name, req.description,
                req.tier, req.region, req.domain_choice, ip, user_agent,
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
        message="Thanks. We're setting your Hatchik up — check your email within the hour.",
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
