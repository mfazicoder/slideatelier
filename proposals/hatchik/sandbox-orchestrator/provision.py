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

This file is for Cursor, Windsurf, Claude Code, Cline, Codex,
Antigravity, or any other AI coding tool you point at this repo.
Read it first.

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

## Building your first feature — instructions for the AI

> **AI tool reading this**: the human is non-technical. They use you to
> vibe-code. They probably don't know git, Docker, or what "clone a
> repo" means. **You handle the mechanics; they describe the feature.**
>
> When they say things like "ship it", "deploy", "save my changes",
> "push it live", or "make it real" — just do it:
> 1. `git add -A && git commit -m "<short description>"`
> 2. `git push`
> Their sandbox redeploys in ~30s. Confirm when it's live.
>
> Don't ask the human to open a terminal, run `git`, or paste commands.
> If you can't run the command yourself in the current environment,
> POST to the deploy URL below instead (curl command is in this file).

### Recommended kick-off prompt

When the human first opens this repo in you, here's a sensible
starting prompt to internalise:

> {first_prompt}

Start by:
1. Reading this whole AI_CONTEXT.md (you're doing that now).
2. Reading `apps/web/src/App.tsx` and `apps/api/src/index.ts` to see
   the current routes wired in.
3. Asking the human ONE concrete question about the very first
   user-facing flow they want to see working. Not a list of options;
   ONE question.
4. Proposing a tiny, shippable first slice — a single page or a
   single API route. Push that. Get the feedback loop going.

Anything under `apps/web/src/product/`, `apps/api/src/product/`, and
`supabase/migrations/` is yours to edit freely. The "Don't edit" list
below is substrate plumbing — leave it alone.

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
        action_link = (
            data.get("action_link")
            or data.get("properties", {}).get("action_link")
        )
        # GoTrue templates the magic-link URL using whatever base address
        # we used to call its admin API. We address it via
        # `http://127.0.0.1:{port}/auth/v1` for network reachability
        # (the auth container is bound to localhost on the host), so
        # the action_link comes back pointed at that same internal
        # base — useless to a customer clicking from their browser.
        # Rewrite the host portion to the public sandbox URL.
        #
        # Defence-in-depth: substrate-template should also set
        # GOTRUE_API_EXTERNAL_URL so GoTrue templates correctly at
        # source. This rewrite keeps logins working for any tenant
        # that hasn't picked up that substrate change yet.
        if action_link:
            internal_base = f"http://127.0.0.1:{port}"
            public_base = f"https://{slug}.{DOMAIN}/auth/v1"
            if action_link.startswith(internal_base + "/"):
                # action_link is "http://127.0.0.1:18000/verify?token=..."
                # we want "https://slug.hatchik.com/auth/v1/verify?token=..."
                tail = action_link[len(internal_base):]  # "/verify?..."
                action_link = public_base + tail
        return action_link
    except httpx.HTTPError as e:
        print(f"WARN: GoTrue admin API call failed: {e}")
        return None


# Canonical API key generation lives in signup-service/main.py; this is a
# deliberate, audited duplication so provision.py can issue one key per
# signup at provision time and bake the plaintext into the sandbox-ready
# email. If you change the format or prefix, change it in both places
# (and bump callers + tests). Trade-off: plaintext-in-email is more
# leakable than the dashboard reveal-once flow — founder's call for the
# non-tech persona. Customer can always revoke + reissue from
# /account → API keys, and the auto-issued key is labelled accordingly.
_API_KEY_PREFIX = "hk_live_"
_API_KEY_RANDOM_BYTES = 24


def _issue_api_key_for_signup(email: str, label: str = "") -> str | None:
    """Generate an API key, insert into signups.db, return plaintext.

    Returns None on any DB error — the caller falls back to a placeholder
    string in the email so the sandbox-ready email always sends, even if
    the api_keys table is unreachable. (Failed key issuance must not
    block the customer's welcome flow; they can create one manually from
    /account → API keys.)
    """
    if not email:
        return None
    try:
        from datetime import datetime, timezone
        raw = secrets.token_bytes(_API_KEY_RANDOM_BYTES)
        body = base64.b32encode(raw).decode("ascii").lower().rstrip("=")
        plaintext = f"{_API_KEY_PREFIX}{body}"
        key_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        now_iso = datetime.now(timezone.utc).isoformat()
        name = (label or "Auto-issued at signup").strip()[:80]
        with sqlite3.connect(SIGNUPS_DB) as conn:
            conn.execute(
                "INSERT INTO api_keys (email, key_hash, name, created_at) "
                "VALUES (?, ?, ?, ?)",
                (email, key_hash, name, now_iso),
            )
            conn.commit()
        return plaintext
    except sqlite3.Error as e:
        print(f"WARN: couldn't issue API key for {email}: {e}")
        return None


def send_sandbox_ready_email(
    to: str,
    slug: str,
    product_name: str,
    first_name: str = "",
    signin_link: str | None = None,
    tenant_dir: Path | None = None,
    repo_url: str = "",
    api_key: str | None = None,
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

    # Repo link bits — the AI tool line in "Kick the tyres" can deep-link
    # to the customer's own repo when GitHub provisioned cleanly. The full
    # service inventory lives at hatchik.com/account → Services, not in
    # this email — non-tech founders find the technical detail (TLS,
    # subdomains, Postgres etc.) overwhelming and skip the whole email.
    repo_display = repo_url or "the GitHub repo we made for you"
    account_url = f"https://{DOMAIN}/account"
    # API-key fallback if issuance failed at provision time. The
    # placeholder matches what install.html prints, so a customer
    # who got the fallback can still recover by reading /install.
    api_key_display = api_key or "YOUR_HATCHIK_API_KEY"
    api_key_missing = api_key is None

    text = f"""{greeting}

Your sandbox for {product_name} is live.

{primary_link_label}
{primary_link}

You're pre-set as the owner — that link signs you straight in. No
password to remember (you can set one later from Settings if you want).

It's a real working version of your app stack. When your end-users
sign up, they get their own accounts inside it — your owner account
stays separate.

What's already working in your sandbox
──────────────────────────────────────

• Live website — anyone with the link can visit it; the button above
  signs you in as owner.
• Sign-up & sign-in — your test users can register and log in.
• Database — stores everything your app needs to remember.
• File storage — photos, documents, anything your app uploads.
• Email sending — sign-up and password-reset emails go out automatically.
• Test payments — Stripe in test mode. Use card 4242 4242 4242 4242,
  any future expiry, any CVC.
• Mobile app shells (iOS + Android) — ready to build into installable
  files. Trigger from {DOMAIN}/account → Mobile builds (8–15 min).
• Starter to-do list — features tailored to your idea, ready for the AI.
• £0.50 of AI credit — enough to wire up your first AI-powered feature.

Wire up your AI tool (one-time, ~2 min)
───────────────────────────────────────

Copy the JSON below and paste it into your AI tool's MCP config file.
Your Hatchik API key is already filled in for you.

{{
  "mcpServers": {{
    "hatchik": {{
      "command": "npx",
      "args": ["-y", "hatchik-mcp"],
      "env": {{
        "HATCHIK_API_KEY": "{api_key_display}",
        "HATCHIK_API_URL": "https://api.{DOMAIN}"
      }}
    }}
  }}
}}

Where to paste it:

  • Cursor       →  ~/.cursor/mcp.json
  • Windsurf     →  ~/.codeium/windsurf/mcp_config.json
  • Claude Code  →  ~/.claude/mcp.json
  • Cline        →  VS Code → Cline icon → MCP settings panel
  • Codex        →  ~/.codex/mcp.json
  • Antigravity  →  Settings → MCP Servers

Then restart your AI tool and tell it:
  "read AI_CONTEXT.md and let's start."

Full guide with per-tool screenshots: {DOMAIN}/install

When you're ready to go live
────────────────────────────

Upgrading to Launch adds your own domain, real (live) payments,
5 mailboxes on your domain, and a dedicated server.

Full breakdown of what's wired up — and how much of each you have —
lives in your account at {DOMAIN}/account → Services.

— Hatchik

Further information at {faq_url} if you need it.
"""
    install_url = f"https://{DOMAIN}/install"
    # Tile grid for "what's working". Each tile is one cell in a 2-column
    # table; on narrow viewports the @media query in the <head> stacks
    # them. We use emoji for icons because external image references in
    # email get blocked by default in most clients (Gmail's "Display
    # images" prompt), and SVG support in email is unreliable. Emoji
    # render natively and are colour-consistent across modern clients.
    tile = (
        '<td class="tile" valign="top" style="width:50%;padding:8px;">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;">'
        '<tr><td style="padding:14px 16px;">'
        '<div style="font-size:22px;line-height:1;margin-bottom:6px;">{icon}</div>'
        '<div style="font-weight:600;font-size:14px;color:#0f172a;margin-bottom:4px;">{title}</div>'
        '<div style="color:#475569;font-size:13px;line-height:1.5;">{body}</div>'
        '</td></tr></table>'
        '</td>'
    )
    tiles = [
        ("🌐", "Live website", "A working URL anyone can visit. The button above signs you in as owner."),
        ("🔐", "Sign-up &amp; sign-in", "Your test users can register and log in &mdash; email + magic link out of the box."),
        ("🗄️", "Database", "Stores everything your app needs to remember, with backups built in."),
        ("📁", "File storage", "For photos, documents, anything your app uploads."),
        ("✉️", "Email sending", "Sign-up and password-reset emails go out automatically."),
        ("💳", "Test payments", "Stripe in test mode. Use card <strong>4242 4242 4242 4242</strong>, any future expiry, any CVC."),
        ("📱", "Mobile app shells", "iOS + Android, ready to build. Trigger from your account &rarr; Mobile builds (8&ndash;15 min)."),
        ("✅", "Starter to-do list", "Features tailored to your idea, ready for the AI to start working through."),
        ("✨", "&pound;0.50 AI credit", "Enough to wire up your first AI-powered feature without setting up a provider key."),
    ]
    # Build rows of 2 tiles each. If the list length is odd, the final
    # row has one tile and one empty spacer cell so the layout doesn't
    # collapse.
    tile_rows_html = ""
    for i in range(0, len(tiles), 2):
        left = tile.format(icon=tiles[i][0], title=tiles[i][1], body=tiles[i][2])
        if i + 1 < len(tiles):
            right = tile.format(icon=tiles[i+1][0], title=tiles[i+1][1], body=tiles[i+1][2])
        else:
            right = '<td class="tile" style="width:50%;padding:8px;">&nbsp;</td>'
        tile_rows_html += f'<tr>{left}{right}</tr>'

    html = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Your {product_name} sandbox is ready</title>
  <style>
    @media only screen and (max-width:480px) {{
      .tile {{ display:block !important; width:100% !important; padding:6px 0 !important; }}
      .container {{ padding:20px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;background:#ffffff;border-radius:8px;">
          <tr>
            <td class="container" style="padding:32px;font-size:16px;line-height:1.6;color:#1a1a1a;">
              <p style="margin:0 0 16px 0;">{greeting}</p>
              <p style="margin:0 0 24px 0;">Your sandbox for <strong>{product_name}</strong> is live.</p>
              <p style="margin:0 0 16px 0;"><a href="{primary_link}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;">Sign in to your sandbox &rarr;</a></p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">You&rsquo;re pre-set as the owner &mdash; this link signs you straight in. No password to remember (you can set one later from Settings if you want).</p>
              <p style="margin:24px 0 24px 0;">It&rsquo;s a real working version of your app stack. When your end-users sign up, they get their own accounts inside it &mdash; your owner account stays separate.</p>

              <p style="margin:32px 0 12px 0;font-weight:700;font-size:18px;color:#0f172a;">What&rsquo;s already working in your sandbox</p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                {tile_rows_html}
              </table>

              <hr style="margin:32px 0 16px 0;border:none;border-top:1px solid #e5e7eb;">
              <p style="margin:0 0 8px 0;font-weight:700;font-size:18px;color:#0f172a;">Wire up your AI tool</p>
              <p style="margin:0 0 12px 0;color:#333;font-size:15px;">One-time, takes about 2 minutes. Copy the config below and paste it into your AI tool&rsquo;s MCP file. <strong>Your API key is already filled in.</strong></p>
              <div style="background:#0b1020;border-radius:8px;padding:16px;margin:0 0 16px 0;overflow-x:auto;">
                <pre style="margin:0;font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;font-size:12px;line-height:1.55;color:#e2e8f0;white-space:pre;">{{
  <span style="color:#a5b4fc;">"mcpServers"</span>: {{
    <span style="color:#a5b4fc;">"hatchik"</span>: {{
      <span style="color:#a5b4fc;">"command"</span>: <span style="color:#fcd34d;">"npx"</span>,
      <span style="color:#a5b4fc;">"args"</span>: [<span style="color:#fcd34d;">"-y"</span>, <span style="color:#fcd34d;">"hatchik-mcp"</span>],
      <span style="color:#a5b4fc;">"env"</span>: {{
        <span style="color:#a5b4fc;">"HATCHIK_API_KEY"</span>: <span style="color:#fcd34d;">"{api_key_display}"</span>,
        <span style="color:#a5b4fc;">"HATCHIK_API_URL"</span>: <span style="color:#fcd34d;">"https://api.{DOMAIN}"</span>
      }}
    }}
  }}
}}</pre>
              </div>
              <p style="margin:0 0 8px 0;font-weight:600;font-size:14px;color:#0f172a;">Where to paste it</p>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:0 0 16px 0;font-size:14px;color:#333;border-collapse:collapse;">
                <tr><td style="padding:6px 0;width:35%;color:#0f172a;font-weight:600;">Cursor</td><td style="padding:6px 0;font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;font-size:13px;color:#475569;">~/.cursor/mcp.json</td></tr>
                <tr><td style="padding:6px 0;color:#0f172a;font-weight:600;">Windsurf</td><td style="padding:6px 0;font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;font-size:13px;color:#475569;">~/.codeium/windsurf/mcp_config.json</td></tr>
                <tr><td style="padding:6px 0;color:#0f172a;font-weight:600;">Claude Code</td><td style="padding:6px 0;font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;font-size:13px;color:#475569;">~/.claude/mcp.json</td></tr>
                <tr><td style="padding:6px 0;color:#0f172a;font-weight:600;">Cline</td><td style="padding:6px 0;color:#475569;font-size:13px;">VS Code &rarr; Cline icon &rarr; MCP settings panel</td></tr>
                <tr><td style="padding:6px 0;color:#0f172a;font-weight:600;">Codex</td><td style="padding:6px 0;font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;font-size:13px;color:#475569;">~/.codex/mcp.json</td></tr>
                <tr><td style="padding:6px 0;color:#0f172a;font-weight:600;">Antigravity</td><td style="padding:6px 0;color:#475569;font-size:13px;">Settings &rarr; MCP Servers</td></tr>
              </table>
              <p style="margin:0 0 12px 0;color:#333;font-size:15px;">Restart your AI tool, then tell it:</p>
              <p style="margin:0 0 12px 0;padding:12px 16px;background:#f9fafb;border-left:3px solid #4f46e5;border-radius:4px;font-style:italic;color:#0f172a;">&ldquo;read <code style="background:#fff;padding:1px 4px;border-radius:3px;border:1px solid #e5e7eb;">AI_CONTEXT.md</code> and let&rsquo;s start.&rdquo;</p>
              <p style="margin:0 0 16px 0;color:#475569;font-size:13px;">Full guide with per-tool screenshots at <a href="{install_url}" style="color:#4f46e5;text-decoration:underline;">{DOMAIN}/install</a>.</p>

              <hr style="margin:32px 0 16px 0;border:none;border-top:1px solid #e5e7eb;">
              <p style="margin:0 0 8px 0;font-weight:700;font-size:18px;color:#0f172a;">When you&rsquo;re ready to go live</p>
              <p style="margin:0 0 12px 0;color:#333;font-size:15px;">Upgrading to Launch adds your own domain, real (live) payments, 5 mailboxes on your domain, and a dedicated server.</p>
              <p style="margin:0 0 24px 0;color:#475569;font-size:14px;">Full breakdown of what&rsquo;s wired up &mdash; and how much of each you have &mdash; lives in your account at <a href="{account_url}" style="color:#4f46e5;text-decoration:underline;">{DOMAIN}/account &rarr; Services</a>.</p>

              <p style="margin:32px 0 0 0;color:#0f172a;">&mdash; Hatchik</p>
              <p style="margin:16px 0 0 0;color:#555;font-size:14px;">Further information <a href="{faq_url}" style="color:#4f46e5;text-decoration:underline;">here</a> if you need it.</p>
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
    """Send the "build your first feature with your AI tool" follow-up.

    Written for the non-tech, vibe-coding founder: no git knowledge
    assumed, no terminal steps required. The recommended path is the
    web-only route (Codespaces / github.dev); the local-AI-tool route is
    presented as an alternative for people who already have Cursor /
    Claude Code installed.

    The "first prompt" content (substrate orientation, where to put new
    code, etc.) lives inside AI_CONTEXT.md in the customer's repo — see
    write_ai_context() — so the AI can self-serve when read with the
    one-line "read AI_CONTEXT.md and let's start" instruction.

    Fires right after send_sandbox_ready_email so the customer has both
    inbox messages by the time they sit down to actually build. Failures
    are logged but don't fail provisioning.
    """
    if not RESEND_API_KEY:
        print("WARN: no RESEND_API_KEY, skipping walkthrough email")
        return False
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    # docs.hatchik.com is under construction — link to the FAQ until it's
    # ready so customers don't land on a 404.
    docs_url = f"https://{DOMAIN}/#faq"
    install_url = f"https://{DOMAIN}/install"
    support_email = "support@hatchik.com"
    # github.dev opens a browser editor on the repo (no install needed);
    # Codespaces gives a full VS Code in the browser. Both are "click
    # the green Code button" pathways the customer can do without ever
    # touching a terminal.
    codespaces_url = repo_url
    githubdev_url = repo_url.replace("https://github.com/", "https://github.dev/") if repo_url else ""

    text = f"""{greeting}

How to build your first feature with your AI tool.

Your code lives in a GitHub repo we made for you. It's already a
working app — you just add the features that make it yours. Your AI
tool does the actual code-writing; you describe what you want.

Your repo: {repo_url}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Use it on the web (recommended)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

No installs. No terminal. Works in any browser.

1. Visit your repo: {repo_url}
2. Click the green "Code" button at the top right.
3. Pick one of:
   • "Open in GitHub Codespaces" — full editor in your browser, AI
     tools that support remote MCP read the repo directly.
   • "Open in github.dev" (or press . on the repo page) — quick
     in-browser file editor at {githubdev_url}.
4. Tell your AI helper: "read AI_CONTEXT.md and let's start."

That's it. AI_CONTEXT.md tells your AI everything it needs to know
about your sandbox — what's wired up, where to put new code, how to
deploy. You just describe the feature.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Or use a local AI tool on your laptop
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

If you prefer Cursor, Windsurf, Claude Code, Cline, Codex, or
Antigravity on your own laptop, your AI tool will get the repo for
you with one command — we'll guide you through it on first run.
After that:

• You tell the AI what you want to build.
• Your sandbox updates within about 30 seconds. Reload and look at it.

Setup instructions for each tool are at {install_url}.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Works with any AI coding tool: Cursor, Windsurf, Claude Code, Cline,
Codex, Antigravity, and others. If yours isn't listed, ask it — most
of them will just work.

Already built your app elsewhere? Reach out via {support_email} and
we'll help migrate your code in.

Reply if you're stuck — we'll help you get unstuck.

— Hatchik

More at {docs_url}.
"""
    html = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Build your first feature in {product_name}</title>
</head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;background:#ffffff;border-radius:8px;padding:32px;">
          <tr>
            <td style="font-size:16px;line-height:1.6;color:#1a1a1a;">
              <p style="margin:0 0 8px 0;font-weight:600;font-size:20px;color:#0f172a;">How to build your first feature with your AI tool</p>
              <p style="margin:0 0 24px 0;">{greeting}</p>

              <p style="margin:0 0 16px 0;">Your code lives in a GitHub repo we made for you. It&rsquo;s already a working app &mdash; you just add the features that make it yours. Your AI tool does the actual code-writing; you describe what you want.</p>

              <p style="margin:0 0 24px 0;"><a href="{repo_url}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:10px 20px;border-radius:8px;font-weight:600;">Open your repo on GitHub &rarr;</a></p>

              <hr style="margin:24px 0 16px 0;border:none;border-top:1px solid #e5e7eb;">
              <p style="margin:0 0 4px 0;font-weight:600;font-size:16px;color:#0f172a;">Use it on the web</p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;"><strong>Recommended for non-coders.</strong> No installs, no terminal, works in any browser.</p>

              <ol style="margin:0 0 16px 0;padding-left:20px;">
                <li style="margin:0 0 8px 0;">Visit <a href="{repo_url}" style="color:#4f46e5;text-decoration:underline;">your repo</a>.</li>
                <li style="margin:0 0 8px 0;">Click the green <strong>Code</strong> button (top right of the repo page).</li>
                <li style="margin:0 0 8px 0;">Pick one of:
                  <ul style="margin:6px 0 0 0;padding-left:18px;">
                    <li style="margin:0 0 4px 0;"><strong>Open in GitHub Codespaces</strong> &mdash; full VS Code in your browser. AI tools with remote MCP support read the repo directly.</li>
                    <li style="margin:0 0 4px 0;"><strong>Open in github.dev</strong> (or press <code style="background:#f6f5f1;padding:1px 4px;border-radius:3px;">.</code> on the repo page) &mdash; lightweight in-browser editor.</li>
                  </ul>
                </li>
                <li style="margin:0 0 8px 0;">Tell your AI helper: <em>&ldquo;read <code style="background:#f6f5f1;padding:1px 4px;border-radius:3px;">AI_CONTEXT.md</code> and let&rsquo;s start.&rdquo;</em></li>
              </ol>
              <p style="margin:0 0 16px 0;color:#333;font-size:14px;">That&rsquo;s it. <code style="background:#f6f5f1;padding:1px 4px;border-radius:3px;">AI_CONTEXT.md</code> tells your AI everything it needs to know about your sandbox &mdash; what&rsquo;s wired up, where to put new code, how to deploy. You just describe the feature.</p>

              <hr style="margin:24px 0 16px 0;border:none;border-top:1px solid #e5e7eb;">
              <p style="margin:0 0 4px 0;font-weight:600;font-size:16px;color:#0f172a;">Or use a local AI tool on your laptop</p>
              <p style="margin:0 0 16px 0;color:#555;font-size:14px;">If you prefer to run your AI on your own laptop:</p>
              <ul style="margin:0 0 16px 0;padding-left:20px;color:#333;font-size:14px;">
                <li style="margin:0 0 6px 0;">Your AI tool will get the repo for you with one command &mdash; we&rsquo;ll guide you through it on first run.</li>
                <li style="margin:0 0 6px 0;">You tell the AI what you want to build.</li>
                <li style="margin:0 0 6px 0;">Your sandbox updates within about 30 seconds. Reload and look at it.</li>
              </ul>
              <p style="margin:0 0 16px 0;color:#333;font-size:14px;">Setup instructions for each tool are at <a href="{install_url}" style="color:#4f46e5;text-decoration:underline;">{install_url}</a>.</p>

              <hr style="margin:24px 0 16px 0;border:none;border-top:1px solid #e5e7eb;">
              <p style="margin:0 0 8px 0;font-weight:600;font-size:15px;">Works with any AI coding tool</p>
              <p style="margin:0 0 16px 0;color:#333;font-size:14px;">Cursor, Windsurf, Claude Code, Cline, Codex, Antigravity, and others. If yours isn&rsquo;t listed, just ask it &mdash; most of them will work.</p>

              <p style="margin:0 0 16px 0;color:#333;font-size:14px;"><strong>Already built your app elsewhere?</strong> Reach out via <a href="mailto:{support_email}" style="color:#4f46e5;text-decoration:underline;">{support_email}</a> &mdash; we&rsquo;ll help migrate your code in.</p>

              <p style="margin:24px 0 16px 0;">Reply if you&rsquo;re stuck &mdash; we&rsquo;ll help you get unstuck.</p>

              <p style="margin:24px 0 0 0;">&mdash; Hatchik</p>
              <p style="margin:16px 0 0 0;color:#555;font-size:14px;">More at <a href="{docs_url}" style="color:#4f46e5;text-decoration:underline;">{docs_url}</a>.</p>
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
        "subject": f"Build your first feature in {product_name} with your AI tool",
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
            # Issue an API key for the customer before the email goes
            # out, so the MCP snippet in the email arrives pre-filled.
            # Failure here is non-fatal — the email function falls back
            # to "YOUR_HATCHIK_API_KEY" placeholder and the customer can
            # create one manually from /account → API keys.
            print("  8a. issue API key for the customer")
            api_key = _issue_api_key_for_signup(email, label="Auto-issued at signup")
            if api_key:
                print(f"     ✓ key issued ({api_key[:14]}…)")
            else:
                print("     (issuance failed — email will carry placeholder)")

            print("  8b. send sandbox-ready email")
            send_sandbox_ready_email(
                email,
                slug,
                product_name,
                first_name,
                signin_link,
                tenant_dir=target,
                repo_url=repo_url,
                api_key=api_key,
            )

        # Walkthrough email — DROPPED in favour of folding the one-line
        # "tell your AI to read AI_CONTEXT.md" instruction directly into
        # send_sandbox_ready_email above. Founder feedback on the
        # walkthrough email: "is it even needed or can the instructions
        # be super simplified and just included in the second email?"
        # — yes; the AI itself reads AI_CONTEXT.md and the install page
        # has the per-tool MCP wiring. A separate email was waffle.
        # send_walkthrough_email() is retained as a function for
        # historical reference + the AI_CONTEXT.md template helper it
        # carries; callers should not invoke it.

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
