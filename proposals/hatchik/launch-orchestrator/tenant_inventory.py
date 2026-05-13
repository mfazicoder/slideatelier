"""
Launch-tier tenant inventory — source of truth for what's wired in a
Launch / Growth tenant.

Mirrors sandbox-orchestrator/service_inventory.py for the paid tiers.
Used by:
  - signup-service's /api/account/services/{slug} (when slug is a Launch
    tenant), so the customer sees a consistent "what's set up" view
  - promote.py to template the welcome email
  - docs/what-is-included.html as a single rendering source

Versions are intentional. Bump them when the inventory changes — the
account dashboard renders the version next to the inventory date so the
customer knows their list is current.
"""

from __future__ import annotations

from typing import Any

LAUNCH_INVENTORY_VERSION = "2026.05.01"


def launch_inventory(growth: bool = False) -> dict[str, Any]:
    """Return what's wired in a Launch (or Growth) tenant.

    Shape matches sandbox_inventory()'s contract so the account dashboard
    can render either with the same code path.
    """
    tier = "growth" if growth else "launch"
    server_class = "CAX41 (8 vCPU, 16 GB RAM)" if growth else "CAX31 (4 vCPU, 8 GB RAM)"
    ai_allowance = "£10/month included (~500k tokens)" if growth else "£3/month included (~150k tokens)"
    support_sla = "Same business day" if growth else "1 business day"
    backup_retention = "30 days rolling + monthly archive (12 months)" if growth else "14 days rolling"
    domain_renewal = "Free annual renewal from year 2" if growth else "First year included; £14/yr from year 2 (passthrough)"

    return {
        "tier": tier,
        "version": LAUNCH_INVENTORY_VERSION,
        "wired": [
            {
                "name": "Dedicated VPS",
                "detail": f"{server_class} in your chosen region",
                "quota": "100% of one server",
            },
            {
                "name": "Postgres",
                "detail": "Unlimited size, unlimited connections",
                "quota": "Bounded only by disk",
            },
            {
                "name": "Authentication",
                "detail": "Email, magic-link, 6-digit code, Google OAuth",
                "quota": "Unlimited users",
            },
            {
                "name": "File storage",
                "detail": "Supabase Storage on customer's VPS",
                "quota": "Bounded only by disk",
            },
            {
                "name": "Custom domain",
                "detail": "Your own domain or one we register for you",
                "quota": "Included",
            },
            {
                "name": "TLS + edge",
                "detail": "Cloudflare proxy + Caddy origin TLS",
                "quota": "Free Cloudflare tier",
            },
            {
                "name": "Mailboxes",
                "detail": "Infomaniak mailboxes on your domain",
                "quota": "5 inboxes",
            },
            {
                "name": "Transactional email",
                "detail": "Resend (we pay)",
                "quota": "3,000 emails/month included",
            },
            {
                "name": "Payments (substrate)",
                "detail": "Stripe Checkout + Customer Portal, or Paddle (your choice)",
                "quota": "Live mode",
            },
            {
                "name": "Mobile shells",
                "detail": "iOS + Android (store-submittable; you handle App Store / Play submission)",
                "quota": "Unlimited builds",
            },
            {
                "name": "AI token allowance",
                "detail": ai_allowance,
                "quota": "Passthrough at provider cost above",
            },
            {
                "name": "Backups",
                "detail": backup_retention,
                "quota": "Off-site to Backblaze B2",
            },
            {
                "name": "Domain renewal",
                "detail": domain_renewal,
                "quota": "Year 1 included",
            },
            {
                "name": "Substrate updates",
                "detail": "Opt-in PRs to your repo when we ship features"
                          + (" — Growth gets 2-week early access" if growth else ""),
                "quota": "All updates",
            },
            {
                "name": "Support",
                "detail": f"Email, {support_sla} response",
                "quota": "Unlimited",
            },
        ],
        "available_on_upgrade": (
            [
                {"tier": "growth",
                 "name": "Bigger VPS",
                 "detail": "CAX41 (8 vCPU, 16 GB) — Growth tier"},
                {"tier": "growth",
                 "name": "Extended retention",
                 "detail": "30-day rolling backups + 12-month monthly archive"},
                {"tier": "growth",
                 "name": "More AI allowance",
                 "detail": "£10/month (~500k tokens) included"},
                {"tier": "growth",
                 "name": "Founder time",
                 "detail": "Quarterly 30-min 1:1 with the Hatchik team"},
                {"tier": "growth",
                 "name": "Early-access updates",
                 "detail": "Substrate features 2 weeks before Launch tier"},
            ]
            if not growth
            else []
        ),
    }


__all__ = ["launch_inventory", "LAUNCH_INVENTORY_VERSION"]
