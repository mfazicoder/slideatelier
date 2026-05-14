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
  • You now have a dedicated VPS in your chosen region
  • Your app's data is migrated from the Sandbox — nothing lost
  • Your GitHub repo stays the same; push to main triggers a deploy
  • Your custom domain is wired with TLS
  • Email (5 mailboxes), payments, and mobile builds are on Launch-tier quotas

Billing:
  • £89 setup fee was charged today — covers the VPS provisioning,
    domain wiring, mailboxes, and the first month of hosting.
  • £14/month starts from month 2. Update card any time from
    https://hatchik.com/account → Billing.

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


# ─── Execute (real Hetzner / Cloudflare calls) ──────────────────────────

def _execute(plan: dict[str, Any], log_lines: list[str]) -> dict[str, Any]:
    """Real provisioning. Idempotent where possible."""
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
