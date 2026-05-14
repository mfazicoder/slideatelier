#!/usr/bin/env python3
"""
lifecycle.py — daily idle-archive reconciler for Hatchik sandboxes.

Customer-facing copy on hatchik.com promises "archived if idle 7 days".
This script enforces that. Runs once a day from a systemd timer
(``hatchik-lifecycle.timer``). For each live tenant in registry.json:

    - day 5: send a polite warning email (sign in to keep it)
    - day 6: send a firmer reminder (last chance, magic-link inside)
    - day 7: archive — stop containers, snapshot volumes to
              /var/hatchik-archive/<slug>/, drop the Caddy route, mark
              registry status='archived', send archival notice
    - day 14: hard-delete — remove the volume snapshots + tenant dir
              entirely, mark registry status='purged' and the signup
              row status='archived_purged', send final-purge notice

"Idle" = no Supabase auth activity in the tenant. Computed by querying
the tenant's Postgres for max(last_sign_in_at) on auth.users, falling
back to max(created_at) if no one ever signed in.

Idempotent — safe to run multiple times per day. Sent-email markers
live in the tenant's registry entry (archive_warning_23_at,
archive_warning_29_at, archived_at, purged_at — names kept stable for
registry compatibility, but they fire at the new warn1/warn2 day
boundaries) so we never double-send.

Defensive — if the per-tenant Postgres query fails, log and skip that
tenant. One sick container should not block the reconciler.

Run as root on the sandbox host.

Usage:
    lifecycle.py                       # run reconcile
    lifecycle.py --dry-run             # log decisions but make no changes
    lifecycle.py --slug <slug>         # only consider one tenant (testing)
    lifecycle.py --json                # print summary JSON

Env overrides (for testing):
    HATCHIK_LIFECYCLE_FAKE_NOW=2026-06-12T02:00:00Z
        Pretend "now" is this ISO instant. Lets you walk a tenant
        forward through the 5/6/7/14 day boundaries without waiting.
    HATCHIK_LIFECYCLE_WARN1_DAY=5
    HATCHIK_LIFECYCLE_WARN2_DAY=6
    HATCHIK_LIFECYCLE_ARCHIVE_DAY=7
    HATCHIK_LIFECYCLE_PURGE_DAYS_AFTER_ARCHIVE=7
        Override the day boundaries (handy when collapsing the 7-day
        cycle to 30-second intervals for end-to-end tests).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

# Reuse provision.py helpers — same .env loader, same JWT helper, same
# magic-link minting. Keeps the lifecycle config in one place.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from provision import (  # noqa: E402 — sys.path edit above is intentional
    _load_env_file,
    supabase_jwt,
)

_load_env_file()


# ─── Paths + config (mirror provision.py) ─────────────────────────────────
SIGNUPS_DB = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
TENANTS_DIR = Path(os.environ.get("HATCHIK_TENANTS_DIR", "/opt/hatchik-tenants"))
HOST_CADDY_DIR = Path(os.environ.get("HATCHIK_HOST_CADDY_DIR", "/opt/hatchik-host-caddy"))
TENANTS_CADDY_D = HOST_CADDY_DIR / "tenants.d"
REGISTRY_FILE = TENANTS_DIR / "registry.json"
ARCHIVE_DIR = Path(os.environ.get("HATCHIK_ARCHIVE_DIR", "/var/hatchik-archive"))
HOST_CADDY_CONTAINER = os.environ.get("HATCHIK_HOST_CADDY_CONTAINER", "hatchik-host-caddy-caddy-1")

DOMAIN = "hatchik.com"

# Day boundaries — overridable for testing.
WARN1_DAY = int(os.environ.get("HATCHIK_LIFECYCLE_WARN1_DAY", "5"))
WARN2_DAY = int(os.environ.get("HATCHIK_LIFECYCLE_WARN2_DAY", "6"))
ARCHIVE_DAY = int(os.environ.get("HATCHIK_LIFECYCLE_ARCHIVE_DAY", "7"))
PURGE_DAYS_AFTER_ARCHIVE = int(os.environ.get("HATCHIK_LIFECYCLE_PURGE_DAYS_AFTER_ARCHIVE", "7"))

# Resend
RESEND_API_URL = "https://api.resend.com/emails"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
HATCHIK_FROM_EMAIL = os.environ.get("HATCHIK_FROM_EMAIL", "noreply@hatchik.com")
HATCHIK_FOUNDER_EMAIL = os.environ.get("HATCHIK_FOUNDER_EMAIL", "appmanager@namaasol.com")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("hatchik-lifecycle")


# ─── Time ─────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    """Return "now" — honours HATCHIK_LIFECYCLE_FAKE_NOW for tests."""
    fake = os.environ.get("HATCHIK_LIFECYCLE_FAKE_NOW", "").strip()
    if fake:
        # Accept either trailing Z or explicit offset.
        if fake.endswith("Z"):
            fake = fake[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(fake).astimezone(timezone.utc)
        except ValueError:
            log.warning("Invalid HATCHIK_LIFECYCLE_FAKE_NOW=%r — falling back to wall clock", fake)
    return datetime.now(timezone.utc)


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


# ─── Registry ─────────────────────────────────────────────────────────────
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
    """Atomic field update on a single tenant entry. Reloads from disk
    before writing so concurrent reconciler runs don't clobber each
    other (belt-and-braces — the timer is daily but admins may also
    invoke manually)."""
    reg = load_registry()
    if slug not in reg["tenants"]:
        return
    reg["tenants"][slug].update(fields)
    save_registry(reg)


# ─── Activity probe ───────────────────────────────────────────────────────
def query_last_activity(slug: str) -> datetime | None:
    """Return the most recent auth activity timestamp for a tenant.

    Greatest of:
      - max(last_sign_in_at) across all auth.users rows
      - max(created_at)      across all auth.users rows

    Falling back to created_at means the owner's pre-creation
    (provision.py admin/users call) still counts as "activity" — so a
    sandbox that has only ever been pre-provisioned still has the full
    30-day clock from creation, not "instant archive because nobody
    signed in".

    Returns None on any failure (container down, table missing, etc.) —
    caller treats None as "skip this tenant".
    """
    container = f"{slug}-postgres-1"
    sql = (
        "SELECT GREATEST("
        "COALESCE(MAX(last_sign_in_at), '1970-01-01'::timestamptz), "
        "COALESCE(MAX(created_at),      '1970-01-01'::timestamptz)"
        ") FROM auth.users"
    )
    try:
        r = subprocess.run(
            [
                "docker", "exec", container,
                "psql", "-U", "postgres", "-d", "postgres",
                "-tAc", sql,
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("activity probe for %s timed out/failed: %s", slug, e)
        return None
    if r.returncode != 0:
        log.warning("activity probe for %s exited %s: %s", slug, r.returncode, r.stderr.strip()[:200])
        return None
    out = r.stdout.strip()
    if not out or out == "(0 rows)":
        return None
    return parse_iso(out)


# ─── Magic-link minting ───────────────────────────────────────────────────
def _read_tenant_jwt_secret(target: Path) -> str | None:
    env_file = target / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("JWT_SECRET="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def mint_magic_link(slug: str, port: int, email: str) -> str | None:
    """Mint a fresh GoTrue magic-link for the tenant owner.

    Mirrors provision.py:provision_owner_user but assumes the user
    already exists (provisioning created them). Returns None on
    failure; caller falls back to the bare sandbox URL.
    """
    target = TENANTS_DIR / slug
    jwt_secret = _read_tenant_jwt_secret(target)
    if not jwt_secret:
        log.warning("no JWT_SECRET in %s/.env — cannot mint magic-link", target)
        return None
    exp = int(time.time()) + 60 * 60  # 1h
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
            log.warning("magic-link mint failed for %s: %s %s", slug, r.status_code, r.text[:200])
            return None
        data = r.json()
        return data.get("action_link") or data.get("properties", {}).get("action_link")
    except httpx.HTTPError as e:
        log.warning("magic-link mint errored for %s: %s", slug, e)
        return None


# ─── Email ────────────────────────────────────────────────────────────────
def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def _send(subject: str, to: str, text: str, html: str) -> bool:
    if not RESEND_API_KEY:
        log.warning("no RESEND_API_KEY — skipping email to %s (%r)", to, subject)
        return False
    try:
        r = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": HATCHIK_FROM_EMAIL,
                "to": [to],
                "subject": subject,
                "text": text,
                "html": html,
            },
            timeout=10,
        )
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        log.error("email to %s failed (%r): %s", to, subject, e)
        return False


def _shell(greeting_html: str, body_html: str, title: str) -> str:
    """Wrap inner content in the brand-consistent beige/white email shell."""
    return f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_html_escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:8px;padding:32px;">
          <tr>
            <td style="font-size:16px;line-height:1.6;color:#1a1a1a;">
              <p style="margin:0 0 16px 0;">{greeting_html}</p>
              {body_html}
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


def send_warn1_email(to: str, first_name: str, slug: str, product_name: str, magic_link: str | None) -> bool:
    """Day-23 polite warning — soft tone, "we'll archive in a week"."""
    url = f"https://{slug}.{DOMAIN}"
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    cta_label = "Sign in to your sandbox" if magic_link else "Open your sandbox"
    cta_link = magic_link or url

    text = f"""{greeting}

Your {product_name} sandbox at {url} has been quiet for {WARN1_DAY} days.

We archive sandboxes after {ARCHIVE_DAY} idle days to keep things tidy.
Sign in any time in the next week to keep it active — the clock resets
the moment you do.

{cta_label}: {cta_link}

If you've finished with this sandbox, no action needed — we'll archive
it for you on day {ARCHIVE_DAY}, and you'll have a further {PURGE_DAYS_AFTER_ARCHIVE} days to
restore it if you change your mind.

Further information can be found at https://hatchik.com/#faq.

— Hatchik

(This is an automated message — please don't reply.)
"""

    body_html = f"""\
              <p style="margin:0 0 16px 0;">Your <strong>{_html_escape(product_name)}</strong> sandbox at <a href="{url}" style="color:#4f46e5;text-decoration:underline;">{_html_escape(url)}</a> has been quiet for {WARN1_DAY} days.</p>
              <p style="margin:0 0 16px 0;">We archive sandboxes after {ARCHIVE_DAY} idle days to keep things tidy. Sign in any time in the next week to keep it active &mdash; the clock resets the moment you do.</p>
              <p style="margin:24px 0;"><a href="{cta_link}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;">{_html_escape(cta_label)} &rarr;</a></p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">If you&rsquo;ve finished with this sandbox, no action needed &mdash; we&rsquo;ll archive it for you on day {ARCHIVE_DAY}, and you&rsquo;ll have a further {PURGE_DAYS_AFTER_ARCHIVE} days to restore it if you change your mind.</p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">Further information can be found <a href="https://{DOMAIN}/#faq" style="color:#4f46e5;text-decoration:underline;">here</a>.</p>
"""

    html = _shell(_html_escape(greeting), body_html, f"Your {product_name} sandbox has been quiet")
    return _send(f"Your {product_name} sandbox is heading for archive", to, text, html)


def send_warn2_email(to: str, first_name: str, slug: str, product_name: str, magic_link: str | None) -> bool:
    """Day-29 firmer reminder — "tomorrow we archive"."""
    url = f"https://{slug}.{DOMAIN}"
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    cta_label = "Sign in to your sandbox" if magic_link else "Open your sandbox"
    cta_link = magic_link or url

    text = f"""{greeting}

Tomorrow we'll archive your {product_name} sandbox at {url}.

Sign in today if you want to keep building — a single sign-in resets
the clock by another {ARCHIVE_DAY} days.

{cta_label}: {cta_link}

If you'd rather we archived it, you don't need to do anything. We'll
keep a snapshot for {PURGE_DAYS_AFTER_ARCHIVE} days after that in case you change your mind,
then it's permanently deleted.

— Hatchik

(This is an automated message — please don't reply.)
"""

    body_html = f"""\
              <p style="margin:0 0 16px 0;">Tomorrow we&rsquo;ll archive your <strong>{_html_escape(product_name)}</strong> sandbox at <a href="{url}" style="color:#4f46e5;text-decoration:underline;">{_html_escape(url)}</a>.</p>
              <p style="margin:0 0 16px 0;">Sign in today if you want to keep building &mdash; a single sign-in resets the clock by another {ARCHIVE_DAY} days.</p>
              <p style="margin:24px 0;"><a href="{cta_link}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;">{_html_escape(cta_label)} &rarr;</a></p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">If you&rsquo;d rather we archived it, you don&rsquo;t need to do anything. We&rsquo;ll keep a snapshot for {PURGE_DAYS_AFTER_ARCHIVE} days after that in case you change your mind, then it&rsquo;s permanently deleted.</p>
"""

    html = _shell(_html_escape(greeting), body_html, f"Your {product_name} sandbox archives tomorrow")
    return _send(f"Tomorrow: your {product_name} sandbox archives", to, text, html)


def send_archived_email(to: str, first_name: str, slug: str, product_name: str, archive_expiry: datetime) -> bool:
    """Day-30 archival notice — "restore by email"."""
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    expiry_str = archive_expiry.strftime("%-d %B %Y")
    restore_url = f"https://{DOMAIN}/restore-sandbox"

    text = f"""{greeting}

Your {product_name} sandbox has been archived.

Your data is safe. We're keeping a snapshot for {PURGE_DAYS_AFTER_ARCHIVE} days so you can
restore the sandbox if you change your mind — after {expiry_str},
the snapshot is permanently deleted.

To restore, either:
  - reply to this email, or
  - fill the form at {restore_url}

An admin will revive the sandbox within a working day and email you a
fresh sign-in link.

If you'd rather start fresh later, you can sign up again any time at
https://{DOMAIN}.

— Hatchik

(This is an automated message — please don't reply.)
"""

    body_html = f"""\
              <p style="margin:0 0 16px 0;">Your <strong>{_html_escape(product_name)}</strong> sandbox has been archived.</p>
              <p style="margin:0 0 16px 0;">Your data is safe. We&rsquo;re keeping a snapshot for {PURGE_DAYS_AFTER_ARCHIVE} days so you can restore the sandbox if you change your mind &mdash; after <strong>{_html_escape(expiry_str)}</strong>, the snapshot is permanently deleted.</p>
              <p style="margin:24px 0;"><a href="{restore_url}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;">Restore my sandbox &rarr;</a></p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">Or simply reply to this email and an admin will revive it within a working day, then send you a fresh sign-in link.</p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">If you&rsquo;d rather start fresh later, you can sign up again any time at <a href="https://{DOMAIN}" style="color:#4f46e5;text-decoration:underline;">{DOMAIN}</a>.</p>
"""

    html = _shell(_html_escape(greeting), body_html, f"{product_name} sandbox archived")
    return _send(f"Your {product_name} sandbox has been archived", to, text, html)


def send_purged_email(to: str, first_name: str, product_name: str) -> bool:
    """Day-37 final-purge notice — "data permanently deleted"."""
    greeting = f"Hi {first_name}," if first_name else "Hi,"

    text = f"""{greeting}

Your {product_name} sandbox data has now been permanently deleted.

We hope it was useful. You can sign up again any time at
https://{DOMAIN} — it takes about five minutes.

— Hatchik

(This is an automated message — please don't reply.)
"""

    body_html = f"""\
              <p style="margin:0 0 16px 0;">Your <strong>{_html_escape(product_name)}</strong> sandbox data has now been permanently deleted.</p>
              <p style="margin:0 0 16px 0;">We hope it was useful. You can sign up again any time at <a href="https://{DOMAIN}" style="color:#4f46e5;text-decoration:underline;">{DOMAIN}</a> &mdash; it takes about five minutes.</p>
"""

    html = _shell(_html_escape(greeting), body_html, f"{product_name} sandbox deleted")
    return _send(f"Your {product_name} sandbox has been deleted", to, text, html)


# ─── Signup row lookup ────────────────────────────────────────────────────
def fetch_signup_for_email(email: str) -> dict[str, Any] | None:
    """Return (id, first_name, product_name) for the most recent signup
    matching this email. Used to greet customers by name in lifecycle
    emails even though the registry only stores plain ``email``."""
    if not SIGNUPS_DB.exists():
        return None
    try:
        conn = sqlite3.connect(SIGNUPS_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, first_name, product_name FROM signups "
            "WHERE LOWER(email) = ? ORDER BY id DESC LIMIT 1",
            (email.lower(),),
        ).fetchone()
        conn.close()
    except sqlite3.Error as e:
        log.warning("signup lookup for %s failed: %s", email, e)
        return None
    return dict(row) if row else None


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


# ─── Archive (day 30) ─────────────────────────────────────────────────────
def reload_host_caddy() -> None:
    subprocess.run(
        ["docker", "exec", HOST_CADDY_CONTAINER, "caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _list_tenant_volumes(slug: str, target: Path) -> list[str]:
    """Return docker volume names belonging to a tenant.

    The compose project name defaults to the directory name (``<slug>``),
    so volumes are ``<slug>_postgres-data``, ``<slug>_storage-data``,
    etc. We list real volumes via ``docker volume ls`` and filter by
    prefix so we capture whichever ones the substrate actually creates,
    not a hardcoded list that may drift.
    """
    try:
        r = subprocess.run(
            ["docker", "volume", "ls", "--format", "{{.Name}}", "--filter", f"name={slug}_"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return []
        return [line.strip() for line in r.stdout.splitlines() if line.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _save_volume(volume: str, dest: Path) -> bool:
    """Snapshot a docker volume to a tar.gz on disk.

    ``docker volume save`` doesn't exist; the canonical idiom is to run
    a throwaway alpine container that mounts the volume and tars its
    contents to stdout. Output redirects to ``dest``.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{volume}:/from:ro",
        "alpine:3.20",
        "tar", "czf", "-", "-C", "/from", ".",
    ]
    try:
        with dest.open("wb") as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=600)
        if r.returncode != 0:
            log.error("volume snapshot %s -> %s failed: %s", volume, dest, r.stderr.decode(errors="replace")[:200])
            dest.unlink(missing_ok=True)
            return False
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        log.error("volume snapshot %s -> %s errored: %s", volume, dest, e)
        dest.unlink(missing_ok=True)
        return False


def archive_tenant(slug: str, tenant: dict[str, Any], dry_run: bool = False) -> bool:
    """Day-30 archival. Returns True on success."""
    log.info("archiving %s …", slug)
    if dry_run:
        log.info("  [dry-run] would stop containers, snapshot volumes, remove Caddy route")
        return True

    target = TENANTS_DIR / slug
    route = TENANTS_CADDY_D / f"{slug}.caddy"
    archive_root = ARCHIVE_DIR / slug
    archive_root.mkdir(parents=True, exist_ok=True)

    # 1. Stop containers (NOT down -v — we need the volumes intact to snapshot).
    if target.exists():
        log.info("  docker compose stop in %s", target)
        subprocess.run(
            ["docker", "compose", "-f", str(target / "docker-compose.yml"), "stop"],
            cwd=str(target),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
        )

    # 2. Snapshot every volume that looks like ours.
    volumes = _list_tenant_volumes(slug, target)
    saved: list[str] = []
    for vol in volumes:
        dest = archive_root / f"{vol}.tar.gz"
        if _save_volume(vol, dest):
            saved.append(vol)
            log.info("  snapshot %s -> %s (%d bytes)", vol, dest, dest.stat().st_size)

    # 3. Copy the tenant dir so we have the compose + .env + Caddyfile
    #    on disk separately from the volumes (restore needs both).
    tenant_dir_archive = archive_root / "tenant-dir.tar.gz"
    if target.exists():
        try:
            with tenant_dir_archive.open("wb") as f:
                r = subprocess.run(
                    ["tar", "czf", "-", "-C", str(target.parent), target.name],
                    stdout=f, stderr=subprocess.PIPE, timeout=120,
                )
            if r.returncode != 0:
                log.warning("  tenant-dir snapshot failed: %s", r.stderr.decode(errors="replace")[:200])
                tenant_dir_archive.unlink(missing_ok=True)
        except (subprocess.TimeoutExpired, OSError) as e:
            log.warning("  tenant-dir snapshot errored: %s", e)
            tenant_dir_archive.unlink(missing_ok=True)

    # 4. Write the archive manifest — restore reads this to know which
    #    volumes existed + which port we used.
    manifest = {
        "slug": slug,
        "archived_at": now_utc().isoformat(),
        "port": tenant.get("port"),
        "email": tenant.get("email"),
        "product_name": tenant.get("product_name"),
        "signup_id": tenant.get("signup_id"),
        "url": tenant.get("url"),
        "volumes": saved,
        "tenant_dir_archive": tenant_dir_archive.name if tenant_dir_archive.exists() else None,
    }
    (archive_root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))

    # 5. Now safely remove the live tenant dir + caddy route (we still
    #    have the snapshots in /var/hatchik-archive).
    if target.exists():
        # Remove containers + volumes — both are captured in the snapshots.
        subprocess.run(
            ["docker", "compose", "-f", str(target / "docker-compose.yml"), "down", "-v"],
            cwd=str(target),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
        shutil.rmtree(target, ignore_errors=True)

    if route.exists():
        route.unlink()
        reload_host_caddy()

    return True


# ─── Purge (day 37) ───────────────────────────────────────────────────────
def purge_tenant(slug: str, dry_run: bool = False) -> bool:
    """Hard-delete the archive volumes + tenant dir entirely."""
    log.info("purging archive for %s …", slug)
    if dry_run:
        log.info("  [dry-run] would remove %s", ARCHIVE_DIR / slug)
        return True
    archive_root = ARCHIVE_DIR / slug
    if archive_root.exists():
        shutil.rmtree(archive_root, ignore_errors=True)
        log.info("  removed %s", archive_root)
    return True


# ─── Reconcile ────────────────────────────────────────────────────────────
def reconcile_one(
    slug: str,
    tenant: dict[str, Any],
    *,
    now: datetime,
    dry_run: bool,
    summary: list[dict[str, Any]],
) -> None:
    """Apply lifecycle decisions to a single tenant."""
    status = tenant.get("status")
    email = tenant.get("email") or ""
    product_name = tenant.get("product_name") or "your sandbox"
    signup_id = int(tenant.get("signup_id") or 0)
    port = int(tenant.get("port") or 0)

    signup = fetch_signup_for_email(email) if email else None
    first_name = (signup or {}).get("first_name") or ""

    entry: dict[str, Any] = {"slug": slug, "status": status, "actions": []}
    summary.append(entry)

    # ── Purge path (already-archived tenants past their grace period) ─────
    if status == "archived":
        archived_at = parse_iso(tenant.get("archived_at"))
        if archived_at is None:
            log.warning("%s is archived but missing archived_at — skipping purge", slug)
            return
        days_since_archive = (now - archived_at).total_seconds() / 86400.0
        entry["days_since_archive"] = round(days_since_archive, 2)
        if days_since_archive >= PURGE_DAYS_AFTER_ARCHIVE:
            log.info("%s archived %.1f days ago — purging", slug, days_since_archive)
            if purge_tenant(slug, dry_run=dry_run):
                entry["actions"].append("purged")
                if not dry_run:
                    update_tenant(slug, status="purged", purged_at=now.isoformat())
                    mark_signup_status(signup_id, "archived_purged")
                    send_purged_email(email, first_name, product_name)
                    entry["actions"].append("purge-email")
        else:
            entry["actions"].append("archive-grace-period")
        return

    if status != "live":
        # Don't touch provisioning/failed/decommissioned/purged tenants.
        entry["actions"].append("skip-not-live")
        return

    # ── Live path: query activity, compare to thresholds ─────────────────
    last_activity = query_last_activity(slug)
    if last_activity is None:
        # Fall back to created_at from the registry — better than nothing,
        # but flag it. A consistently-failing probe means the tenant is
        # dead anyway and will hit the archive threshold by created_at.
        registry_created = parse_iso(tenant.get("created_at"))
        if registry_created is None and isinstance(tenant.get("created_at"), (int, float)):
            registry_created = datetime.fromtimestamp(float(tenant["created_at"]), timezone.utc)
        if registry_created is None:
            log.warning("%s: no activity probe AND no created_at — cannot reconcile, skipping", slug)
            entry["actions"].append("skip-no-activity-no-created-at")
            return
        last_activity = registry_created
        entry["activity_fallback"] = "registry.created_at"

    days_idle = (now - last_activity).total_seconds() / 86400.0
    entry["days_idle"] = round(days_idle, 2)
    entry["last_activity"] = last_activity.isoformat()

    # ── Archive (day >= 30) ──────────────────────────────────────────────
    if days_idle >= ARCHIVE_DAY:
        log.info("%s idle %.1f days — archiving (threshold %s)", slug, days_idle, ARCHIVE_DAY)
        if archive_tenant(slug, tenant, dry_run=dry_run):
            entry["actions"].append("archived")
            if not dry_run:
                archive_expiry = now + timedelta(days=PURGE_DAYS_AFTER_ARCHIVE)
                update_tenant(slug, status="archived", archived_at=now.isoformat())
                send_archived_email(email, first_name, slug, product_name, archive_expiry)
                entry["actions"].append("archive-email")
        return

    # ── Day-29 firmer reminder ───────────────────────────────────────────
    if days_idle >= WARN2_DAY and not tenant.get("archive_warning_29_at"):
        log.info("%s idle %.1f days — sending day-%s reminder", slug, days_idle, WARN2_DAY)
        magic = mint_magic_link(slug, port, email) if (not dry_run and port and email) else None
        if dry_run:
            entry["actions"].append("would-send-warn29")
        else:
            if send_warn2_email(email, first_name, slug, product_name, magic):
                update_tenant(slug, archive_warning_29_at=now.isoformat())
                entry["actions"].append("warn29-sent")
            else:
                entry["actions"].append("warn29-send-failed")
        return  # don't also fire warn1 in the same run

    # ── Day-23 polite warning ────────────────────────────────────────────
    if days_idle >= WARN1_DAY and not tenant.get("archive_warning_23_at"):
        log.info("%s idle %.1f days — sending day-%s warning", slug, days_idle, WARN1_DAY)
        magic = mint_magic_link(slug, port, email) if (not dry_run and port and email) else None
        if dry_run:
            entry["actions"].append("would-send-warn23")
        else:
            if send_warn1_email(email, first_name, slug, product_name, magic):
                update_tenant(slug, archive_warning_23_at=now.isoformat())
                entry["actions"].append("warn23-sent")
            else:
                entry["actions"].append("warn23-send-failed")
        return

    # ── Sign-in detected after a warning — reset the markers ─────────────
    # If the customer signed in (days_idle dropped back below WARN1_DAY)
    # we clear the warning timestamps so a future idle stretch sends them
    # again. We don't need to clear archived_at because archive is
    # terminal for the live path.
    reset_fields: dict[str, Any] = {}
    if days_idle < WARN1_DAY and tenant.get("archive_warning_23_at"):
        reset_fields["archive_warning_23_at"] = None
    if days_idle < WARN2_DAY and tenant.get("archive_warning_29_at"):
        reset_fields["archive_warning_29_at"] = None
    if reset_fields and not dry_run:
        update_tenant(slug, **reset_fields)
        entry["actions"].append("reset-warnings")

    if not entry["actions"]:
        entry["actions"].append("no-action")


def reconcile(*, dry_run: bool = False, only_slug: str | None = None) -> dict[str, Any]:
    reg = load_registry()
    now = now_utc()
    log.info("reconciler tick at %s (dry_run=%s, thresholds: warn1=%s warn2=%s archive=%s purge_after=%s)",
             now.isoformat(), dry_run, WARN1_DAY, WARN2_DAY, ARCHIVE_DAY, PURGE_DAYS_AFTER_ARCHIVE)
    summary: list[dict[str, Any]] = []
    tenants = reg.get("tenants", {})
    for slug, tenant in sorted(tenants.items()):
        if only_slug and slug != only_slug:
            continue
        try:
            reconcile_one(slug, tenant, now=now, dry_run=dry_run, summary=summary)
        except Exception as e:  # noqa: BLE001 — one bad tenant must not kill the run
            log.exception("reconcile %s crashed: %s", slug, e)
            summary.append({"slug": slug, "error": str(e)})
    return {"now": now.isoformat(), "dry_run": dry_run, "tenants": summary}


# ─── Main ─────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="Hatchik sandbox idle-archive reconciler")
    ap.add_argument("--dry-run", action="store_true", help="Log decisions, make no changes")
    ap.add_argument("--slug", help="Only reconcile this single tenant")
    ap.add_argument("--json", action="store_true", help="Print summary JSON on stdout")
    args = ap.parse_args()

    result = reconcile(dry_run=args.dry_run, only_slug=args.slug)
    if args.json:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
