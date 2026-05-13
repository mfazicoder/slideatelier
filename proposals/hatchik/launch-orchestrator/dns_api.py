"""
Thin wrapper over the Cloudflare DNS API.

Used by promote.py to point a customer's domain at the Launch tenant's
fresh VPS IP. If the customer brought their own domain and didn't move it
to Cloudflare, this wrapper raises a soft error and promote.py falls back
to emailing the customer with manual DNS instructions.

Reference: https://developers.cloudflare.com/api/

Required env:
    CLOUDFLARE_API_TOKEN  — scoped token with Zone:Read + DNS:Edit on the
                            zones we manage. Generate at
                            cloudflare.com -> My Profile -> API Tokens.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_TIMEOUT = 30.0


class CloudflareError(RuntimeError):
    pass


class ZoneNotFound(CloudflareError):
    """The customer's domain is not in our Cloudflare account."""


def _client() -> httpx.Client:
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        raise CloudflareError(
            "CLOUDFLARE_API_TOKEN not set. Generate a scoped token at "
            "Cloudflare -> My Profile -> API Tokens with Zone:Read + DNS:Edit."
        )
    return httpx.Client(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=DEFAULT_TIMEOUT,
    )


def _check(resp: httpx.Response) -> dict[str, Any]:
    body = resp.json()
    if not body.get("success"):
        errs = body.get("errors") or []
        raise CloudflareError(f"Cloudflare API error: {errs}")
    return body


def list_zones() -> list[dict[str, Any]]:
    with _client() as c:
        r = c.get("/zones", params={"per_page": 50})
        return _check(r)["result"]


def get_zone_id(domain: str) -> str:
    """Resolve a domain (e.g. ``mything.com``) to its Cloudflare zone ID.

    For subdomains (``foo.bar.com``) the zone is the apex (``bar.com``).
    """
    apex = ".".join(domain.split(".")[-2:])
    with _client() as c:
        r = c.get("/zones", params={"name": apex})
        body = _check(r)
        result = body.get("result") or []
        if not result:
            raise ZoneNotFound(
                f"Zone {apex} not found in Cloudflare account. The customer "
                "likely brought their own domain and hasn't pointed nameservers "
                "at Cloudflare. promote.py falls back to manual-DNS email."
            )
        return result[0]["id"]


def list_a_records(zone_id: str, name: str) -> list[dict[str, Any]]:
    with _client() as c:
        r = c.get(f"/zones/{zone_id}/dns_records", params={"type": "A", "name": name})
        return _check(r)["result"]


def set_a_record(
    domain: str,
    ip: str,
    *,
    proxied: bool = True,
    ttl: int = 1,  # 1 = automatic
) -> dict[str, Any]:
    """Idempotent: create or update the A record for ``domain`` -> ``ip``.

    By default ``proxied=True`` puts the customer's domain behind
    Cloudflare's edge (free CDN + TLS termination + DDoS). The substrate
    Caddy still issues an origin cert via Cloudflare's flexible/full
    mode — set Cloudflare SSL mode to ``Full (strict)`` for production.
    """
    zone_id = get_zone_id(domain)
    existing = list_a_records(zone_id, domain)

    payload = {
        "type": "A",
        "name": domain,
        "content": ip,
        "ttl": ttl,
        "proxied": proxied,
    }

    with _client() as c:
        if existing:
            rec_id = existing[0]["id"]
            r = c.put(f"/zones/{zone_id}/dns_records/{rec_id}", json=payload)
        else:
            r = c.post(f"/zones/{zone_id}/dns_records", json=payload)
        return _check(r)["result"]


def delete_a_record(domain: str) -> None:
    """Idempotent: remove the A record (used at decommission)."""
    try:
        zone_id = get_zone_id(domain)
    except ZoneNotFound:
        return  # nothing to delete
    existing = list_a_records(zone_id, domain)
    with _client() as c:
        for rec in existing:
            c.delete(f"/zones/{zone_id}/dns_records/{rec['id']}")
