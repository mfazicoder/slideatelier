#!/usr/bin/env python3
"""
porkbun_domain.py — register customer domains via Porkbun's API.

Customer-facing promise: "Year-1 registration is included free for the
most common TLDs — .com, .co, .net, .org, .uk, .co.uk, .app, .dev, .tech,
.online (anything reliably £14/yr or less)."

This module owns the Hatchik side of that promise:

  1. is_available(domain)          — check availability + price
  2. price_for(domain)             — return retail price in pence
  3. register(domain, contact)     — register for 1 year, register at
                                     the rate Hatchik's reseller account
                                     gets, NOT the retail price
  4. set_nameservers(domain, [..]) — point at Cloudflare (so dns_api
                                     can write A/MX/TXT/etc. as usual)
  5. renew(domain, years)          — used by the Growth tier (free
                                     annual renewal) and as a fallback
                                     if a customer wants to renew early
  6. transfer_out_unlock(domain)   — needed on customer exit; no
                                     transfer fee from us

Called from promote.py during Sandbox → Launch promotion. Domain ownership
is registered to the customer's name + address (collected by start.html);
Hatchik never owns the customer's domain.

# ─── API integration ──────────────────────────────────────────────────────
Porkbun JSON API: https://porkbun.com/api/json/v3/documentation
Auth: each request POSTs apikey + secretapikey in the JSON body.
Set HATCHIK_PORKBUN_API_KEY + HATCHIK_PORKBUN_SECRET in
/etc/hatchik/orchestrator.env.

Endpoints we hit:
  - POST /pricing/get                    list of retail prices per TLD
  - POST /domain/checkDomain/{domain}    availability
  - POST /domain/register/{domain}       1-year registration
  - POST /domain/updateNs/{domain}       point at Cloudflare nameservers
  - POST /domain/renew/{domain}          renewal (Growth tier)
  - POST /domain/getNs/{domain}          state check

# ─── Pricing policy ──────────────────────────────────────────────────────
Per PRODUCT_OFFERING §2.2:
  - Standard TLDs (.com/.co/.net/.org/.uk/.co.uk/.app/.dev/.tech/.online):
    we cover the full year-1 cost out of the £89 setup fee.
  - Premium TLDs (.ai, .io, .tv, .gg, .so, .me, .xyz, …):
    we cover up to £14/yr; the customer covers the difference. The
    signup wizard's domain quote (signup-service/domains.py) calculates
    the passthrough at checkout time using Porkbun's wholesale price ×
    1.0 (no markup).

# ─── Safety ───────────────────────────────────────────────────────────────
- If HATCHIK_PORKBUN_API_KEY is unset, all mutations are no-ops that
  return realistic stub data. The launch flow continues; founder gets
  warned via promote.py's email summary.
- Idempotent: register() returns "already registered" cleanly if the
  domain was registered in a previous attempt.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("porkbun_domain")

API_BASE = "https://api.porkbun.com/api/json/v3"
API_KEY = os.environ.get("HATCHIK_PORKBUN_API_KEY", "")
API_SECRET = os.environ.get("HATCHIK_PORKBUN_SECRET", "")
TIMEOUT = float(os.environ.get("HATCHIK_PORKBUN_TIMEOUT", "30"))

# Per spec — the TLDs we'll fully cover under year-1 included
INCLUDED_TLDS = {
    "com", "co", "net", "org", "uk", "co.uk",
    "app", "dev", "tech", "online",
}
# Pence — anything over this, customer covers the difference.
PRICE_CEILING_PENCE = int(os.environ.get("HATCHIK_DOMAIN_PRICE_CEILING_PENCE", "1400"))

# Cloudflare nameservers we point everything at — registered domains
# get DNS managed via dns_api.py.
CLOUDFLARE_NAMESERVERS = [
    os.environ.get("HATCHIK_CF_NS1", "ns1.cloudflare.com"),
    os.environ.get("HATCHIK_CF_NS2", "ns2.cloudflare.com"),
]


# ─── Data shapes ──────────────────────────────────────────────────────────
@dataclass
class Availability:
    domain: str
    available: bool
    price_pence: int
    premium: bool   # outside INCLUDED_TLDS
    coverage_pence: int   # what Hatchik covers
    customer_pence: int   # what the customer pays at checkout


@dataclass
class Contact:
    """The legal registrant. Customer info captured at signup."""
    first_name: str
    last_name: str
    email: str
    phone: str         # E.164 e.g. +447700900123
    address_line_1: str
    city: str
    postcode: str
    country: str       # ISO-2 country code
    organisation: str | None = None


# ─── Helpers ──────────────────────────────────────────────────────────────
def _stub_warning(operation: str) -> None:
    log.warning(
        "Porkbun API not configured (HATCHIK_PORKBUN_API_KEY missing) "
        "— %s is a no-op. Configure /etc/hatchik/orchestrator.env to enable.",
        operation,
    )


def _api_call(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"apikey": API_KEY, "secretapikey": API_SECRET, **(body or {})}
    r = httpx.post(f"{API_BASE}{path}", json=payload, timeout=TIMEOUT)
    try:
        data = r.json()
    except ValueError:
        log.error("Porkbun returned non-JSON at %s: %s", path, r.text[:200])
        return {"status": "ERROR", "message": "non-json response"}
    return data


def _tld_of(domain: str) -> str:
    parts = domain.lower().split(".")
    # .co.uk style
    if len(parts) >= 3 and parts[-2] in {"co", "ac", "gov", "org"} and parts[-1] == "uk":
        return f"{parts[-2]}.{parts[-1]}"
    return parts[-1] if len(parts) >= 2 else domain.lower()


def _included(domain: str) -> bool:
    return _tld_of(domain) in INCLUDED_TLDS


# ─── Public API ───────────────────────────────────────────────────────────
def is_available(domain: str) -> Availability:
    """Check availability + compute coverage / customer-paid split."""
    if not API_KEY:
        _stub_warning("is_available")
        return Availability(
            domain=domain, available=True, price_pence=1400,
            premium=not _included(domain),
            coverage_pence=1400 if _included(domain) else PRICE_CEILING_PENCE,
            customer_pence=0 if _included(domain) else max(0, 1400 - PRICE_CEILING_PENCE),
        )

    body = _api_call(f"/domain/checkDomain/{domain}")
    available = body.get("status") == "SUCCESS" and body.get("response", {}).get("avail") == "yes"
    # Porkbun returns USD; convert at orchestrator-config FX (default 0.78).
    fx = float(os.environ.get("HATCHIK_USD_GBP_FX", "0.78"))
    price_usd = float(body.get("response", {}).get("price", "14"))
    price_pence = int(round(price_usd * fx * 100))

    premium = not _included(domain)
    coverage = price_pence if not premium else min(price_pence, PRICE_CEILING_PENCE)
    customer = 0 if not premium else max(0, price_pence - PRICE_CEILING_PENCE)
    return Availability(
        domain=domain, available=available, price_pence=price_pence,
        premium=premium, coverage_pence=coverage, customer_pence=customer,
    )


def register(domain: str, contact: Contact, years: int = 1) -> dict[str, Any]:
    """Register the domain in the customer's name at the registrar.

    Returns:
        {"ok": bool, "already_registered": bool, "expires": iso_date,
         "passthrough_pence": int, "error": str | None}
    """
    if not API_KEY:
        _stub_warning("register")
        return {
            "ok": True, "already_registered": False,
            "expires": None, "passthrough_pence": 0, "stub": True,
        }

    body = {
        "domain": domain, "years": years,
        "registrant_first_name": contact.first_name,
        "registrant_last_name": contact.last_name,
        "registrant_email": contact.email,
        "registrant_phone": contact.phone,
        "registrant_address_line_1": contact.address_line_1,
        "registrant_city": contact.city,
        "registrant_postcode": contact.postcode,
        "registrant_country": contact.country,
        "nameservers": CLOUDFLARE_NAMESERVERS,
    }
    if contact.organisation:
        body["registrant_organisation"] = contact.organisation
    r = _api_call(f"/domain/register/{domain}", body)
    if r.get("status") == "SUCCESS":
        return {
            "ok": True, "already_registered": False,
            "expires": r.get("response", {}).get("expires"),
            "passthrough_pence": is_available(domain).customer_pence,
        }
    msg = (r.get("message") or "").lower()
    if "already" in msg or "exists" in msg:
        # Idempotent path — domain was registered in a previous attempt.
        return {"ok": True, "already_registered": True,
                "expires": None, "passthrough_pence": 0}
    log.error("Porkbun register failed for %s: %s", domain, r)
    return {"ok": False, "already_registered": False,
            "expires": None, "passthrough_pence": 0,
            "error": r.get("message") or "unknown"}


def set_nameservers(domain: str, nameservers: list[str] | None = None) -> bool:
    nameservers = nameservers or CLOUDFLARE_NAMESERVERS
    if not API_KEY:
        _stub_warning("set_nameservers")
        return True
    r = _api_call(f"/domain/updateNs/{domain}", {"ns": nameservers})
    return r.get("status") == "SUCCESS"


def renew(domain: str, years: int = 1) -> dict[str, Any]:
    """Renew an existing domain. Used by Growth tier annual renewal."""
    if not API_KEY:
        _stub_warning("renew")
        return {"ok": True, "stub": True}
    r = _api_call(f"/domain/renew/{domain}", {"years": years})
    return {"ok": r.get("status") == "SUCCESS", "raw": r}


def transfer_out_unlock(domain: str) -> bool:
    """Unlock a domain so the customer can transfer it away (no exit fee)."""
    if not API_KEY:
        _stub_warning("transfer_out_unlock")
        return True
    r = _api_call(f"/domain/updateLock/{domain}", {"locked": False})
    return r.get("status") == "SUCCESS"


# ─── CLI ──────────────────────────────────────────────────────────────────
def _cli() -> int:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(description="Porkbun domain registration")
    sub = p.add_subparsers(dest="cmd", required=True)

    ca = sub.add_parser("check", help="check availability + pricing")
    ca.add_argument("--domain", required=True)

    cr = sub.add_parser("register", help="register a domain")
    cr.add_argument("--domain", required=True)
    cr.add_argument("--contact-json", required=True,
                    help="path to JSON file with Contact fields")
    cr.add_argument("--years", type=int, default=1)

    cn = sub.add_parser("set-ns", help="point at Cloudflare nameservers")
    cn.add_argument("--domain", required=True)

    cu = sub.add_parser("unlock", help="unlock for transfer-out")
    cu.add_argument("--domain", required=True)

    args = p.parse_args()
    if args.cmd == "check":
        print(json.dumps(is_available(args.domain).__dict__, indent=2))
    elif args.cmd == "register":
        with open(args.contact_json) as f:
            contact = Contact(**json.load(f))
        print(json.dumps(register(args.domain, contact, years=args.years), indent=2))
    elif args.cmd == "set-ns":
        print(json.dumps({"ok": set_nameservers(args.domain)}))
    elif args.cmd == "unlock":
        print(json.dumps({"ok": transfer_out_unlock(args.domain)}))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
