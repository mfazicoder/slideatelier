# Agent: docs.hatchik.com

Built a minimal static documentation site at `docs.hatchik.com` to replace the 404 that the marketing-site footer currently links to.

## Files created

All pages under `proposals/hatchik/docs/`:

| File | Purpose |
|---|---|
| `index.html` | Welcome / Overview. What Hatchik is, who it's for, the three core flows (sign up, build, ship). |
| `getting-started.html` | End-to-end first-customer walkthrough — signup → magic-link → clone → AI tool → first change → live. With `<!-- TODO: screenshot -->` placeholders. |
| `ai-tool-setup.html` | Per-tool wiring: Claude Code, Cursor, Windsurf, Perplexity Comet, ChatGPT in browser. Universal first-prompt template adapted from `provision.py`'s `first_prompt_template()`. |
| `deploying-changes.html` | Both deploy paths (`git push` + `curl` POST with `X-Deploy-Token`). Worked example, response-code table, 6-per-5-minute rate-limit explainer. |
| `account-management.html` | The `hatchik.com/account` dashboard. Two-auth-surface explainer (Hatchik account vs end-user login). Name, GitHub, sign-out, delete, restore. |
| `what-is-included.html` | Honest substrate-vs-Launch table. Explicit "App Store submission never included on any tier" line per the constraint. |
| `faq.html` | All eight required FAQ questions plus six extras lifted from the marketing site's FAQ section. |
| `troubleshooting.html` | Eight failure scenarios with concrete fixes — pushes not updating, AI tool can't read `AI_CONTEXT.md`, magic link expired, accidental delete, slowness, AI broke substrate, custom domain not working, Stripe stuck in test mode. |

## Files modified

| File | Change |
|---|---|
| `proposals/hatchik/sandbox-orchestrator/host-caddy/Caddyfile` | Added `docs.hatchik.com` server block: wildcard TLS via Cloudflare DNS-01, static files served from `/var/www/hatchik-docs/`, security headers (HSTS, X-Frame-Options, etc.), 1-hour edge cache. Modelled on the existing `status.hatchik.com` block. |

## Design decisions

### Visual language

Pulled the brand bits (indigo + amber gradient logo, Inter + JetBrains Mono, slate text palette) from `proposals/hatchik/index.html` and `install.html`, but stripped out the marketing flourishes (no gradient hero text, no animated chat panels, no `.grain` background, no `.card-warn`/`.chip-amber` chips). End result is closer to Vercel / Linear / Stripe docs aesthetic — heavy whitespace, sidebar nav, plain prose at `max-w-3xl`, content-first.

### Layout

Two-column on desktop (`grid md:grid-cols-[220px_1fr]`), sidebar stacks above content on mobile. Sidebar is sticky on desktop (`md:sticky md:top-20`) with its own scroll. On mobile it just becomes a stack at the top — no JS, no hamburger.

### Navigation structure

Three sidebar sections matched to user journey: **Start here** (overview + getting started) → **Build** (AI tool setup, deploying, what's included) → **Manage** (account, FAQ, troubleshooting). Plus a "Need a human?" link to `hello@hatchik.com`.

### Header

Same look as `index.html` (logo + sticky nav), but with `Home` linking to `https://hatchik.com` (not `/`) and a `Sign in` link to `https://hatchik.com/account`. Added a small `/ docs` qualifier next to the Hatchik wordmark so it's visually clear this is a sub-site.

### Footer

Identical to `index.html`'s footer (Privacy / Terms / Status / Docs / Contact / My account / Restore sandbox / Delete sandbox + Paddle MoR disclaimer). The `Docs` link points to `/` since the user is already on docs.

### Per-page link strategy

All cross-links inside the docs use root-relative paths (`/getting-started`, `/ai-tool-setup`, etc.) — Caddy's `try_files {path} {path}.html` directive resolves `/getting-started` → `getting-started.html` so the URLs stay clean without trailing slashes.

### Tone

British English throughout. Friendly-but-confident. None of the banned words (`leverage`, `facilitate`, `revolutionary`, `game-changer`, `disrupt`) appear. Lightly informal where it helps — "Don't panic", "the boring bit". Honest about what Hatchik does *not* do (App Store submission, building your product, hand-holding architecture decisions).

### Caddy routing

Followed the `status.hatchik.com` shape exactly. The new block sits after `status.hatchik.com` so per-tenant routes (loaded via `import tenants.d/*.caddy`) still get a chance to match first if a tenant ever takes `docs` as a slug (it'd be blocked elsewhere, but defence-in-depth).

Files served from `/var/www/hatchik-docs/` (separate from `/var/www/hatchik/` so the docs deploy is independent of marketing-site changes). Source files in `proposals/hatchik/docs/`, deployment is a `rsync proposals/hatchik/docs/ user@host:/var/www/hatchik-docs/`. No API needed — pages are entirely static.

`Cache-Control: public, max-age=3600` — one hour at the edge. Docs change more often than the marketing site but not by the minute. A longer cache would need an invalidation step we don't have yet.

### What I deliberately *didn't* do

- **No JS framework.** All sidebar state is in the HTML — each page sets `is-active` on its own sidebar link. No client-side router.
- **No highlight.js.** Inspected the code samples and the syntax-highlighting value-add wasn't worth the extra CDN dependency. Code blocks use a dark monospace style consistent with `install.html`'s `.code-window`.
- **No "Edit on GitHub" links.** Customers aren't expected to PR the docs.
- **No search.** A future Algolia DocSearch makes sense once content size justifies it; for 8 short pages, sidebar nav is fine.
- **No deploy script.** Per task brief — worktree only, the user reviews + merges.

## Cross-references

Every page links forwards to "what to read next" so a customer following the docs sequentially doesn't get stuck on an island. The most common transitions:

- `index` → `getting-started` (the obvious next step)
- `getting-started` → `ai-tool-setup` + `deploying-changes`
- `ai-tool-setup` → `deploying-changes` + `troubleshooting`
- `account-management` ↔ `faq` ↔ `troubleshooting` (the three "manage" pages cross-link each other)

## Open questions for the founder

1. **Screenshot placeholders.** `getting-started.html` has four `<!-- TODO: screenshot -->` placeholders rendered as visible striped boxes. Need real screenshots before public launch: (a) the signup form, (b) the welcome email, (c) the default sandbox landing page, (d) a sandbox with a customer's first change live. Want me to capture these from a dev run, or are you doing it manually?

2. **Deploy mechanism.** Currently no script that copies `proposals/hatchik/docs/` to `/var/www/hatchik-docs/` on the host. Options: (a) extend the existing first-deploy bundle to include docs, (b) add a `make deploy-docs` target, (c) bake it into `setup-host.sh`. (a) seems lightest.

3. **Docs versioning.** As the product evolves the docs will drift. Not addressed yet — we're at v0 and the docs are versionless. Worth revisiting when we cut Launch tier publicly.

4. **The 30-second deploy claim** — I cited "twenty to forty seconds" in `deploying-changes.html` and "thirty seconds" in `getting-started.html` and `faq.html`, matching the existing pages. Verify against real production numbers once we have telemetry from `last_redeploy_at` in the registry.

5. **The Sandbox tier mailbox copy** — I wrote "shared `no-reply@hatchik.com` sender" for Sandbox tier mail. Confirm this matches what `provision.py` actually does today (vs Launch where SPF/DKIM/DMARC is set up on the customer's domain). If the Sandbox tier currently sends from somewhere else, update `what-is-included.html` accordingly.

6. **"Bring your own domain on Sandbox: never"** — I marked this as Launch-only in the matrix. The marketing site agrees, but worth a final sanity-check against any beta-promise wording in `WELCOME_EMAILS.md` or `FIRST_CUSTOMER_RUNBOOK.md` before public launch.

7. **Sidebar nesting.** Currently flat (one level under each section). If we add more pages — e.g. `migrations.html`, `secrets.html`, `webhooks.html` — we'll need to either flatten further or introduce a second level. No code change required now, just a flag.

## Verification

- All eight pages render valid HTML (manual visual check of structure: opening tags, closing tags, sidebar markers, footer).
- No banned words present (`grep -in "leverage|facilitate|revolutionary|game-changer|disrupt" docs/*.html` → empty).
- All sidebar links resolve to actual files in the directory.
- All external links use absolute `https://hatchik.com/...` URLs; intra-docs links are root-relative.
- Caddy block matches the `status.hatchik.com` template and references the wildcard cert correctly.

## Summary

Built a clean, sidebar-driven static docs site at `docs.hatchik.com` covering the eight pages the brief required. The look is utilitarian — Inter/JetBrains Mono on slate, no marketing flourishes, no JS framework — and follows the same brand cues as the rest of Hatchik but more readable for long-form content. Copy is grounded in what the substrate and orchestrator actually do today (cross-referenced against `provision.py`, the redeploy endpoint and the marketing pages). Caddy routing follows the existing `status.hatchik.com` pattern with the new block adjacent. No deploy step was run — the worktree is ready for review and merge. Open items for the founder centre on screenshots, a deploy script, and a couple of minor copy verifications. Build time roughly thirty minutes of file writing on top of fifteen minutes of source reading.
