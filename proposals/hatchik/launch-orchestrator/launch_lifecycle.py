#!/usr/bin/env python3
"""
launch_lifecycle.py — daily reconciler for the Launch / Growth tier.

Fired by hatchik-launch-lifecycle.timer (daily, 03:00 UTC, 10-min jitter).
Walks every entry in the launch registry and:

  * payment-failed customers (Paddle subscription status ``past_due``):
      Day 3:  email customer ("payment failed, here's the portal link")
      Day 7:  email customer ("we're suspending your service in 2 days")
      Day 9:  suspend tenant (containers down, data preserved, custom 503)
      Day 30: hard decommission (snapshot + delete VPS)

  * canceled customers (``subscription.canceled`` was fired):
      Day 0:  email customer ("30-day grace, here's how to come back")
      Day 25: email customer ("5 days until tear-down")
      Day 30: decommission (snapshot + delete VPS)

  * active customers: no action; just log a health-check ping.

The Paddle status is the source of truth. We poll the payments / events
tables for the latest known status per subscription_id. We do NOT call
Paddle's API from here — the webhook is the canonical signal.

SAFE_MODE: by default, this script ONLY emails the founder a summary
plus a per-tenant runbook. It does not suspend or decommission. Flip
with ``--execute`` once you're comfortable.

Usage:
    launch_lifecycle.py                       # SAFE_MODE summary
    launch_lifecycle.py --dry-run --json      # machine-readable plan
    launch_lifecycle.py --execute             # actually act
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
REGISTRY_PATH = Path(os.environ.get(
    "HATCHIK_LAUNCH_REGISTRY", str(Path(__file__).parent / "registry.json"),
))
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FOUNDER_EMAIL = os.environ.get("HATCHIK_FOUNDER_EMAIL", "hello@hatchik.com")
FROM_EMAIL = os.environ.get("HATCHIK_FROM_EMAIL", "hello@hatchik.com")
DECOM_SCRIPT = Path(__file__).parent / "decommission_launch.py"


# ─── Helpers ────────────────────────────────────────────────────────────

def _load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"schema_version": 1, "tenants": {}}
    return json.loads(REGISTRY_PATH.read_text())


def _save_registry(reg: dict[str, Any]) -> None:
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2))
    tmp.replace(REGISTRY_PATH)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_since(iso: str | None, now: datetime) -> int | None:
    dt = _parse_iso(iso)
    if not dt:
        return None
    return (now - dt).days


def _latest_subscription_status(subscription_id: str | None) -> str | None:
    """Most-recent known status for this Paddle subscription_id from local
    events. Returns None if we have no events. The signups DB stores
    raw_payload on transactions but subscription events are logged via
    processed_events; we use the last transaction for this subscription
    as a proxy for status freshness.
    """
    if not subscription_id:
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT status FROM payments "
                "WHERE paddle_subscription_id = ? "
                "ORDER BY id DESC LIMIT 1", (subscription_id,),
            ).fetchone()
            return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def _send_email(to: str, subject: str, text: str) -> None:
    if not RESEND_API_KEY:
        print(f"[NO RESEND_API_KEY] To: {to}\nSubject: {subject}\n\n{text}",
              file=sys.stderr)
        return
    try:
        import httpx
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": FROM_EMAIL, "to": [to], "subject": subject, "text": text},
            timeout=10.0,
        )
        if r.status_code >= 300:
            print(f"Resend send failed {r.status_code}: {r.text[:300]}",
                  file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"Resend exception (non-fatal): {e}", file=sys.stderr)


# ─── Reconciliation ────────────────────────────────────────────────────

def classify_tenant(slug: str, t: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Compute the action for one tenant. Returns a dict with:
        slug, action, reason, days, customer_email, hetzner_server_id

    Actions:
        none                — healthy / no work
        notify_past_due     — Day-3 email
        warn_past_due       — Day-7 email
        suspend             — Day-9 suspend
        decom               — Day-30 decom (past_due OR canceled)
        cancel_grace        — Day-0 to 24 of canceled grace
        cancel_warn         — Day-25 of canceled grace
        unknown_status      — Paddle status missing; needs operator look
    """
    status = t.get("status")
    if status == "decommissioned":
        return {"slug": slug, "action": "none", "reason": "already decommissioned"}

    canceled_at = t.get("canceled_at")
    paddle_status = _latest_subscription_status(t.get("paddle_subscription_id"))

    # Canceled flow takes priority over past_due
    if canceled_at:
        days = _days_since(canceled_at, now) or 0
        if days < 25:
            return {"slug": slug, "action": "cancel_grace", "reason": f"day {days} of 30-day grace",
                    "days": days, "customer_email": t.get("customer_email"),
                    "hetzner_server_id": t.get("hetzner_server_id")}
        if days < 30:
            return {"slug": slug, "action": "cancel_warn", "reason": f"day {days} — 5-day warning",
                    "days": days, "customer_email": t.get("customer_email")}
        return {"slug": slug, "action": "decom", "reason": "30-day grace expired",
                "days": days, "customer_email": t.get("customer_email")}

    # Past-due ladder (Paddle says payment failed)
    if paddle_status == "past_due":
        past_due_since = t.get("past_due_since") or now.isoformat()
        days = _days_since(past_due_since, now) or 0
        if days < 3:
            return {"slug": slug, "action": "none", "reason": f"past_due day {days} (Paddle is still retrying)"}
        if days < 7:
            return {"slug": slug, "action": "notify_past_due", "reason": f"past_due day {days}",
                    "days": days, "customer_email": t.get("customer_email")}
        if days < 9:
            return {"slug": slug, "action": "warn_past_due", "reason": f"past_due day {days}",
                    "days": days, "customer_email": t.get("customer_email")}
        if days < 30:
            return {"slug": slug, "action": "suspend", "reason": f"past_due day {days}",
                    "days": days, "customer_email": t.get("customer_email")}
        return {"slug": slug, "action": "decom", "reason": "past_due 30 days",
                "days": days, "customer_email": t.get("customer_email")}

    # Active / healthy
    if paddle_status in ("active", "trialing", None):
        return {"slug": slug, "action": "none",
                "reason": f"healthy (paddle={paddle_status or 'unknown'})"}

    return {"slug": slug, "action": "unknown_status",
            "reason": f"unknown paddle status: {paddle_status!r}",
            "customer_email": t.get("customer_email")}


def _customer_email_for(action: str, slug: str, plan: dict[str, Any]) -> tuple[str, str]:
    days = plan.get("days")
    if action == "notify_past_due":
        return (
            "Your Hatchik payment didn't go through",
            f"""Hi,

We tried to charge your subscription for {slug} on Hatchik and the
payment didn't go through (this happens — expired card, billing-address
mismatch, bank security check).

Update your card here: https://hatchik.com/account → Billing

Paddle will retry automatically. If it still doesn't go through after
a few days, we'll suspend the service until you're able to update — but
your data stays safe for 30 days either way.

— Hatchik
""",
        )
    if action == "warn_past_due":
        return (
            "Service suspending in 2 days — update card?",
            f"""Hi,

Your subscription for {slug} is still showing as past-due (day {days}).
We'll have to suspend the service in 2 days if the card isn't updated.

Two minutes to fix: https://hatchik.com/account → Billing

Suspending means your tenant goes offline, but your data and your VPS
are preserved for a full 30 days. The moment a payment goes through,
we bring you straight back up.

— Hatchik
""",
        )
    if action == "cancel_grace":
        return (
            "Your Hatchik subscription is canceled — what happens next",
            f"""Hi,

We see you've canceled your Hatchik subscription. Sorry to see you go.

Here's what happens:
  - Your service stays online for 30 days from cancellation
  - At day 30 we snapshot your VPS, then take it offline
  - The snapshot is kept for an extra 30 days — if you change your mind
    in that window, you can re-subscribe and we'll restore from snapshot
  - After 60 days total, the snapshot is purged

Need to export anything? Reply to this email and we'll arrange a data
export at no charge.

— Hatchik
""",
        )
    if action == "cancel_warn":
        return (
            "Heads up: 5 days until {slug} comes down".format(slug=slug),
            f"""Hi,

Friendly reminder: your Hatchik subscription was canceled and we're
5 days from the 30-day grace window closing for {slug}.

If you want to come back, re-subscribe at
https://hatchik.com/account → Upgrade and your tenant will keep
running uninterrupted.

If you want a data export before we tear it down, reply to this email.

— Hatchik
""",
        )
    return ("", "")


def reconcile(execute: bool = False, dry_run: bool = False) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    reg = _load_registry()
    plans: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for slug, t in reg.get("tenants", {}).items():
        plan = classify_tenant(slug, t, now)
        counts[plan["action"]] = counts.get(plan["action"], 0) + 1
        plans.append(plan)

    summary = {"now": now.isoformat(), "tenants": len(plans), "counts": counts}
    if dry_run:
        return {"summary": summary, "plans": plans}

    # ── Apply actions ────────────────────────────────────────────────
    applied: list[dict[str, Any]] = []
    for plan in plans:
        action = plan["action"]
        slug = plan["slug"]
        if action in ("none",):
            continue

        if action in ("notify_past_due", "warn_past_due", "cancel_grace", "cancel_warn"):
            if execute and plan.get("customer_email"):
                subject, body = _customer_email_for(action, slug, plan)
                _send_email(plan["customer_email"], subject, body)
            applied.append({**plan, "executed": execute})

        elif action == "suspend":
            # SAFE_MODE: never actually suspend without --execute; even
            # with --execute, send the operator a heads-up rather than
            # auto-shutting the tenant. Suspending a tenant is rare
            # enough that one founder click is the right cost.
            _send_email(
                FOUNDER_EMAIL,
                f"[Launch lifecycle] SUSPEND ready for {slug}",
                f"""Tenant {slug} ({plan.get('customer_email')}) is past_due day "
                f"{plan.get('days')}.

To suspend manually:
  ssh root@<vps-ip>  # IP from registry
  cd /opt/app && docker compose down

When the payment is updated and Paddle webhook fires
subscription.activated, run:
  ssh root@<vps-ip>
  cd /opt/app && docker compose up -d

If you prefer auto-suspend, add an SSH-based suspend command into
launch_lifecycle.py and re-run.
""",
            )
            applied.append({**plan, "executed": False, "manual_required": True})

        elif action == "decom":
            cmd = ["python3", str(DECOM_SCRIPT), "--slug", slug]
            if execute:
                cmd.append("--execute")
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                applied.append({**plan, "rc": r.returncode, "executed": execute,
                                "stdout": r.stdout[-500:], "stderr": r.stderr[-500:]})
            except Exception as e:  # noqa: BLE001
                applied.append({**plan, "error": str(e)})

        elif action == "unknown_status":
            _send_email(
                FOUNDER_EMAIL,
                f"[Launch lifecycle] Unknown Paddle status for {slug}",
                f"Tenant {slug} ({plan.get('customer_email')}) has an "
                f"unrecognised paddle status. Check the Paddle dashboard "
                f"and update the registry manually.",
            )
            applied.append({**plan, "executed": False})

    return {"summary": summary, "applied": applied}


# ─── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execute", action="store_true",
                   help="Actually send emails / call decommission_launch.py")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute and print the plan, do nothing.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.dry_run and args.execute:
        print("Use either --dry-run or --execute, not both.", file=sys.stderr)
        return 2

    result = reconcile(execute=args.execute, dry_run=args.dry_run)

    # Always email the founder a summary on each scheduled run
    if not args.dry_run:
        summary = result.get("summary", {})
        counts_str = ", ".join(f"{k}={v}" for k, v in (summary.get("counts") or {}).items())
        _send_email(
            FOUNDER_EMAIL,
            f"[Launch lifecycle] daily run — {counts_str or 'no work'}",
            json.dumps(result, indent=2),
        )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"=== Launch lifecycle: {result['summary']['tenants']} tenants ===")
        for k, v in (result["summary"].get("counts") or {}).items():
            print(f"  {k}: {v}")
        if not args.dry_run:
            for entry in (result.get("applied") or []):
                print(f"  → {entry['slug']}: {entry['action']} "
                      f"(executed={entry.get('executed')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
