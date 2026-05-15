#!/usr/bin/env python3
"""
auto_graduate.py — daily scheduler that walks Launch tenants and triggers
Growth promotion when end-user signup count crosses the threshold.

Customer-facing promise: "Auto-graduates to Growth at your 15th end-user
signup." This script is the thing that makes that "auto" true.

Runs once a day from ``hatchik-auto-graduate.timer``. For each shared
Launch tenant in registry.json:

  - Query the per-tenant Postgres for COUNT(*) of auth.users.
  - The owner account counts as 1; the threshold below is the OWNER+N
    total so the spec "15th end-user signup" lines up with
    HATCHIK_AUTO_GRADUATE_USER_COUNT=16.
  - If count >= threshold AND tenant is on a shared host AND not already
    graduated/in-flight, invoke ``promote_to_growth.py --signup-id N
    --execute`` in a subprocess.
  - Idempotent: skips tenants with ``auto_graduate_*_at`` markers in
    registry or tier already 'growth'.

Defensive: per-tenant probe failures (container down, schema missing,
etc.) are logged and skipped. One sick container shouldn't block the
sweep.

Usage:
    auto_graduate.py                       # do the sweep
    auto_graduate.py --dry-run             # log decisions only
    auto_graduate.py --slug <slug>         # only consider one tenant
    auto_graduate.py --json                # emit summary JSON

Env overrides:
    HATCHIK_AUTO_GRADUATE_USER_COUNT=16    # owner+15 end-users
    HATCHIK_AUTO_GRADUATE_DISABLE=1        # short-circuit (block release)

Designed to run as root on the same host as promote_to_growth.py
(typically the orchestrator host, not the tenant host).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Reuse promote.py helpers (registry load, _send_email, _fetch_signup).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import promote  # noqa: E402
from promote import REGISTRY_FILE  # noqa: E402

USER_COUNT_THRESHOLD = int(os.environ.get("HATCHIK_AUTO_GRADUATE_USER_COUNT", "16"))
DISABLED = os.environ.get("HATCHIK_AUTO_GRADUATE_DISABLE", "") == "1"
PROMOTE_SCRIPT = Path(__file__).resolve().parent / "promote_to_growth.py"
FOUNDER_EMAIL = os.environ.get("HATCHIK_FOUNDER_EMAIL", "appmanager@namaasol.com")

logging.basicConfig(
    level=os.environ.get("HATCHIK_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("auto_graduate")


# ─── Registry helpers ─────────────────────────────────────────────────────
def _load_registry() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        return {"tenants": {}}
    return json.loads(REGISTRY_FILE.read_text())


def _save_registry(reg: dict[str, Any]) -> None:
    tmp = REGISTRY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=2))
    tmp.replace(REGISTRY_FILE)


def _mark(slug: str, key: str) -> None:
    reg = _load_registry()
    t = (reg.get("tenants") or {}).get(slug)
    if not t:
        return
    t[key] = datetime.now(timezone.utc).isoformat()
    _save_registry(reg)


# ─── Per-tenant user count probe ──────────────────────────────────────────
def query_user_count(slug: str) -> int | None:
    """Return COUNT(*) FROM auth.users for the tenant, or None on failure."""
    container = f"{slug}-postgres-1"
    try:
        r = subprocess.run(
            [
                "docker", "exec", container,
                "psql", "-U", "postgres", "-d", "postgres",
                "-tAc", "SELECT COUNT(*) FROM auth.users",
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("user-count probe for %s failed: %s", slug, e)
        return None
    if r.returncode != 0:
        log.warning("user-count probe for %s exited %s: %s",
                    slug, r.returncode, r.stderr.strip()[:200])
        return None
    out = r.stdout.strip()
    try:
        return int(out)
    except ValueError:
        log.warning("user-count probe for %s returned non-int: %r", slug, out)
        return None


# ─── Trigger ──────────────────────────────────────────────────────────────
def _trigger_growth_promotion(signup_id: int, slug: str, count: int,
                              dry_run: bool) -> dict[str, Any]:
    cmd = [sys.executable, str(PROMOTE_SCRIPT),
           "--signup-id", str(signup_id), "--execute", "--json"]
    if dry_run:
        log.info("[dry-run] would invoke: %s", " ".join(cmd))
        return {"slug": slug, "dry_run": True, "user_count": count}

    log.info("triggering Growth promotion for %s (signup #%s, count=%s)",
             slug, signup_id, count)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired as e:
        log.error("promote_to_growth.py timed out for %s: %s", slug, e)
        return {"slug": slug, "ok": False, "error": "timeout"}

    if r.returncode != 0:
        log.error("promote_to_growth.py exited %s for %s: %s",
                  r.returncode, slug, r.stderr.strip()[:500])
        _mark(slug, "auto_graduate_failed_at")
        # Email the founder so a human can intervene.
        promote._send_email(  # type: ignore[attr-defined]
            FOUNDER_EMAIL,
            f"[Auto-graduate FAILED] {slug} signup #{signup_id}",
            f"""auto_graduate.py tried to promote {slug} (signup #{signup_id})
to Growth and failed. Re-run manually:

  promote_to_growth.py --signup-id {signup_id} --execute --json

stderr (truncated):
{r.stderr.strip()[:2000]}

stdout (truncated):
{r.stdout.strip()[:2000]}
""",
        )
        return {"slug": slug, "ok": False, "exit_code": r.returncode}

    _mark(slug, "auto_graduated_at")
    try:
        return {"slug": slug, "ok": True, **json.loads(r.stdout or "{}")}
    except json.JSONDecodeError:
        return {"slug": slug, "ok": True, "raw": r.stdout[:500]}


# ─── Main sweep ───────────────────────────────────────────────────────────
def sweep(only_slug: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    if DISABLED:
        log.info("HATCHIK_AUTO_GRADUATE_DISABLE=1 — exiting without sweep")
        return {"disabled": True, "considered": 0}

    reg = _load_registry()
    tenants = reg.get("tenants") or {}
    summary: dict[str, Any] = {
        "threshold": USER_COUNT_THRESHOLD,
        "dry_run": dry_run,
        "considered": 0,
        "below_threshold": [],
        "skipped": [],
        "probe_failed": [],
        "triggered": [],
    }

    for slug, tenant in tenants.items():
        if only_slug and slug != only_slug:
            continue
        if tenant.get("tier") == "growth":
            summary["skipped"].append({"slug": slug, "reason": "already growth"})
            continue
        if tenant.get("tier") != "launch":
            summary["skipped"].append({"slug": slug, "reason": f"tier={tenant.get('tier')}"})
            continue
        if not tenant.get("shared"):
            # Dedicated Launch tenants need a different graduation path
            # (they don't need migration — just tier tag + billing flip).
            # That's promote_to_growth.py's job; safer to leave manual for now.
            summary["skipped"].append({"slug": slug, "reason": "dedicated launch (manual)"})
            continue
        if tenant.get("auto_graduated_at"):
            summary["skipped"].append({"slug": slug, "reason": "already auto-graduated"})
            continue
        if tenant.get("auto_graduate_failed_at"):
            summary["skipped"].append({"slug": slug, "reason": "previous failure — manual"})
            continue
        if not tenant.get("signup_id"):
            summary["skipped"].append({"slug": slug, "reason": "no signup_id in registry"})
            continue

        summary["considered"] += 1
        count = query_user_count(slug)
        if count is None:
            summary["probe_failed"].append({"slug": slug})
            continue
        if count < USER_COUNT_THRESHOLD:
            summary["below_threshold"].append({"slug": slug, "count": count})
            continue

        result = _trigger_growth_promotion(
            tenant["signup_id"], slug, count, dry_run
        )
        summary["triggered"].append(result)

    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--slug", help="only sweep this slug")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    summary = sweep(only_slug=args.slug, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        triggered = len(summary.get("triggered", []))
        print(f"considered={summary.get('considered')} "
              f"triggered={triggered} "
              f"below_threshold={len(summary.get('below_threshold', []))} "
              f"skipped={len(summary.get('skipped', []))} "
              f"probe_failed={len(summary.get('probe_failed', []))}")
        if triggered:
            for r in summary["triggered"]:
                print(f"  ✓ {r.get('slug')}: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
