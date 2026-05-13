#!/usr/bin/env python3
"""
restore.py — bring an archived sandbox back to life.

Counterpart to ``decommission.py``. Used by the admin CLI and by the
signup-service ``POST /api/admin/account/{slug}/restore`` endpoint
(subprocess-invoked).

Restore window is the {PURGE_DAYS_AFTER_ARCHIVE}-day grace period between
``lifecycle.py``'s archive and purge actions (typically days 30–37).
After purge the snapshots are gone and the customer must re-signup.

What it does:
    1. Reads ``/var/hatchik-archive/<slug>/manifest.json``
    2. Extracts ``tenant-dir.tar.gz`` back into /opt/hatchik-tenants/<slug>/
    3. For each archived volume, ``docker volume create`` + restore from
       its tar.gz snapshot
    4. Re-writes the per-tenant Caddy route + reloads host Caddy
    5. ``docker compose up -d`` in the restored tenant directory
    6. Waits for tenant health
    7. Mints a fresh GoTrue magic-link for the owner
    8. Sends a "your sandbox is back" email
    9. Marks registry status='live', clears archived_at, records
       restored_at, clears any lingering warning markers

Idempotent — if the tenant is already live, restore is a no-op (with a
warning).

Usage:
    restore.py <slug>            # restore by slug
    restore.py <slug> --no-email # skip the customer email
    restore.py <slug> --json     # machine-readable summary

Run as root on the sandbox host.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provision import (  # noqa: E402
    _load_env_file,
    supabase_jwt,
    wait_for_tenant_health,
    write_tenant_caddy_route,
)

_load_env_file()


SIGNUPS_DB = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
TENANTS_DIR = Path(os.environ.get("HATCHIK_TENANTS_DIR", "/opt/hatchik-tenants"))
HOST_CADDY_DIR = Path(os.environ.get("HATCHIK_HOST_CADDY_DIR", "/opt/hatchik-host-caddy"))
TENANTS_CADDY_D = HOST_CADDY_DIR / "tenants.d"
REGISTRY_FILE = TENANTS_DIR / "registry.json"
ARCHIVE_DIR = Path(os.environ.get("HATCHIK_ARCHIVE_DIR", "/var/hatchik-archive"))
HOST_CADDY_CONTAINER = os.environ.get("HATCHIK_HOST_CADDY_CONTAINER", "hatchik-host-caddy-caddy-1")

DOMAIN = "hatchik.com"

RESEND_API_URL = "https://api.resend.com/emails"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
HATCHIK_FROM_EMAIL = os.environ.get("HATCHIK_FROM_EMAIL", "noreply@hatchik.com")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hatchik-restore")


# ─── Registry helpers (kept local — lifecycle.py also has these) ──────────
def load_registry() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        return {"version": 1, "tenants": {}}
    return json.loads(REGISTRY_FILE.read_text())


def save_registry(reg: dict[str, Any]) -> None:
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2, sort_keys=True))
    tmp.rename(REGISTRY_FILE)


def update_tenant(slug: str, **fields: Any) -> None:
    reg = load_registry()
    if slug not in reg["tenants"]:
        return
    for key, val in fields.items():
        if val is None:
            reg["tenants"][slug].pop(key, None)
        else:
            reg["tenants"][slug][key] = val
    save_registry(reg)


def reload_host_caddy() -> None:
    subprocess.run(
        ["docker", "exec", HOST_CADDY_CONTAINER, "caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ─── Volume restore ───────────────────────────────────────────────────────
def restore_volume(volume: str, archive_path: Path) -> bool:
    """Recreate a docker volume and populate it from a tar.gz snapshot.

    Mirror of lifecycle.py:_save_volume — runs a throwaway alpine
    container that mounts the (empty) target volume and untars the
    snapshot into it.
    """
    if not archive_path.exists():
        log.error("snapshot %s missing — cannot restore volume %s", archive_path, volume)
        return False

    # Create the empty volume first (idempotent — `docker volume create`
    # is a no-op if it already exists).
    subprocess.run(
        ["docker", "volume", "create", volume],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    cmd = [
        "docker", "run", "--rm", "-i",
        "-v", f"{volume}:/to",
        "alpine:3.20",
        "tar", "xzf", "-", "-C", "/to",
    ]
    try:
        with archive_path.open("rb") as f:
            r = subprocess.run(cmd, stdin=f, stderr=subprocess.PIPE, timeout=600)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.error("volume restore %s errored: %s", volume, e)
        return False
    if r.returncode != 0:
        log.error("volume restore %s failed: %s", volume, r.stderr.decode(errors="replace")[:200])
        return False
    return True


# ─── Tenant dir restore ───────────────────────────────────────────────────
def restore_tenant_dir(archive_root: Path) -> bool:
    """Untar tenant-dir.tar.gz back into TENANTS_DIR.

    The snapshot was created with ``tar -C <parent> <slug>`` so it
    expands cleanly back to ``TENANTS_DIR/<slug>/...``.
    """
    archive_path = archive_root / "tenant-dir.tar.gz"
    if not archive_path.exists():
        log.error("tenant-dir.tar.gz missing in %s", archive_root)
        return False
    TENANTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["tar", "xzf", str(archive_path), "-C", str(TENANTS_DIR)],
            stderr=subprocess.PIPE, timeout=120,
        )
        if r.returncode != 0:
            log.error("tenant-dir restore failed: %s", r.stderr.decode(errors="replace")[:200])
            return False
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        log.error("tenant-dir restore errored: %s", e)
        return False


# ─── Magic-link minting (re-mints owner link after restore) ───────────────
def _read_tenant_jwt_secret(target: Path) -> str | None:
    env_file = target / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("JWT_SECRET="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def mint_owner_magic_link(slug: str, port: int, email: str) -> str | None:
    target = TENANTS_DIR / slug
    jwt_secret = _read_tenant_jwt_secret(target)
    if not jwt_secret:
        log.warning("no JWT_SECRET in restored tenant .env — cannot mint magic-link")
        return None
    exp = int(time.time()) + 60 * 60
    service_jwt = supabase_jwt(jwt_secret, "service_role", exp)
    headers = {
        "Authorization": f"Bearer {service_jwt}",
        "apikey": service_jwt,
        "Content-Type": "application/json",
    }
    base = f"http://127.0.0.1:{port}/auth/v1"
    site_url = f"https://{slug}.{DOMAIN}"
    try:
        r = httpx.post(
            f"{base}/admin/generate_link",
            headers=headers,
            json={
                "type": "magiclink",
                "email": email,
                "options": {"redirect_to": site_url + "/"},
            },
            timeout=15,
        )
        if r.status_code >= 400:
            log.warning("owner magic-link mint failed: %s %s", r.status_code, r.text[:200])
            return None
        data = r.json()
        return data.get("action_link") or data.get("properties", {}).get("action_link")
    except httpx.HTTPError as e:
        log.warning("owner magic-link mint errored: %s", e)
        return None


# ─── Email ────────────────────────────────────────────────────────────────
def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def send_restored_email(to: str, first_name: str, slug: str, product_name: str, magic_link: str | None) -> bool:
    if not RESEND_API_KEY:
        log.warning("no RESEND_API_KEY — skipping restore email to %s", to)
        return False
    url = f"https://{slug}.{DOMAIN}"
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    cta_label = "Sign in to your sandbox" if magic_link else "Open your sandbox"
    cta_link = magic_link or url

    text = f"""{greeting}

Good news — your {product_name} sandbox is back at {url}.

Everything's where you left it: database, users, files. Sign in with
the link below to pick up where you left off.

{cta_label}: {cta_link}

The 30-day idle clock has reset, so you've got a fresh month of quiet
time before we'd next consider archiving.

— Hatchik

(This is an automated message — please don't reply.)
"""

    body_html = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_html_escape(product_name)} sandbox is back</title>
</head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:8px;padding:32px;">
          <tr>
            <td style="font-size:16px;line-height:1.6;color:#1a1a1a;">
              <p style="margin:0 0 16px 0;">{_html_escape(greeting)}</p>
              <p style="margin:0 0 16px 0;">Good news &mdash; your <strong>{_html_escape(product_name)}</strong> sandbox is back at <a href="{url}" style="color:#4f46e5;text-decoration:underline;">{_html_escape(url)}</a>.</p>
              <p style="margin:0 0 16px 0;">Everything&rsquo;s where you left it: database, users, files. Sign in with the link below to pick up where you left off.</p>
              <p style="margin:24px 0;"><a href="{cta_link}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;">{_html_escape(cta_label)} &rarr;</a></p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">The 30-day idle clock has reset, so you&rsquo;ve got a fresh month of quiet time before we&rsquo;d next consider archiving.</p>
              <p style="margin:24px 0 0 0;">&mdash; Hatchik</p>
              <p style="margin:24px 0 0 0;color:#888;font-size:12px;">This is an automated message &mdash; please don&rsquo;t reply.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    try:
        r = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": HATCHIK_FROM_EMAIL,
                "to": [to],
                "subject": f"Your {product_name} sandbox is back",
                "text": text,
                "html": body_html,
            },
            timeout=10,
        )
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        log.error("restore email to %s failed: %s", to, e)
        return False


# ─── Signup row ───────────────────────────────────────────────────────────
def fetch_signup_first_name(email: str) -> str:
    if not SIGNUPS_DB.exists() or not email:
        return ""
    try:
        conn = sqlite3.connect(SIGNUPS_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT first_name FROM signups WHERE LOWER(email) = ? ORDER BY id DESC LIMIT 1",
            (email.lower(),),
        ).fetchone()
        conn.close()
        return (row["first_name"] if row and row["first_name"] else "") or ""
    except sqlite3.Error:
        return ""


def mark_signup_status(signup_id: int, status: str) -> None:
    if not signup_id:
        return
    try:
        conn = sqlite3.connect(SIGNUPS_DB)
        conn.execute("UPDATE signups SET status = ? WHERE id = ?", (status, signup_id))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        log.warning("signup #%s status update failed: %s", signup_id, e)


# ─── Restore ──────────────────────────────────────────────────────────────
def restore(slug: str, *, no_email: bool = False, verbose: bool = True) -> dict[str, Any]:
    log_fn = log.info if verbose else (lambda *a, **k: None)
    summary: dict[str, Any] = {"slug": slug, "steps": []}

    reg = load_registry()
    tenant = reg["tenants"].get(slug)
    if not tenant:
        raise SystemExit(f"no tenant '{slug}' in registry — cannot restore")

    if tenant.get("status") == "live":
        log_fn("%s is already live — restore is a no-op", slug)
        summary["status"] = "already-live"
        return summary

    if tenant.get("status") not in ("archived",):
        log_fn("%s status=%s — only 'archived' tenants can be restored", slug, tenant.get("status"))
        summary["status"] = "wrong-status"
        summary["error"] = f"status={tenant.get('status')}"
        return summary

    archive_root = ARCHIVE_DIR / slug
    manifest_path = archive_root / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"archive manifest missing at {manifest_path} — cannot restore (already purged?)")

    manifest = json.loads(manifest_path.read_text())
    port = int(manifest.get("port") or tenant.get("port") or 0)
    email = manifest.get("email") or tenant.get("email") or ""
    product_name = manifest.get("product_name") or tenant.get("product_name") or "your sandbox"
    signup_id = int(manifest.get("signup_id") or tenant.get("signup_id") or 0)
    volumes = list(manifest.get("volumes") or [])

    target = TENANTS_DIR / slug

    log_fn("→ restoring %s (port %s, %d volumes)", slug, port, len(volumes))

    # 1. Tenant dir
    if target.exists():
        log_fn("  tenant dir already present — leaving in place")
    else:
        log_fn("  restoring tenant-dir.tar.gz -> %s", target)
        if not restore_tenant_dir(archive_root):
            raise SystemExit("tenant-dir restore failed — aborting")
        summary["steps"].append("tenant-dir-restored")

    # 2. Volumes
    for vol in volumes:
        archive_path = archive_root / f"{vol}.tar.gz"
        log_fn("  restoring volume %s from %s", vol, archive_path)
        if not restore_volume(vol, archive_path):
            raise SystemExit(f"volume {vol} restore failed — aborting")
        summary["steps"].append(f"volume-restored:{vol}")

    # 3. Caddy route (we still have the port from the manifest; the
    #    original assignment was sticky and no other tenant should have
    #    claimed it while we were archived, but if someone did, the
    #    docker compose `up` will fail loud and the admin can pick a new
    #    port manually).
    log_fn("  writing tenant Caddy route -> :%s", port)
    write_tenant_caddy_route(slug, port)
    summary["steps"].append("caddy-route-written")

    # 4. Bring containers back up
    log_fn("  docker compose up -d")
    r = subprocess.run(
        ["docker", "compose", "-f", str(target / "docker-compose.yml"), "up", "-d"],
        cwd=str(target),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )
    if r.returncode != 0:
        log.error("docker compose up failed: %s", r.stdout.decode(errors="replace")[:500])
        raise SystemExit("compose up failed — see log")
    summary["steps"].append("compose-up")

    log_fn("  waiting for tenant health on :%s", port)
    if not wait_for_tenant_health(port):
        raise SystemExit(f"tenant did not become healthy on :{port} within timeout")
    summary["steps"].append("health-ok")

    log_fn("  reload host Caddy")
    reload_host_caddy()
    summary["steps"].append("caddy-reload")

    # 5. Mint a fresh magic-link
    magic = mint_owner_magic_link(slug, port, email)
    if magic:
        summary["steps"].append("magic-link-minted")
    else:
        log.warning("magic-link mint failed — restore email will use the bare URL")

    # 6. Mark registry live + clear archive markers
    now_iso = datetime.now(timezone.utc).isoformat()
    update_tenant(
        slug,
        status="live",
        restored_at=now_iso,
        archived_at=None,
        archive_warning_23_at=None,
        archive_warning_29_at=None,
    )
    summary["steps"].append("registry-live")

    # 7. Signup row back to 'new' so /api/account/me + the dashboard
    #    don't keep showing "archived_purged". Use 'restored' as the
    #    status — explicit, queryable, doesn't lose the history.
    mark_signup_status(signup_id, "restored")

    # 8. Email the customer
    if not no_email:
        first_name = fetch_signup_first_name(email)
        if send_restored_email(email, first_name, slug, product_name, magic):
            summary["steps"].append("restore-email-sent")
        else:
            summary["steps"].append("restore-email-failed")

    summary["status"] = "restored"
    summary["magic_link_minted"] = bool(magic)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Restore an archived Hatchik sandbox.")
    ap.add_argument("slug", help="Tenant slug to restore")
    ap.add_argument("--no-email", action="store_true", help="Don't send the customer email")
    ap.add_argument("--quiet", action="store_true", help="Suppress per-step output")
    ap.add_argument("--json", action="store_true", help="Print summary JSON")
    args = ap.parse_args()

    summary = restore(args.slug, no_email=args.no_email, verbose=not args.quiet)
    if args.json:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
