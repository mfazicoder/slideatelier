# Domain registration — scope and TLD allowlist

Scoping doc for honouring Launch tier's "year 1 of domain registration included
in the £89 setup" promise without eating margin on premium TLDs.

## The margin constraint

Launch is £89 setup + £14/mo (yr 1 ≈ £243). The £89 covers VPS provisioning,
mailbox setup, payments wiring, AI credit and a registered domain. Our
operational ceiling for **year-1 domain cost** is **£14 retail/yr** — any TLD
priced above that either eats Launch margin outright or has to be passed on as
an upcharge.

This doc scopes a phased solution: a server-side TLD allowlist in phase 1,
registrar API integration in phase 2, live availability check in phase 3.

## Current state (audited 2026-05-14)

- **No registrar client exists.** `launch-orchestrator/dns_api.py` wraps the
  Cloudflare DNS API for A-record writes only — that's DNS hosting, not
  registration. No Porkbun, Namecheap, Namesilo, or Cloudflare Registrar
  integration is present.
- **`domain_choice` is opaque free-text at signup.** `signup-service/main.py`
  defines it as `Field(None, max_length=255)` with zero validation. The
  customer types whatever they want.
- **`promote.py` treats `domain_choice` as opaque.** If the zone isn't already
  on our Cloudflare account, it logs "customer brought own domain; manual DNS
  needed" — i.e. the "year 1 included" promise is fulfilled by the founder
  hand-registering today, or not at all.
- **Marketing copy** promises "year 1 included in the £89" on `index.html` and
  `vs.html`. No TLD restriction is stated.

## The £14/yr TLD landscape (retail, 2026 ballpark)

These prices are approximate and **must be verified against the chosen
registrar's live price list before phase 2 lands**. Figures are typical
retail seen across Porkbun / Namesilo / Cloudflare Registrar in 2025–26.

**Comfortably under £14/yr — propose for the allowlist:**

| TLD       | Approx retail / yr | Notes                               |
|-----------|--------------------|-------------------------------------|
| `.com`    | £9–12              | Default. Universally recognised.    |
| `.net`    | £10–13             | Tech-leaning fallback to `.com`.    |
| `.org`    | £10–13             | Non-profit / community flavour.     |
| `.co`     | £10–13             | Short, brandable, startup-friendly. |
| `.uk`     | £8–10              | British, short.                     |
| `.co.uk`  | £8–10              | British, conventional.              |
| `.app`    | £12–14             | HTTPS-only by default; tight margin.|
| `.dev`    | £12–14             | HTTPS-only by default; tight margin.|
| `.tech`   | £8–13 first yr     | First-year promo pricing varies.    |
| `.online` | £4–14 first yr     | Heavy first-year promo; verify.     |

**Border cases — verify and reconsider per registrar:**

- `.io` — historically £30–40/yr; some registrars offer £25 promos but it
  doesn't fit £14. **Excluded from phase-1 allowlist** despite popularity.
  Customer can BYO if they really want one.
- `.app` / `.dev` — sit right at the £14 ceiling at most registrars. Include
  but flag for re-pricing if a registrar bumps them.

**Definitely over £14/yr — block and explain:**

| TLD     | Approx retail / yr | Why blocked                  |
|---------|--------------------|------------------------------|
| `.ai`   | ~£90–130           | Premium; AI-hype priced.     |
| `.io`   | ~£30–40            | Premium; not at our ceiling. |
| `.tv`   | ~£25–35            | Premium (Tuvalu).            |
| `.gg`   | ~£60–100           | Premium gamer TLD.           |
| `.so`   | ~£20–30            | Premium (Somalia).           |
| `.me`   | ~£15–20            | Just over ceiling.           |
| `.xyz`  | wildly variable    | Premium tiers exist within `.xyz`; safer to exclude. |

## Recommended registrar — Porkbun (phase 2)

**Reasoning:**

1. **Flat-rate, transparent pricing.** No "first year £1, renews at £35"
   gotchas; the retail price is the price we pay. Margin maths actually works.
2. **REST API is documented, simple, and free to use** (no monthly minimums).
3. **WHOIS privacy is included free** on supported TLDs — Namecheap charges
   extra, Namesilo includes it, Cloudflare Registrar includes it.
4. **Indie-friendly support.** Matters when a customer's registration breaks
   at 23:00 and we need a human.

**Alternatives considered:**

- **Cloudflare Registrar** — at-cost pricing (literally registry wholesale),
  but you can only register domains whose DNS is *already* on Cloudflare and
  there's no programmatic register-via-API; it's UI-only. Disqualifying for
  our automated flow.
- **Namesilo** — competitive pricing, decent API, but the API is older /
  XML-based and the UX of debugging it is worse than Porkbun.

## Implementation phases

### Phase 1 — input-side allowlist (this branch)

Goal: customer cannot submit a `domain_choice` that would bust our margin.
No registrar integration yet; the founder still hand-registers.

- New `signup-service/domains.py` with `ALLOWED_TLDS`, `BLOCKED_TLDS`, and
  `validate_domain()`.
- Pydantic validator on `SignupRequest.domain_choice` (Launch tier only;
  Sandbox doesn't get a domain).
- Marketing copy qualified to "(most popular TLDs — .com, .co, .net, etc.)".
- Front-end nudge on `start.html` (datalist or hint).

### Phase 2 — registrar API integration in `promote.py`

Goal: replace the manual "log a TODO" path with an actual register call.

- Add `launch-orchestrator/registrar_api.py` (Porkbun client: check
  availability, register, set nameservers to Cloudflare).
- `promote.py` step 7 splits into 7a (register if not BYO) and 7b (DNS A
  record).
- Cost-cap circuit-breaker: even with the allowlist, fetch the *current*
  price from Porkbun before registering. If > £14, abort and email the
  founder for manual review.
- Renewal scheduling: a new `launch_renewals.py` (timer-driven) checks domains
  due in < 30 days, charges (for Launch) or auto-renews (for Growth).

### Phase 3 — live availability check at signup time

Goal: avoid the "you typed `foo.com` but it's taken" surprise after payment.

- `/api/domains/check?candidate=foo.com` returns `{available, price_gbp,
  allowed}` — backed by Porkbun's availability API plus our TLD allowlist.
- `start.html` gains typeahead: as the customer types, show ✅/❌ next to the
  input. Probably gate this behind a debounce + a rate-limit per IP.

## Edge cases — phase 1 decisions

| Case                                  | Decision                                              |
|---------------------------------------|-------------------------------------------------------|
| Customer wants to BYO an existing domain | Allowed — `validate_domain` accepts any TLD when the customer indicates BYO (TODO: surface a "do you already own this?" checkbox; for now, any allowlisted-TLD value is treated as "register for me", anything else gets a friendly rejection asking them to email us). |
| Customer wants a TLD outside our list (e.g. `.ai`) | Reject at signup with a specific message naming the TLD and the cap: "We can't register `.ai` (≈ £90/yr) inside the £89 Launch fee. We can register a `.com` / `.co` / `.net` / `.org` / `.uk` / `.app` / `.dev` / `.tech` instead, or you can register the `.ai` yourself and we'll wire it up." |
| Customer types just a TLD-less string (`foo`) | Reject — "please include a top-level domain like `.com`". |
| Customer types a malformed value (`http://foo.com/path`) | Normalise: strip scheme, strip path, lowercase. If still malformed, reject. |
| Multi-part TLDs (`.co.uk`, `.org.uk`) | Match against multi-part allowlist entries before falling through to single-part. |
| Unknown / not-in-allowlist TLD (e.g. `.xyz`, `.shop`) | **Reject** in phase 1 with a "supported list" message. Reasoning: any TLD we haven't researched might secretly be > £14 or have a premium pricing tier. Safer to whitelist and broaden later than to passthrough and absorb a surprise £40 cost. |
| Year-2 renewal — Launch tier | Customer pays the registrar fee separately (we expose it in their account dashboard). Promised in launch copy as "year 1 included" — year 2 is at-cost passthrough. |
| Year-2 renewal — Growth tier | "Free annual renewal" — we eat the renewal cost; phase 2 timer-driven. Same TLD allowlist applies, so we know what we're committing to. |

## Open questions / TODOs for FAQ

- Add a Launch-tier FAQ entry: "Which domains can you register for me?" listing
  the allowlist and the BYO escape hatch.
- Pricing-page footnote: at the "year 1 included" line, link to the same FAQ.
- Phase 2: real prices must be fetched from Porkbun at call time, not
  hard-coded. The allowlist is a *coarse* gate; the live price is the
  authoritative gate.
