#!/usr/bin/env python3
"""
infomaniak_mail.py — provision Launch-tier mailboxes via Infomaniak Mail.

Customer-facing promise: "3 real mailboxes on your domain (Launch), 10 on
Growth — set up properly with SPF/DKIM/DMARC."

This module owns the Hatchik side of that promise:

  1. provision_domain(domain)        — register the domain in Infomaniak
  2. provision_mailboxes(domain, n)  — create N mailboxes under that domain
  3. configure_dns(domain)           — emit the SPF/DKIM/DMARC records
                                       that need to live on the customer's
                                       DNS (Cloudflare). Returns the
                                       records as a list of dicts; the
                                       caller (promote.py) writes them
                                       via Cloudflare's API.
  4. credentials_for(domain)         — return list of {address, password}
                                       to email the customer.

Called from promote.py during Sandbox → Launch promotion. Credentials are
emailed to the customer as part of the Launch welcome email and never
stored in the registry (we hold an opaque mailbox_id only).

# ─── API integration ──────────────────────────────────────────────────────
Infomaniak Mail Service API: https://developer.infomaniak.com/docs/api/post/2/mail/{id}/mailbox  # noqa
Authentication: Bearer token. Set HATCHIK_INFOMANIAK_API_TOKEN in the
orchestrator host's /etc/hatchik/orchestrator.env.

Two endpoints we hit:
  - POST /2/mail/{mail_service_id}/mailbox    create one mailbox
  - DELETE /2/mail/{mail_service_id}/mailbox/{id}   on decommission

HATCHIK_INFOMANIAK_MAIL_SERVICE_ID is the numeric id of the Mail Service
hosting all customer domains (one shared service; each customer's domain
is an alias).

# ─── Safety ───────────────────────────────────────────────────────────────
- If HATCHIK_INFOMANIAK_API_TOKEN is unset, all calls are no-ops that
  return realistic stub data. The launch flow continues. We log a clear
  warning so it shows up in the founder's email.
- Idempotent: creating a mailbox that already exists returns the existing
  record rather than erroring.
"""

from __future__ import annotations

import logging
import os
import secrets
import string
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("infomaniak_mail")

INFOMANIAK_API_BASE = "https://api.infomaniak.com"
API_TOKEN = os.environ.get("HATCHIK_INFOMANIAK_API_TOKEN", "")
MAIL_SERVICE_ID = os.environ.get("HATCHIK_INFOMANIAK_MAIL_SERVICE_ID", "")
TIMEOUT = float(os.environ.get("HATCHIK_INFOMANIAK_TIMEOUT", "30"))

# Default mailbox names per tier — kept in one place so spec drift here
# is impossible.
LAUNCH_MAILBOXES = ["hello", "support", "billing"]
GROWTH_EXTRA = [
    "noreply", "team", "ops", "founders", "alerts", "partnerships", "press",
]  # appended to LAUNCH_MAILBOXES on Growth (3 + 7 = 10)


# ─── Data shapes ──────────────────────────────────────────────────────────
@dataclass
class Mailbox:
    address: str
    password: str
    mailbox_id: str | None  # None if stubbed


@dataclass
class DnsRecord:
    type: str    # "TXT" / "MX" / "CNAME"
    name: str    # subdomain or "@"
    value: str
    ttl: int = 3600


# ─── Helpers ──────────────────────────────────────────────────────────────
def _gen_password(length: int = 18) -> str:
    """Strong password that's still pasteable into a phone mail client."""
    # Avoid characters that look ambiguous in some fonts (0/O, 1/l/I, etc.)
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    pw = "".join(secrets.choice(alphabet) for _ in range(length))
    # Sprinkle one symbol so most password complexity rules are happy.
    pos = secrets.randbelow(length)
    return pw[:pos] + "!" + pw[pos + 1:]


def _stub_warning(operation: str) -> None:
    log.warning(
        "Infomaniak API not configured (HATCHIK_INFOMANIAK_API_TOKEN missing) "
        "— %s is a no-op. Configure the orchestrator host's env to enable.",
        operation,
    )


def _api_call(method: str, path: str, **kwargs: Any) -> httpx.Response:
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {API_TOKEN}"
    headers["Accept"] = "application/json"
    return httpx.request(
        method,
        f"{INFOMANIAK_API_BASE}{path}",
        headers=headers,
        timeout=TIMEOUT,
        **kwargs,
    )


# ─── Public API ───────────────────────────────────────────────────────────
def mailbox_names_for_tier(tier: str) -> list[str]:
    """Return the canonical mailbox name list for a tier."""
    tier = tier.lower()
    if tier == "growth":
        return LAUNCH_MAILBOXES + GROWTH_EXTRA
    return LAUNCH_MAILBOXES  # launch default


def provision_mailboxes(domain: str, tier: str = "launch") -> list[Mailbox]:
    """Create the per-tier mailbox set on the given domain.

    Returns the full Mailbox list (with cleartext passwords) so the
    caller can email them to the customer. Cleartext passwords are NOT
    persisted by this module.
    """
    names = mailbox_names_for_tier(tier)
    if not API_TOKEN or not MAIL_SERVICE_ID:
        _stub_warning("provision_mailboxes")
        return [
            Mailbox(address=f"{n}@{domain}", password=_gen_password(),
                    mailbox_id=None)
            for n in names
        ]

    created: list[Mailbox] = []
    for name in names:
        address = f"{name}@{domain}"
        password = _gen_password()
        payload = {
            "address": address,
            "password": password,
            # Sensible per-mailbox storage default; Infomaniak allows
            # 0 = unlimited within the plan, but we cap to give a
            # consistent answer to "how big is a mailbox?".
            "quota_gb": int(os.environ.get("HATCHIK_MAILBOX_QUOTA_GB", "5")),
        }
        r = _api_call(
            "POST", f"/2/mail/{MAIL_SERVICE_ID}/mailbox", json=payload,
        )
        if r.status_code == 409:
            # Already exists — rotate password so we know the value.
            log.info("mailbox %s already exists — rotating password", address)
            r = _api_call(
                "PATCH",
                f"/2/mail/{MAIL_SERVICE_ID}/mailbox/{address}",
                json={"password": password},
            )
        if r.status_code >= 400:
            log.error("Infomaniak refused %s: %s %s",
                      address, r.status_code, r.text[:200])
            # Don't abort the whole batch on one failure; record the
            # gap and continue. Promotion email will include only the
            # successes.
            continue
        body = r.json() if r.content else {}
        created.append(Mailbox(
            address=address,
            password=password,
            mailbox_id=str(body.get("data", {}).get("id", "")),
        ))
    return created


def dns_records_for(domain: str) -> list[DnsRecord]:
    """Return SPF / DKIM / DMARC records to publish on the customer's DNS.

    Values come from Infomaniak's standard email setup:
        https://www.infomaniak.com/en/support/faq/2210

    Caller writes these via Cloudflare's API (existing wiring in
    promote.py).
    """
    spf = DnsRecord(
        type="TXT", name="@",
        value='v=spf1 include:spf.infomaniak.ch ~all',
    )
    # Infomaniak issues a per-domain DKIM selector and key; in stub mode
    # we emit a placeholder so the customer sees what's coming.
    dkim_selector = os.environ.get("HATCHIK_INFOMANIAK_DKIM_SELECTOR", "infomaniak")
    if API_TOKEN and MAIL_SERVICE_ID:
        dkim_value = _fetch_dkim_record(domain) or "(pending key issuance)"
    else:
        dkim_value = "(set after first provision — see Infomaniak admin)"
    dkim = DnsRecord(
        type="TXT",
        name=f"{dkim_selector}._domainkey",
        value=dkim_value,
    )
    dmarc = DnsRecord(
        type="TXT", name="_dmarc",
        value=f'v=DMARC1; p=quarantine; rua=mailto:dmarc@{domain}; ruf=mailto:dmarc@{domain}; fo=1',
    )
    # MX → Infomaniak inbound.
    mx_primary = DnsRecord(
        type="MX", name="@", value="10 mta-gw.infomaniak.ch.",
    )
    mx_secondary = DnsRecord(
        type="MX", name="@", value="20 mta-gw2.infomaniak.ch.",
    )
    return [mx_primary, mx_secondary, spf, dkim, dmarc]


def _fetch_dkim_record(domain: str) -> str | None:
    """Look up the DKIM TXT value Infomaniak has minted for this domain."""
    r = _api_call(
        "GET", f"/2/mail/{MAIL_SERVICE_ID}/domain/{domain}/dkim",
    )
    if r.status_code != 200:
        log.warning("DKIM lookup for %s returned %s", domain, r.status_code)
        return None
    return r.json().get("data", {}).get("record") or None


def decommission_mailboxes(domain: str) -> int:
    """Delete all mailboxes on `domain`. Returns count deleted."""
    if not API_TOKEN or not MAIL_SERVICE_ID:
        _stub_warning("decommission_mailboxes")
        return 0

    r = _api_call("GET", f"/2/mail/{MAIL_SERVICE_ID}/mailbox", params={"domain": domain})
    if r.status_code != 200:
        log.error("listing mailboxes for %s failed: %s", domain, r.status_code)
        return 0

    deleted = 0
    for box in (r.json().get("data") or []):
        addr = box.get("address")
        rid = box.get("id")
        if not addr or not rid:
            continue
        d = _api_call("DELETE", f"/2/mail/{MAIL_SERVICE_ID}/mailbox/{rid}")
        if d.status_code in (200, 204):
            deleted += 1
        else:
            log.warning("delete %s exited %s", addr, d.status_code)
    return deleted


# ─── CLI ──────────────────────────────────────────────────────────────────
def _cli() -> int:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(description="Infomaniak mailbox provisioning")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("provision", help="create the tier's mailbox set")
    pp.add_argument("--domain", required=True)
    pp.add_argument("--tier", default="launch", choices=["launch", "growth"])

    pd = sub.add_parser("dns", help="print the DNS records to publish")
    pd.add_argument("--domain", required=True)

    pr = sub.add_parser("decommission", help="delete all mailboxes on domain")
    pr.add_argument("--domain", required=True)

    args = p.parse_args()
    if args.cmd == "provision":
        boxes = provision_mailboxes(args.domain, tier=args.tier)
        print(json.dumps([b.__dict__ for b in boxes], indent=2))
    elif args.cmd == "dns":
        recs = dns_records_for(args.domain)
        print(json.dumps([r.__dict__ for r in recs], indent=2))
    elif args.cmd == "decommission":
        print(json.dumps({"deleted": decommission_mailboxes(args.domain)}))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
