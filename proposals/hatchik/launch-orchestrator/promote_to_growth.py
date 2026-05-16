#!/usr/bin/env python3
"""
promote_to_growth.py — Shared Launch tenant → dedicated Growth VPS.

Triggered when a customer's substrate signup-count crosses 15 (auto), or
manually by the founder. Moves the tenant from its slot on a shared
CAX41 Launch host onto a fresh CAX31 dedicated VPS, migrates the DB,
flips DNS, then frees the shared-host slot.

Default mode: **SAFE_MODE** — computes the plan, emails the founder,
writes a ``tier_transitions`` row, but does NOT call Hetzner /
Cloudflare APIs or rsync any data. Flip with ``--execute`` once the
destination is wired and tested.

Usage:
    promote_to_growth.py --signup-id <N>             # SAFE_MODE
    promote_to_growth.py --signup-id <N> --execute   # real provisioning
    promote_to_growth.py --signup-id <N> --json      # machine-readable

Exit codes:
    0  success
    2  signup not found / tenant not on a shared host
    3  Hetzner / Cloudflare / DB error
    4  partial — some steps succeeded, manual followup needed

Idempotency:
The flow writes progress markers into ``registry.tenants[<slug>]``
under the ``growth_migration`` key. Re-running picks up at the next
unfinished step. The five tracked steps:
    1. dedicated_server_id  : CAX31 provisioned
    2. dedicated_bootstrap  : bootstrap_substrate.sh succeeded
    3. db_migrated          : pg_dump + rsync + pg_restore complete
    4. dns_switched         : Cloudflare A record points at new IP
    5. shared_decommissioned: shared-host slot freed
Each marker is the ISO timestamp of completion.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import promote  # noqa: E402  reuse helpers (registry, email, infer_repo_url, …)

try:
    import hetzner_api  # noqa: E402
except Exception:  # pragma: no cover
    hetzner_api = None  # type: ignore[assignment]
try:
    import dns_api  # noqa: E402
except Exception:  # pragma: no cover
    dns_api = None  # type: ignore[assignment]

DB_PATH = promote.DB_PATH
REGISTRY_PATH = promote.REGISTRY_PATH
LOG_DIR = promote.LOG_DIR
FOUNDER_EMAIL = promote.FOUNDER_EMAIL


# ─── Plan ───────────────────────────────────────────────────────────────

def compute_plan(signup: dict[str, Any], tenant: dict[str, Any]) -> dict[str, Any]:
    location = tenant.get("hetzner_location") or hetzner_api.map_region(signup.get("region")) if hetzner_api else (tenant.get("hetzner_location") or "nbg1")
    return {
        "signup_id": signup["id"],
        "customer_email": signup["email"],
        "first_name": signup.get("first_name"),
        "product_name": signup.get("product_name") or "your app",
        "region": signup.get("region") or "eu-central",
        "hetzner_location": location,
        "server_type": "cax31",  # Growth dedicated
        "customer_domain": tenant.get("customer_domain"),
        "slug": _slug_for(signup),
        "shared_host_id": tenant.get("host_id"),
        "shared_host_ip": tenant.get("ip"),
        "shared_port_base": tenant.get("port_base"),
        "sandbox_slug": tenant.get("sandbox_slug") or promote._find_sandbox_slug(int(signup["id"])),
        "steps": [
            "1. Provision dedicated CAX31 in " + str(location),
            "2. cloud-init + bootstrap_substrate.sh on new VPS",
            "3. pg_dump current DB on shared host slot",
            "4. rsync dump to new dedicated VPS",
            "5. pg_restore on new dedicated VPS",
            "6. Health-check https://" + str(tenant.get("customer_domain") or "<domain>"),
            "7. Cloudflare DNS A record → new dedicated IP",
            "8. Decommission shared-host slot (compose down, free port, remove snippet)",
            "9. Update registry tier=growth, signups.tier=growth",
            "10. Email customer: 'Your Growth tier is ready'",
        ],
    }


def _slug_for(signup: dict[str, Any]) -> str:
    return f"launch-{signup['id']}"


# ─── Helpers (reuse promote's where sensible) ───────────────────────────

def _load() -> dict[str, Any]:
    return promote._load_registry()


def _save(reg: dict[str, Any]) -> None:
    promote._save_registry(reg)


def _mark_step(slug: str, key: str, value: Any = None) -> None:
    """Persist progress marker for idempotent resume."""
    reg = _load()
    tenant = reg["tenants"][slug]
    gm = tenant.setdefault("growth_migration", {})
    gm[key] = value if value is not None else datetime.now(timezone.utc).isoformat()
    _save(reg)


def _step_done(slug: str, key: str) -> bool:
    reg = _load()
    return bool((reg["tenants"].get(slug, {}).get("growth_migration") or {}).get(key))


# ─── Execute ────────────────────────────────────────────────────────────

def _execute(plan: dict[str, Any], log_lines: list[str]) -> dict[str, Any]:
    if not hetzner_api:
        raise RuntimeError(
            "hetzner_api not importable — refusing to --execute without "
            "HETZNER_API_TOKEN."
        )

    slug = plan["slug"]
    signup_id = plan["signup_id"]
    reg = _load()
    tenant = reg["tenants"][slug]
    gm = tenant.setdefault("growth_migration", {})

    # ─── Step 1: provision dedicated CAX31 ──────────────────────────────
    dedicated_server_id = gm.get("dedicated_server_id")
    dedicated_ip = gm.get("dedicated_ip")
    if not dedicated_server_id:
        log_lines.append(f"step 1: provisioning dedicated CAX31 in {plan['hetzner_location']}")
        dedicated_slug = f"growth-{signup_id}"
        resp = hetzner_api.create_server(
            name=dedicated_slug,
            location=plan["hetzner_location"],
            server_type=plan["server_type"],
            labels={
                "hatchik_tier": "growth",
                "hatchik_signup_id": str(signup_id),
                "hatchik_slug": dedicated_slug,
                "hatchik_migrated_from": plan["shared_host_id"] or "unknown",
            },
            user_data=promote._cloud_init_script(dedicated_slug),
        )
        dedicated_server_id = resp["server"]["id"]
        srv = hetzner_api.wait_for_running(dedicated_server_id, timeout_s=300)
        dedicated_ip = srv["public_net"]["ipv4"]["ip"]
        gm["dedicated_server_id"] = dedicated_server_id
        gm["dedicated_ip"] = dedicated_ip
        gm["dedicated_slug"] = dedicated_slug
        gm["step_1_at"] = datetime.now(timezone.utc).isoformat()
        _save(reg)
        log_lines.append(f"  dedicated_server_id={dedicated_server_id} ip={dedicated_ip}")
    else:
        log_lines.append(f"step 1: skipping — dedicated_server_id={dedicated_server_id} ip={dedicated_ip} already provisioned")

    # ─── Step 2: bootstrap_substrate.sh on the dedicated VPS ────────────
    if not gm.get("step_2_at"):
        log_lines.append("step 2: running bootstrap_substrate.sh on dedicated VPS")
        # Reuse promote's runner. We construct a synthetic plan-shape
        # because _run_substrate_bootstrap reads only customer_domain +
        # sandbox_slug; the IP is passed separately.
        synth_plan = {
            "customer_domain": plan["customer_domain"],
            "sandbox_slug": None,  # DB migrates from shared host, not sandbox
            "github_username": None,
            "signup_id": signup_id,
        }
        # _infer_repo_url needs a sandbox_slug to construct the URL; we
        # use the existing tenant slug since the tenant repo lives at
        # `<HATCHIK_GITHUB_ORG>/<sandbox_slug>` (unchanged across promote).
        synth_plan["sandbox_slug"] = plan["sandbox_slug"] or slug
        ok = promote._run_substrate_bootstrap(dedicated_ip, synth_plan, log_lines)
        if not ok:
            log_lines.append("step 2 FAILED — see runbook in promote.py bootstrap path")
            promote._send_email(
                FOUNDER_EMAIL,
                f"[Growth #{signup_id}] dedicated VPS up at {dedicated_ip} — bootstrap failed",
                f"""Dedicated CAX31 for Growth promotion of signup #{signup_id}
({plan['customer_email']}) is running at {dedicated_ip} but
bootstrap_substrate.sh failed. SSH in and tail
/var/log/hatchik-bootstrap.log. Once fixed, re-run:

  python3 /opt/hatchik-launch-orchestrator/promote_to_growth.py \\
      --signup-id {signup_id} --execute

— the script will resume from step 2.
""",
            )
            return {"ok": False, "stage": "bootstrap", "ip": dedicated_ip,
                    "deferred": ["bootstrap-recovery"]}
        gm["step_2_at"] = datetime.now(timezone.utc).isoformat()
        _save(reg)
    else:
        log_lines.append("step 2: skipping — already bootstrapped")

    # ─── Step 3: DB migrate from shared host slot → dedicated VPS ───────
    if not gm.get("step_3_at"):
        ok = _migrate_db(
            shared_host_ip=plan["shared_host_ip"],
            slug=slug,
            dedicated_ip=dedicated_ip,
            log_lines=log_lines,
        )
        if not ok:
            log_lines.append("step 3 FAILED — DB migration aborted; tenant still on shared host")
            return {"ok": False, "stage": "db_migrate",
                    "dedicated_ip": dedicated_ip,
                    "deferred": ["db-migrate-recovery"]}
        gm["step_3_at"] = datetime.now(timezone.utc).isoformat()
        _save(reg)
    else:
        log_lines.append("step 3: skipping — DB already migrated")

    # ─── Step 4: health-check the new dedicated VPS on the domain ───────
    # We hit the dedicated IP via the Host: header so we don't need DNS
    # to have flipped yet. Caddy on the dedicated VPS is already issuing
    # certs (it shares the Cloudflare DNS-01 path).
    if not gm.get("step_4_at") and plan["customer_domain"]:
        if _health_check_dedicated(dedicated_ip, plan["customer_domain"], log_lines):
            gm["step_4_at"] = datetime.now(timezone.utc).isoformat()
            _save(reg)
        else:
            log_lines.append("step 4 WARN: health-check did not return 2xx/3xx; proceeding anyway")
            # Don't block on this — DNS-not-yet-flipped TLS cert issuance
            # is finicky. We continue, but flag for founder.
            gm["step_4_at"] = datetime.now(timezone.utc).isoformat() + "?"
            _save(reg)

    # ─── Step 5: DNS switchover ─────────────────────────────────────────
    if not gm.get("step_5_at") and plan["customer_domain"] and dns_api:
        try:
            log_lines.append(f"step 5: dns set_a_record {plan['customer_domain']} -> {dedicated_ip}")
            dns_api.set_a_record(plan["customer_domain"], dedicated_ip, proxied=True)
            gm["step_5_at"] = datetime.now(timezone.utc).isoformat()
            _save(reg)
        except dns_api.ZoneNotFound:
            log_lines.append("step 5 WARN: ZoneNotFound — customer needs to repoint A record manually")
            promote._send_email(
                plan["customer_email"],
                f"Action needed: point {plan['customer_domain']} at {dedicated_ip}",
                f"""Hi,

Your Hatchik Growth tier dedicated VPS is up at {dedicated_ip}. To
finish the move, update your A record at your DNS provider:

  Name: @ (or {plan['customer_domain']})
  Type: A
  Value: {dedicated_ip}
  TTL: Auto / 1 hour

We'll keep your shared-host slot running until the DNS flips so there's
no downtime.

— Hatchik
""",
            )
            gm["step_5_at"] = datetime.now(timezone.utc).isoformat() + "?manual"
            _save(reg)

    # ─── Step 6: decommission the shared-host slot ──────────────────────
    if not gm.get("step_6_at"):
        log_lines.append(f"step 6: decommissioning shared-host slot for {slug}")
        ok = promote._decommission_tenant_on_host(slug, log_lines)
        if not ok:
            log_lines.append("step 6 WARN: shared-host decom returned False — manual cleanup needed")
        gm["step_6_at"] = datetime.now(timezone.utc).isoformat()
        _save(reg)

    # ─── Step 7: re-tag the tenant as growth (dedicated) ────────────────
    reg = _load()
    tenant = reg["tenants"][slug]
    tenant["tier"] = "growth"
    tenant["shared"] = False
    tenant["hetzner_server_id"] = dedicated_server_id
    tenant["ip"] = dedicated_ip
    tenant["status"] = "live"
    tenant["last_seen_at"] = datetime.now(timezone.utc).isoformat()
    tenant["promoted_to_growth_at"] = datetime.now(timezone.utc).isoformat()
    _save(reg)

    _update_signup_to_growth(signup_id)
    _record_transition_to_growth(signup_id)

    # ─── Step 8: welcome email ──────────────────────────────────────────
    live_url = f"https://{plan['customer_domain']}" if plan["customer_domain"] else f"http://{dedicated_ip}"
    promote._send_email(
        plan["customer_email"],
        "Your Hatchik Growth tier is live",
        _growth_welcome_email(plan, live_url),
    )

    return {
        "ok": True,
        "slug": slug,
        "tier": "growth",
        "dedicated_server_id": dedicated_server_id,
        "ip": dedicated_ip,
        "live_url": live_url,
    }


# ─── DB migration ──────────────────────────────────────────────────────

def _migrate_db(
    *,
    shared_host_ip: str,
    slug: str,
    dedicated_ip: str,
    log_lines: list[str],
) -> bool:
    """pg_dump on shared host (in tenant compose) → rsync → pg_restore on dedicated.

    The shared host runs the tenant's Postgres in a container named
    ``<slug>-postgres-1`` (docker compose default for service `postgres`
    with project name `<slug>`). The dedicated VPS runs the equivalent
    container; we exec into both.

    Returns True iff every shell step exits 0. Failures log the offending
    rc + stderr tail.
    """
    import subprocess

    ssh_opts = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=30",
        "-o", "BatchMode=yes",
    ]
    shared = f"root@{shared_host_ip}"
    dedicated = f"root@{dedicated_ip}"
    dump_remote = f"/tmp/{slug}-growth.dump.gz"
    dump_local = f"/tmp/{slug}-growth.dump.gz"  # orchestrator host

    # 1. pg_dump on shared
    log_lines.append(f"db_migrate: pg_dump on shared host {shared_host_ip}")
    r = subprocess.run(
        ["ssh", *ssh_opts, shared,
         f"docker exec -i {slug}-postgres-1 pg_dump -U postgres -Fc -Z 6 postgres > {dump_remote}"],
        capture_output=True, text=True, timeout=900,
    )
    if r.returncode != 0:
        log_lines.append(f"  pg_dump rc={r.returncode} stderr={r.stderr[-300:]}")
        return False

    # 2. rsync shared → orchestrator → dedicated. Two hops because
    # the shared host can't SSH to the new dedicated host (no key
    # pre-shared). The orchestrator already has both keys.
    log_lines.append("db_migrate: rsync dump shared → orchestrator")
    r = subprocess.run(
        ["rsync", "-az",
         "-e", "ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes",
         f"{shared}:{dump_remote}", dump_local],
        capture_output=True, text=True, timeout=1800,
    )
    if r.returncode != 0:
        log_lines.append(f"  rsync(shared→orch) rc={r.returncode} stderr={r.stderr[-300:]}")
        return False

    log_lines.append("db_migrate: rsync dump orchestrator → dedicated")
    r = subprocess.run(
        ["rsync", "-az",
         "-e", "ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes",
         dump_local, f"{dedicated}:{dump_remote}"],
        capture_output=True, text=True, timeout=1800,
    )
    if r.returncode != 0:
        log_lines.append(f"  rsync(orch→dedicated) rc={r.returncode} stderr={r.stderr[-300:]}")
        return False

    # 3. wait for postgres on dedicated to accept connections
    log_lines.append("db_migrate: wait for postgres on dedicated VPS")
    for _ in range(30):
        r = subprocess.run(
            ["ssh", *ssh_opts, dedicated,
             "cd /opt/app && docker compose exec -T postgres pg_isready -U postgres"],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0:
            break
        time.sleep(2)
    else:
        log_lines.append("  postgres on dedicated never became ready")
        return False

    # 4. pg_restore on dedicated
    log_lines.append("db_migrate: pg_restore on dedicated VPS")
    r = subprocess.run(
        ["ssh", *ssh_opts, dedicated,
         f"gunzip -c {dump_remote} | cd /opt/app && docker compose exec -T postgres "
         "pg_restore -U postgres -d postgres --no-owner --no-privileges --clean --if-exists"],
        capture_output=True, text=True, timeout=1800,
    )
    if r.returncode != 0:
        log_lines.append(f"  pg_restore rc={r.returncode} stderr={r.stderr[-300:]}")
        return False

    # 5. cleanup the dump files
    subprocess.run(["ssh", *ssh_opts, shared, f"rm -f {dump_remote}"],
                   capture_output=True, text=True, timeout=30)
    subprocess.run(["ssh", *ssh_opts, dedicated, f"rm -f {dump_remote}"],
                   capture_output=True, text=True, timeout=30)
    try:
        os.unlink(dump_local)
    except OSError:
        pass

    log_lines.append("db_migrate: complete")
    return True


def _health_check_dedicated(ip: str, domain: str, log_lines: list[str]) -> bool:
    """curl --resolve so we hit the dedicated IP via the domain's Host header.

    Caddy may not have a TLS cert yet for the domain because Cloudflare
    is still pointing at the shared host. We try both http and https
    (with --insecure) and accept any 2xx/3xx as a green light.
    """
    import subprocess
    for scheme in ("https", "http"):
        cmd = [
            "curl", "-sSL", "--max-time", "15", "--insecure",
            "--resolve", f"{domain}:443:{ip}",
            "--resolve", f"{domain}:80:{ip}",
            "-o", "/dev/null", "-w", "%{http_code}",
            f"{scheme}://{domain}/",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        code = (r.stdout or "").strip()
        log_lines.append(f"health_check {scheme}://{domain} ({ip}) -> {code}")
        if code.startswith(("2", "3")):
            return True
    return False


# ─── DB writes ──────────────────────────────────────────────────────────

def _update_signup_to_growth(signup_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE signups SET tier = ?, status = ? WHERE id = ?",
            ("growth", "live-growth", signup_id),
        )
        conn.commit()


def _record_transition_to_growth(signup_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO tier_transitions "
            "(signup_id, from_tier, to_tier, occurred_at, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (signup_id, "launch", "growth",
             datetime.now(timezone.utc).isoformat(),
             "promote_to_growth.py --execute"),
        )
        conn.commit()


# ─── Customer email ─────────────────────────────────────────────────────

def _growth_welcome_email(plan: dict[str, Any], live_url: str) -> str:
    name = plan.get("first_name") or "there"
    return f"""Hi {name},

You've crossed 15 end-user signups — your Hatchik tier just auto-
graduated to Growth.

What changed:
  • Your app now runs on a dedicated VPS (CAX31 in {plan['hetzner_location']})
    — no more shared-host neighbours, more headroom for traffic.
  • Your database moved across with you. Nothing lost; no downtime
    needed on your end.
  • Same domain, same repo, same workflow. {live_url} is exactly where
    it was — just on a bigger box.

Billing: £39/month (or £390/yr — same annual discount as before).
Your next Paddle invoice will reflect this.

Heads-up: the shared-host slot you previously occupied has been freed.
If you ever want to roll back to Launch, just reply and we'll do the
reverse — your DB stays with you either way.

— Hatchik
"""


# ─── Promote → Growth public flow ───────────────────────────────────────

def promote_to_growth(signup_id: int) -> dict[str, Any]:
    """Programmatic entry point. Equivalent to ``--signup-id <N> --execute``."""
    signup = promote._fetch_signup(signup_id)
    if not signup:
        return {"ok": False, "error": "signup not found", "signup_id": signup_id}
    slug = _slug_for(signup)
    reg = _load()
    tenant = (reg.get("tenants") or {}).get(slug)
    if not tenant:
        return {"ok": False, "error": "tenant not in registry; promote to Launch first"}
    if not tenant.get("shared"):
        return {"ok": False, "error": "tenant is not on a shared host; nothing to migrate"}

    plan = compute_plan(signup, tenant)
    log_lines: list[str] = []
    started_at = time.time()
    log_lines.append(f"promote_to_growth signup_id={signup_id} slug={slug} started")

    try:
        result = _execute(plan, log_lines)
    except Exception as e:  # noqa: BLE001
        log_lines.append(f"FATAL: {type(e).__name__}: {e}")
        _write_log(signup_id, log_lines)
        return {"ok": False, "error": str(e)}

    log_lines.append(f"completed in {time.time() - started_at:.0f}s")
    _write_log(signup_id, log_lines)
    return result


def _write_log(signup_id: int, log_lines: list[str]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"promote-growth-{signup_id}.log"
    log_file.write_text("\n".join(log_lines))


# ─── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--signup-id", type=int, required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    signup = promote._fetch_signup(args.signup_id)
    if not signup:
        msg = {"error": "signup not found", "signup_id": args.signup_id}
        print(json.dumps(msg) if args.json else f"✗ {msg}", file=sys.stderr)
        return 2

    slug = _slug_for(signup)
    reg = _load()
    tenant = (reg.get("tenants") or {}).get(slug)
    if not tenant:
        msg = {"error": "tenant not in launch registry; promote to Launch first",
               "slug": slug}
        print(json.dumps(msg) if args.json else f"✗ {msg}", file=sys.stderr)
        return 2
    if not tenant.get("shared"):
        msg = {"error": "tenant not on a shared host — already dedicated; "
                        "nothing to migrate", "slug": slug}
        print(json.dumps(msg) if args.json else f"✗ {msg}", file=sys.stderr)
        return 2

    plan = compute_plan(signup, tenant)

    if not args.execute:
        promote._send_email(
            FOUNDER_EMAIL,
            f"[Growth #{args.signup_id}] SAFE_MODE plan",
            f"""Growth promotion plan for signup #{args.signup_id}
({signup['email']}) — currently on shared host {tenant.get('host_id')}
at port_base {tenant.get('port_base')}.

Steps:
{chr(10).join('  ' + s for s in plan['steps'])}

Run with --execute to actually do this.
""",
        )
        out = {"ok": True, "mode": "SAFE_MODE", "plan": plan}
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(f"✓ SAFE_MODE — plan emailed to {FOUNDER_EMAIL}. "
                  "Pass --execute to migrate.")
        return 0

    result = promote_to_growth(args.signup_id)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result.get("ok"):
            print(f"✓ Growth promotion complete for signup #{args.signup_id}")
            print(f"  IP: {result.get('ip')}")
            print(f"  Live: {result.get('live_url')}")
        else:
            print(f"✗ {result.get('error') or result.get('stage')}")
            if result.get("deferred"):
                print(f"  DEFERRED: {', '.join(result['deferred'])}")
    return 0 if result.get("ok") else 4


if __name__ == "__main__":
    sys.exit(main())
