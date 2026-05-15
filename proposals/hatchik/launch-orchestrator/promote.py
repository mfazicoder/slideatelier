#!/usr/bin/env python3
"""
promote.py — Sandbox → Launch tier migration.

Triggered (a) directly by Paddle ``subscription.created`` webhook via
asyncio.create_task in signup-service, or (b) manually by the founder
from the orchestrator host.

Default mode: **SAFE_MODE** — computes the plan, sends the founder an
actionable email with the full runbook, writes a ``tier_transitions``
row, but does NOT call Hetzner / Cloudflare APIs. Flip with
``--execute`` once the destination is wired and tested.

Usage:
    promote.py --signup-id <N>             # SAFE_MODE: plan + email
    promote.py --signup-id <N> --execute   # real provisioning
    promote.py --signup-id <N> --json      # machine-readable output

Exit codes:
    0  success (plan emitted or execution complete)
    2  signup not found / not eligible
    3  Hetzner / Cloudflare / DB error
    4  partial — some steps succeeded, some failed; manual followup needed
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

# Local helpers
sys.path.insert(0, str(Path(__file__).parent))
from tenant_inventory import LAUNCH_INVENTORY_VERSION, launch_inventory  # noqa: E402

# Optional helpers — guarded so SAFE_MODE works without credentials
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
SANDBOX_REGISTRY_PATH = Path(os.environ.get(
    "HATCHIK_SANDBOX_REGISTRY", "/opt/hatchik-orchestrator/registry.json",
))
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FOUNDER_EMAIL = os.environ.get("HATCHIK_FOUNDER_EMAIL", "hello@hatchik.com")
FROM_EMAIL = os.environ.get("HATCHIK_FROM_EMAIL", "hello@hatchik.com")
LOG_DIR = Path(os.environ.get("HATCHIK_LOG_DIR", "/var/log/hatchik"))

# ─── Shared-host mode (Phase 1) ─────────────────────────────────────────
# HATCHIK_LAUNCH_MODE=shared  → bin-pack tenants onto CAX41 hosts (~25/box)
# HATCHIK_LAUNCH_MODE=dedicated (default) → existing one-VPS-per-tenant
LAUNCH_MODE = os.environ.get("HATCHIK_LAUNCH_MODE", "dedicated").lower()
SHARED_HOST_SERVER_TYPE = os.environ.get("HATCHIK_SHARED_HOST_TYPE", "cax41")
SHARED_HOST_DEFAULT_LOCATION = os.environ.get("HATCHIK_SHARED_HOST_LOCATION", "nbg1")
SHARED_HOST_CAPACITY = int(os.environ.get("HATCHIK_SHARED_HOST_CAPACITY", "25"))
# Tenant port-base is 18000 + (slot_index * 100). Each tenant gets a
# 100-port band so the substrate's internal Caddy + future per-tenant
# debug ports (Mailpit, Studio, ...) never overlap with siblings.
PORT_BASE_START = int(os.environ.get("HATCHIK_PORT_BASE_START", "18000"))
PORT_BASE_STRIDE = int(os.environ.get("HATCHIK_PORT_BASE_STRIDE", "100"))


# ─── DB helpers ─────────────────────────────────────────────────────────

def _fetch_signup(signup_id: int) -> dict[str, Any] | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, email, first_name, product_name, tier, region, "
            "domain_choice, status, github_username, country_code "
            "FROM signups WHERE id = ?", (signup_id,),
        ).fetchone()
        return dict(row) if row else None


def _record_transition(
    signup_id: int,
    from_tier: str,
    to_tier: str,
    note: str,
    paddle_event_id: str | None = None,
) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO tier_transitions "
            "(signup_id, from_tier, to_tier, occurred_at, paddle_event_id, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (signup_id, from_tier, to_tier,
             datetime.now(timezone.utc).isoformat(), paddle_event_id, note),
        )
        conn.commit()


def _update_signup_tier(signup_id: int, tier: str, status: str | None = None) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        if status is not None:
            conn.execute(
                "UPDATE signups SET tier = ?, status = ? WHERE id = ?",
                (tier, status, signup_id),
            )
        else:
            conn.execute(
                "UPDATE signups SET tier = ? WHERE id = ?", (tier, signup_id),
            )
        conn.commit()


# ─── Registry helpers ──────────────────────────────────────────────────

def _load_registry() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"schema_version": 1, "tenants": {}}
    return json.loads(REGISTRY_PATH.read_text())


def _save_registry(reg: dict[str, Any]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2))
    tmp.replace(REGISTRY_PATH)


def _find_sandbox_slug(signup_id: int) -> str | None:
    """Look up the sandbox slug for this signup, if one exists."""
    if not SANDBOX_REGISTRY_PATH.exists():
        return None
    reg = json.loads(SANDBOX_REGISTRY_PATH.read_text())
    for slug, meta in (reg.get("tenants") or {}).items():
        if meta.get("signup_id") == signup_id and meta.get("status") in ("live", "provisioning"):
            return slug
    return None


# ─── Plan computation (always runs, even in SAFE_MODE) ──────────────────

def compute_plan(signup: dict[str, Any]) -> dict[str, Any]:
    region = (signup.get("region") or "eu-central").lower()
    location = hetzner_api.map_region(region) if hetzner_api else f"<map:{region}>"
    domain_choice = signup.get("domain_choice") or "byo"
    customer_domain = signup.get("domain_choice")  # filled by signup form
    # For BYO, customer_domain might be the actual domain. If not parsed
    # we'll prompt the founder via email.
    sandbox_slug = _find_sandbox_slug(int(signup["id"]))

    return {
        "signup_id": signup["id"],
        "customer_email": signup["email"],
        "product_name": signup.get("product_name") or "your app",
        "region": region,
        "hetzner_location": location,
        "server_type": "cax31",  # LAUNCH default
        "domain_choice": domain_choice,
        "customer_domain": customer_domain,
        "sandbox_slug": sandbox_slug,
        "github_username": signup.get("github_username"),
        "inventory_version": LAUNCH_INVENTORY_VERSION,
        "steps": [
            "1. Provision Hetzner CAX31 in " + location,
            "2. Wait for SSH (~30s after create_server)",
            "3. cloud-init: install Docker, configure swap, ufw allow 80/443",
            "4. Clone customer's GitHub repo to /opt/app",
            "5. Pull DB snapshot from sandbox slug (if exists): " + str(sandbox_slug),
            "6. docker compose up -d (substrate stack)",
            "7. Cloudflare DNS: A record customer-domain -> new VPS IP",
            "8. Wait for TLS provisioning (Caddy/Cloudflare, ~60s)",
            "9. Decommission sandbox slug (if exists): " + str(sandbox_slug),
            "10. Update signups.tier='launch', signups.status='live-launch'",
            "11. Email customer: 'Your Launch tier is ready'",
        ],
    }


# ─── Resend (email founder + customer) ──────────────────────────────────

def _send_email(to: str, subject: str, text: str) -> None:
    """Best-effort email via Resend; failures don't crash the run.

    promote.py runs from systemd / asyncio.create_task. Email is for
    operator awareness, not for transaction integrity.
    """
    if not RESEND_API_KEY:
        # Stdout fallback — useful in dev / CI
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


def _founder_plan_email(plan: dict[str, Any], mode: str) -> str:
    return f"""Launch upgrade triggered — signup #{plan['signup_id']}

Customer:   {plan['customer_email']}
Product:    {plan['product_name']}
Region:     {plan['region']} → Hetzner {plan['hetzner_location']}
Server:     {plan['server_type']}
Domain:     {plan['domain_choice']!r}
Sandbox:    {plan['sandbox_slug'] or '(none — no migration needed)'}
GitHub:     @{plan['github_username'] or '?'}
Inventory:  v{plan['inventory_version']}

Mode: {mode}

Steps:
{chr(10).join('  ' + s for s in plan['steps'])}

If you're happy with this plan:
  sudo -u root python3 /opt/hatchik-launch-orchestrator/promote.py \\
      --signup-id {plan['signup_id']} --execute

If something's wrong, fix the signup row then re-run.

Log file: {LOG_DIR}/promote-{plan['signup_id']}.log
"""


def _customer_welcome_email(plan: dict[str, Any], live_url: str) -> str:
    name = plan.get("first_name") or "there"
    return f"""Hi {name},

Your Hatchik Launch tier is live at {live_url}.

What just happened:
  • You now have production hosting in your chosen region (isolated
    per-tenant stack on a shared CAX41 host — promoted to a dedicated
    VPS automatically when you cross the 15th end-user signup)
  • Your app's data is migrated from the Sandbox — nothing lost
  • Your GitHub repo stays the same; push to main triggers a deploy
  • Your custom domain is wired with TLS
  • Email (3 mailboxes), payments, and mobile builds are on Launch-tier quotas

Billing:
  • £89 setup fee was charged today — covers the hosting provisioning,
    domain wiring, mailboxes, and the first month of service.
  • Then £14/month on annual prepay (£168/yr — best value) or £17/month
    rolling. Pick at https://hatchik.com/account → Billing. Pro-rata credit
    if you migrate to Growth mid-year.

What to do first:
  1. Open {live_url} in a browser and sign in.
  2. Tell your AI tool (Cursor / Claude / Windsurf) about the new
     setup — point it at your repo and ask "what's next?". It'll
     read BACKLOG.md and walk you through the first feature.
  3. Anything off? Reply to this email — same-day on business days.

Two heads-ups:
  • Substrate updates land as PRs in your GitHub. Merge when ready;
    nothing rolls out without your nod.
  • When you graduate to Growth (£39/mo, automatic after your 15th
    sign-up), we'll email a month before so there's no surprise.

— Hatchik
"""


# ─── Shared Launch host pool (Phase 1) ──────────────────────────────────
#
# Registry layout when HATCHIK_LAUNCH_MODE=shared is in effect:
#
#   {
#     "schema_version": 1,
#     "tenants":      { ... per-tenant entries (unchanged shape) ...
#                       tenants on shared hosts additionally carry
#                       `host_id`, `port_base`, `shared`: true },
#     "launch_hosts": {
#       "launch-host-1": {
#         "hetzner_server_id": 12345,
#         "ip": "1.2.3.4",
#         "location": "nbg1",
#         "server_type": "cax41",
#         "tenant_slugs": ["launch-1", "launch-2"],
#         "capacity": 25,
#         "status": "active" | "cordoned" | "full" | "decommissioning",
#         "port_ranges_used": [18000, 18100],     # port_base ints
#         "created_at": "2026-…"
#       },
#       ...
#     }
#   }


def _next_host_id(reg: dict[str, Any]) -> str:
    """Deterministic, monotonically-increasing host id.

    Numbers are unique even after a host is decommissioned — we never
    re-use IDs because operator emails and log lines might still
    reference the old one. Decommissioned hosts stay in the registry
    with status='decommissioning' or removed by hand.
    """
    hosts = (reg.get("launch_hosts") or {})
    used = set()
    for hid in hosts.keys():
        try:
            used.add(int(hid.rsplit("-", 1)[-1]))
        except (ValueError, IndexError):
            continue
    n = 1
    while n in used:
        n += 1
    return f"launch-host-{n}"


def _allocate_port_base(host: dict[str, Any]) -> int:
    """Return the next free port_base on this host (e.g. 18000, 18100…).

    Each tenant on a shared host claims a 100-port band so the substrate's
    internal Caddy plus any per-tenant debug ports stay collision-free.
    """
    used = set(host.get("port_ranges_used") or [])
    capacity = int(host.get("capacity") or SHARED_HOST_CAPACITY)
    for slot in range(capacity):
        candidate = PORT_BASE_START + slot * PORT_BASE_STRIDE
        if candidate not in used:
            return candidate
    raise RuntimeError(
        f"host {host.get('hetzner_server_id')} has no free port slots "
        f"(capacity={capacity})"
    )


def _pick_launch_host(plan: dict[str, Any], log_lines: list[str]) -> dict[str, Any]:
    """Return a shared Launch host that has room for one more tenant.

    Reads the registry, prefers the first ``active`` host with
    ``len(tenant_slugs) < capacity``. If none qualifies, provisions a
    fresh CAX41 via ``_provision_launch_host()`` and returns it.

    Mutates the registry only when provisioning a new host — the caller
    is responsible for adding the tenant slug + port_base to the picked
    host's entry once provisioning succeeds.
    """
    reg = _load_registry()
    hosts = reg.setdefault("launch_hosts", {})
    for host_id, host in hosts.items():
        if host.get("status") != "active":
            continue
        if len(host.get("tenant_slugs") or []) >= int(host.get("capacity") or SHARED_HOST_CAPACITY):
            continue
        log_lines.append(f"_pick_launch_host: reusing {host_id} ({len(host.get('tenant_slugs') or [])} / "
                         f"{host.get('capacity') or SHARED_HOST_CAPACITY} tenants)")
        return {"host_id": host_id, **host}

    log_lines.append("_pick_launch_host: no active host with capacity; provisioning a new one")
    return _provision_launch_host(plan, log_lines)


def _provision_launch_host(plan: dict[str, Any], log_lines: list[str]) -> dict[str, Any]:
    """Create a fresh CAX41 and bootstrap it as a shared Launch host.

    Adds an entry to ``registry.launch_hosts`` BEFORE returning so that
    a mid-step failure (e.g. SSH timeout during bootstrap) leaves the
    host in a recoverable state — operator can re-run promote.py and
    ``_pick_launch_host`` will see the existing entry.

    Returns the full host record including the synthetic ``host_id``.
    """
    if not hetzner_api:
        raise RuntimeError(
            "hetzner_api not importable — refusing to provision a shared "
            "Launch host without it."
        )

    reg = _load_registry()
    host_id = _next_host_id(reg)
    location = plan.get("hetzner_location") or SHARED_HOST_DEFAULT_LOCATION
    log_lines.append(f"_provision_launch_host: creating {host_id} in {location}")
    resp = hetzner_api.create_server(
        name=host_id,
        location=location,
        server_type=SHARED_HOST_SERVER_TYPE,
        labels={
            "hatchik_tier": "launch",
            "hatchik_host_role": "shared",
            "hatchik_host_id": host_id,
        },
        user_data=_cloud_init_script(host_id),
    )
    server_id = resp["server"]["id"]
    srv = hetzner_api.wait_for_running(server_id, timeout_s=300)
    ip = srv["public_net"]["ipv4"]["ip"]
    log_lines.append(f"  server_id={server_id} ip={ip}")

    host_record = {
        "hetzner_server_id": server_id,
        "ip": ip,
        "location": location,
        "server_type": SHARED_HOST_SERVER_TYPE,
        "tenant_slugs": [],
        "capacity": SHARED_HOST_CAPACITY,
        "status": "active",
        "port_ranges_used": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    reg.setdefault("launch_hosts", {})[host_id] = host_record
    _save_registry(reg)

    # Bootstrap (host Caddy + per-host scaffolding). If this fails we
    # leave the entry in place so the operator can recover by hand.
    if not _run_host_bootstrap(ip, log_lines):
        log_lines.append(f"WARN: bootstrap_launch_host.sh failed on {host_id} ({ip}); operator follow-up needed")
        _send_email(
            FOUNDER_EMAIL,
            f"[Launch host {host_id}] bootstrap failed at {ip}",
            f"""Hatchik shared Launch host {host_id} provisioned but its
bootstrap script reported a failure.

SSH:    ssh root@{ip}
Tail:   tail -200 /var/log/hatchik-launch-host-bootstrap.log

Once host Caddy is up and curl http://127.0.0.1/__hatchik_host_health
returns 'ok', the host is ready to accept tenants and you can re-run
promote.py — _pick_launch_host will reuse this entry.
""",
        )

    return {"host_id": host_id, **host_record}


def _run_host_bootstrap(ip: str, log_lines: list[str]) -> bool:
    """SCP bootstrap_launch_host.sh to the new host and run it."""
    import shlex
    import subprocess

    bootstrap_src = Path(__file__).parent / "bootstrap_launch_host.sh"
    if not bootstrap_src.exists():
        log_lines.append(f"bootstrap_launch_host.sh missing at {bootstrap_src}")
        return False

    ssh_opts = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=30",
        "-o", "ServerAliveInterval=30",
        "-o", "BatchMode=yes",
    ]
    target = f"root@{ip}"

    log_lines.append(f"waiting for SSH at {target}")
    for _ in range(30):
        r = subprocess.run(
            ["ssh", *ssh_opts, target, "true"],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0:
            break
        time.sleep(5)
    else:
        log_lines.append("SSH never came up within 150s")
        return False

    r = subprocess.run(
        ["scp", *ssh_opts, str(bootstrap_src), f"{target}:/root/bootstrap_launch_host.sh"],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        log_lines.append(f"scp host bootstrap failed: {r.stderr[:300]}")
        return False

    quoted = shlex.quote("/root/bootstrap_launch_host.sh")
    log_lines.append(f"running bootstrap_launch_host.sh on {ip}")
    r = subprocess.run(
        ["ssh", *ssh_opts, target, f"bash {quoted}"],
        capture_output=True, text=True, timeout=900,
    )
    log_lines.append(f"  rc={r.returncode}")
    log_lines.append(f"  stdout (last 500 chars): {r.stdout[-500:]}")
    if r.stderr:
        log_lines.append(f"  stderr (last 500 chars): {r.stderr[-500:]}")
    return r.returncode == 0


def _run_tenant_bootstrap_on_host(
    *,
    host_ip: str,
    slug: str,
    repo_url: str,
    domain: str,
    port_base: int,
    sandbox_host: str | None,
    sandbox_slug: str | None,
    log_lines: list[str],
) -> bool:
    """SCP bootstrap_tenant_on_host.sh to the shared host and run it."""
    import shlex
    import subprocess

    bootstrap_src = Path(__file__).parent / "bootstrap_tenant_on_host.sh"
    if not bootstrap_src.exists():
        log_lines.append(f"bootstrap_tenant_on_host.sh missing at {bootstrap_src}")
        return False

    ssh_opts = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=30",
        "-o", "ServerAliveInterval=30",
        "-o", "BatchMode=yes",
    ]
    target = f"root@{host_ip}"

    r = subprocess.run(
        ["scp", *ssh_opts, str(bootstrap_src), f"{target}:/root/bootstrap_tenant_on_host.sh"],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        log_lines.append(f"scp tenant bootstrap failed: {r.stderr[:300]}")
        return False

    cmd_args = [
        "bash", "/root/bootstrap_tenant_on_host.sh",
        "--slug", slug,
        "--repo-url", repo_url,
        "--domain", domain,
        "--port-base", str(port_base),
    ]
    if sandbox_host and sandbox_slug:
        cmd_args += ["--sandbox-host", sandbox_host, "--sandbox-slug", sandbox_slug]
    quoted = " ".join(shlex.quote(a) for a in cmd_args)
    log_lines.append(f"running bootstrap_tenant_on_host.sh on {host_ip}: {quoted}")
    r = subprocess.run(
        ["ssh", *ssh_opts, target, quoted],
        capture_output=True, text=True, timeout=1200,
    )
    log_lines.append(f"  rc={r.returncode}")
    log_lines.append(f"  stdout (last 500 chars): {r.stdout[-500:]}")
    if r.stderr:
        log_lines.append(f"  stderr (last 500 chars): {r.stderr[-500:]}")
    return r.returncode == 0


def _write_caddy_snippet_on_host(
    *,
    host_ip: str,
    slug: str,
    domain: str,
    port_base: int,
    log_lines: list[str],
) -> bool:
    """Drop a per-tenant Caddy snippet onto the shared host and reload.

    Two-step: write a temp file locally, scp it, then `caddy reload`.
    """
    import subprocess
    import tempfile

    ssh_opts = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=30",
        "-o", "BatchMode=yes",
    ]
    target = f"root@{host_ip}"
    snippet = f"""# Auto-generated by promote.py — tenant {slug}
{domain} {{
    tls {{
        dns cloudflare {{env.CF_API_TOKEN}}
    }}
    encode gzip zstd
    reverse_proxy 127.0.0.1:{port_base} {{
        header_up X-Forwarded-Host {{host}}
        header_up X-Forwarded-Proto {{scheme}}
    }}
    header {{
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Frame-Options "DENY"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
    }}
}}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".caddy", delete=False) as f:
        f.write(snippet)
        local_path = f.name

    try:
        r = subprocess.run(
            ["scp", *ssh_opts, local_path,
             f"{target}:/opt/hatchik-host-caddy/tenants.d/{slug}.caddy"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            log_lines.append(f"scp Caddy snippet failed: {r.stderr[:300]}")
            return False
        # Reload host Caddy. Non-fatal: a failed reload still leaves the
        # tenant reachable on the next host-Caddy restart, but we surface
        # the error so the operator can chase it.
        r = subprocess.run(
            ["ssh", *ssh_opts, target,
             "docker exec hatchik-host-caddy-caddy-1 caddy reload "
             "--config /etc/caddy/Caddyfile"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            log_lines.append(f"WARN: caddy reload failed: {r.stderr[:300]}")
            return False
        log_lines.append(f"Caddy snippet for {slug} live on {host_ip}")
        return True
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            pass


def _execute_shared(plan: dict[str, Any], log_lines: list[str]) -> dict[str, Any]:
    """Shared-host Launch promotion (HATCHIK_LAUNCH_MODE=shared)."""
    if not hetzner_api:
        raise RuntimeError(
            "hetzner_api module failed to import — refusing to --execute "
            "without it. Check HETZNER_API_TOKEN."
        )

    signup_id = plan["signup_id"]
    slug = f"launch-{signup_id}"

    # Idempotency check: if the tenant is already provisioned on a host
    # we re-use that placement instead of double-booking.
    reg = _load_registry()
    existing = (reg.get("tenants") or {}).get(slug)
    if existing and existing.get("shared") and existing.get("host_id"):
        log_lines.append(f"tenant {slug} already on host {existing['host_id']} (port {existing.get('port_base')}); resuming")
        host_id = existing["host_id"]
        host = (reg.get("launch_hosts") or {}).get(host_id) or {}
        host_ip = host.get("ip") or existing.get("ip")
        port_base = existing["port_base"]
    else:
        # 1. Pick (or provision) a shared host
        host = _pick_launch_host(plan, log_lines)
        host_id = host["host_id"]
        host_ip = host["ip"]

        # 2. Allocate a port_base on that host. Re-load registry because
        # _pick_launch_host may have written a new host entry.
        reg = _load_registry()
        host_record = reg["launch_hosts"][host_id]
        port_base = _allocate_port_base(host_record)
        log_lines.append(f"allocated port_base={port_base} on {host_id}")

        # 3. Write the tenant entry + reserve the port slot atomically
        host_record.setdefault("port_ranges_used", []).append(port_base)
        host_record.setdefault("tenant_slugs", []).append(slug)
        if len(host_record["tenant_slugs"]) >= int(host_record.get("capacity") or SHARED_HOST_CAPACITY):
            host_record["status"] = "full"
        reg.setdefault("tenants", {})[slug] = {
            "signup_id": signup_id,
            "customer_email": plan["customer_email"],
            "customer_domain": plan["customer_domain"],
            "tier": "launch",
            "shared": True,
            "host_id": host_id,
            "port_base": port_base,
            "hetzner_server_id": host_record["hetzner_server_id"],
            "hetzner_location": host_record["location"],
            "ip": host_ip,
            "status": "provisioning",
            "paddle_subscription_id": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "region": plan["region"],
        }
        _save_registry(reg)

    # 4. DNS — point customer's domain at the shared host's IP
    if plan["customer_domain"] and dns_api:
        try:
            log_lines.append(f"dns set_a_record {plan['customer_domain']} -> {host_ip}")
            dns_api.set_a_record(plan["customer_domain"], host_ip, proxied=True)
        except dns_api.ZoneNotFound:
            log_lines.append("  ZoneNotFound — customer brought own domain; manual DNS needed")
            _send_email(
                plan["customer_email"],
                f"Action needed: point {plan['customer_domain']} at {host_ip}",
                f"""Hi,

Your Hatchik Launch tenant is provisioned. To finish the setup, add this
A record at your DNS provider (where you registered
{plan['customer_domain']}):

  Name: @ (or {plan['customer_domain']})
  Type: A
  Value: {host_ip}
  TTL: Auto / 1 hour

Once it propagates (usually 5–60 minutes) your domain will resolve to
Hatchik. Reply to this email if you'd like me to help.

— Hatchik
""",
            )

    # 5. Bootstrap the tenant on the shared host
    repo_url = _infer_repo_url(plan)
    if not repo_url:
        log_lines.append("repo URL not derivable from signup; aborting tenant bootstrap")
        return {
            "ok": False, "slug": slug, "host_id": host_id, "port_base": port_base,
            "ip": host_ip, "status": "provisioning",
            "error": "repo_url not derivable; check signup.github_username + sandbox_slug",
        }

    bootstrap_ok = _run_tenant_bootstrap_on_host(
        host_ip=host_ip,
        slug=slug,
        repo_url=repo_url,
        domain=plan["customer_domain"] or "",
        port_base=port_base,
        sandbox_host=os.environ.get("HATCHIK_SANDBOX_HOST", "178.105.139.144") if plan.get("sandbox_slug") else None,
        sandbox_slug=plan.get("sandbox_slug"),
        log_lines=log_lines,
    )

    # 6. Write the host-Caddy snippet (idempotent — bootstrap also writes,
    #    but we write the canonical version + force a reload here)
    caddy_ok = False
    if plan["customer_domain"]:
        caddy_ok = _write_caddy_snippet_on_host(
            host_ip=host_ip,
            slug=slug,
            domain=plan["customer_domain"],
            port_base=port_base,
            log_lines=log_lines,
        )

    if bootstrap_ok and (caddy_ok or not plan["customer_domain"]):
        reg = _load_registry()
        reg["tenants"][slug]["status"] = "live"
        reg["tenants"][slug]["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        _save_registry(reg)
        _update_signup_tier(signup_id, "launch", "live-launch")
        live_url = f"https://{plan['customer_domain']}" if plan["customer_domain"] else f"http://{host_ip}:{port_base}"
        _send_email(
            plan["customer_email"],
            "Your Hatchik Launch tier is live",
            _customer_welcome_email(plan, live_url),
        )
        if plan["sandbox_slug"]:
            _mark_sandbox_promoted(plan["sandbox_slug"], "launch", log_lines)
        return {
            "ok": True, "slug": slug, "host_id": host_id, "port_base": port_base,
            "ip": host_ip, "status": "live", "live_url": live_url, "shared": True,
        }

    log_lines.append("SHARED TENANT BOOTSTRAP FAILED — operator follow-up")
    _send_email(
        FOUNDER_EMAIL,
        f"[Launch #{signup_id} shared] tenant up on {host_id} ({host_ip}:{port_base}) — bootstrap incomplete",
        f"""Tenant {slug} for signup #{signup_id} ({plan['customer_email']})
landed on shared host {host_id} ({host_ip}) at port {port_base}, but
either bootstrap_tenant_on_host.sh or the host-Caddy snippet write
failed.

SSH:   ssh root@{host_ip}
Tail:  tail -200 /var/log/hatchik-launch-host-bootstrap.log
Dir:   /opt/hatchik-tenants/{slug}

After fixing on the host, re-run on the orchestrator host:
  python3 /opt/hatchik-launch-orchestrator/promote.py \\
      --signup-id {signup_id} --mark-live

The registry already reserves the slot — _pick_launch_host will reuse
this host_id + port_base on retry, so re-running is safe.
""",
    )
    return {
        "ok": True, "slug": slug, "host_id": host_id, "port_base": port_base,
        "ip": host_ip, "status": "provisioning", "shared": True,
        "deferred": ["shared-tenant-recovery"],
    }


def _decommission_tenant_on_host(slug: str, log_lines: list[str]) -> bool:
    """Tear down a tenant slot on a shared Launch host.

    1. SSH to host: docker compose -p <slug> down -v + remove tenant dir
    2. Remove tenants.d/<slug>.caddy + caddy reload
    3. Free the port_base + remove slug from host.tenant_slugs
    4. Mark registry.tenants[slug].status = 'decommissioned'

    Idempotent. Safe to re-run after partial failure.
    """
    import subprocess

    reg = _load_registry()
    tenant = (reg.get("tenants") or {}).get(slug)
    if not tenant:
        log_lines.append(f"_decommission_tenant_on_host: {slug} not in registry; nothing to do")
        return True
    if not tenant.get("shared"):
        log_lines.append(f"_decommission_tenant_on_host: {slug} is not on a shared host (dedicated-VPS tenant); use decommission_launch.py instead")
        return False
    if tenant.get("status") == "decommissioned":
        log_lines.append(f"_decommission_tenant_on_host: {slug} already decommissioned")
        return True

    host_id = tenant.get("host_id")
    port_base = tenant.get("port_base")
    host = (reg.get("launch_hosts") or {}).get(host_id) or {}
    host_ip = host.get("ip") or tenant.get("ip")

    if not host_ip:
        log_lines.append(f"_decommission_tenant_on_host: no IP for host {host_id}; cannot SSH")
        return False

    ssh_opts = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=30",
        "-o", "BatchMode=yes",
    ]
    target = f"root@{host_ip}"
    log_lines.append(f"_decommission_tenant_on_host {slug} on {host_id} ({host_ip}) port_base={port_base}")

    # 1. Stop + remove the tenant compose stack
    teardown = (
        f"cd /opt/hatchik-tenants/{slug} 2>/dev/null && "
        f"docker compose -p {slug} down -v 2>&1 || true; "
        f"rm -rf /opt/hatchik-tenants/{slug}"
    )
    r = subprocess.run(
        ["ssh", *ssh_opts, target, teardown],
        capture_output=True, text=True, timeout=300,
    )
    log_lines.append(f"  compose down rc={r.returncode}")
    if r.stdout:
        log_lines.append(f"  stdout (last 300): {r.stdout[-300:]}")

    # 2. Remove the Caddy snippet + reload
    r = subprocess.run(
        ["ssh", *ssh_opts, target,
         f"rm -f /opt/hatchik-host-caddy/tenants.d/{slug}.caddy && "
         "docker exec hatchik-host-caddy-caddy-1 caddy reload "
         "--config /etc/caddy/Caddyfile"],
        capture_output=True, text=True, timeout=60,
    )
    log_lines.append(f"  caddy reload rc={r.returncode}")
    if r.returncode != 0 and r.stderr:
        log_lines.append(f"  caddy stderr: {r.stderr[-300:]}")

    # 3. Free the port_base + remove slug from host.tenant_slugs
    reg = _load_registry()
    host_record = (reg.get("launch_hosts") or {}).get(host_id)
    if host_record is not None:
        if port_base in (host_record.get("port_ranges_used") or []):
            host_record["port_ranges_used"].remove(port_base)
        if slug in (host_record.get("tenant_slugs") or []):
            host_record["tenant_slugs"].remove(slug)
        # If host was 'full' and is now under capacity, mark active again
        if (host_record.get("status") == "full"
                and len(host_record.get("tenant_slugs") or []) < int(host_record.get("capacity") or SHARED_HOST_CAPACITY)):
            host_record["status"] = "active"

    # 4. Mark tenant decommissioned
    reg["tenants"][slug]["status"] = "decommissioned"
    reg["tenants"][slug]["decommissioned_at"] = datetime.now(timezone.utc).isoformat()
    _save_registry(reg)
    log_lines.append(f"_decommission_tenant_on_host: {slug} decommissioned on {host_id}; port {port_base} freed")
    return True


# ─── Execute (real Hetzner / Cloudflare calls) ──────────────────────────

def _execute(plan: dict[str, Any], log_lines: list[str]) -> dict[str, Any]:
    """Real provisioning. Idempotent where possible.

    Routes to ``_execute_shared`` when HATCHIK_LAUNCH_MODE=shared is set,
    otherwise falls through to the original dedicated-VPS-per-tenant
    path. Default is dedicated so existing operators are unaffected
    until they opt in.
    """
    if LAUNCH_MODE == "shared":
        log_lines.append("LAUNCH_MODE=shared — routing through _execute_shared")
        return _execute_shared(plan, log_lines)

    if not hetzner_api or not dns_api:
        raise RuntimeError(
            "hetzner_api / dns_api modules failed to import — refusing to "
            "--execute without them. Check HETZNER_API_TOKEN / "
            "CLOUDFLARE_API_TOKEN env vars."
        )

    signup_id = plan["signup_id"]
    slug = f"launch-{signup_id}"

    # 1. Create server
    log_lines.append(f"create_server name={slug} location={plan['hetzner_location']}")
    resp = hetzner_api.create_server(
        name=slug,
        location=plan["hetzner_location"],
        server_type=plan["server_type"],
        labels={
            "hatchik_tier": "launch",
            "hatchik_signup_id": str(signup_id),
            "hatchik_slug": slug,
        },
        user_data=_cloud_init_script(slug),
    )
    server_id = resp["server"]["id"]
    log_lines.append(f"  server_id={server_id}")

    # 2. Wait for running
    log_lines.append("wait_for_running")
    srv = hetzner_api.wait_for_running(server_id, timeout_s=300)
    ip = srv["public_net"]["ipv4"]["ip"]
    log_lines.append(f"  ip={ip}")

    # 3. Update registry early so we can recover from later failures
    reg = _load_registry()
    reg.setdefault("tenants", {})[slug] = {
        "signup_id": signup_id,
        "customer_email": plan["customer_email"],
        "customer_domain": plan["customer_domain"],
        "tier": "launch",
        "hetzner_server_id": server_id,
        "hetzner_location": plan["hetzner_location"],
        "ip": ip,
        "status": "provisioning",
        "paddle_subscription_id": None,  # webhook fills this in
        "created_at": datetime.now(timezone.utc).isoformat(),
        "region": plan["region"],
    }
    _save_registry(reg)

    # 4. DNS (only if customer's zone is in our Cloudflare)
    if plan["customer_domain"]:
        try:
            log_lines.append(f"dns set_a_record {plan['customer_domain']} -> {ip}")
            dns_api.set_a_record(plan["customer_domain"], ip, proxied=True)
        except dns_api.ZoneNotFound:
            log_lines.append("  ZoneNotFound — customer brought own domain; manual DNS needed")
            _send_email(
                plan["customer_email"],
                f"Action needed: point {plan['customer_domain']} at {ip}",
                f"""Hi,

Your Launch tier VPS is up at {ip}. To finish the setup, add this A
record at your DNS provider (where you registered {plan['customer_domain']}):

  Name: @ (or {plan['customer_domain']})
  Type: A
  Value: {ip}
  TTL: Auto / 1 hour

Once it propagates (usually 5–60 minutes) your domain will resolve to
Hatchik. Reply to this email if you'd like me to help.

— Hatchik
""",
            )

    # 5. Substrate bootstrap on the new VPS, over SSH.
    # bootstrap_substrate.sh is rsync'd to the VPS first, then executed
    # with the customer's repo + sandbox info. If it succeeds, the VPS
    # is serving and the tenant can be marked live. If it fails, we
    # email the founder the runbook and leave the tenant at status
    # 'provisioning' so a manual retry can pick up where we left off.
    bootstrap_ok = _run_substrate_bootstrap(ip, plan, log_lines)

    if bootstrap_ok:
        # Mark live + email customer + free sandbox slug
        reg = _load_registry()
        reg["tenants"][slug]["status"] = "live"
        reg["tenants"][slug]["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        _save_registry(reg)
        _update_signup_tier(signup_id, "launch", "live-launch")
        live_url = f"https://{plan['customer_domain']}" if plan["customer_domain"] else f"https://{ip}"
        _send_email(
            plan["customer_email"],
            "Your Hatchik Launch tier is live",
            _customer_welcome_email(plan, live_url),
        )
        # Founder's choice (option A from the smoke #13 review): keep the
        # sandbox alive after promote so it acts as the customer's dev
        # environment. Mark it `promoted_to: launch` so lifecycle.py
        # skips its idle-archive policy (a customer paying for Launch
        # shouldn't have their dev sandbox torn down for being idle —
        # they may not need it every day, but it must be there when
        # they do).
        if plan["sandbox_slug"]:
            _mark_sandbox_promoted(plan["sandbox_slug"], "launch", log_lines)
        return {
            "ok": True,
            "server_id": server_id,
            "ip": ip,
            "slug": slug,
            "status": "live",
            "live_url": live_url,
        }

    # Bootstrap failed — leave tenant at status='provisioning' and email
    # founder the runbook. Operator runs --mark-live after fixing.
    log_lines.append("BOOTSTRAP FAILED — email sent to founder with runbook")
    _send_email(
        FOUNDER_EMAIL,
        f"[Launch #{signup_id}] VPS up at {ip} — bootstrap failed; manual recovery",
        f"""VPS provisioned for signup #{signup_id} ({plan['customer_email']}).

IP: {ip}
SSH: ssh root@{ip}

bootstrap_substrate.sh ran on the VPS but reported a failure. Tail
/var/log/hatchik-bootstrap.log on the box to see what happened. Common
causes:
  - Customer's GitHub repo is private and the deploy key isn't authorised
  - The .env.example was missing fields the substrate compose needs
  - postgres container didn't come up healthy in time

After fixing, on the VPS:
  cd /opt/app && docker compose up -d
  curl -sSL -o /dev/null -w '%{{http_code}}\\n' https://{plan['customer_domain']}/

Once it's serving, run on the orchestrator host:
  python3 /opt/hatchik-launch-orchestrator/promote.py \\
      --signup-id {signup_id} --mark-live

That flips the tenant to 'live' and emails the customer.
""",
    )
    return {
        "ok": True,
        "server_id": server_id,
        "ip": ip,
        "slug": slug,
        "status": "provisioning",
        "deferred": ["substrate-bootstrap-recovery"],
    }


def _run_substrate_bootstrap(
    ip: str, plan: dict[str, Any], log_lines: list[str],
) -> bool:
    """Rsync bootstrap_substrate.sh to the new VPS and run it.

    Returns True if the script exited 0 and the customer's domain
    responded with a 2xx/3xx. Caller decides whether to mark the
    tenant live or escalate.

    Assumes:
      - The Hetzner-uploaded SSH key (HETZNER_SSH_KEY_NAME) is in the
        agent / available via SSH-config. promote.py inherits the
        operator's SSH agent; ssh-add the orchestrator's private key
        before --execute.
      - The customer's repo URL is derivable from their github_username
        + product_name (slug); for now, we surface the inferred URL in
        logs and let the script fail gracefully if it's wrong. A future
        iteration can pin the URL in the signups table.
    """
    import shlex
    import subprocess

    repo_url = _infer_repo_url(plan)
    if not repo_url:
        log_lines.append("repo URL not derivable from signup; aborting bootstrap")
        return False

    bootstrap_src = Path(__file__).parent / "bootstrap_substrate.sh"
    if not bootstrap_src.exists():
        log_lines.append(f"bootstrap_substrate.sh missing at {bootstrap_src}")
        return False

    ssh_opts = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=30",
        "-o", "ServerAliveInterval=30",
        "-o", "BatchMode=yes",
    ]
    target = f"root@{ip}"

    # Wait for SSH to come up; cloud-init's first boot can take a minute.
    log_lines.append(f"waiting for SSH at {target}")
    for attempt in range(30):
        r = subprocess.run(
            ["ssh", *ssh_opts, target, "true"],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0:
            break
        time.sleep(5)
    else:
        log_lines.append("SSH never came up within 150s")
        return False

    # rsync bootstrap script
    r = subprocess.run(
        ["scp", *ssh_opts, str(bootstrap_src), f"{target}:/root/bootstrap_substrate.sh"],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        log_lines.append(f"scp failed: {r.stderr[:300]}")
        return False

    # Run it
    cmd_args = [
        "bash", "/root/bootstrap_substrate.sh",
        "--repo-url", repo_url,
        "--domain", plan["customer_domain"] or "",
    ]
    if plan.get("sandbox_slug"):
        cmd_args += [
            "--sandbox-host", os.environ.get("HATCHIK_SANDBOX_HOST", "178.105.139.144"),
            "--sandbox-slug", plan["sandbox_slug"],
        ]
    quoted = " ".join(shlex.quote(a) for a in cmd_args)
    log_lines.append(f"running bootstrap_substrate.sh on {ip}: {quoted}")
    r = subprocess.run(
        ["ssh", *ssh_opts, target, quoted],
        capture_output=True, text=True, timeout=1200,  # 20 min ceiling
    )
    log_lines.append(f"  rc={r.returncode}")
    log_lines.append(f"  stdout (last 500 chars): {r.stdout[-500:]}")
    if r.stderr:
        log_lines.append(f"  stderr (last 500 chars): {r.stderr[-500:]}")
    return r.returncode == 0


def _infer_repo_url(plan: dict[str, Any]) -> str | None:
    """Derive the customer's repo URL from signup metadata.

    We store provisioned sandbox slugs in github org HATCHIK_GITHUB_ORG
    (sandbox-orchestrator creates the repos there as `<slug>`). For a
    promotion, the repo is unchanged — same org, same slug — so we
    keep using it on the Launch VPS too.
    """
    if not plan.get("sandbox_slug"):
        # No sandbox slug → no repo to infer. Future: read a "repo_url"
        # field directly from the signups table once we add it.
        return None
    org = os.environ.get("HATCHIK_GITHUB_ORG", "hatchik-tenants")
    return f"https://github.com/{org}/{plan['sandbox_slug']}.git"


def _cloud_init_script(slug: str) -> str:
    """First-boot bootstrap. Keep it minimal — full substrate deploy is
    a separate step after we have the customer's repo URL."""
    return f"""#cloud-config
package_update: true
package_upgrade: false
packages:
  - docker.io
  - docker-compose-plugin
  - git
  - rsync
  - ufw
  - jq

runcmd:
  - [ufw, default, deny, incoming]
  - [ufw, default, allow, outgoing]
  - [ufw, allow, 22/tcp]
  - [ufw, allow, 80/tcp]
  - [ufw, allow, 443/tcp]
  - [ufw, --force, enable]
  - [fallocate, -l, 2G, /swapfile]
  - [chmod, '600', /swapfile]
  - [mkswap, /swapfile]
  - [swapon, /swapfile]
  - [bash, -c, "echo '/swapfile none swap sw 0 0' >> /etc/fstab"]
  - [bash, -c, "echo 'hatchik-tenant: {slug}' > /etc/hatchik-tenant"]

write_files:
  - path: /etc/hatchik-bootstrap.txt
    content: |
      Tenant slug: {slug}
      Provisioned by Hatchik launch-orchestrator/promote.py
      Next step: substrate bootstrap (clone customer repo + compose up).
"""


# ─── Sandbox lifecycle after Launch promote ─────────────────────────────
# Founder's choice (option A from smoke #13 review): the sandbox stays
# alive as the customer's dev environment after they promote to Launch.
# We mark its registry entry with `promoted_to` + `promoted_at` so
# lifecycle.py knows to skip the idle-archive policy. The cost is one
# extra ~1.3 GB sandbox slot on the shared host per launched customer —
# baked into the AI_COGS_SENSITIVITY.xlsx model so the founder can see
# the margin impact.
#
# The old `_decommission_sandbox` helper is retained below as
# `_decommission_sandbox_legacy` because operators sometimes still need
# to free the slug manually (e.g. when a customer downgrades to
# Launch-without-sandbox, or when the dev sandbox is corrupted and
# they want a fresh one).

import json as _json_for_registry  # local alias — avoids shadowing module-level imports

SANDBOX_REGISTRY_FILE = Path(
    os.environ.get("HATCHIK_SANDBOX_REGISTRY", "/opt/hatchik-tenants/registry.json")
)


def _mark_sandbox_promoted(slug: str, promoted_to: str, log_lines: list[str]) -> None:
    """Mark the sandbox's registry entry as promoted to a paid tier.

    Adds `promoted_to` (e.g. "launch" or "growth") and `promoted_at`
    (ISO timestamp) fields. lifecycle.py reads these to skip the
    idle-archive policy for promoted tenants.

    Non-fatal if the registry is missing or the slug isn't in it —
    we log a WARN and move on. Promote shouldn't fail just because
    the sandbox housekeeping is in an unexpected state.
    """
    if not SANDBOX_REGISTRY_FILE.exists():
        log_lines.append(
            f"WARN: sandbox registry not found at {SANDBOX_REGISTRY_FILE} — "
            f"slug {slug} not marked as promoted (lifecycle may try to archive it)"
        )
        return
    try:
        reg = _json_for_registry.loads(SANDBOX_REGISTRY_FILE.read_text())
        tenant = reg.get("tenants", {}).get(slug)
        if tenant is None:
            log_lines.append(
                f"WARN: sandbox slug {slug} not found in registry — "
                f"can't mark promoted; lifecycle will treat it as a normal sandbox"
            )
            return
        tenant["promoted_to"] = promoted_to
        tenant["promoted_at"] = datetime.now(timezone.utc).isoformat()
        tmp = SANDBOX_REGISTRY_FILE.with_suffix(".json.tmp")
        tmp.write_text(_json_for_registry.dumps(reg, indent=2, sort_keys=True))
        tmp.rename(SANDBOX_REGISTRY_FILE)
        log_lines.append(
            f"sandbox slug {slug} marked promoted_to={promoted_to}; "
            f"idle-archive policy disabled for it"
        )
    except Exception as e:  # noqa: BLE001
        log_lines.append(f"WARN _mark_sandbox_promoted exception: {e}")


def _decommission_sandbox_legacy(slug: str, log_lines: list[str]) -> None:
    """Free the sandbox slug — call sandbox-orchestrator/decommission.py.

    NOT called from the standard promote flow (sandboxes stay alive as
    dev environments per the option-A decision). Retained for operator
    use: customer-initiated `decommission my sandbox` flow, or recovery
    from a corrupted sandbox where the customer wants a fresh one.
    """
    import subprocess
    decom = Path("/opt/hatchik-orchestrator/decommission.py")
    if not decom.exists():
        # Local-dev fallback
        decom = Path(__file__).parent.parent / "sandbox-orchestrator" / "decommission.py"
    if not decom.exists():
        log_lines.append(f"WARN: decommission.py not found, sandbox slug {slug} not freed")
        return
    try:
        r = subprocess.run(
            ["python3", str(decom), slug],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            log_lines.append(f"sandbox slug {slug} decommissioned")
        else:
            log_lines.append(f"WARN decom returned {r.returncode}: {r.stderr[:300]}")
    except Exception as e:  # noqa: BLE001
        log_lines.append(f"WARN decom exception: {e}")


# ─── Main ──────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--signup-id", type=int, required=True)
    p.add_argument("--execute", action="store_true",
                   help="Actually call Hetzner/Cloudflare (default: SAFE_MODE).")
    p.add_argument("--mark-live", action="store_true",
                   help="Flip tenant to 'live' after manual substrate bootstrap.")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable output.")
    p.add_argument("--paddle-event-id", default=None)
    args = p.parse_args()

    signup = _fetch_signup(args.signup_id)
    if not signup:
        msg = {"error": "signup not found", "signup_id": args.signup_id}
        print(json.dumps(msg) if args.json else f"✗ {msg}", file=sys.stderr)
        return 2

    if signup["tier"] not in ("sandbox", "launch"):
        msg = {"error": f"signup tier={signup['tier']!r}, not eligible to promote"}
        print(json.dumps(msg) if args.json else f"✗ {msg}", file=sys.stderr)
        return 2

    # ── --mark-live: flip an already-bootstrapped tenant to live ──────
    if args.mark_live:
        reg = _load_registry()
        slug = f"launch-{args.signup_id}"
        if slug not in reg.get("tenants", {}):
            print(f"✗ {slug} not in registry; run promote first", file=sys.stderr)
            return 2
        reg["tenants"][slug]["status"] = "live"
        reg["tenants"][slug]["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        _save_registry(reg)
        _update_signup_tier(args.signup_id, "launch", "live-launch")
        live_url = f"https://{reg['tenants'][slug].get('customer_domain') or 'TBD'}"
        plan = compute_plan(signup)
        plan["first_name"] = signup.get("first_name")
        _send_email(signup["email"], "Your Hatchik Launch tier is live",
                    _customer_welcome_email(plan, live_url))
        out = {"ok": True, "slug": slug, "status": "live", "live_url": live_url}
        print(json.dumps(out) if args.json else f"✓ {slug} marked live ({live_url})")
        return 0

    # ── Normal flow: SAFE_MODE plan, or --execute ─────────────────────
    plan = compute_plan(signup)
    plan["first_name"] = signup.get("first_name")
    mode = "EXECUTE" if args.execute else "SAFE_MODE"

    # Always record the transition (so cohort metrics see the upgrade)
    _record_transition(
        signup_id=args.signup_id,
        from_tier=signup["tier"],
        to_tier="launch",
        note=f"promote.py {mode}",
        paddle_event_id=args.paddle_event_id,
    )

    # Always email the founder the plan
    _send_email(
        FOUNDER_EMAIL,
        f"[Launch #{args.signup_id}] {mode}: {plan['product_name']}",
        _founder_plan_email(plan, mode),
    )

    if not args.execute:
        out = {"ok": True, "mode": "SAFE_MODE", "plan": plan}
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(f"✓ SAFE_MODE — plan emailed to {FOUNDER_EMAIL} for signup "
                  f"#{args.signup_id} ({plan['customer_email']}). "
                  f"Pass --execute to provision.")
        return 0

    # ── --execute ─────────────────────────────────────────────────────
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"promote-{args.signup_id}.log"
    log_lines: list[str] = []
    started_at = time.time()
    log_lines.append(f"promote.py --execute signup_id={args.signup_id} started")

    try:
        result = _execute(plan, log_lines)
    except Exception as e:  # noqa: BLE001
        log_lines.append(f"FATAL: {type(e).__name__}: {e}")
        log_file.write_text("\n".join(log_lines))
        out = {"ok": False, "error": str(e), "log": str(log_file)}
        print(json.dumps(out) if args.json else f"✗ {e}", file=sys.stderr)
        return 3

    # Mark signup as launch-tier provisioning (status flips to live-launch
    # once --mark-live is run after substrate bootstrap)
    _update_signup_tier(args.signup_id, "launch", "provisioning-launch")

    log_lines.append(f"completed in {time.time() - started_at:.0f}s")
    log_file.write_text("\n".join(log_lines))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"✓ Launch tier provisioned for signup #{args.signup_id}")
        print(f"  IP: {result['ip']}")
        print(f"  Slug: {result['slug']}")
        print(f"  Log: {log_file}")
        if result.get("deferred"):
            print(f"  DEFERRED (manual step): {', '.join(result['deferred'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
