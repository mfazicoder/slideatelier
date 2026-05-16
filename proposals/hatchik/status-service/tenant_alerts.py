#!/usr/bin/env python3
"""
tenant_alerts.py — per-tenant uptime check + email-the-customer alert.

Customer-facing promise: "Uptime monitoring + alerts to your inbox."

Status-service main.py already probes every-N-minutes and records
per-component states. This module adds the *per-tenant* layer:

  1. Read the launch + sandbox registries.
  2. For each customer-domain tenant (live URL is reachable in theory),
     do a 2 × HTTP probe with a small spacing.
  3. State machine, persisted in a tiny SQLite db:
       up           — last probe 2xx
       degraded     — last probe 5xx OR 3-30s response time
       down         — last 2 probes failed
       recovered    — was down, last probe 2xx
  4. On `up → down` transition, email customer + founder.
  5. On `down → up` transition, email customer + founder ("back up").

Designed to run from a systemd timer every 5 minutes. Idempotent.

Env:
    HATCHIK_TENANT_PROBE_TIMEOUT_S=10
    HATCHIK_TENANT_PROBE_DEGRADED_MS=3000
    HATCHIK_TENANT_PROBE_DOWN_AFTER=2     # consecutive failures
    HATCHIK_FROM_EMAIL=noreply@hatchik.com
    HATCHIK_FOUNDER_EMAIL=appmanager@namaasol.com
    RESEND_API_KEY=…
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(
    level=os.environ.get("HATCHIK_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("tenant_alerts")

# ─── Config ───────────────────────────────────────────────────────────────
PROBE_TIMEOUT_S = float(os.environ.get("HATCHIK_TENANT_PROBE_TIMEOUT_S", "10"))
DEGRADED_MS = int(os.environ.get("HATCHIK_TENANT_PROBE_DEGRADED_MS", "3000"))
DOWN_AFTER = int(os.environ.get("HATCHIK_TENANT_PROBE_DOWN_AFTER", "2"))
LAUNCH_REGISTRY = Path(os.environ.get(
    "HATCHIK_LAUNCH_REGISTRY",
    "/opt/hatchik-tenants/registry.json"))
SANDBOX_REGISTRY = Path(os.environ.get(
    "HATCHIK_SANDBOX_REGISTRY",
    "/opt/hatchik-tenants-sandbox/registry.json"))
SIGNUPS_DB = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
ALERT_DB = Path(os.environ.get(
    "HATCHIK_TENANT_ALERT_DB",
    "/var/lib/hatchik/tenant_alerts.db"))

RESEND_API_URL = "https://api.resend.com/emails"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
HATCHIK_FROM_EMAIL = os.environ.get("HATCHIK_FROM_EMAIL", "noreply@hatchik.com")
HATCHIK_FOUNDER_EMAIL = os.environ.get("HATCHIK_FOUNDER_EMAIL", "appmanager@namaasol.com")


# ─── Persistence (lightweight, single-table) ──────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS tenant_state (
    slug              TEXT PRIMARY KEY,
    state             TEXT NOT NULL,         -- 'up' | 'degraded' | 'down'
    consecutive_fail  INTEGER NOT NULL DEFAULT 0,
    last_probe_at     TEXT,
    last_state_change TEXT,
    last_alert_at     TEXT
);
"""


def _db() -> sqlite3.Connection:
    ALERT_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ALERT_DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _state_of(slug: str) -> dict[str, Any]:
    with _db() as c:
        row = c.execute(
            "SELECT * FROM tenant_state WHERE slug = ?", (slug,)
        ).fetchone()
        if row:
            return dict(row)
    return {"slug": slug, "state": "up", "consecutive_fail": 0,
            "last_probe_at": None, "last_state_change": None,
            "last_alert_at": None}


def _save_state(slug: str, state: str, consecutive_fail: int,
                state_changed: bool, alerted: bool) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _db() as c:
        c.execute(
            """INSERT INTO tenant_state
                (slug, state, consecutive_fail, last_probe_at,
                 last_state_change, last_alert_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(slug) DO UPDATE SET
                   state = excluded.state,
                   consecutive_fail = excluded.consecutive_fail,
                   last_probe_at = excluded.last_probe_at,
                   last_state_change = CASE WHEN ? THEN excluded.last_probe_at
                                            ELSE tenant_state.last_state_change END,
                   last_alert_at = CASE WHEN ? THEN excluded.last_probe_at
                                        ELSE tenant_state.last_alert_at END""",
            (slug, state, consecutive_fail, now,
             now if state_changed else None,
             now if alerted else None,
             1 if state_changed else 0,
             1 if alerted else 0),
        )


# ─── Probe ────────────────────────────────────────────────────────────────
def probe(url: str) -> dict[str, Any]:
    """Single HTTP GET. Returns {status, http_code, latency_ms, error?}."""
    start = time.time()
    try:
        r = httpx.get(url, timeout=PROBE_TIMEOUT_S, follow_redirects=True)
        latency_ms = int((time.time() - start) * 1000)
        if r.status_code >= 500:
            return {"status": "down", "http_code": r.status_code,
                    "latency_ms": latency_ms, "error": f"{r.status_code}"}
        if r.status_code >= 400:
            # 4xx = customer config issue (login wall) — count as up.
            return {"status": "up", "http_code": r.status_code,
                    "latency_ms": latency_ms}
        if latency_ms >= DEGRADED_MS:
            return {"status": "degraded", "http_code": r.status_code,
                    "latency_ms": latency_ms}
        return {"status": "up", "http_code": r.status_code,
                "latency_ms": latency_ms}
    except httpx.HTTPError as e:
        return {"status": "down", "http_code": None,
                "latency_ms": int((time.time() - start) * 1000),
                "error": str(e)[:200]}


# ─── Email (Resend) ───────────────────────────────────────────────────────
def _send(to: str, subject: str, text: str) -> None:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY missing — would have sent to %s: %s",
                    to, subject)
        return
    try:
        httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": HATCHIK_FROM_EMAIL, "to": to,
                  "subject": subject, "text": text},
            timeout=15,
        )
    except httpx.HTTPError as e:
        log.warning("Resend POST failed to %s: %s", to, e)


def _down_email(slug: str, url: str, customer_email: str,
                first_name: str | None, probe_result: dict[str, Any]) -> None:
    name = first_name or "there"
    text = f"""Hi {name},

Your Hatchik app at {url} hasn't responded to our last {DOWN_AFTER} checks.

What we're seeing:
  status:  {probe_result.get('status')}
  http:    {probe_result.get('http_code')}
  latency: {probe_result.get('latency_ms')} ms
  error:   {probe_result.get('error') or '—'}

We're investigating. If you're in the middle of a deploy this is probably
that. Otherwise reply to this email and we'll dig in. We'll also email
you when it recovers.

— Hatchik
"""
    _send(customer_email, f"[Hatchik] {slug} appears to be down", text)
    _send(HATCHIK_FOUNDER_EMAIL, f"[FOUNDER] {slug} down: {url}", text)


def _recovered_email(slug: str, url: str, customer_email: str,
                     first_name: str | None) -> None:
    name = first_name or "there"
    text = f"""Hi {name},

Good news — {url} is responding again.

If you want a quick look at what happened, the status page has the last
24h of checks for your app: https://status.hatchik.com/?slug={slug}

— Hatchik
"""
    _send(customer_email, f"[Hatchik] {slug} is back up", text)
    _send(HATCHIK_FOUNDER_EMAIL, f"[FOUNDER] {slug} recovered: {url}", text)


# ─── Registry walking ─────────────────────────────────────────────────────
def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"tenants": {}}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("failed to load registry at %s", path)
        return {"tenants": {}}


def _lookup_signup(signup_id: int | None) -> dict[str, Any] | None:
    """Get customer email + first name for a signup_id."""
    if not signup_id or not SIGNUPS_DB.exists():
        return None
    try:
        with sqlite3.connect(SIGNUPS_DB) as c:
            c.row_factory = sqlite3.Row
            r = c.execute(
                "SELECT email, first_name FROM signups WHERE id = ?",
                (signup_id,),
            ).fetchone()
            return dict(r) if r else None
    except sqlite3.Error as e:
        log.warning("signups.db lookup for #%s failed: %s", signup_id, e)
        return None


# ─── Sweep ────────────────────────────────────────────────────────────────
def sweep_once(dry_run: bool = False) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "checked": 0, "up": [], "degraded": [], "down": [],
        "recovered": [], "alerts_sent": [],
    }

    for registry_path in (LAUNCH_REGISTRY, SANDBOX_REGISTRY):
        reg = _load_registry(registry_path)
        for slug, tenant in (reg.get("tenants") or {}).items():
            if tenant.get("status") not in (None, "live", "promoted"):
                continue
            url = tenant.get("url") or tenant.get("live_url")
            if not url:
                continue
            summary["checked"] += 1

            r1 = probe(url)
            # Only re-probe if first one failed — saves load.
            r2 = r1 if r1["status"] == "up" else probe(url)
            new_state = "up"
            if r1["status"] == "down" and r2["status"] == "down":
                new_state = "down"
            elif r1["status"] == "degraded" or r2["status"] == "degraded":
                new_state = "degraded"

            prev = _state_of(slug)
            prev_state = prev["state"]
            prev_fails = int(prev.get("consecutive_fail") or 0)

            consecutive = prev_fails + 1 if new_state == "down" else 0
            state_changed = (prev_state != new_state)
            alerted = False

            summary[new_state].append({"slug": slug, "url": url,
                                       "latency_ms": r2.get("latency_ms")})

            # Alert on UP → DOWN and DOWN → UP transitions
            if not dry_run and state_changed and consecutive >= DOWN_AFTER and new_state == "down":
                info = _lookup_signup(tenant.get("signup_id"))
                if info:
                    _down_email(slug, url, info["email"], info.get("first_name"), r2)
                    summary["alerts_sent"].append({"slug": slug, "kind": "down"})
                    alerted = True
            elif not dry_run and prev_state == "down" and new_state == "up":
                info = _lookup_signup(tenant.get("signup_id"))
                if info:
                    _recovered_email(slug, url, info["email"], info.get("first_name"))
                    summary["alerts_sent"].append({"slug": slug, "kind": "recovered"})
                    alerted = True
                    summary["recovered"].append(slug)

            if not dry_run:
                _save_state(slug, new_state, consecutive, state_changed, alerted)

    return summary


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    summary = sweep_once(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"checked={summary['checked']} "
              f"up={len(summary['up'])} "
              f"degraded={len(summary['degraded'])} "
              f"down={len(summary['down'])} "
              f"alerts={len(summary['alerts_sent'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
