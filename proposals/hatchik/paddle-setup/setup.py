#!/usr/bin/env python3
"""
Hatchik — Paddle Billing setup.

Creates the products and prices Hatchik needs in your Paddle account, plus a
hosted Payment Link ready to paste into the marketing page's "Launch your
idea" button.

Run this once. Re-running is safe — idempotent by `custom_data.lookup_key`.

Prerequisites:
  - Paddle account approved (sandbox is instant; live takes 1-2 weeks)
  - API key from Paddle dashboard -> Developer Tools -> Authentication
  - `pip install -r requirements.txt`
  - export PADDLE_API_KEY=pdl_sdbx_apikey_...   (sandbox)
    or PADDLE_API_KEY=pdl_live_apikey_...       (live)

Usage:
  python setup.py                     # sandbox mode (default)
  python setup.py --live              # production mode
  python setup.py --enable-ppp        # add localized PPP pricing overrides
  python setup.py --live --enable-ppp # both

Paddle Billing API reference: https://developer.paddle.com/api-reference/overview
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests


# ─── EDIT ME — Localized pricing overrides ────────────────────────────────
# Amounts are in the *minor unit* of the currency (cents/pence/paise/centavos).
# GBP base prices:
#   hatchik_launch_setup   = 7900  (£79.00 one-time)
#   hatchik_launch_monthly = 900   (£9.00/month, 30-day trial)
#   hatchik_growth_monthly = 2400  (£24.00/month)
#
# When --enable-ppp is passed, these overrides are attached to each price.
# Keys: ISO-3166 alpha-2 country code (Paddle applies per-country, not per-
# currency, to ensure local-tax + local-currency match).
#
# PPP rationale (rough, tune as needed):
#   - INR / BRL / ID / ZA: ~50%-60% discount (high-PPP-discount markets)
#   - USD / EUR: parity (~1.25/1.16 GBP fx, but rounded for psychology)
#
# Each entry: { country_code, unit_price: { amount, currency_code } }
PRICE_OVERRIDES: dict[str, list[dict[str, Any]]] = {
    "hatchik_launch_setup": [
        {"country_code": "US", "unit_price": {"amount": "9900",  "currency_code": "USD"}},
        {"country_code": "DE", "unit_price": {"amount": "8900",  "currency_code": "EUR"}},
        {"country_code": "FR", "unit_price": {"amount": "8900",  "currency_code": "EUR"}},
        {"country_code": "ES", "unit_price": {"amount": "8900",  "currency_code": "EUR"}},
        {"country_code": "IT", "unit_price": {"amount": "8900",  "currency_code": "EUR"}},
        {"country_code": "NL", "unit_price": {"amount": "8900",  "currency_code": "EUR"}},
        {"country_code": "IN", "unit_price": {"amount": "349900", "currency_code": "INR"}},  # ~£35 PPP
        {"country_code": "BR", "unit_price": {"amount": "24900", "currency_code": "BRL"}},   # ~£45 PPP
    ],
    "hatchik_launch_monthly": [
        {"country_code": "US", "unit_price": {"amount": "1100",  "currency_code": "USD"}},
        {"country_code": "DE", "unit_price": {"amount": "1000",  "currency_code": "EUR"}},
        {"country_code": "FR", "unit_price": {"amount": "1000",  "currency_code": "EUR"}},
        {"country_code": "ES", "unit_price": {"amount": "1000",  "currency_code": "EUR"}},
        {"country_code": "IT", "unit_price": {"amount": "1000",  "currency_code": "EUR"}},
        {"country_code": "NL", "unit_price": {"amount": "1000",  "currency_code": "EUR"}},
        {"country_code": "IN", "unit_price": {"amount": "39900", "currency_code": "INR"}},   # ~£4 PPP
        {"country_code": "BR", "unit_price": {"amount": "2800",  "currency_code": "BRL"}},   # ~£5 PPP
    ],
    "hatchik_growth_monthly": [
        {"country_code": "US", "unit_price": {"amount": "3000",  "currency_code": "USD"}},
        {"country_code": "DE", "unit_price": {"amount": "2700",  "currency_code": "EUR"}},
        {"country_code": "FR", "unit_price": {"amount": "2700",  "currency_code": "EUR"}},
        {"country_code": "ES", "unit_price": {"amount": "2700",  "currency_code": "EUR"}},
        {"country_code": "IT", "unit_price": {"amount": "2700",  "currency_code": "EUR"}},
        {"country_code": "NL", "unit_price": {"amount": "2700",  "currency_code": "EUR"}},
        {"country_code": "IN", "unit_price": {"amount": "99900", "currency_code": "INR"}},   # ~£10 PPP
        {"country_code": "BR", "unit_price": {"amount": "7500",  "currency_code": "BRL"}},   # ~£14 PPP
    ],
}


# ─── Paddle API base + lookup keys ────────────────────────────────────────
SANDBOX_BASE = "https://api.sandbox.paddle.com"
LIVE_BASE = "https://api.paddle.com"

LOOKUP = {
    "launch_product": "hatchik_launch",
    "growth_product": "hatchik_growth",
    "launch_setup":   "hatchik_launch_setup",
    "launch_monthly": "hatchik_launch_monthly",
    "growth_monthly": "hatchik_growth_monthly",
}


# ─── HTTP helpers ─────────────────────────────────────────────────────────
class Paddle:
    def __init__(self, base: str, api_key: str):
        self.base = base
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # TODO: pin the Paddle-Version header if Paddle prompts you to.
            # As of 2024+ the default version is fine; bump if you see
            # deprecation notices in responses.
            # "Paddle-Version": "1",
        })

    def _req(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        url = f"{self.base}{path}"
        r = self.session.request(method, url, **kwargs)
        if r.status_code >= 400:
            print(f"  HTTP {r.status_code} {method} {path}", file=sys.stderr)
            try:
                print("  " + json.dumps(r.json(), indent=2), file=sys.stderr)
            except Exception:
                print("  " + r.text, file=sys.stderr)
            r.raise_for_status()
        return r.json()

    def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        return self._req("GET", path, params=params or {})

    def post(self, path: str, payload: dict) -> dict[str, Any]:
        return self._req("POST", path, data=json.dumps(payload))


# ─── Idempotency: search by custom_data.lookup_key ────────────────────────
def find_by_lookup_key(paddle: Paddle, resource_path: str, lookup_key: str) -> dict | None:
    """
    Page through `resource_path` (products or prices) and return the first
    item whose `custom_data.lookup_key` matches. Paddle doesn't currently
    support server-side filtering by custom_data, so we paginate client-side.
    """
    after: str | None = None
    while True:
        params: dict[str, Any] = {"per_page": 200, "status": "active"}
        if after:
            params["after"] = after
        resp = paddle.get(resource_path, params=params)
        for item in resp.get("data", []):
            cd = item.get("custom_data") or {}
            if isinstance(cd, dict) and cd.get("lookup_key") == lookup_key:
                return item
        meta = resp.get("meta", {}).get("pagination", {})
        if not meta.get("has_more"):
            return None
        # Paddle's cursor pagination: pass last item's id as `after`.
        last = resp["data"][-1] if resp.get("data") else None
        if not last:
            return None
        after = last["id"]


# ─── Product / Price creation ─────────────────────────────────────────────
def ensure_product(paddle: Paddle, lookup_key: str, name: str, description: str,
                   tier: str) -> str:
    existing = find_by_lookup_key(paddle, "/products", lookup_key)
    if existing:
        print(f"  [exists] product {name}: {existing['id']}")
        return existing["id"]

    payload = {
        "name": name,
        "description": description,
        "tax_category": "saas",  # Paddle's SaaS tax category
        "custom_data": {
            "lookup_key": lookup_key,
            "loftik_tier": tier,
        },
    }
    resp = paddle.post("/products", payload)
    pid = resp["data"]["id"]
    print(f"  [created] product {name}: {pid}")
    return pid


def ensure_price(
    paddle: Paddle,
    *,
    product_id: str,
    lookup_key: str,
    description: str,
    amount_minor: str,           # string per Paddle convention
    currency: str = "GBP",
    recurring: bool = False,
    interval: str = "month",
    frequency: int = 1,
    trial_days: int | None = None,
    enable_ppp: bool = False,
) -> str:
    existing = find_by_lookup_key(paddle, "/prices", lookup_key)
    if existing:
        print(f"  [exists] price {lookup_key}: {existing['id']}")
        return existing["id"]

    payload: dict[str, Any] = {
        "product_id": product_id,
        "description": description,
        "unit_price": {"amount": amount_minor, "currency_code": currency},
        # `billing_cycle` is null for one-time, populated for recurring.
        "billing_cycle": (
            {"interval": interval, "frequency": frequency} if recurring else None
        ),
        "quantity": {"minimum": 1, "maximum": 1},
        "tax_mode": "account_setting",  # inherit from account-level tax settings
        "custom_data": {"lookup_key": lookup_key},
    }
    if recurring and trial_days:
        payload["trial_period"] = {"interval": "day", "frequency": trial_days}

    if enable_ppp and lookup_key in PRICE_OVERRIDES:
        payload["unit_price_overrides"] = PRICE_OVERRIDES[lookup_key]
        print(f"    + {len(PRICE_OVERRIDES[lookup_key])} country overrides")

    resp = paddle.post("/prices", payload)
    price_id = resp["data"]["id"]
    print(f"  [created] price {lookup_key}: {price_id}")
    return price_id


# ─── Payment Link via Transaction ─────────────────────────────────────────
def create_payment_link(paddle: Paddle, setup_price_id: str, monthly_price_id: str,
                        dry_run: bool = False) -> str:
    """
    Paddle Billing creates hosted checkout URLs by POSTing a transaction in
    `draft` (or `ready`) status with items attached. The response includes
    `checkout.url` which is the hosted Payment Link.

    A new transaction is created per call — we don't dedupe here because
    transactions are cheap and represent a single intended checkout.

    NOTE: Paddle dashboard also exposes a "Default checkout URL" template you
    can configure under Checkout Settings -> Default payment link. For a
    permanent link, you may prefer to wire each price_id into a URL like:
        {DEFAULT_LINK}?items[0][priceId]=pri_xxx&items[0][quantity]=1&...
    See: https://developer.paddle.com/build/checkout/build-overlay-checkout
    """
    payload = {
        "items": [
            {"price_id": setup_price_id, "quantity": 1},
            {"price_id": monthly_price_id, "quantity": 1},
        ],
        "collection_mode": "automatic",
        "custom_data": {"loftik_flow": "launch_signup"},
        # TODO: replace this success URL with your real post-checkout page.
        # Paddle substitutes `{transaction_id}` placeholder at redirect time.
        "checkout": {
            "url": "https://hatchik.com/welcome?txn={transaction_id}",
        },
    }
    if dry_run:
        print("  (dry-run, skipping transaction creation)")
        return "https://example.com/dry-run"

    resp = paddle.post("/transactions", payload)
    data = resp["data"]
    checkout_url = (data.get("checkout") or {}).get("url")
    if not checkout_url:
        # Defensive: some Paddle account configs require status=ready before
        # checkout.url is materialized. If empty, fall back to building it
        # from the default checkout URL + transaction id.
        # See https://developer.paddle.com/build/transactions/create-transaction
        print("  ⚠ checkout.url empty in response; transaction id is "
              f"{data['id']}. Configure default checkout URL in dashboard.")
        return f"<configure default checkout URL; transaction: {data['id']}>"
    return checkout_url


# ─── Main ─────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Hatchik Paddle setup")
    parser.add_argument("--live", action="store_true",
                        help="Use production API (default: sandbox)")
    parser.add_argument("--enable-ppp", action="store_true",
                        help="Attach localized PPP pricing overrides")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip the Payment Link transaction step")
    args = parser.parse_args()

    api_key = os.environ.get("PADDLE_API_KEY")
    if not api_key:
        print("PADDLE_API_KEY env var not set.", file=sys.stderr)
        print("  Get one at: Paddle dashboard -> Developer Tools -> "
              "Authentication", file=sys.stderr)
        return 1

    base = LIVE_BASE if args.live else SANDBOX_BASE
    mode = "LIVE" if args.live else "SANDBOX"

    print()
    print("── Hatchik Paddle setup ────────────────────────────────────")
    print(f"   Mode: {mode}")
    print(f"   PPP overrides: {'on' if args.enable_ppp else 'off'}")
    print(f"   Base URL: {base}")
    print()

    if args.live:
        print("⚠️  LIVE mode — these products will be real and visible to "
              "customers.")
        print("    Press Ctrl+C in 5 seconds to abort.")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nAborted.")
            return 1

    paddle = Paddle(base, api_key)

    # 1. Hatchik Launch product
    print("→ Hatchik Launch product")
    launch_product_id = ensure_product(
        paddle,
        lookup_key=LOOKUP["launch_product"],
        name="Hatchik Launch",
        description=(
            "Your domain, your server, your mailboxes, payments live — set up "
            "properly. £79 covers month 1. £7/month from month 2."
        ),
        tier="launch",
    )

    # 2. £79 one-time setup price
    print("→ £79 setup price")
    setup_price_id = ensure_price(
        paddle,
        product_id=launch_product_id,
        lookup_key=LOOKUP["launch_setup"],
        description="Hatchik Launch — setup fee",
        amount_minor="7900",
        currency="GBP",
        recurring=False,
        enable_ppp=args.enable_ppp,
    )

    # 3. £9/month recurring price with 30-day trial
    print("→ £9/month subscription price (30-day trial)")
    monthly_price_id = ensure_price(
        paddle,
        product_id=launch_product_id,
        lookup_key=LOOKUP["launch_monthly"],
        description="Hatchik Launch — £9/month from month 2",
        amount_minor="900",
        currency="GBP",
        recurring=True,
        interval="month",
        frequency=1,
        trial_days=30,
        enable_ppp=args.enable_ppp,
    )

    # 4. Hatchik Growth product
    print("→ Hatchik Growth product")
    growth_product_id = ensure_product(
        paddle,
        lookup_key=LOOKUP["growth_product"],
        name="Hatchik Growth",
        description=(
            "The Growth tier you graduate to once your app crosses 15 sign-ups."
            " Same product, more headroom, deeper support, included AI token "
            "allowance."
        ),
        tier="growth",
    )

    # 5. £24/month Growth price
    print("→ £24/month Growth price")
    growth_price_id = ensure_price(
        paddle,
        product_id=growth_product_id,
        lookup_key=LOOKUP["growth_monthly"],
        description="Hatchik Growth — £24/month",
        amount_minor="2400",
        currency="GBP",
        recurring=True,
        interval="month",
        frequency=1,
        enable_ppp=args.enable_ppp,
    )

    # 6. Payment Link (transaction with checkout url)
    print("→ Payment Link (bundling setup + monthly)")
    checkout_url = create_payment_link(
        paddle, setup_price_id, monthly_price_id, dry_run=args.dry_run,
    )

    # 7. Summary
    print()
    print("── Done ───────────────────────────────────────────────────")
    print()
    print("Paddle IDs:")
    print(f"  Launch product:       {launch_product_id}")
    print(f"  Launch setup price:   {setup_price_id}  (lookup_key: {LOOKUP['launch_setup']})")
    print(f"  Launch monthly price: {monthly_price_id}  (lookup_key: {LOOKUP['launch_monthly']})")
    print(f"  Growth product:       {growth_product_id}")
    print(f"  Growth monthly price: {growth_price_id}  (lookup_key: {LOOKUP['growth_monthly']})")
    print()
    print("Payment Link (paste into index.html):")
    print()
    print(f"  {checkout_url}")
    print()
    print("Next steps:")
    print("  1. Open proposals/loftik/index.html")
    print("  2. Find both occurrences of:")
    print("       mailto:hello@hatchik.com?subject=Hatchik%20Launch%20-%20early%20access")
    print("  3. Replace with the Payment Link URL above")
    print("  4. Test in sandbox first (Paddle test cards — see README.md)")
    print("  5. When happy, re-run with --live and update index.html again")
    print()
    if not args.live:
        print("  ⚠️  Currently SANDBOX mode. To go live: "
              "PADDLE_API_KEY=pdl_live_... python setup.py --live")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
