# Agent report — service-inventory transparency

**Goal:** make the post-signup experience tell customers exactly what they
got — service-by-service, quantified, honest about what's NOT wired.
Same words, same numbers, across the sandbox-ready email, the
`/account` dashboard, the marketing pricing card, the docs and the
terms page.

## Files touched

| File | Change |
|---|---|
| `proposals/hatchik/sandbox-orchestrator/service_inventory.py` | **New.** Canonical "what ships in a sandbox" data + plain-text/HTML renderers + dynamic .env probes (Stripe live vs test, Google OAuth, customer Resend key, custom domain). |
| `proposals/hatchik/sandbox-orchestrator/provision.py` | Imports `service_inventory`; `send_sandbox_ready_email` now renders a "What's set up for you" + "What's NOT yet wired" block in both text and HTML versions. Provisioning step order reshuffled so GitHub repo creation runs before the sandbox-ready email (so the email can deep-link to the repo). |
| `proposals/hatchik/signup-service/main.py` | Adds a `sys.path` import shim for `service_inventory`. New endpoint `GET /api/account/services/{slug}` (session-cookie-auth, tenant scoping reuses `_tenant_for_session`). |
| `proposals/hatchik/account.html` | New "What's set up" tab with a two-column Wired / Available-on-upgrade grid. Renders per-sandbox sections; deep-link "Configure →" buttons hit Supabase Studio, the GitHub repo, or jump to the Upgrade tab as appropriate. |
| `proposals/hatchik/index.html` | Sandbox pricing card features rewritten with real numbers (replaces "Up to 3 users / 100MB"). Inline signup-form caption updated to match. |
| `proposals/hatchik/docs/what-is-included.html` | New quantified Sandbox-inventory table at the top of the page; mirror "Not yet wired" table; sidebar gets two new anchors. |
| `proposals/hatchik/terms.html` | "3 users / 100MB" clause replaced with the real cap list + link to the docs page. |

`vs.html` — scanned, no quantified Sandbox claims in the comparison
table; nothing to change.

## New endpoint shape

```
GET /api/account/services/<slug>
```

Auth: same session cookie as the rest of `/api/account/*`. Returns 401
if not signed in, 404 if the slug doesn't belong to the session user,
503 if the orchestrator module isn't on `sys.path` (defensive — should
not happen in production).

Response:

```json
{
  "slug": "<slug>",
  "sandbox_url": "https://<slug>.hatchik.com",
  "repo_url": "https://github.com/hatchik-sandboxes/<slug>",
  "tier": "sandbox",
  "wired": [
    {
      "name": "Postgres database",
      "detail": "Supabase-managed Postgres, 512 MB RAM cap. Disk shares the host budget — ~10 GB practical before we'd ask you to upgrade.",
      "status": "live",
      "configure_url": "https://<slug>.hatchik.com/studio",
      "category": "compute"
    },
    {
      "name": "Payments",
      "detail": "Stripe SDK wired with your live keys. Subscriptions, Checkout — go live.",
      "status": "live",
      "configure_url": "https://github.com/hatchik-sandboxes/<slug>",
      "category": "payments"
    }
    // 12 more entries
  ],
  "available_on_upgrade": [
    {
      "name": "Custom domain",
      "tier": "launch",
      "blurb": "Bring your own domain or register a new one. Year-one registration in the £79."
    }
    // 6 more entries
  ]
}
```

`status` values: `live | test-mode | not_configured | policy` — the UI
maps these to coloured pill badges (emerald / amber / slate / sky).

## Design decisions

**Single source of truth.** All three surfaces (email, dashboard, docs)
ultimately answer to `service_inventory.py`. Tweaking a quota or wording
there propagates to:
- the next sandbox-ready email (no redeploy needed beyond bumping the
  orchestrator submodule), and
- the next call to `/api/account/services/<slug>` (live), and
- the docs page (when the HTML is regenerated to match — currently
  hand-mirrored; see open question 3 below).

**Dynamic vs static.** The substrate's defaults (Stripe test mode,
Google OAuth disabled, shared Resend) are the static baseline. When the
customer brings their own key (sets `STRIPE_SECRET_KEY` to `sk_live_*`,
flips `GOOGLE_OAUTH_ENABLED=true` and pastes a client ID, sets
`CUSTOMER_RESEND_API_KEY`, changes `SITE_URL` to a non-`hatchik.com`
host) the inventory reflects that. Detection is purely env-file based
— no DB lookups, no calls into the tenant's auth server — so it works
even when the tenant container is down for redeploy.

**Quantification source.** Where the number is configured in code, I
cited the variable name in the docs page note. Where it's a policy
default (100 emails/day, ~10 GB disk), I labeled it as such and called
out that enforcement is social today, in-code tomorrow.

**Email re-ordering.** GitHub repo creation now runs *before* the
sandbox-ready email so the email can include repo deep-links in the
"Configure" buttons. Walkthrough email order is unchanged. If GitHub
fails, the inventory still renders — the repo-linked entries just lose
their `configure_url` and the UI/email drops the link.

**Tab placement on /account.** New "What's set up" tab sits second
(after "Sandbox") rather than first. The Sandbox tab is the
moment-of-arrival surface (URLs, status, "Open" / "Open repo"
buttons) — the services tab is reference material the customer dips
into when they're wondering "wait, does this have realtime?" Keeping
Sandbox first preserves muscle memory.

## Honesty checks (per task brief)

| Promise | Status |
|---|---|
| No invented quotas | All numbers traceable to `service_inventory.py` constants, which are either `mem_limit` values in `substrate-template/docker-compose.yml` or rate-limit constants in `signup-service/main.py`, or policy defaults flagged as such. |
| No "automated backups" | Listed under `available_on_upgrade` with `tier: roadmap`. Email + docs + endpoint all say "today: on-demand pg_dump". |
| No store-submission promise | Listed with `tier: customer` and the cost of Apple/Google developer accounts spelled out. |
| British English | "centre", "colour" not used in net-new copy; "organisation"/"transaction"/"cap" used. Audit pass clean — no Americanisms in new strings. |

## Open questions for the founder

1. **Is 100 emails/day on Sandbox the right cap?** Hatchik's Resend
   account is on the free tier (3K/mo across *all* sandboxes). At 100
   live tenants × 30 emails/month average that's 3,000 — already at
   the cap. The honest fix is per-tenant Resend subkeys (Resend
   supports them on paid tier); recommend documenting 100/day as the
   social cap today, with a roadmap commit to subkeys before we hit
   ~30 active tenants.

2. **~10 GB practical disk per tenant — what's the real ceiling?**
   The CAX21 host is 80 GB total minus the OS and substrate-template
   cache. At 5 active sandboxes (the MAX_CONCURRENT_PROVISIONS cap)
   that's ~12-15 GB per tenant after the host overhead. I picked 10
   GB as a publishable round number. If the host grows or shrinks,
   bump `SANDBOX_DISK_GB_PRACTICAL` in `service_inventory.py`.

3. **Should the docs page consume the inventory programmatically?**
   Currently the HTML table mirrors `service_inventory.py` by hand —
   so a constant change there requires editing two files. Options:
   (a) accept the duplication (current), (b) generate the HTML page
   from the Python module at deploy time, (c) have the docs page
   fetch `/api/account/services` on render (requires no auth or a
   public read-only variant). Option (b) is cleanest; (c) leaks too
   much complexity into a static docs page.

4. **Should we expose the inventory pre-signup?** A read-only
   `/api/services/sandbox-defaults` returning the static inventory
   could power the marketing page directly. Same source of truth,
   one fewer place to drift. Not in scope today — flagging for the
   next pricing-page rewrite.

5. **STACK.md, PRODUCT_OFFERING.md, WELCOME_EMAILS.md, and the
   orchestrator README** still cite "3 users / 100 MB" in internal
   prose. Customer-facing files (`index.html`, `terms.html`,
   docs/what-is-included.html, the sandbox-ready email) are
   migrated. Decision needed on whether to scrub the internal docs
   too — they're not rendered to customers but will confuse anyone
   onboarding to the project.

## Test plan (manual, on next deploy)

- Provision a fresh test sandbox; check the sandbox-ready email
  shows the "What's set up" + "What's NOT yet wired" sections in
  both text and HTML.
- Sign into `/account`, click the new "What's set up" tab; verify
  it loads the inventory per-sandbox and that "Configure →" buttons
  open Supabase Studio / the GitHub repo / the Upgrade tab.
- Flip `STRIPE_SECRET_KEY` to `sk_live_...` in the tenant `.env` and
  re-fetch `/api/account/services/<slug>` — Payments should flip
  from "test mode" to "live".
- Open `docs/what-is-included.html` and check the new tables render
  correctly and the sidebar anchors jump.
- Open `index.html` and verify the Sandbox pricing card no longer
  shows "3 users / 100MB".

## Out of scope (deliberately)

- Substrate-template changes — the inventory describes what the
  substrate ships with; it doesn't add new substrate capabilities.
- Live enforcement of the 100/day email cap. Today it's a published
  number; code enforcement waits for per-tenant Resend subkeys.
- A "Bring my own Resend key" UI on `/account`. Customers add the
  key by editing `.env` directly; a self-serve form is the natural
  follow-up once we trust the validation flow.
