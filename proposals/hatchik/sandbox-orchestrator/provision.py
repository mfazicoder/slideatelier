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

from github_repo import GITHUB_ORG, create_tenant_repo
from service_inventory import email_lines, html_blocks, sandbox_inventory


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
# Placeholders the substrate-template can interpolate. PRODUCT_IDEA is the
# raw description from the signup; the marketing-landing template
# (routes/index.tsx) bakes it into the hero tagline.
TEMPLATE_VARS = [
    "PRODUCT_NAME", "PRODUCT_IDEA", "DOMAIN", "REGION", "PROJECT_SLUG",
    "PROJECT_DIR", "SERVER_IP", "REPO_URL",
    "PRODUCT_DESCRIPTION", "ADMIN_EMAIL", "GITHUB_USERNAME", "REGISTRAR_URL",
    "HOSTING_PROVIDER",
]


def render_substrate(
    slug: str,
    port: int,
    product_name: str,
    email: str,
    idea: str,
    target: Path,
    deploy_token: str,
) -> None:
    """Copy substrate-template into target, substitute placeholders, write .env."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SUBSTRATE_TEMPLATE, target, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))

    # The landing-page template wants a one-line tagline. Take the first
    # sentence of the idea (or the whole thing if it's short) and trim to
    # 160 chars so it doesn't overflow the hero.
    idea_clean = (idea or "").strip()
    tagline = (idea_clean.split(".")[0] if idea_clean else "").strip()
    if not tagline:
        tagline = f"{product_name} — a new product."
    tagline = tagline[:160]

    subs = {
        "PRODUCT_NAME": product_name,
        # PRODUCT_IDEA flows into routes/index.tsx as the hero subtitle.
        # Trimmed for safety; the marketing template additionally clamps.
        "PRODUCT_IDEA": tagline,
        "DOMAIN": f"{slug}.{DOMAIN}",
        "REGION": "nbg1",
        "PROJECT_SLUG": slug,
        "PROJECT_DIR": str(target),
        "SERVER_IP": os.environ.get("HATCHIK_HOST_IP", "178.105.139.144"),
        "REPO_URL": f"https://github.com/{GITHUB_ORG}/{slug}",
        "PRODUCT_DESCRIPTION": idea[:200] if idea else "",
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
            # PRODUCT_IDEA powers the marketing-landing hero subtitle.
            # Quotes inside the idea would break the .env line, so strip
            # them — only the front-end reads this and it's display-only.
            safe_idea = subs["PRODUCT_IDEA"].replace('"', "").replace("\n", " ")
            f.write(f'VITE_PRODUCT_IDEA="{safe_idea}"\n')
            # Sandbox-tier tenants get a "Built with Hatchik" footer link
            # for organic referrals. Launch/Growth tenants flip this off
            # via their post-deploy .env override.
            f.write('VITE_BUILT_WITH_HATCHIK="true"\n')
            # Resend SMTP for tenant Supabase Auth — lets magic-link, password
            # reset and signup confirmation emails work out of the box in
            # sandboxes. Tenants share Hatchik's Resend account; the from
            # header is noreply@hatchik.com until the customer brings their
            # own domain on Launch.
            if RESEND_API_KEY:
                f.write('SMTP_HOST="smtp.resend.com"\n')
                f.write('SMTP_PORT="587"\n')
                f.write('SMTP_USER="resend"\n')
                f.write(f'SMTP_PASSWORD="{RESEND_API_KEY}"\n')
                f.write(f'SMTP_ADMIN_EMAIL="{HATCHIK_FROM_EMAIL}"\n')
                f.write('AUTH_MAGIC_LINK_ENABLED="true"\n')
            else:
                f.write('AUTH_MAGIC_LINK_ENABLED="false"\n')
            # Google OAuth off by default — customer enables after registering
            # their own OAuth app and pasting client_id/secret here.
            f.write('GOOGLE_OAUTH_ENABLED="false"\n')

    # Override the Caddy port mapping in docker-compose to bind the tenant
    # Caddy to the allocated host port instead of the hardcoded 8080.
    compose = target / "docker-compose.yml"
    if compose.exists():
        text = compose.read_text()
        text = text.replace('- "8080:80"', f'- "127.0.0.1:{port}:80"')
        text = text.replace('- "8443:443"', f'# tenant TLS handled by host Caddy; no public 443 binding')
        compose.write_text(text)

    # AI_CONTEXT.md — the file customer-facing AI tools (Claude Code,
    # Cursor, Windsurf) read first. Lives at the repo root so opening
    # the folder in a coding agent gives it everything it needs.
    write_ai_context(target, slug, port, product_name, idea, email, deploy_token)


def write_ai_context(
    target: Path,
    slug: str,
    port: int,
    product_name: str,
    idea: str,
    email: str,
    deploy_token: str,
) -> None:
    """Write AI_CONTEXT.md at the tenant repo root.

    Goal: any AI coding tool opened in this directory should be able to
    pick up the substrate boundaries, the live sandbox URL, and the
    Supabase anon key without having to grep half the codebase. No
    Postgres / service-role secrets — those stay in .env (gitignored).
    The deploy_token is per-tenant and only authorises POSTs to the
    redeploy endpoint, so it's safe to include for the AI tool's use.
    """
    anon_key = _extract_env_value(target / ".env", "VITE_SUPABASE_ANON_KEY") or ""
    sandbox_url = f"https://{slug}.{DOMAIN}"
    deploy_url = f"https://{DOMAIN}/api/tenants/{slug}/redeploy"
    first_prompt = first_prompt_template(product_name, idea, sandbox_url, anon_key)

    body = f"""# AI_CONTEXT — {product_name}

This file is for Claude Code, Cursor, Windsurf, or any other AI coding
tool you point at this repo. Read it first.

> **Tip for the AI tool reading this**: when the human asks you to
> "ship", "push", "deploy", or "go live", run `git push` (the easy
> path) OR POST to the deploy URL below. Don't ask the human to open
> a terminal — do it yourself. The deploy token is in this file.

## What this is

A Hatchik sandbox for **{product_name}**. The substrate (auth, database,
hosting, mail, payments-in-test) is already wired up — your job is to
add the product features on top.

What the customer wants to build:
> {idea or '(no description provided yet)'}

## Live sandbox

- URL: {sandbox_url}
- Owner email: {email}
- The URL is also the Supabase project URL — Hatchik routes
  `/auth/v1/*`, `/rest/v1/*`, `/storage/v1/*`, `/realtime/v1/*` and
  `/studio` through Caddy to the per-tenant Supabase stack.

## Supabase

```
SUPABASE_URL          = {sandbox_url}
SUPABASE_ANON_KEY     = {anon_key or '(see .env in this repo)'}
```

The anon key is safe to expose in the frontend (`VITE_*` env vars).
**Do not** put the service-role key or Postgres password into the
frontend or commit them — they live in `.env`, which is gitignored.

## Database access

The Postgres database lives inside the sandbox host's Docker network.
You **cannot** connect to it directly from your laptop — port 5432
isn't exposed publicly. Two ways to talk to it:

1. **Via Supabase JS client** (recommended) — use the anon key for
   end-user reads/writes governed by RLS policies, or the service-role
   key from your `apps/api/` backend code for admin queries.
2. **Via Supabase Studio** — open `{sandbox_url}/studio` in your
   browser. SQL editor, table browser, auth user list, the lot.

When you push commits, Hatchik picks the change up within ~30 seconds
via the per-tenant GitHub webhook and your schema migrations / API
code run inside the substrate's network with full DB access. See
"Deploying changes" below.

## Repo layout — where to add your code

```
apps/
  web/
    src/
      product/        ← put your product UI here (pages, components)
      lib/            ← shared client-side helpers
      App.tsx         ← entry point — wire new routes in here
  api/
    src/
      product/        ← put your product backend here (routes, business logic)
      index.ts        ← Fastify entry — register your new routes here
supabase/
  migrations/         ← drop new .sql files here for schema changes
```

**Edit freely:** anything under `apps/web/src/product/`,
`apps/api/src/product/`, and `supabase/migrations/`.

**Don't edit** (these are substrate plumbing — changing them breaks the
sandbox):
- `docker-compose.yml`
- `Caddyfile`
- Anything under `supabase/volumes/` or `supabase/config/`
- `apps/web/src/lib/supabase.ts` (auto-wired from env)
- `apps/api/src/lib/supabase.ts` (auto-wired from env)
- `apps/api/src/lib/auth.ts` (JWT verification — uses substrate JWT_SECRET)

## Deploying changes

Your sandbox redeploys automatically in two ways:

1. **`git push`** — the repo has a webhook wired up; pushing triggers
   a redeploy within ~30 seconds.

2. **Direct trigger from your AI tool** — if you want to redeploy
   without going through GitHub (e.g. you've made a change you don't
   want committed yet), POST to the redeploy URL below. Most AI tools
   can do this for you if you ask them to.

```bash
curl -X POST {deploy_url} \\
  -H "X-Deploy-Token: {deploy_token}"
```

After either trigger, watch the deploy by visiting `{sandbox_url}` —
it'll reflect your changes once Docker has rebuilt the affected
service (usually 20–40 seconds; longer if Python deps or schemas
changed).

You don't run the stack locally — Docker + Supabase + Caddy is too heavy.
The sandbox is your dev environment. Push small, push often.

## First-prompt template — paste into your AI tool

Once you've cloned the repo and opened it in Claude Code (or Cursor):

> {first_prompt}

The AI will read this file, understand the substrate, and start
proposing changes. Accept, push, watch the sandbox update.

## Building for mobile

This repo ships with a GitHub Actions workflow at
`.github/workflows/build-mobile.yml` that builds an iOS IPA and an
Android APK from the same React code as the web app. You don't need
Xcode or the Android SDK locally — GitHub's hosted runners do the
work.

Three ways to trigger a build:

1. **From the Hatchik dashboard** — https://hatchik.com/account →
   Mobile tab → *Build now*. Most users do this.
2. **From GitHub** — Actions tab → *Build mobile* → *Run workflow*.
3. **Automatically on push to `main`** — if your commit changes files
   under `apps/mobile/`, `apps/web/`, or `capacitor.config.ts`, the
   workflow fires on its own.

When the run finishes, download the `android-apk` and `ios-ipa`
artefacts from the workflow run page. **The binaries are unsigned.**
To produce App Store / Play Store-ready signed builds you need your
own Apple Developer (~£99/year) and Google Play Console (~£25 one-off)
accounts — see `apps/mobile/README.md` for the signing setup.

## Useful URLs

- Sandbox: {sandbox_url}
- Supabase Studio: {sandbox_url}/studio (sign in with the owner magic link)
- Hatchik account: https://hatchik.com/account
- Mobile builds: https://hatchik.com/account → Mobile tab
- FAQ: https://hatchik.com/#faq
- Docs: https://hatchik.com/docs

— Hatchik (this file is generated per-tenant by provision.py)
"""
    (target / "AI_CONTEXT.md").write_text(body)


def _extract_env_value(env_file: Path, key: str) -> str | None:
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def first_prompt_template(product_name: str, idea: str, sandbox_url: str, anon_key: str) -> str:
    return (
        f"I want to build {product_name}. The idea is: \"{idea or 'a new product'}\". "
        f"This repo is a Hatchik substrate — auth, database, hosting and email are "
        f"already wired up. The live sandbox is at {sandbox_url} and the Supabase "
        f"anon key is in AI_CONTEXT.md. Read AI_CONTEXT.md, then help me design the "
        f"first feature: a minimal user-facing flow that proves the core value. "
        f"Put new UI under apps/web/src/product/ and new API routes under "
        f"apps/api/src/product/. Don't touch the substrate files."
    )


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
def _read_tenant_jwt_secret(target: Path) -> str | None:
    """Pull JWT_SECRET out of the tenant's .env so we can mint a service role JWT."""
    env_file = target / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("JWT_SECRET="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def provision_owner_user(slug: str, port: int, email: str, target: Path) -> str | None:
    """Pre-create the Hatchik customer as a Supabase user in their tenant.

    Returns a magic-link URL the customer can click to sign into their
    sandbox as the owner — single-use, generated by GoTrue's admin API.
    Returns None on failure (the sandbox-ready email falls back to the
    plain URL).
    """
    jwt_secret = _read_tenant_jwt_secret(target)
    if not jwt_secret:
        print("WARN: no JWT_SECRET in tenant .env — skipping owner user creation")
        return None
    exp = int(time.time()) + 60 * 60  # 1h is plenty for the admin call
    service_jwt = supabase_jwt(jwt_secret, "service_role", exp)
    base = f"http://127.0.0.1:{port}/auth/v1"
    headers = {
        "Authorization": f"Bearer {service_jwt}",
        "apikey": service_jwt,
        "Content-Type": "application/json",
    }

    try:
        # Step 1: create the user (idempotent — 422 if email already exists, fine).
        r = httpx.post(
            f"{base}/admin/users",
            headers=headers,
            json={
                "email": email,
                "email_confirm": True,
                "user_metadata": {"hatchik_owner": True, "created_by": "provision.py"},
            },
            timeout=15,
        )
        if r.status_code >= 400 and "already" not in r.text.lower() and "exists" not in r.text.lower():
            print(f"WARN: failed to pre-create owner user: {r.status_code} {r.text[:200]}")
            return None

        # Step 2: generate a magic-link the customer can click to sign in.
        site_url = f"https://{slug}.{DOMAIN}"
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
            print(f"WARN: failed to generate owner magic link: {r.status_code} {r.text[:200]}")
            return None
        data = r.json()
        return data.get("action_link") or data.get("properties", {}).get("action_link")
    except httpx.HTTPError as e:
        print(f"WARN: GoTrue admin API call failed: {e}")
        return None


def send_sandbox_ready_email(
    to: str,
    slug: str,
    product_name: str,
    first_name: str = "",
    signin_link: str | None = None,
    tenant_dir: Path | None = None,
    repo_url: str = "",
) -> bool:
    if not RESEND_API_KEY:
        print("WARN: no RESEND_API_KEY, skipping email")
        return False
    url = f"https://{slug}.{DOMAIN}"
    faq_url = f"https://{DOMAIN}/#faq"
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    # Primary CTA: magic-link straight into the sandbox as the owner. Falls
    # back to the bare URL if pre-provisioning the owner failed.
    primary_link = signin_link or url
    primary_link_label = "Sign in to your sandbox" if signin_link else f"Open your sandbox: {url}"

    # Pull the canonical "what ships" inventory and render plain-text /
    # HTML blocks. Customers reading the email should know exactly what
    # they have, with the same quantification as /account and the docs.
    inventory = sandbox_inventory(
        sandbox_url=url,
        repo_url=repo_url,
        tenant_dir=tenant_dir,
    )
    wired_text_lines, upgrade_text_lines = email_lines(inventory)
    wired_html, upgrade_html = html_blocks(inventory)
    wired_block_text = "\n".join(f"  {line}" for line in wired_text_lines)
    upgrade_block_text = "\n".join(f"  {line}" for line in upgrade_text_lines)

    text = f"""{greeting}

Your sandbox for {product_name} is live.

{primary_link_label}
{primary_link}

You're pre-set as the owner — that link signs you straight in. No
password to remember (you can set one later from Settings if you want).

It's a real working version of your app stack (database, auth, payments
in test mode, mailboxes). When your end-users sign up, they get their
own accounts inside it — your owner account stays separate.

What's set up for you (Sandbox tier)

{wired_block_text}

What's NOT yet wired (you can add at any time)

{upgrade_block_text}

What to do next:
1. Click the link above to open your sandbox as the owner
2. Have a play, kick the tyres
3. When you're ready to make it real (your own domain, live payments,
   the mobile scaffold ready to build), upgrade to Launch from
   hatchik.com/account.

To manage your Hatchik subscription (delete sandbox, upgrade, edit your
name, see the full live service list) go to https://{DOMAIN}/account.

Further information can be found at {faq_url} if you need it.

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
              <p style="margin:0 0 16px 0;">{greeting}</p>
              <p style="margin:0 0 24px 0;">Your sandbox for <strong>{product_name}</strong> is live.</p>
              <p style="margin:0 0 16px 0;"><a href="{primary_link}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;">Sign in to your sandbox &rarr;</a></p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">You&rsquo;re pre-set as the owner &mdash; this link signs you straight in. No password to remember (you can set one later from Settings if you want).</p>
              <p style="margin:24px 0 16px 0;">It&rsquo;s a real working version of your app stack (database, auth, payments in test mode, mailboxes). When your end-users sign up, they get their own accounts inside it &mdash; your owner account stays separate.</p>

              <p style="margin:28px 0 8px 0;font-weight:600;font-size:15px;color:#0f172a;">What&rsquo;s set up for you (Sandbox tier)</p>
              {wired_html}

              <p style="margin:24px 0 8px 0;font-weight:600;font-size:15px;color:#0f172a;">What&rsquo;s NOT yet wired (you can add at any time)</p>
              {upgrade_html}

              <p style="margin:24px 0 8px 0;font-weight:600;">What to do next</p>
              <ol style="margin:0 0 16px 0;padding-left:20px;">
                <li style="margin:0 0 8px 0;">Click the button above to open your sandbox</li>
                <li style="margin:0 0 8px 0;">Have a play, kick the tyres</li>
                <li style="margin:0 0 8px 0;">When you&rsquo;re ready to make it real (your own domain, live payments, the mobile scaffold ready to build), upgrade to Launch from <a href="https://{DOMAIN}/account" style="color:#4f46e5;text-decoration:underline;">your Hatchik account</a>.</li>
              </ol>
              <p style="margin:24px 0 16px 0;color:#555;font-size:14px;">To manage your Hatchik subscription (delete sandbox, upgrade, edit your name, see the full live service list) go to <a href="https://{DOMAIN}/account" style="color:#4f46e5;text-decoration:underline;">hatchik.com/account</a>.</p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">Further information can be found <a href="{faq_url}" style="color:#4f46e5;text-decoration:underline;">here</a> if you need it.</p>
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


def send_walkthrough_email(
    to: str,
    slug: str,
    product_name: str,
    repo_url: str,
    first_name: str = "",
    first_prompt: str = "",
) -> bool:
    """Send the "get started with Claude Code" follow-up email.

    Fires right after send_sandbox_ready_email so the customer has both
    inbox messages by the time they sit down to actually build. Failures
    are logged but don't fail provisioning.
    """
    if not RESEND_API_KEY:
        print("WARN: no RESEND_API_KEY, skipping walkthrough email")
        return False
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    clone_cmd = f"git clone {repo_url}.git"
    # docs.hatchik.com is under construction — link to the FAQ until it's
    # ready so customers don't land on a 404.
    docs_url = f"https://{DOMAIN}/#faq"
    text = f"""{greeting}

Your {product_name} sandbox is up — now to the fun part: building it.

Hatchik gives you a GitHub repo that mirrors the substrate running in
your sandbox. Clone it, open it in an AI coding tool, push your
changes; your sandbox redeploys automatically — usually within 30
seconds of your push or your AI tool calling our deploy endpoint.

Step 1 — open your repo
{repo_url}

Step 2 — clone it locally
  {clone_cmd}

Step 3 — open the folder in Claude Code, Cursor, or Windsurf
Any one of them will do. They all read the AI_CONTEXT.md file in the
repo root, which tells them where the substrate boundaries are, how
to talk to your sandbox, and how to deploy.

Step 4 — paste this prompt to get rolling
  {first_prompt}

Step 5 — push your changes
  git add . && git commit -m "first feature" && git push
Your sandbox redeploys automatically — usually within 30 seconds of
your push (or sooner if your AI tool calls the deploy endpoint
directly; AI_CONTEXT.md shows it how).

Stuck? Reply to this email — Farhan reads everything personally.

— Hatchik

More at {docs_url}.

(This is an automated message — please don't reply.)
"""
    html = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Get started building {product_name}</title>
</head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;background:#ffffff;border-radius:8px;padding:32px;">
          <tr>
            <td style="font-size:16px;line-height:1.6;color:#1a1a1a;">
              <p style="margin:0 0 16px 0;">{greeting}</p>
              <p style="margin:0 0 16px 0;">Your <strong>{product_name}</strong> sandbox is up &mdash; now to the fun part: building it.</p>
              <p style="margin:0 0 24px 0;">Hatchik gives you a GitHub repo that mirrors the substrate running in your sandbox. Clone it, open it in an AI coding tool, push your changes; your sandbox redeploys automatically &mdash; usually within 30 seconds of your push or your AI tool calling our deploy endpoint.</p>

              <p style="margin:24px 0 8px 0;font-weight:600;">Step 1 &mdash; open your repo</p>
              <p style="margin:0 0 16px 0;"><a href="{repo_url}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:10px 20px;border-radius:8px;font-weight:600;">Open on GitHub &rarr;</a></p>

              <p style="margin:24px 0 8px 0;font-weight:600;">Step 2 &mdash; clone it locally</p>
              <pre style="margin:0 0 16px 0;background:#f6f5f1;padding:12px 16px;border-radius:8px;font-size:13px;overflow:auto;"><code>{clone_cmd}</code></pre>

              <p style="margin:24px 0 8px 0;font-weight:600;">Step 3 &mdash; open the folder in your AI tool</p>
              <p style="margin:0 0 16px 0;">Claude Code, Cursor, or Windsurf &mdash; any of them. They all read the <code style="background:#f6f5f1;padding:1px 4px;border-radius:3px;">AI_CONTEXT.md</code> file in the repo root, which tells them where the substrate boundaries are and how to talk to your sandbox.</p>

              <p style="margin:24px 0 8px 0;font-weight:600;">Step 4 &mdash; paste this prompt to get rolling</p>
              <blockquote style="margin:0 0 16px 0;padding:12px 16px;background:#f6f5f1;border-left:3px solid #4f46e5;border-radius:0 8px 8px 0;font-size:14px;color:#333;">{_html_escape(first_prompt)}</blockquote>

              <p style="margin:24px 0 8px 0;font-weight:600;">Step 5 &mdash; push your changes</p>
              <pre style="margin:0 0 16px 0;background:#f6f5f1;padding:12px 16px;border-radius:8px;font-size:13px;overflow:auto;"><code>git add . &amp;&amp; git commit -m "first feature" &amp;&amp; git push</code></pre>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">Your sandbox redeploys automatically &mdash; usually within 30 seconds of your push (or sooner if your AI tool calls the deploy endpoint directly; <code style="background:#f6f5f1;padding:1px 4px;border-radius:3px;">AI_CONTEXT.md</code> shows it how).</p>

              <p style="margin:32px 0 16px 0;">Stuck? Reply to this email &mdash; Farhan reads everything personally.</p>

              <p style="margin:24px 0 0 0;">&mdash; Hatchik</p>
              <p style="margin:24px 0 0 0;color:#555;font-size:14px;">More at <a href="{docs_url}" style="color:#4f46e5;text-decoration:underline;">{docs_url}</a>.</p>
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
        "subject": f"Start building {product_name} — your AI-coding handoff",
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
        print(f"WARN: failed to send walkthrough email: {e}")
        return False


def _html_escape(s: str) -> str:
    """Minimal HTML escape for inline embedding in the walkthrough email."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


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
    ap.add_argument("--first-name", default="", help="Customer first name (manual mode)")
    ap.add_argument("--github-username", default="", help="Customer GitHub handle for repo invite (manual mode)")
    ap.add_argument("--no-email", action="store_true", help="Skip the customer email")
    args = ap.parse_args()

    if args.signup_id is not None:
        row = fetch_signup(args.signup_id)
        email = row["email"]
        product_name = row["product_name"] or "Untitled"
        idea = row["description"] or "A new app"
        # first_name + github_username added to schema after some signups
        # landed — be defensive when the columns are missing.
        first_name = (row["first_name"] if "first_name" in row.keys() else "") or ""
        github_username = (row["github_username"] if "github_username" in row.keys() else "") or ""
        signup_id = args.signup_id
    elif args.slug and args.email and args.product:
        email = args.email
        product_name = args.product
        idea = args.idea
        first_name = args.first_name
        github_username = args.github_username
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

    # Per-tenant redeploy token. Acts as both:
    #   - X-Deploy-Token bearer for AI-tool direct calls
    #   - GitHub webhook secret (HMAC-SHA256 of payload, X-Hub-Signature-256)
    # Idempotent: if the tenant entry already exists with a deploy_token,
    # keep it — re-running provision must NOT rotate the token because
    # the customer's AI_CONTEXT.md and the GitHub webhook secret are
    # locked to the original value.
    existing_entry = reg["tenants"].get(slug, {}) or {}
    deploy_token = existing_entry.get("deploy_token") or secrets.token_urlsafe(32)

    reg["tenants"][slug] = {
        "slug": slug, "port": port, "email": email, "product_name": product_name,
        "signup_id": signup_id, "status": "provisioning", "created_at": int(time.time()),
        "url": f"https://{slug}.{DOMAIN}",
        "deploy_token": deploy_token,
    }
    save_registry(reg)

    try:
        print("  1. render substrate")
        render_substrate(slug, port, product_name, email, idea, target, deploy_token)

        print("  2. write tenant Caddy route")
        write_tenant_caddy_route(slug, port)

        print("  3. docker compose up")
        compose_up(target)

        print(f"  4. wait for tenant healthy on :{port}")
        if not wait_for_tenant_health(port):
            raise RuntimeError(f"tenant did not become healthy within {HEALTH_TIMEOUT_SEC}s")

        print("  5. reload host Caddy")
        reload_host_caddy()

        print("  6. pre-create owner user in tenant + mint magic-link")
        signin_link = provision_owner_user(slug, port, email, target)
        if signin_link:
            print(f"     ✓ owner sign-in link ready")
        else:
            print(f"     (failed — sandbox-ready email will use plain URL)")

        # GitHub handoff — create per-tenant repo, push the rendered
        # substrate, invite the customer if they gave us their GH handle.
        # Resilient: any failure here is logged and the customer still
        # has a working sandbox + the AI_CONTEXT.md file written locally.
        # Run BEFORE the sandbox-ready email so the "What's set up" block
        # in the email can deep-link to the repo (and so the customer's
        # inbox shows both emails in a consistent order).
        print("  7. create GitHub repo + push substrate")
        gh_result = create_tenant_repo(slug, target, product_name, idea, github_username, deploy_token)
        repo_url = gh_result.get("repo_url") or ""
        if repo_url:
            print(
                f"     ✓ repo at {repo_url} "
                f"(pushed={gh_result['pushed']} invited={gh_result['invited']} "
                f"webhook={gh_result.get('webhook', False)})"
            )
        else:
            print(f"     (skipped: {gh_result.get('skipped_reason')})")

        if not args.no_email:
            print("  8. send sandbox-ready email")
            send_sandbox_ready_email(
                email,
                slug,
                product_name,
                first_name,
                signin_link,
                tenant_dir=target,
                repo_url=repo_url,
            )

        # Walkthrough email — only useful if the repo actually exists, so
        # skip silently when GitHub was unavailable.
        if not args.no_email and repo_url:
            print("  9. send walkthrough email")
            sandbox_url = f"https://{slug}.{DOMAIN}"
            anon_key = _extract_env_value(target / ".env", "VITE_SUPABASE_ANON_KEY") or ""
            send_walkthrough_email(
                email,
                slug,
                product_name,
                repo_url,
                first_name,
                first_prompt_template(product_name, idea, sandbox_url, anon_key),
            )

        reg = load_registry()
        reg["tenants"][slug]["status"] = "live"
        if repo_url:
            reg["tenants"][slug]["repo_url"] = repo_url
            reg["tenants"][slug]["github_username"] = github_username or None
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
