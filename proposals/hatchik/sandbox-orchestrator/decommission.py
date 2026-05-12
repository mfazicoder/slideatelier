#!/usr/bin/env python3
"""
decommission.py — tear down a sandbox tenant.

Usage:
    decommission.py <slug>          # by slug
    decommission.py --signup <id>   # by signup_id

Runs:
    1. docker compose down -v in /opt/hatchik-tenants/<slug>/
    2. Remove /opt/hatchik-host-caddy/tenants.d/<slug>.caddy
    3. Reload host Caddy
    4. Remove /opt/hatchik-tenants/<slug>/ directory
    5. Mark registry entry as decommissioned (keep history)

Idempotent — re-running for a decommissioned slug is a no-op.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

TENANTS_DIR = Path(os.environ.get("HATCHIK_TENANTS_DIR", "/opt/hatchik-tenants"))
HOST_CADDY_DIR = Path(os.environ.get("HATCHIK_HOST_CADDY_DIR", "/opt/hatchik-host-caddy"))
TENANTS_CADDY_D = HOST_CADDY_DIR / "tenants.d"
REGISTRY_FILE = TENANTS_DIR / "registry.json"


def load_registry() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        return {"version": 1, "tenants": {}}
    return json.loads(REGISTRY_FILE.read_text())


def save_registry(reg: dict[str, Any]) -> None:
    tmp = REGISTRY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2, sort_keys=True))
    tmp.rename(REGISTRY_FILE)


def reload_host_caddy() -> None:
    subprocess.run(
        ["docker", "exec", "hatchik-host-caddy-caddy-1", "caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
        check=False,  # tolerate caddy not running if host is down
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--signup", type=int)
    ap.add_argument("--keep-data", action="store_true", help="Don't wipe the tenant directory (keep db dump)")
    args = ap.parse_args()

    reg = load_registry()
    slug = args.slug
    if args.signup:
        slug = next((s for s, t in reg["tenants"].items() if t.get("signup_id") == args.signup), None)
        if not slug:
            sys.exit(f"no tenant for signup {args.signup}")
    if not slug:
        ap.error("either slug or --signup required")

    if slug not in reg["tenants"]:
        sys.exit(f"slug {slug} not in registry")

    tenant = reg["tenants"][slug]
    if tenant.get("status") == "decommissioned":
        print(f"already decommissioned: {slug}")
        return

    target = TENANTS_DIR / slug
    route = TENANTS_CADDY_D / f"{slug}.caddy"

    print(f"→ decommissioning {slug}")
    if target.exists():
        print("  1. docker compose down -v")
        subprocess.run(
            ["docker", "compose", "-f", str(target / "docker-compose.yml"), "down", "-v"],
            cwd=target, check=False,
        )

    print("  2. remove Caddy route")
    route.unlink(missing_ok=True)

    print("  3. reload host Caddy")
    reload_host_caddy()

    if not args.keep_data and target.exists():
        print("  4. remove tenant directory")
        shutil.rmtree(target)

    reg["tenants"][slug]["status"] = "decommissioned"
    reg["tenants"][slug]["decommissioned_at"] = int(time.time())
    save_registry(reg)
    print(f"✓ decommissioned {slug}")


if __name__ == "__main__":
    main()
