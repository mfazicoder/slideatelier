#!/usr/bin/env python3
"""
decommission.py — tear down a sandbox tenant.

Counterpart to provision.py. Used by the admin CLI and by the
signup-service API endpoints (admin DELETE + self-serve confirmation),
which subprocess this file.

Usage:
    decommission.py <slug>                # soft: keep registry history + signup row
    decommission.py <slug> --hard         # also purge registry entry + signup row
    decommission.py --signup <id>          # look up slug via registry, decommission
    decommission.py --list                # show all tenants and statuses
    decommission.py <slug> --json         # machine-readable summary

Idempotent — re-running for a partially-decommissioned slug cleans
whatever residue remains.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SIGNUPS_DB = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
TENANTS_DIR = Path(os.environ.get("HATCHIK_TENANTS_DIR", "/opt/hatchik-tenants"))
HOST_CADDY_DIR = Path(os.environ.get("HATCHIK_HOST_CADDY_DIR", "/opt/hatchik-host-caddy"))
TENANTS_CADDY_D = HOST_CADDY_DIR / "tenants.d"
REGISTRY_FILE = TENANTS_DIR / "registry.json"
HOST_CADDY_CONTAINER = os.environ.get("HATCHIK_HOST_CADDY_CONTAINER", "hatchik-host-caddy-caddy-1")


def load_registry() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        return {"version": 1, "tenants": {}}
    return json.loads(REGISTRY_FILE.read_text())


def save_registry(reg: dict[str, Any]) -> None:
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2, sort_keys=True))
    tmp.rename(REGISTRY_FILE)


def reload_host_caddy() -> None:
    subprocess.run(
        ["docker", "exec", HOST_CADDY_CONTAINER, "caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def list_tenants() -> None:
    reg = load_registry()
    if not reg["tenants"]:
        print("(no tenants)")
        return
    print(f"{'slug':<32} {'port':<6} {'status':<14} {'signup':<8} {'email'}")
    print("-" * 90)
    for slug, t in sorted(reg["tenants"].items()):
        print(f"{slug:<32} {t.get('port', '-'):<6} {t.get('status', '-'):<14} "
              f"{t.get('signup_id', '-'):<8} {t.get('email', '')}")


def decommission(slug: str, hard: bool = False, verbose: bool = True) -> dict[str, Any]:
    """Tear down a tenant. Returns a summary dict.

    Idempotent — cleans up whichever pieces (compose dir, caddy route,
    registry entry, signup row) are still present.
    """
    log = print if verbose else (lambda *_a, **_k: None)
    summary: dict[str, Any] = {"slug": slug, "hard": hard, "steps": []}

    reg = load_registry()
    tenant = reg["tenants"].get(slug)
    target = TENANTS_DIR / slug
    route = TENANTS_CADDY_D / f"{slug}.caddy"
    nothing_left = (tenant is None and not target.exists() and not route.exists())
    if nothing_left:
        log(f"'{slug}' is already fully cleaned up")
        summary["status"] = "already-clean"
        return summary

    signup_id = (tenant or {}).get("signup_id", 0)

    if target.exists():
        log(f"→ docker compose down -v in {target}")
        subprocess.run(
            ["docker", "compose", "-f", str(target / "docker-compose.yml"), "down", "-v"],
            cwd=str(target),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        summary["steps"].append("compose-down")
        shutil.rmtree(target)
        summary["steps"].append("rmdir")
        log(f"  removed {target}")

    if route.exists():
        route.unlink()
        summary["steps"].append("rm-route")
        reload_host_caddy()
        summary["steps"].append("caddy-reload")
        log(f"  removed {route} + reloaded host Caddy")

    reg = load_registry()  # reload — caddy reload may have taken a moment
    if hard:
        reg["tenants"].pop(slug, None)
        summary["steps"].append("registry-purge")
        log("  registry: entry removed (hard)")
    elif slug in reg["tenants"]:
        reg["tenants"][slug]["status"] = "decommissioned"
        reg["tenants"][slug]["decommissioned_at"] = int(time.time())
        summary["steps"].append("registry-mark-decommissioned")
        log("  registry: status=decommissioned")
    save_registry(reg)

    if hard and signup_id:
        try:
            conn = sqlite3.connect(SIGNUPS_DB)
            conn.execute("DELETE FROM signups WHERE id = ?", (signup_id,))
            conn.commit()
            conn.close()
            summary["steps"].append("signup-row-deleted")
            log(f"  signup row #{signup_id} deleted (hard)")
        except sqlite3.Error as e:
            log(f"  WARN: failed to delete signup row #{signup_id}: {e}")

    summary["status"] = "purged" if hard else "decommissioned"
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Decommission a Hatchik sandbox tenant.")
    ap.add_argument("slug", nargs="?", help="Tenant slug to decommission")
    ap.add_argument("--signup", type=int, help="Look up slug by signup id, then decommission")
    ap.add_argument("--list", action="store_true", help="List all tenants and exit")
    ap.add_argument("--hard", action="store_true",
                    help="Also remove registry entry and DELETE signup row")
    ap.add_argument("--quiet", action="store_true", help="Suppress per-step output")
    ap.add_argument("--json", action="store_true", help="Print summary JSON on stdout")
    args = ap.parse_args()

    if args.list:
        list_tenants()
        return

    slug = args.slug
    if args.signup is not None:
        reg = load_registry()
        slug = next((s for s, t in reg["tenants"].items() if t.get("signup_id") == args.signup), None)
        if not slug:
            sys.exit(f"no tenant in registry has signup_id={args.signup}")
    if not slug:
        ap.error("provide <slug>, --signup <id>, or --list")

    summary = decommission(slug, hard=args.hard, verbose=not args.quiet)
    if args.json:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
