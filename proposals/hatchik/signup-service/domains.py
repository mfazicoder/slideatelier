"""
TLD allowlist for Launch-tier domain registration.

Launch tier promises "year 1 of domain registration included in the £89
setup fee". Our operational ceiling for year-1 retail cost is **£14/yr** —
anything pricier eats Launch margin or has to be passed on. This module
is the phase-1 input guard: a coarse server-side check that rejects
customer-provided ``domain_choice`` values whose TLD we don't (yet)
register for free.

Phases (see ``proposals/hatchik/DOMAIN_REGISTRATION_SCOPE.md``):
  * Phase 1 (this module): input-side allowlist.
  * Phase 2: registrar API integration in ``launch-orchestrator/promote.py``
    (Porkbun, with a live ≤£14 cost-cap circuit-breaker).
  * Phase 3: live availability check at signup time (typeahead-ish).

This module does NOT call a registrar. It does NOT check availability.
Its only job is to reject values that we know in advance would breach
the £14 ceiling, so the customer gets a clear message at signup rather
than a stalled provision (or a surprise upcharge) after payment.

Prices in ``BLOCKED_TLDS`` strings are 2026 ballpark retail and must be
re-verified before phase 2 ships.
"""

from __future__ import annotations

import re


# Allowed TLDs — entries are matched longest-first so multi-part TLDs
# like ".co.uk" are tried before falling through to ".uk".
#
# Pricing assumptions (2026 retail, must be re-verified before phase 2):
#   .com / .net / .org / .co / .uk / .co.uk: comfortably under £14/yr.
#   .app / .dev: at the £14 ceiling — include but flag for re-pricing.
#   .tech / .online: usually ≤ £14, first-year promos vary.
ALLOWED_TLDS: dict[str, str] = {
    ".com": "Classic, universally recognised",
    ".net": "Tech-leaning fallback to .com",
    ".org": "Non-profit / community flavour",
    ".co": "Short, brandable, startup-friendly",
    ".uk": "British, short",
    ".co.uk": "British, conventional",
    ".app": "Modern, HTTPS-only by default",
    ".dev": "Developer-leaning, HTTPS-only by default",
    ".tech": "Tech-leaning, often promo-priced",
    ".online": "Generic, broadly available",
}


# Blocked TLDs — common requests we DON'T register inside the £89 fee.
# Reasons are surfaced verbatim to the customer so they can decide whether
# to pick something else or bring their own (BYO escape hatch).
BLOCKED_TLDS: dict[str, str] = {
    ".ai": "premium TLD, ~£90/yr — over the £14/yr included in Launch",
    ".io": "premium TLD, ~£30/yr — over the £14/yr included in Launch",
    ".tv": "premium TLD, ~£30/yr — over the £14/yr included in Launch",
    ".gg": "premium TLD, ~£70/yr — over the £14/yr included in Launch",
    ".so": "premium TLD, ~£25/yr — over the £14/yr included in Launch",
    ".me": "just over the £14/yr ceiling included in Launch",
    ".xyz": "premium pricing tiers exist within .xyz — we can't guarantee ≤ £14/yr",
}


# Friendly, copy-paste-ready supported-TLD line for error messages and
# front-end hints. Order matters — common first, niche last.
SUPPORTED_TLDS_DISPLAY: str = ".com, .co, .net, .org, .uk, .co.uk, .app, .dev, .tech, .online"


# Loose syntactic check — RFC-1035-ish, intentionally not strict (we lean
# on the registrar to be authoritative). We just want to reject obvious
# garbage like ``http://`` URLs with paths, empty strings, or strings
# with whitespace.
_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def _normalise(domain: str) -> str:
    """Strip scheme/path/whitespace, lowercase.

    Customers paste ``https://Foo.Com/path`` more often than you'd think.
    """
    s = (domain or "").strip().lower()
    # Strip scheme.
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    # Strip path / query / fragment.
    for sep in ("/", "?", "#"):
        if sep in s:
            s = s.split(sep, 1)[0]
    # Strip leading "www.".
    if s.startswith("www."):
        s = s[4:]
    # Strip trailing dot (FQDN form).
    s = s.rstrip(".")
    return s


def _extract_tld(domain: str) -> str | None:
    """Return the longest matching TLD suffix from ALLOWED ∪ BLOCKED, or
    ``None`` if neither set matches. Longest-first ensures ``.co.uk``
    wins over ``.uk``.
    """
    known = sorted(
        list(ALLOWED_TLDS.keys()) + list(BLOCKED_TLDS.keys()),
        key=len,
        reverse=True,
    )
    for tld in known:
        # TLD entries all start with ``.`` so ``endswith`` is sufficient
        # to require the dot boundary (``barcom`` won't match ``.com``).
        # Bare TLDs (``com``) are rejected upstream by ``_DOMAIN_RE``
        # because they have no dot.
        if domain.endswith(tld) and len(domain) > len(tld):
            return tld
    return None


def validate_domain(domain: str | None) -> tuple[bool, str]:
    """Validate a customer-supplied domain for Launch-tier registration.

    Returns ``(ok, message)``:
        * ``(True, "")`` — accepted (allowlisted TLD, normaliseable syntax).
        * ``(False, "<friendly reason>")`` — rejected; the message is
          customer-facing and safe to render verbatim.

    Empty / ``None`` input is rejected — the Launch wizard collects a
    ``domain_choice`` and we don't want to silently provision against a
    blank value.

    This is a coarse phase-1 guard, not a live availability check. Even
    an allowlisted TLD may be unregistrable (taken) or surprise-priced
    above £14 — phase 2 will add the registrar-side circuit-breaker.
    """
    if not domain or not domain.strip():
        return (
            False,
            "Please enter a domain you'd like us to register (e.g. yourapp.com).",
        )

    normalised = _normalise(domain)

    if not normalised:
        return (
            False,
            "That doesn't look like a domain — please try again "
            "(e.g. yourapp.com).",
        )

    if not _DOMAIN_RE.match(normalised):
        return (
            False,
            f"'{domain}' doesn't look like a valid domain. "
            "Try just the name and TLD, e.g. yourapp.com.",
        )

    tld = _extract_tld(normalised)
    if tld is None:
        return (
            False,
            f"We can't register '{normalised}' inside the £89 Launch fee "
            f"— we only register common TLDs that cost ≤ £14/yr. "
            f"Supported: {SUPPORTED_TLDS_DISPLAY}. "
            f"If you already own this domain, email hello@hatchik.com "
            f"and we'll wire it up as a bring-your-own.",
        )

    if tld in BLOCKED_TLDS:
        reason = BLOCKED_TLDS[tld]
        return (
            False,
            f"We can't register a {tld} domain inside the £89 Launch fee "
            f"({reason}). We can register a "
            f"{', '.join(list(ALLOWED_TLDS.keys())[:5])} or similar for "
            f"you instead — or you can register the {tld} yourself and "
            f"email hello@hatchik.com so we wire it up as a "
            f"bring-your-own.",
        )

    # Allowlisted TLD, syntactically clean — accept.
    return (True, "")
