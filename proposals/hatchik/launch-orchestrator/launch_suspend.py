#!/usr/bin/env python3
"""
launch_suspend.py — suspend / resume a Launch tenant's container stack.

Used at the day-9 past-due step of launch_lifecycle (when configured to
auto-suspend) or by hand when a customer asks to pause without churning.

Suspend = `docker compose down` on the customer's VPS. Disk + DB stay
intact; Caddy returns 503 from the host container until resumed. No
data loss, no VPS teardown.

Resume = `docker compose up -d` on the customer's VPS once the
subscription is current again. Substrate comes back up where it left
off.

Default mode: **DRY_RUN unless --execute is passed.** Suspending a
paying customer is a serious operator action.

Usage:
    launch_suspend.py --slug <s>                 # dry-run suspend plan
    launch_suspend.py --slug <s> --execute        # actually suspend
    launch_suspend.py --slug <s> --resume         # dry-run resume plan
    launch_suspend.py --slug <s> --resume --execute
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_PATH = Path(os.environ.get(
    "HATCHIK_LAUNCH_REGISTRY",
    str(Path(__file__).parent / "registry.json"),
))
APP_DIR = os.environ.get("HATCHIK_LAUNCH_APP_DIR", "/opt/app")


def _load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"schema_version": 1, "tenants": {}}
    return json.loads(REGISTRY_PATH.read_text())


def _save_registry(reg: dict) -> None:
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2))
    tmp.replace(REGISTRY_PATH)


def _ssh(ip: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    opts = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=20",
    ]
    return subprocess.run(
        ["ssh", *opts, f"root@{ip}", *args],
        capture_output=True, text=True, timeout=timeout,
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--resume", action="store_true",
                   help="Bring the stack back up instead of taking it down.")
    p.add_argument("--execute", action="store_true",
                   help="Actually SSH and run docker compose. Default dry-run.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    reg = _load_registry()
    tenant = reg.get("tenants", {}).get(args.slug)
    if not tenant:
        out = {"error": "slug not in launch registry"}
        print(json.dumps(out) if args.json else f"✗ {out}", file=sys.stderr)
        return 2

    ip = tenant.get("ip")
    if not ip:
        print("✗ tenant has no IP — cannot SSH", file=sys.stderr)
        return 2

    op = "RESUME" if args.resume else "SUSPEND"
    cmd_remote = f"cd {APP_DIR} && docker compose {'up -d' if args.resume else 'down'}"

    plan = f"""{op} plan for {args.slug}

Customer: {tenant.get('customer_email')}
IP:       {ip}
Domain:   {tenant.get('customer_domain')}
Status:   {tenant.get('status')}

Remote command:
  ssh root@{ip} '{cmd_remote}'

Expected: {'all containers up; Caddy 200 within ~30s' if args.resume else
          'all containers down; Caddy 503 within ~10s'}.
Reversible with: {'launch_suspend.py --slug ' + args.slug if args.resume else
                  'launch_suspend.py --slug ' + args.slug + ' --resume'}
"""

    if not args.execute:
        print(plan)
        out = {"ok": True, "mode": "DRY_RUN", "operation": op, "plan": plan}
        if args.json:
            print(json.dumps(out, indent=2))
        return 0

    r = _ssh(ip, "bash", "-c", cmd_remote, timeout=180)
    if r.returncode != 0:
        out = {"ok": False, "rc": r.returncode, "stderr": r.stderr[-500:]}
        print(json.dumps(out) if args.json else f"✗ ssh failed: {r.stderr[-300:]}",
              file=sys.stderr)
        return 3

    tenant["status"] = "suspended" if not args.resume else "live"
    tenant[("suspended_at" if not args.resume else "resumed_at")] = \
        datetime.now(timezone.utc).isoformat()
    _save_registry(reg)

    out = {"ok": True, "operation": op, "slug": args.slug,
           "stdout": r.stdout[-300:]}
    print(json.dumps(out, indent=2) if args.json else
          f"✓ {args.slug} {op.lower()}d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
