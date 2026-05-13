#!/usr/bin/env python3
"""
launch_resize.py — resize a Launch tenant's VPS up (CAX31→CAX41 for the
Launch→Growth graduation) or back down.

Called manually by the founder when launch_lifecycle.py flags a plan
change, or by hand to upgrade ahead of schedule. The Hetzner API
requires the server to be powered off first; hetzner_api.change_type
handles that + the resize + power-on as one atomic operation.

Resize is **non-reversible disk-wise**: upgrade_disk=True grows the
disk during the resize, and Hetzner won't let you shrink it later.
That's fine for Launch→Growth (going up) but mark this clearly for
operators considering a downgrade.

Usage:
    launch_resize.py --slug <s> --to growth          # CAX31 → CAX41
    launch_resize.py --slug <s> --to launch          # CAX41 → CAX31 (downgrade)
    launch_resize.py --slug <s> --to growth --dry-run

Default mode: **DRY_RUN unless --execute is passed.** No accidental
£70/mo upgrades from a typo.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import hetzner_api  # noqa: E402
except Exception:  # pragma: no cover
    hetzner_api = None  # type: ignore[assignment]

DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
REGISTRY_PATH = Path(os.environ.get(
    "HATCHIK_LAUNCH_REGISTRY", str(Path(__file__).parent / "registry.json"),
))
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FOUNDER_EMAIL = os.environ.get("HATCHIK_FOUNDER_EMAIL", "hello@hatchik.com")
FROM_EMAIL = os.environ.get("HATCHIK_FROM_EMAIL", "hello@hatchik.com")

TIER_TO_TYPE = {
    "launch": "cax31",
    "growth": "cax41",
}


def _load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"schema_version": 1, "tenants": {}}
    return json.loads(REGISTRY_PATH.read_text())


def _save_registry(reg: dict) -> None:
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2))
    tmp.replace(REGISTRY_PATH)


def _record_transition(signup_id: int, from_tier: str, to_tier: str, note: str) -> None:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO tier_transitions "
                "(signup_id, from_tier, to_tier, occurred_at, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (signup_id, from_tier, to_tier,
                 datetime.now(timezone.utc).isoformat(), note),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        print(f"WARN tier_transitions: {e}", file=sys.stderr)


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
        print(f"Resend exception: {e}", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--to", choices=("launch", "growth"), required=True,
                   help="Target tier — 'growth' upgrades, 'launch' downgrades.")
    p.add_argument("--execute", action="store_true",
                   help="Actually call Hetzner. Default is dry-run.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    reg = _load_registry()
    tenant = reg.get("tenants", {}).get(args.slug)
    if not tenant:
        out = {"error": "slug not in launch registry", "slug": args.slug}
        print(json.dumps(out) if args.json else f"✗ {out}", file=sys.stderr)
        return 2

    current_tier = tenant.get("tier", "launch")
    current_type = TIER_TO_TYPE.get(current_tier, "cax31")
    target_type = TIER_TO_TYPE[args.to]
    server_id = tenant.get("hetzner_server_id")

    if current_tier == args.to:
        out = {"ok": True, "no_op": True, "reason": f"already on tier {args.to}"}
        print(json.dumps(out) if args.json else f"already on {args.to}; no work")
        return 0

    is_downgrade = TIER_TO_TYPE[args.to] < current_type  # lex compare OK for cax31/41

    plan = f"""Resize plan for {args.slug}

Current:    {current_tier} ({current_type})
Target:     {args.to} ({target_type})
Server ID:  {server_id}
Downgrade:  {is_downgrade}

Hetzner price delta (rough):
  CAX31 → CAX41: +€4.32/month (£3.70)
  CAX41 → CAX31: -€4.32/month (but disk does NOT shrink)

Steps (atomic via hetzner_api.change_type):
  1. Power off server {server_id}
  2. Change type {current_type} → {target_type} (upgrade_disk={not is_downgrade})
  3. Power back on
  4. Update launch registry tenant.tier = {args.to}
  5. Record tier_transitions row

Customer-facing impact: ~3 minutes of downtime during the resize.
Notify the customer before --execute if this is during business hours.
"""

    if not args.execute:
        print(plan)
        out = {"ok": True, "mode": "DRY_RUN", "plan": plan}
        if args.json:
            print(json.dumps(out, indent=2))
        return 0

    if not hetzner_api:
        print("✗ hetzner_api import failed; cannot --execute", file=sys.stderr)
        return 3

    try:
        action = hetzner_api.change_type(server_id, target_type)
    except Exception as e:  # noqa: BLE001
        print(f"✗ resize failed: {e}", file=sys.stderr)
        return 3

    tenant["tier"] = args.to
    tenant["last_resized_at"] = datetime.now(timezone.utc).isoformat()
    _save_registry(reg)

    if tenant.get("signup_id"):
        _record_transition(
            int(tenant["signup_id"]),
            current_tier, args.to,
            note=f"launch_resize.py {current_type}→{target_type}",
        )

    if tenant.get("customer_email"):
        _send_email(
            tenant["customer_email"],
            f"You're now on Hatchik {args.to.title()}",
            f"""Hi,

Your Hatchik plan is now on the {args.to.title()} tier. Server resized
from {current_type} to {target_type}.

You should see:
  - More headroom (CPU + RAM)
  - The same domain, the same data — nothing was migrated
  - 2-3 minutes of brief downtime during the resize, now back

Manage your subscription: https://hatchik.com/account → Billing

— Hatchik
""",
        )

    out = {"ok": True, "slug": args.slug, "from": current_type,
           "to": target_type, "action_id": action.get("action", {}).get("id")}
    print(json.dumps(out, indent=2) if args.json else
          f"✓ {args.slug} resized {current_type} → {target_type}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
