"""service_inventory.py — single source of truth for "what's in a Hatchik sandbox".

This module is the canonical answer to "what did the customer just get?". It
backs three customer-facing surfaces, and they must all agree:

  1. The "What's set up for you" / "What's NOT yet wired" block in the
     sandbox-ready email (sandbox-orchestrator/provision.py).
  2. The Services tab on /account (signup-service/main.py serves the data
     at GET /api/account/services/<slug>; account.html renders it).
  3. The "What's included" docs page (docs/what-is-included.html), which
     mirrors the same enumeration so the pre- and post-signup story line
     up word-for-word.

Numbers cited here are either:
  - configured in code (e.g. mem_limit in substrate-template's
    docker-compose.yml, rate-limit constants in signup-service/main.py)
  - policy defaults that we deliberately publish (e.g. 100 emails/day on
    Sandbox tier, which is a soft cap we enforce socially today and will
    enforce in code once per-tenant Resend subkeys land).

Honesty over salesmanship — capabilities that aren't wired are listed as
"NOT yet wired", not glossed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

# ─── Quantification constants (the single source of truth) ──────────────
# Sandbox tier — Postgres RAM cap, per `mem_limit` in substrate-template's
# docker-compose.yml. The full per-tenant total is ~1.3 GB; we cite the
# Postgres line because that's the bottleneck for the database workload.
SANDBOX_POSTGRES_RAM_MB = 512
# Per-tenant filesystem on the shared CAX21 host. Practical ceiling before
# host-level cleanup is needed — not a hard quota, deliberately published
# so customers don't store backup blobs in their tenant.
SANDBOX_DISK_GB_PRACTICAL = 10
# Storage RAM cap on supabase-storage (RAM working set, not on-disk
# capacity — on-disk piggybacks the disk budget above).
SANDBOX_STORAGE_RAM_MB = 128
# Resend SMTP soft cap on the Sandbox tier. Hatchik's Resend account is on
# the free tier (3K/mo across all sandboxes); 100/day per tenant fits
# comfortably while staying transparent.
SANDBOX_EMAIL_DAILY_SOFT_CAP = 100
# Mobile builds via GitHub Actions — capped at 3/hour per tenant in
# signup-service (HATCHIK_MOBILE_BUILD_RATE_LIMIT_MAX).
MOBILE_BUILDS_PER_HOUR = 3
# Redeploy webhook rate limit (HATCHIK_REDEPLOY_RATE_LIMIT_MAX /
# HATCHIK_REDEPLOY_RATE_LIMIT_WINDOW_SECONDS). 6 redeploys per 5 minutes.
REDEPLOY_PER_5MIN = 6
REDEPLOY_LATENCY_SEC = 30  # observed steady-state, end-to-end
# Backlog seeding — about twenty starter tasks, generated once at provision.
BACKLOG_STARTER_TASKS = 20

# GitHub Actions free-tier monthly minute budgets. We cite both because
# the macOS leg is 10x more expensive and the customer should understand
# why we cap at 3 builds/hour.
GITHUB_ACTIONS_MACOS_MIN_MONTHLY = 500
GITHUB_ACTIONS_LINUX_MIN_MONTHLY = 2000


# ─── Service inventory (per tier) ────────────────────────────────────────
# Each "wired" entry describes a capability that ships on Sandbox tier
# today. Each "available_on_upgrade" entry describes something the
# customer can move up to (Launch or self-serve).
#
# Schema (kept stable so the /api/account/services endpoint can return
# this verbatim plus tenant-specific overlays):
#
#   {
#     "name": str,             # short display label
#     "detail": str,           # one-line description with quantification
#     "status": str | None,    # "live" | "test-mode" | "not_configured"
#                              # | "policy" (a soft, social cap)
#     "configure_url": str?,   # optional deep link (substitutes {sandbox_url})
#     "category": str,         # "compute" | "auth" | "storage" | "email"
#                              # | "payments" | "mobile" | "code" | "deploy"
#                              # | "docs"
#   }

_SANDBOX_WIRED_BASE: list[dict[str, Any]] = [
    {
        "name": "Subdomain",
        "detail": "1 subdomain at <slug>.hatchik.com with wildcard TLS.",
        "status": "live",
        "configure_url": "{sandbox_url}",
        "category": "compute",
    },
    {
        "name": "Postgres database",
        "detail": (
            f"Supabase-managed Postgres, {SANDBOX_POSTGRES_RAM_MB} MB RAM cap. "
            f"Disk shares the host budget — ~{SANDBOX_DISK_GB_PRACTICAL} GB "
            "practical before we'd ask you to upgrade."
        ),
        "status": "live",
        "configure_url": "{sandbox_url}/studio",
        "category": "compute",
    },
    {
        "name": "Authentication",
        "detail": (
            "Supabase Auth — magic-link + email/password out of the box. "
            "Unlimited test users; Google OAuth: not configured (add when ready)."
        ),
        "status": "live",
        "configure_url": "{sandbox_url}/studio",
        "category": "auth",
    },
    {
        "name": "File storage",
        "detail": (
            f"Supabase Storage, {SANDBOX_STORAGE_RAM_MB} MB working RAM. "
            "Bucket size shares the tenant disk budget."
        ),
        "status": "live",
        "configure_url": "{sandbox_url}/studio",
        "category": "storage",
    },
    {
        "name": "Realtime",
        "detail": "Supabase Realtime — subscribe to row changes from the web client.",
        "status": "live",
        "configure_url": "{sandbox_url}/studio",
        "category": "compute",
    },
    {
        "name": "API backend",
        "detail": "FastAPI under apps/api/ — add your routes; redeploys on push.",
        "status": "live",
        "configure_url": "{repo_url}",
        "category": "code",
    },
    {
        "name": "Web frontend",
        "detail": "React + Vite under apps/web/ — your hot-reloaded UI.",
        "status": "live",
        "configure_url": "{repo_url}",
        "category": "code",
    },
    {
        "name": "Transactional email",
        "detail": (
            f"Resend SMTP shared from noreply@hatchik.com. Soft cap "
            f"~{SANDBOX_EMAIL_DAILY_SOFT_CAP} emails/day on Sandbox tier "
            "(Resend free tier is 3K/mo across all sandboxes; bring your "
            "own RESEND_API_KEY for production sends)."
        ),
        "status": "policy",
        "configure_url": None,
        "category": "email",
    },
    {
        "name": "Payments",
        "detail": (
            "Stripe SDK wired in test mode. Live Stripe is customer "
            "self-serve — paste your live keys into .env."
        ),
        "status": "test-mode",
        "configure_url": "{repo_url}",
        "category": "payments",
    },
    {
        "name": "Mobile builds",
        "detail": (
            f"iOS IPA + Android APK via GitHub Actions, capped at "
            f"{MOBILE_BUILDS_PER_HOUR} builds/hour per tenant. GitHub free "
            f"tier: {GITHUB_ACTIONS_MACOS_MIN_MONTHLY} macOS min/mo, "
            f"{GITHUB_ACTIONS_LINUX_MIN_MONTHLY} Linux min/mo. Binaries "
            "are unsigned — store submission is yours."
        ),
        "status": "live",
        "configure_url": "https://hatchik.com/account",
        "category": "mobile",
    },
    {
        "name": "Private GitHub repo",
        "detail": "One private repo under the hatchik-sandboxes org per active sandbox.",
        "status": "live",
        "configure_url": "{repo_url}",
        "category": "code",
    },
    {
        "name": "Push-to-deploy",
        "detail": (
            f"Redeploy webhook fires on every push (~{REDEPLOY_LATENCY_SEC}s "
            f"end-to-end). Rate-limited to {REDEPLOY_PER_5MIN} redeploys "
            "per 5-minute window."
        ),
        "status": "live",
        "configure_url": "{repo_url}",
        "category": "deploy",
    },
    {
        "name": "BACKLOG.md",
        "detail": (
            f"~{BACKLOG_STARTER_TASKS} starter tasks tailored to your idea, "
            "seeded once at provision. Read+write by any AI tool."
        ),
        "status": "live",
        "configure_url": "{repo_url}",
        "category": "docs",
    },
    {
        "name": "AI_CONTEXT.md",
        "detail": (
            "Substrate map + deploy token + first-prompt template. Drop into "
            "Claude Code, Cursor, or Windsurf and start building."
        ),
        "status": "live",
        "configure_url": "{repo_url}",
        "category": "docs",
    },
]


_SANDBOX_AVAILABLE_ON_UPGRADE: list[dict[str, Any]] = [
    {
        "name": "Custom domain",
        "tier": "launch",
        "blurb": "Bring your own domain or register a new one. Year-one registration in the £79.",
    },
    {
        "name": "Mailboxes (hello@, support@…)",
        "tier": "launch",
        "blurb": "Up to 5 real mailboxes on your domain via Infomaniak Mail, SPF/DKIM/DMARC wired.",
    },
    {
        "name": "Live payments",
        "tier": "self-serve",
        "blurb": "Connect your own Stripe (or Paddle MoR on Launch) — swap the test keys for live in .env.",
    },
    {
        "name": "Per-tenant Resend key (full deliverability)",
        "tier": "self-serve",
        "blurb": "Bring your own RESEND_API_KEY for higher volume and a sender on your own domain.",
    },
    {
        "name": "Google OAuth",
        "tier": "self-serve",
        "blurb": "Register a Google OAuth app, paste client_id/secret into .env, flip GOOGLE_OAUTH_ENABLED.",
    },
    {
        "name": "Automated backups",
        "tier": "roadmap",
        "blurb": (
            "Nightly off-site Postgres backups on Backblaze B2 are on the roadmap. "
            "Today: on-demand pg_dump from Supabase Studio."
        ),
    },
    {
        "name": "App Store / Play Store submission",
        "tier": "customer",
        "blurb": (
            "Hatchik never submits on your behalf — needs your Apple Developer "
            "Program (~£99/yr) and Google Play Console (~£25 once)."
        ),
    },
]


# ─── Tenant-state probes ────────────────────────────────────────────────
def _read_env_value(env_file: Path, key: str) -> str:
    """Return the value of `key` in a dotenv file, or '' if absent/blank.

    Mirrors provision.py's _extract_env_value but lives here so the
    signup-service can call it without subprocess'ing provision.py.
    """
    if not env_file.exists():
        return ""
    try:
        for raw in env_file.read_text().splitlines():
            line = raw.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _detect_dynamic_status(tenant_dir: Path | None) -> dict[str, dict[str, str]]:
    """Inspect the tenant's .env to refine status on a few capabilities.

    Returns a {name: {status, detail_override?}} map keyed by inventory
    entry name. Items not in the map use the static defaults.
    """
    overrides: dict[str, dict[str, str]] = {}
    if tenant_dir is None:
        return overrides
    env_file = tenant_dir / ".env"

    # Stripe — live keys vs test
    stripe_secret = _read_env_value(env_file, "STRIPE_SECRET_KEY")
    if stripe_secret:
        if stripe_secret.startswith("sk_live_"):
            overrides["Payments"] = {
                "status": "live",
                "detail": "Stripe SDK wired with your live keys. Subscriptions, Checkout — go live.",
            }
        elif stripe_secret.startswith("sk_test_"):
            overrides["Payments"] = {
                "status": "test-mode",
                "detail": "Stripe SDK wired in test mode. Swap the keys in .env when you're ready to go live.",
            }
        # else: empty placeholder leaves the static default

    # Google OAuth
    google_enabled = _read_env_value(env_file, "GOOGLE_OAUTH_ENABLED").lower()
    google_id = _read_env_value(env_file, "GOOGLE_OAUTH_CLIENT_ID")
    if google_enabled == "true" and google_id:
        overrides["Authentication"] = {
            "status": "live",
            "detail": "Supabase Auth — magic-link + email/password + Google OAuth (all wired). Unlimited test users.",
        }

    # Resend — has the customer brought their own key?
    customer_resend = _read_env_value(env_file, "CUSTOMER_RESEND_API_KEY")
    if customer_resend:
        overrides["Transactional email"] = {
            "status": "live",
            "detail": (
                "Resend SMTP — your own key, your own sender domain. No Hatchik "
                "soft cap; you pay Resend directly."
            ),
        }

    # Custom domain — if SITE_URL doesn't end in .hatchik.com, treat it
    # as a Launch-tier setup (the customer has a real domain).
    site_url = _read_env_value(env_file, "SITE_URL")
    if site_url and ".hatchik.com" not in site_url:
        overrides["Subdomain"] = {
            "status": "live",
            "detail": f"Custom domain configured: {site_url}.",
        }

    return overrides


# ─── Public API ─────────────────────────────────────────────────────────
def sandbox_inventory(
    *,
    sandbox_url: str = "",
    repo_url: str = "",
    tenant_dir: Path | None = None,
) -> dict[str, Any]:
    """Return the live "wired" + "available on upgrade" inventory for a tenant.

    Pass `tenant_dir` to enable .env probes (Stripe live keys, Google
    OAuth, custom Resend key, etc.). Omit it for the static default
    (used by the freshly-provisioned email, where every flag is still
    the substrate default).
    """
    overrides = _detect_dynamic_status(tenant_dir) if tenant_dir else {}

    wired: list[dict[str, Any]] = []
    for entry in _SANDBOX_WIRED_BASE:
        item = dict(entry)
        # Apply per-tenant override if any.
        override = overrides.get(item["name"])
        if override:
            if "status" in override:
                item["status"] = override["status"]
            if "detail" in override:
                item["detail"] = override["detail"]
        # Substitute {sandbox_url} / {repo_url} placeholders.
        if item.get("configure_url"):
            item["configure_url"] = (
                item["configure_url"]
                .replace("{sandbox_url}", sandbox_url or "")
                .replace("{repo_url}", repo_url or "")
            )
            # If the substitution left an empty URL, drop the field so
            # the UI doesn't render a dead link.
            if not item["configure_url"]:
                item["configure_url"] = None
        wired.append(item)

    return {
        "tier": "sandbox",
        "wired": wired,
        "available_on_upgrade": [dict(e) for e in _SANDBOX_AVAILABLE_ON_UPGRADE],
    }


def email_lines(inventory: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Render the inventory as plain-text bullet lists for the email body.

    Returns (wired_lines, upgrade_lines). Lines do not have a leading
    indent — caller indents them to match the surrounding block.
    """
    wired_lines: list[str] = []
    for w in inventory["wired"]:
        marker = "✓"
        if w.get("status") == "test-mode":
            marker = "○"
        elif w.get("status") == "not_configured":
            marker = "○"
        wired_lines.append(f"{marker} {w['name']} — {w['detail']}")

    upgrade_lines: list[str] = []
    for u in inventory["available_on_upgrade"]:
        tier = u.get("tier", "")
        tier_tag = {
            "launch": "→ upgrade to Launch",
            "self-serve": "→ self-serve in .env",
            "roadmap": "→ roadmap",
            "customer": "→ requires your own account",
        }.get(tier, "")
        upgrade_lines.append(f"– {u['name']} {tier_tag} — {u['blurb']}")

    return wired_lines, upgrade_lines


def html_blocks(inventory: dict[str, Any]) -> tuple[str, str]:
    """Render the inventory as two HTML <ul> blocks for the email.

    Returns (wired_html, upgrade_html). Both are inline-styled <ul> blocks
    suitable for inserting into the existing email template.
    """
    def _esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
        )

    wired_items = []
    for w in inventory["wired"]:
        status = w.get("status")
        if status == "test-mode" or status == "not_configured":
            icon = (
                '<span style="display:inline-block;width:18px;color:#d97706;'
                'font-weight:700;">○</span>'
            )
        else:
            icon = (
                '<span style="display:inline-block;width:18px;color:#059669;'
                'font-weight:700;">✓</span>'
            )
        wired_items.append(
            f'<li style="margin:0 0 8px 0;list-style:none;padding-left:0;">'
            f'{icon}<strong>{_esc(w["name"])}</strong> '
            f'<span style="color:#475569;">— {_esc(w["detail"])}</span></li>'
        )
    wired_html = (
        '<ul style="margin:0 0 16px 0;padding:0;list-style:none;">'
        + "".join(wired_items)
        + "</ul>"
    )

    upgrade_items = []
    for u in inventory["available_on_upgrade"]:
        tier_tag = {
            "launch": "upgrade to Launch",
            "self-serve": "self-serve in .env",
            "roadmap": "roadmap",
            "customer": "your own account",
        }.get(u.get("tier", ""), "")
        upgrade_items.append(
            f'<li style="margin:0 0 8px 0;list-style:none;padding-left:0;">'
            f'<span style="display:inline-block;width:18px;color:#94a3b8;'
            f'font-weight:700;">–</span>'
            f'<strong>{_esc(u["name"])}</strong> '
            f'<span style="color:#94a3b8;">({_esc(tier_tag)})</span> '
            f'<span style="color:#475569;">— {_esc(u["blurb"])}</span></li>'
        )
    upgrade_html = (
        '<ul style="margin:0 0 16px 0;padding:0;list-style:none;">'
        + "".join(upgrade_items)
        + "</ul>"
    )

    return wired_html, upgrade_html


# Re-export the symbols the consumers need.
__all__ = [
    "SANDBOX_POSTGRES_RAM_MB",
    "SANDBOX_DISK_GB_PRACTICAL",
    "SANDBOX_STORAGE_RAM_MB",
    "SANDBOX_EMAIL_DAILY_SOFT_CAP",
    "MOBILE_BUILDS_PER_HOUR",
    "REDEPLOY_PER_5MIN",
    "REDEPLOY_LATENCY_SEC",
    "BACKLOG_STARTER_TASKS",
    "GITHUB_ACTIONS_MACOS_MIN_MONTHLY",
    "GITHUB_ACTIONS_LINUX_MIN_MONTHLY",
    "sandbox_inventory",
    "email_lines",
    "html_blocks",
]
