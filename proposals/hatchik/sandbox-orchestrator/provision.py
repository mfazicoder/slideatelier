#!/usr/bin/env python3
r"""
provision.py — instantiate a sandbox tenant for a signup.

Usage:
    provision.py <signup_id>            # provision from signups DB row
    provision.py --slug <slug> \         # provision manually (testing)
                 --email <email> \
                 --product <name>

What it does:
    1. Pulls the signup row from /var/lib/hatchik/signups.db (or arg)
    2. Slugifies the product name with collision-avoidance against the registry
    3. Allocates the next free port in 18000-18099 from the registry
    4. Renders the per-tenant compose stack into /opt/hatchik-tenants/<slug>/
       from the substrate-template at /opt/hatchik-substrate-test/ (later:
       a versioned read-only template path)
    5. Writes the per-tenant Caddy route to /opt/hatchik-host-caddy/tenants.d/<slug>.caddy
    6. `docker compose up -d` in the tenant directory
    7. Polls the tenant for health (tenant's own caddy on :PORT/)
    8. Reloads host Caddy
    9. Sends a "your sandbox is ready" email via Resend
   10. Marks registry status=live with the final URL

Idempotent — re-running for the same signup_id is a no-op if status=live.

Run as root on the sandbox host.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx


# Auto-load /opt/hatchik-orchestrator/.env if present so the script can be
# invoked from anywhere (cron, manual, signup-service subprocess) and still
# see RESEND_API_KEY + HATCHIK_FROM_EMAIL + HATCHIK_FOUNDER_EMAIL +
# HATCHIK_SUBSTRATE_TEMPLATE. Keeps the lifecycle config in one file rather
# than relying on every caller to source it.
def _load_env_file(path: str = "/opt/hatchik-orchestrator/.env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        # Don't overwrite vars already set in the environment (caller takes priority)
        os.environ.setdefault(key, val)


_load_env_file()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def supabase_jwt(secret: str, role: str, expires_at: int) -> str:
    """Mint a Supabase-compatible HS256 JWT.

    Supabase's anon + service_role keys are just JWTs signed with the
    project's JWT_SECRET, with `role` set to either `anon` or `service_role`
    and a far-future `exp`. GoTrue / PostgREST / supabase-js all accept them
    via the standard Bearer flow.
    """
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps(
        {"role": role, "iss": "supabase", "iat": int(time.time()), "exp": expires_at},
        separators=(",", ":"),
    ).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"

# ─── Paths (sandbox host) ─────────────────────────────────────────────────
SIGNUPS_DB = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
SUBSTRATE_TEMPLATE = Path(os.environ.get("HATCHIK_SUBSTRATE_TEMPLATE", "/opt/hatchik-substrate-test"))
TENANTS_DIR = Path(os.environ.get("HATCHIK_TENANTS_DIR", "/opt/hatchik-tenants"))
HOST_CADDY_DIR = Path(os.environ.get("HATCHIK_HOST_CADDY_DIR", "/opt/hatchik-host-caddy"))
TENANTS_CADDY_D = HOST_CADDY_DIR / "tenants.d"
REGISTRY_FILE = TENANTS_DIR / "registry.json"

# ─── Constants ────────────────────────────────────────────────────────────
PORT_RANGE_START = 18000
PORT_RANGE_END = 18100  # exclusive
DOMAIN = "hatchik.com"
SLUG_RESERVED = {"www", "api", "app", "admin", "auth", "docs", "blog", "status", "dashboard", "mail", "smtp", "imap"}
SLUG_MAX_LEN = 40
HEALTH_TIMEOUT_SEC = 90

# Resend
RESEND_API_URL = "https://api.resend.com/emails"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
HATCHIK_FROM_EMAIL = os.environ.get("HATCHIK_FROM_EMAIL", "noreply@hatchik.com")
HATCHIK_FOUNDER_EMAIL = os.environ.get("HATCHIK_FOUNDER_EMAIL", "appmanager@namaasol.com")


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


def allocate_port(reg: dict[str, Any]) -> int:
    used = {t["port"] for t in reg["tenants"].values()}
    for port in range(PORT_RANGE_START, PORT_RANGE_END):
        if port not in used:
            return port
    raise RuntimeError(f"All ports in {PORT_RANGE_START}-{PORT_RANGE_END} exhausted")


# ─── Slug helpers ─────────────────────────────────────────────────────────
def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    s = re.sub(r"-+", "-", s)
    if not s:
        s = "sandbox"
    return s[:SLUG_MAX_LEN]


def unique_slug(base: str, reg: dict[str, Any]) -> str:
    """Find a free slug. Decommissioned slugs are reusable; live + provisioning
    + failed slugs are not (failed sticks because the user may want to inspect)."""
    live_statuses = {"provisioning", "live", "failed"}
    taken = {s for s, t in reg["tenants"].items() if t.get("status") in live_statuses}
    taken |= SLUG_RESERVED
    if base not in taken:
        return base
    for i in range(2, 100):
        candidate = f"{base}-{i}"
        if candidate not in taken:
            return candidate
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    return f"{base}-{suffix}"


# ─── Substrate rendering ──────────────────────────────────────────────────
TEMPLATE_VARS = [
    "PRODUCT_NAME", "DOMAIN", "REGION", "PROJECT_SLUG", "PROJECT_DIR",
    "SERVER_IP", "REPO_URL", "LINEAR_PROJECT_URL", "PRODUCT_DESCRIPTION",
    "ADMIN_EMAIL", "GITHUB_USERNAME", "REGISTRAR_URL", "HOSTING_PROVIDER",
]


def render_substrate(slug: str, port: int, product_name: str, email: str, idea: str, target: Path) -> None:
    """Copy substrate-template into target, substitute placeholders, write .env."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SUBSTRATE_TEMPLATE, target, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))

    subs = {
        "PRODUCT_NAME": product_name,
        "DOMAIN": f"{slug}.{DOMAIN}",
        "REGION": "nbg1",
        "PROJECT_SLUG": slug,
        "PROJECT_DIR": str(target),
        "SERVER_IP": os.environ.get("HATCHIK_HOST_IP", "178.105.139.144"),
        "REPO_URL": f"https://github.com/hatchik/{slug}",
        "LINEAR_PROJECT_URL": "https://linear.app/hatchik",
        "PRODUCT_DESCRIPTION": idea[:200],
        "ADMIN_EMAIL": email,
        "GITHUB_USERNAME": "hatchik",
        "REGISTRAR_URL": "https://cloudflare.com",
        "HOSTING_PROVIDER": "Hetzner Cloud (Nuremberg)",
    }

    exts = {".yml", ".yaml", ".json", ".md", ".sh", ".py", ".ts", ".tsx", ".env", ".example", ".toml", ".html", ".sql"}
    extra = {"Caddyfile", "Dockerfile", ".env.example"}
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in exts and path.name not in extra:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        original = text
        for key, val in subs.items():
            text = text.replace("{{" + key + "}}", val)
        if text != original:
            path.write_text(text)

    # Write the tenant .env from .env.example with secrets generated.
    # Critical: SUPABASE_ANON_KEY + SUPABASE_SERVICE_ROLE_KEY must be JWTs
    # signed with the same JWT_SECRET. lib/supabase.ts throws at module
    # load if VITE_SUPABASE_ANON_KEY is empty (the frontend won't render).
    env_example = target / ".env.example"
    env_file = target / ".env"
    if env_example.exists():
        env_text = env_example.read_text()

        jwt_secret = secrets.token_hex(32)
        # Far-future exp: ~10 years from now (Supabase uses 10y by default)
        exp = int(time.time()) + 10 * 365 * 24 * 3600
        anon_jwt = supabase_jwt(jwt_secret, "anon", exp)
        service_jwt = supabase_jwt(jwt_secret, "service_role", exp)
        pg_password = secrets.token_urlsafe(24).replace("-", "x")  # strip - to avoid URL-encoding pain

        env_text = env_text.replace(
            'JWT_SECRET="change-me-32-chars-or-more-please-do-not-keep-this-default"',
            f'JWT_SECRET="{jwt_secret}"',
        )
        env_text = env_text.replace(
            'SUPABASE_ANON_KEY=""',
            f'SUPABASE_ANON_KEY="{anon_jwt}"',
        )
        env_text = env_text.replace(
            'SUPABASE_SERVICE_ROLE_KEY=""',
            f'SUPABASE_SERVICE_ROLE_KEY="{service_jwt}"',
        )
        env_text = env_text.replace(
            'POSTGRES_PASSWORD="postgres"',
            f'POSTGRES_PASSWORD="{pg_password}"',
        )
        # Public URLs for VITE_* — the frontend uses these to call back
        # through host Caddy → tenant Caddy → supabase-auth/rest/etc.
        env_text = env_text.replace(
            f'SITE_URL="https://{slug}.{DOMAIN}"',
            f'SITE_URL="https://{slug}.{DOMAIN}"',
        )
        env_file.write_text(env_text)

        # Also append explicit VITE_ vars in case .env.example doesn't have
        # them (the docker-compose `environment:` block reads ${SUPABASE_ANON_KEY}
        # but we set VITE_SUPABASE_URL explicitly below so it matches the
        # public sandbox URL rather than localhost).
        with env_file.open("a") as f:
            f.write(f'\nVITE_SUPABASE_URL="https://{slug}.{DOMAIN}"\n')
            f.write(f'VITE_SUPABASE_ANON_KEY="{anon_jwt}"\n')
            f.write(f'VITE_PRODUCT_NAME="{product_name}"\n')

    # Override the Caddy port mapping in docker-compose to bind the tenant
    # Caddy to the allocated host port instead of the hardcoded 8080.
    compose = target / "docker-compose.yml"
    if compose.exists():
        text = compose.read_text()
        text = text.replace('- "8080:80"', f'- "127.0.0.1:{port}:80"')
        text = text.replace('- "8443:443"', f'# tenant TLS handled by host Caddy; no public 443 binding')
        compose.write_text(text)


# ─── Caddy tenant route ───────────────────────────────────────────────────
def write_tenant_caddy_route(slug: str, port: int) -> None:
    TENANTS_CADDY_D.mkdir(parents=True, exist_ok=True)
    route = TENANTS_CADDY_D / f"{slug}.caddy"
    route.write_text(f"""# Auto-generated by provision.py — DO NOT EDIT
# Tenant: {slug} → localhost:{port}

{slug}.{DOMAIN} {{
    tls {{
        dns cloudflare {{env.CF_API_TOKEN}}
    }}
    encode gzip zstd
    reverse_proxy localhost:{port} {{
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
""")


def reload_host_caddy() -> None:
    subprocess.run(
        ["docker", "exec", "hatchik-host-caddy-caddy-1", "caddy", "reload", "--config", "/etc/caddy/Caddyfile"],
        check=True,
    )


# ─── Tenant compose lifecycle ─────────────────────────────────────────────
def compose_up(target: Path) -> None:
    subprocess.run(["docker", "compose", "-f", str(target / "docker-compose.yml"), "up", "-d"], check=True, cwd=target)


def wait_for_tenant_health(port: int, timeout: int = HEALTH_TIMEOUT_SEC) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/", timeout=3)
            if r.status_code in (200, 301, 302):
                return True
        except httpx.RequestError:
            pass
        time.sleep(2)
    return False


# ─── Email ────────────────────────────────────────────────────────────────
def send_sandbox_ready_email(to: str, slug: str, product_name: str) -> bool:
    if not RESEND_API_KEY:
        print("WARN: no RESEND_API_KEY, skipping email")
        return False
    url = f"https://{slug}.{DOMAIN}"
    faq_url = f"https://{DOMAIN}/#faq"
    text = f"""Hi,

Your sandbox for {product_name} is live: {url}

It's running on Hatchik's free Sandbox tier — a real working version of
your app stack (database, auth, payments in test mode, mailboxes).

What to do next:
1. Open {url} in your browser
2. Sign up with your email — you'll get a magic-link to sign in
3. Have a play, kick the tyres
4. When you're ready to make it real (your own domain, live payments,
   mobile builds), upgrade to Launch.

Questions? See the FAQ at {faq_url}.

— Hatchik

(This is an automated message — please don't reply.)
"""
    html = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Your {product_name} sandbox is ready</title>
</head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:8px;padding:32px;">
          <tr>
            <td style="font-size:16px;line-height:1.6;color:#1a1a1a;">
              <p style="margin:0 0 16px 0;">Hi,</p>
              <p style="margin:0 0 16px 0;">Your sandbox for <strong>{product_name}</strong> is live at <a href="{url}" style="color:#4f46e5;text-decoration:underline;">{url}</a>.</p>
              <p style="margin:0 0 16px 0;">It&rsquo;s running on Hatchik&rsquo;s free Sandbox tier &mdash; a real working version of your app stack (database, auth, payments in test mode, mailboxes).</p>
              <p style="margin:24px 0 8px 0;font-weight:600;">What to do next</p>
              <ol style="margin:0 0 16px 0;padding-left:20px;">
                <li style="margin:0 0 8px 0;">Open <a href="{url}" style="color:#4f46e5;text-decoration:underline;">{url}</a> in your browser</li>
                <li style="margin:0 0 8px 0;">Sign up with your email &mdash; you&rsquo;ll get a magic-link to sign in</li>
                <li style="margin:0 0 8px 0;">Have a play, kick the tyres</li>
                <li style="margin:0 0 8px 0;">When you&rsquo;re ready to make it real (your own domain, live payments, mobile builds), upgrade to Launch.</li>
              </ol>
              <p style="margin:0 0 16px 0;">Questions? See the <a href="{faq_url}" style="color:#4f46e5;text-decoration:underline;">FAQ</a>.</p>
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
    payload = {
        "from": HATCHIK_FROM_EMAIL,
        "to": to,
        "subject": f"Your {product_name} sandbox is ready",
        "text": text,
        "html": html,
    }
    try:
        r = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        print(f"WARN: failed to send sandbox-ready email: {e}")
        return False


# ─── Main ─────────────────────────────────────────────────────────────────
def fetch_signup(signup_id: int) -> dict[str, Any]:
    conn = sqlite3.connect(SIGNUPS_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM signups WHERE id = ?", (signup_id,)).fetchone()
    conn.close()
    if not row:
        raise SystemExit(f"signup {signup_id} not found in {SIGNUPS_DB}")
    return dict(row)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("signup_id", nargs="?", type=int)
    ap.add_argument("--slug", help="Manual override (testing)")
    ap.add_argument("--email")
    ap.add_argument("--product", help="Product name (manual mode)")
    ap.add_argument("--idea", default="A new app", help="Product description")
    ap.add_argument("--no-email", action="store_true", help="Skip the customer email")
    args = ap.parse_args()

    if args.signup_id is not None:
        row = fetch_signup(args.signup_id)
        email = row["email"]
        product_name = row["product_name"] or "Untitled"
        idea = row["description"] or "A new app"
        signup_id = args.signup_id
    elif args.slug and args.email and args.product:
        email = args.email
        product_name = args.product
        idea = args.idea
        signup_id = 0
    else:
        ap.error("either signup_id OR --slug + --email + --product required")

    reg = load_registry()
    base_slug = args.slug or slugify(product_name)
    slug = unique_slug(base_slug, reg)

    # Idempotency: if already live, just return
    existing = next((t for t in reg["tenants"].values() if t.get("signup_id") == signup_id and signup_id != 0), None)
    if existing and existing.get("status") == "live":
        print(f"signup {signup_id} already provisioned at {existing['slug']} (port {existing['port']})")
        return

    port = allocate_port(reg)
    target = TENANTS_DIR / slug
    print(f"→ provisioning {slug} on port {port} → {target}")

    reg["tenants"][slug] = {
        "slug": slug, "port": port, "email": email, "product_name": product_name,
        "signup_id": signup_id, "status": "provisioning", "created_at": int(time.time()),
        "url": f"https://{slug}.{DOMAIN}",
    }
    save_registry(reg)

    try:
        print("  1. render substrate")
        render_substrate(slug, port, product_name, email, idea, target)

        print("  2. write tenant Caddy route")
        write_tenant_caddy_route(slug, port)

        print("  3. docker compose up")
        compose_up(target)

        print(f"  4. wait for tenant healthy on :{port}")
        if not wait_for_tenant_health(port):
            raise RuntimeError(f"tenant did not become healthy within {HEALTH_TIMEOUT_SEC}s")

        print("  5. reload host Caddy")
        reload_host_caddy()

        if not args.no_email:
            print("  6. send sandbox-ready email")
            send_sandbox_ready_email(email, slug, product_name)

        reg = load_registry()
        reg["tenants"][slug]["status"] = "live"
        save_registry(reg)

        print(f"✓ live at https://{slug}.{DOMAIN}")

    except Exception as e:
        reg = load_registry()
        reg["tenants"][slug]["status"] = "failed"
        reg["tenants"][slug]["error"] = str(e)
        save_registry(reg)
        print(f"✗ provisioning failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
