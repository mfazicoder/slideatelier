#!/usr/bin/env python3
"""
decommission_launch.py — Tear down a Launch / Growth tenant.

Triggered by ``launch_lifecycle.py`` at end-of-grace (30 days after Paddle
``subscription.canceled``), or by the founder for explicit churn.

Default mode: **SAFE_MODE** — computes the plan, snapshots the server,
emails the founder, but does NOT actually delete the VPS. Flip with
``--execute`` once you've confirmed the snapshot exists.

Usage:
    decommission_launch.py --slug <slug>             # SAFE_MODE
    decommission_launch.py --slug <slug> --execute   # real teardown
    decommission_launch.py --slug <slug> --json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

try:
    import hetzner_api  # noqa: E402
except Exception:  # pragma: no cover
    hetzner_api = None  # type: ignore[assignment]

try:
    import dns_api  # noqa: E402
except Exception:  # pragma: no cover
    dns_api = None  # type: ignore[assignment]

DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
REGISTRY_PATH = Path(os.environ.get(
    "HATCHIK_LAUNCH_REGISTRY", str(Path(__file__).parent / "registry.json"),
))
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FOUNDER_EMAIL = os.environ.get("HATCHIK_FOUNDER_EMAIL", "hello@hatchik.com")
FROM_EMAIL = os.environ.get("HATCHIK_FROM_EMAIL", "hello@hatchik.com")


def _load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"schema_version": 1, "tenants": {}}
    return json.loads(REGISTRY_PATH.read_text())


def _save_registry(reg: dict[str, Any]) -> None:
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2))
    tmp.replace(REGISTRY_PATH)


def _record_transition(signup_id: int, note: str) -> None:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO tier_transitions "
                "(signup_id, from_tier, to_tier, occurred_at, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (signup_id, "launch", "cancelled",
                 datetime.now(timezone.utc).isoformat(), note),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        print(f"WARN: tier_transitions insert failed: {e}", file=sys.stderr)


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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    reg = _load_registry()
    tenant = reg.get("tenants", {}).get(args.slug)
    if not tenant:
        msg = {"error": "slug not in launch registry", "slug": args.slug}
        print(json.dumps(msg) if args.json else f"✗ {msg}", file=sys.stderr)
        return 2

    if tenant.get("status") == "decommissioned":
        out = {"ok": True, "already_decommissioned": True, "slug": args.slug}
        print(json.dumps(out) if args.json else f"already decommissioned: {args.slug}")
        return 0

    plan_text = f"""Decommission plan for {args.slug}

Customer:        {tenant.get('customer_email')}
Domain:          {tenant.get('customer_domain')}
Hetzner server:  {tenant.get('hetzner_server_id')}
Location:        {tenant.get('hetzner_location')}
IP:              {tenant.get('ip')}
Tier:            {tenant.get('tier')}
Status:          {tenant.get('status')}
Created:         {tenant.get('created_at')}
Canceled:        {tenant.get('canceled_at') or '(not set)'}

Steps:
  1. Snapshot the server (full disk image) — retained 30 days
  2. Delete Cloudflare A record for {tenant.get('customer_domain')}
  3. Delete the Hetzner server (releases IP)
  4. Mark registry.tenants[{args.slug}].status = 'decommissioned'
  5. tier_transitions row: launch -> cancelled

Snapshot cost: ~£0.10/month per 10GB at Hetzner rates. Cheap insurance.

Run with --execute to actually do this.
"""

    if not args.execute:
        _send_email(FOUNDER_EMAIL,
                    f"[Decom #{args.slug}] SAFE_MODE plan",
                    plan_text)
        out = {"ok": True, "mode": "SAFE_MODE", "tenant": tenant}
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(plan_text)
            print(f"\n✓ SAFE_MODE — plan emailed to {FOUNDER_EMAIL}. "
                  "Pass --execute to tear down.")
        return 0

    if not hetzner_api:
        print("✗ hetzner_api import failed; cannot --execute", file=sys.stderr)
        return 3

    # 1. Snapshot
    try:
        snap = hetzner_api.snapshot_server(
            tenant["hetzner_server_id"],
            description=f"hatchik-decom-{args.slug}-{datetime.now(timezone.utc).date()}",
        )
        snap_id = snap.get("image", {}).get("id") or snap.get("action", {}).get("id")
    except Exception as e:  # noqa: BLE001
        print(f"✗ snapshot failed: {e}", file=sys.stderr)
        return 3

    # 2. DNS A record removal
    if dns_api and tenant.get("customer_domain"):
        try:
            dns_api.delete_a_record(tenant["customer_domain"])
        except Exception as e:  # noqa: BLE001
            print(f"WARN: DNS delete failed (non-fatal): {e}", file=sys.stderr)

    # 3. Delete server
    try:
        hetzner_api.delete_server(tenant["hetzner_server_id"])
    except Exception as e:  # noqa: BLE001
        print(f"✗ server delete failed: {e}", file=sys.stderr)
        return 3

    # 4. Update registry
    tenant["status"] = "decommissioned"
    tenant["decommissioned_at"] = datetime.now(timezone.utc).isoformat()
    tenant["snapshot_id"] = snap_id
    _save_registry(reg)

    # 5. Record transition
    if tenant.get("signup_id"):
        _record_transition(int(tenant["signup_id"]), f"decom {args.slug}")

    out = {
        "ok": True,
        "slug": args.slug,
        "snapshot_id": snap_id,
        "decommissioned_at": tenant["decommissioned_at"],
    }
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"✓ {args.slug} decommissioned (snapshot {snap_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
