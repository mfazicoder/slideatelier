"""
TLD policy for Launch-tier domain registration.

Launch tier promises "year 1 of registration included* in the £89 setup
fee, for the popular TLDs". Two tiers of acceptance:

  * **Included** (``ALLOWED_TLDS``) — registered free, fits the £14/yr
    operational ceiling.
  * **Passthrough** (``PASSTHROUGH_TLDS``) — we still register on the
    customer's behalf, but they pay the difference above £14/yr at
    Launch checkout. Common premium TLDs (.ai .io .tv etc.) live here.
  * **Unknown** — rejected with a friendly "supported list" message
    and a BYO escape hatch.

Phases (see ``proposals/hatchik/DOMAIN_REGISTRATION_SCOPE.md``):
  * Phase 1 (this module): input-side allow/passthrough classification.
  * Phase 2: registrar API integration in ``launch-orchestrator/promote.py``
    (Porkbun, with a live cost-cap circuit-breaker for ALLOWED, and a
    Stripe upcharge for PASSTHROUGH).
  * Phase 3: live availability check at signup time (typeahead-ish).

This module does NOT call a registrar. It does NOT check availability.
``validate_domain`` returns ``(ok, message)``; for passthrough TLDs the
message is empty (accepted) but ``passthrough_info`` reports the extra
cost so the Launch checkout can add a line item.

Prices in ``PASSTHROUGH_TLDS`` are 2026 ballpark retail and must be
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


# Passthrough TLDs — accepted at signup, but the customer pays the
# difference above the £14/yr Launch-included ceiling. The Launch
# checkout adds a line item for the extra cost; phase 2's registrar
# integration enforces it.
#
# Each entry is ``(approx_retail_per_year_gbp, friendly_label)``. The
# extra-cost figure we charge the customer is ``approx_retail - 14``,
# floored at 0 in case the registrar comes in cheaper than estimated.
#
# 2026 ballpark retail — re-verify against Porkbun pricing before phase 2.
PASSTHROUGH_TLDS: dict[str, tuple[int, str]] = {
    ".ai":  (90, "premium TLD, ~£90/yr"),
    ".io":  (30, "premium TLD, ~£30/yr"),
    ".tv":  (30, "premium TLD, ~£30/yr"),
    ".gg":  (70, "premium TLD, ~£70/yr"),
    ".so":  (25, "premium TLD, ~£25/yr"),
    ".me":  (15, "just over the £14/yr ceiling"),
    ".xyz": (20, "premium pricing tiers vary — assume ~£20/yr"),
}

# Backward-compat alias for any caller that still imports BLOCKED_TLDS.
# Maps TLD → human reason (the same string the old BLOCKED_TLDS used).
# New code should branch on PASSTHROUGH_TLDS instead.
BLOCKED_TLDS: dict[str, str] = {}


def passthrough_extra_gbp(tld: str) -> int:
    """Customer-paid balance above the £14 included allowance, in GBP.

    Returns 0 for allowlisted TLDs (no extra cost) and for unknown TLDs.
    Returns max(0, approx_retail - 14) for passthrough TLDs.
    """
    if tld not in PASSTHROUGH_TLDS:
        return 0
    approx, _ = PASSTHROUGH_TLDS[tld]
    return max(0, approx - 14)


def passthrough_info(domain: str | None) -> tuple[str, int, str] | None:
    """If ``domain``'s TLD is on the passthrough list, return a tuple of
    ``(tld, extra_gbp, friendly_label)``. Otherwise ``None``.

    Caller (signup endpoint / Launch checkout UI) uses this to add a
    line item: ``+£{extra_gbp} for {tld} ({friendly_label})``.
    """
    if not domain:
        return None
    normalised = _normalise(domain)
    if not normalised:
        return None
    tld = _extract_tld(normalised)
    if tld is None or tld not in PASSTHROUGH_TLDS:
        return None
    approx, label = PASSTHROUGH_TLDS[tld]
    return (tld, max(0, approx - 14), label)


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
    """Return the longest matching TLD suffix from ALLOWED ∪ PASSTHROUGH,
    or ``None`` if neither set matches. Longest-first ensures ``.co.uk``
    wins over ``.uk``.
    """
    known = sorted(
        list(ALLOWED_TLDS.keys()) + list(PASSTHROUGH_TLDS.keys()),
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
            f"We don't currently support '{normalised}'. "
            f"Included free: {SUPPORTED_TLDS_DISPLAY}. "
            f"Premium TLDs we'll register for you with a top-up "
            f"({', '.join(sorted(PASSTHROUGH_TLDS.keys()))}). "
            f"If you already own this domain, email hello@hatchik.com "
            f"and we'll wire it up as a bring-your-own.",
        )

    # Both allowlisted and passthrough TLDs are accepted. Passthrough
    # TLDs trigger an extra-cost line item at Launch checkout — the
    # signup endpoint surfaces that via ``passthrough_info``, this
    # function just says yes.
    return (True, "")
