# Copy audit report — Hatchik customer-facing surfaces

Branch: `claude/cranky-nash-5e9af5` (worktree only — no deploy)
Date: 2026-05-13

## Files audited

Marketing site + app surfaces:

- `proposals/hatchik/index.html`
- `proposals/hatchik/start.html`
- `proposals/hatchik/vs.html`
- `proposals/hatchik/account.html`
- `proposals/hatchik/delete-sandbox.html`
- `proposals/hatchik/restore-sandbox.html`
- `proposals/hatchik/install.html`
- `proposals/hatchik/privacy.html`
- `proposals/hatchik/terms.html`

Email templates (in code):

- `proposals/hatchik/sandbox-orchestrator/provision.py` (sandbox-ready, walkthrough, AI_CONTEXT.md generator)
- `proposals/hatchik/signup-service/main.py` (welcome, deletion-confirm, magic-link)

Substrate (handed to customer at provision time):

- `proposals/hatchik/substrate-template/CLAUDE.md`
- `proposals/hatchik/substrate-template/mcp.json`
- `proposals/hatchik/substrate-template/.cursor/mcp.json`

## Classification of every claim found

Headline-level summary; full per-fix detail in the diff.

### True today — left alone

- Magic-link sign-in (hatchik.com/account, sign-in emails)
- ~60–90s sandbox provisioning at `<slug>.hatchik.com`
- Free-Sandbox tier (£0, test-mode payments, ≤3 users, 100MB, 30-day idle archive, 7-day restore)
- Self-serve sandbox deletion (`/delete-sandbox`) + restore (`/restore-sandbox`)
- Status page at `status.hatchik.com`
- Abuse protection (Turnstile, rate limits, disposable-email block, geo-IP)
- Push-to-deploy redeploy (~30s) and AI-tool redeploy endpoint — newly shipped, copy hardened in account.html GitHub card and walkthrough email
- AI-tool handoff via `AI_CONTEXT.md` in the per-tenant repo
- Per-tenant private GitHub repo (when `HATCHIK_GITHUB_TOKEN` is configured)
- Paddle MoR for Hatchik subscription billing

### True conditionally — added caveat / scoped

- **Custom domain & mailboxes & DNS** — Launch tier, scoped to "Launch+" in tables and pricing card
- **Bring-your-own Stripe** — substrate wires customer's own Stripe Secret Key; terms.html §6 now says "If you want your end-users to pay you: your own Stripe account (the substrate wires it in once you add the secret key), or a Paddle account if you'd rather have a Merchant of Record"
- **Five hosting regions** — softened from "5 regions across 3 continents" to "Germany live today, additional regions open on demand" everywhere (Hosting Regions section, FAQ "Where is my data hosted?", footers on index.html + vs.html, privacy.html §7)
- **GitHub repo handoff** — terms.html §7 clarifies repo lives under customer account "from day one, once you connect a GitHub username"
- **Mailbox transfer on exit** — terms.html §7 scoped to Launch tier

### Not yet built — softened or reframed

| Promise | Status | Action |
|---|---|---|
| Mobile builds "automatically generated" | Capacitor scaffold ships; cloud-build pipeline in flight | Reframed everywhere to "Capacitor mobile scaffold (build locally today; cloud-build coming on Launch tier)" — hero subtitle, bundle card 7, scope table, comparison matrix, pricing cards, vs.html matrix + pricing snapshot, start.html plan picker, account.html upgrade tab |
| "Nightly backups + one-click restore" | Not wired | Bundle card 9 reframed to "Monitoring + on-demand snapshots (automated backups on the roadmap)"; FAQ rewritten with honest "automated nightly off-site backups aren't wired yet" answer + email-us-for-a-snapshot fallback; scope table row reworded; pricing cards softened; "backed up every night" copy removed from the database card |
| Vibe-coding rails ("try in a private copy", "AI can't wipe data", "yesterday is one click away", "pause the real app") | Not wired | Section rewritten — two "Live" rails (Sandbox-as-safe-playground; migration gating via CLAUDE.md) and two "Roadmap" rails (one-click rollback; read-only mode), each tagged with a status pill. Demo chat reworked from "private preview at 23-recipes.prepsheet.app" to honest "pushed to your sandbox" workflow |
| Linear MCP / Linear-based backlog | Not wired (and Linear MCP server doesn't exist) | All Linear references in `substrate-template/CLAUDE.md` replaced with `BACKLOG.md` workflow. `mcp.json` + `.cursor/mcp.json` Linear blocks removed (Hatchik kept), with a `_comment` describing when they'll come back. FAQ "How does the to-do list work?" + bundle card 10 reworked to lead with BACKLOG.md. Linear positioned as a planned Launch-tier upsell |
| Browser-confirmation for AI-driven risky actions | Not wired | FAQ "Is it safe to let my AI manage my live app?" rewritten — leads with Sandbox-first rail + migration gating, marks browser-confirmation flow as roadmap |
| MCP-driven signup from chat | Not wired (only project management via MCP works) | "From your AI assistant" path B card and FAQ "Can I really sign up through my AI tool?" caveat that website wizard is today's path; MCP "new project from chat" is rolling out |
| docs.hatchik.com | 404s; doc site being built in parallel | Confirmed link only appears in footers; walkthrough email's "More at hatchik.com/docs" link changed to /#faq (with comment explaining why); AI_CONTEXT.md template "Docs:" row changed to "FAQ:". Footer link itself left in place per brief |
| "Operated globally" footer | Single Germany host today | Both index.html + vs.html footers softened to "production hosting today in Nuremberg, Germany — additional regions open on demand" |

### Pricing accuracy on vs.html

Page explicitly stamps "Pricing as of late 2025 / early 2026" in the hero and footer. Each competitor row carries a softened phrasing ("from ~$X", "~$X/mo + usage on top") and links to the vendor pricing page. Added a "double-check on the vendor's site before you sign up" note to the pricing-snapshot caveat. The Hatchik row (£0 → £108/yr) verified against pricing card in index.html.

## Fixes applied — by file

### `index.html`
- Meta description: dropped naked "mobile" → "mobile scaffold"
- Hero subtitle: dropped "mobile apps" + "backups" → "the mobile-app scaffold"
- Bundle card 7 (mobile): renamed chip "iOS + Android shells", added Launch-tier chip, body rewritten to scaffold + local build + cloud build incoming
- Bundle card 9 (safety net): chip + body rewritten — monitoring live, automated backups on roadmap
- Bundle card 10 (backlog): Linear removed, BACKLOG.md is the day-one path
- Scope table: mobile row reworded; backups row reworded; "AI MCP integration" row → "AI-tool handoff via AI_CONTEXT.md"
- Hosting Regions section: 4-of-5 regions tagged "Opening on request"; Germany tagged "Live today"; intro + closing copy softened
- Safe to Experiment section: full rewrite with live/roadmap tags on each rail; demo chat snippet reworked
- Comparison matrix: "iPhone + Android apps" row split into "scaffold ~ shells, you build" for Hatchik; "Safety rails" row reworded
- Pricing card (Launch + Growth): mobile + backups + payments rows softened; backup retention row scoped to "once automated backups land"
- "Two paths in" Path B: caveat that MCP signup is rolling out
- FAQ "How do backups actually work?" — rewritten honestly
- FAQ "Where is my data hosted?" — single-host reality
- FAQ "How does the to-do list work?" — BACKLOG.md instead of Linear
- FAQ "Is it safe to let my AI manage my live app?" — Sandbox-first rail
- FAQ "Can I really sign up through my AI tool?" — added caveat
- FAQ "How quickly do I actually get my Hatchik after signup?" — separated Sandbox (auto, ~60–90s) and Launch (hand-onboarded)
- FAQ "What if I outgrow Hatchik?" — dropped unverifiable "stress-tested" claim
- Apex footer: "operated globally" softened to "production hosting today in Nuremberg, Germany"
- Signup section Launch card: bullets softened (Paddle MoR, Capacitor scaffold, region caveat)

### `vs.html`
- Matrix row "Real auth, payments, mail and mobile wired" reworded
- Matrix row "Mobile builds" → "Mobile scaffold" with partial tick for Hatchik + new footnote 30
- Pricing snapshot card: "mobile shells" → "Capacitor mobile scaffold"
- Pricing snapshot intro: "the only one that ships mobile" → "the only one that bundles a mobile scaffold"
- Pricing snapshot caveat: added "double-check on the vendor's site" line
- Footer: "operated globally" softened the same way as index.html

### `start.html`
- Launch plan card: "mobile builds" → "Capacitor mobile scaffold"

### `account.html`
- Upgrade tab: mobile builds → Capacitor mobile scaffold ready for local build
- GitHub card already honest about ~30s push-to-deploy — left as-is (redeploy agent shipped)

### `privacy.html`
- §3 Paying customers: Stripe → Paddle as MoR, with bring-your-own Stripe caveat
- §6 Sub-processors: removed Backblaze B2 (not wired) and added a separate paragraph saying it's on the roadmap; replaced Stripe with Paddle; removed DigitalOcean / Vultr (only Hetzner used today); Infomaniak scoped to Launch-tier mailboxes
- §7 Where data lives: rewritten — Nuremberg today, more regions on demand; Paddle data-processing pointer

### `terms.html`
- §4 Pricing & billing: Stripe → Paddle MoR explained
- §6 What you provide: customer's Stripe Connect requirement reworded to BYO Stripe / Paddle for charging end-users
- §7 Your code/data/infra: Sandbox vs Launch ownership scoped; database row says "Postgres dump" rather than "full export"; mailbox transfer scoped to Launch

### `delete-sandbox.html` / `restore-sandbox.html`
- Already honest — left alone.

### `install.html`
- Footer docs link unchanged (only in footer, per brief)
- "Built for founders, not engineers" tagline left — substrate is the same regardless of tier, claim holds
- Did NOT rewrite the MCP install instructions themselves — the Hatchik MCP wasn't flagged as not-built in the brief, and the page already documents real config formats. If Hatchik MCP isn't live by launch this becomes a credibility risk; flagged below.

### `sandbox-orchestrator/provision.py`
- `send_sandbox_ready_email` text + HTML: "mobile builds" → "the mobile scaffold ready to build"
- `send_walkthrough_email`: `docs_url` flipped from `/docs` (404) to `/#faq` with a code comment
- `write_ai_context` AI_CONTEXT.md template: "Docs:" line → "FAQ:" line (avoids 404 from inside AI tools)

### `substrate-template/CLAUDE.md`
- "The product" section: Linear-MCP-call-to-action replaced with BACKLOG.md workflow; Linear positioned as planned Launch-tier upsell
- Tech stack table: Mobile row caveat-ed; Deploy row scoped to Hetzner; Backups row rewritten; Monitoring row dropped Uptime Kuma / Sentry mentions; Backlog row replaced
- Branch and PR conventions: dropped LIN-{issue-id} naming; replaced with kebab-case branch + BACKLOG.md task reference in commit body
- Whole "Linear integration" section deleted; replaced with "Working with BACKLOG.md" section describing the file-based workflow
- "Style: commits" — `[LIN-XX]` requirement removed

### `substrate-template/mcp.json` + `substrate-template/.cursor/mcp.json`
- Linear MCP block removed (Hatchik kept). Added a `_comment` field describing when it comes back.

## Open questions for the founder

1. **BACKLOG.md template** — CLAUDE.md now claims the file ships pre-populated at the repo root. The substrate-template doesn't actually include a `BACKLOG.md` file yet. Decision needed: (a) write a generic BACKLOG.md template into substrate-template now (with `{{PRODUCT_NAME}}` and 20 placeholder tasks) and have provision.py interpolate it, or (b) have provision.py *generate* BACKLOG.md from the customer's idea (LLM-style), or (c) ship a tiny stub that says "add tasks here" and trust the AI to populate on first prompt.
2. **Linear-tier upsell** — referenced in two places (FAQ + CLAUDE.md) as a planned Launch-tier upsell. Confirm this is on the post-launch roadmap or strip the forward-looking promise.
3. **Hatchik MCP** — `install.html` documents an `@hatchik/mcp` npm package + `api.hatchik.com` endpoint. Neither exists yet. Either (a) flag this page as "private beta — connect manually" until the MCP ships, (b) build the minimum-viable MCP (project status + redeploy + logs are good first verbs) before launch, or (c) replace the page with a "coming soon" stub and link to the website wizard as the only signup path. Not touched in this audit because it wasn't in the brief's "not yet built" list.
4. **"docs.hatchik.com" launch date** — link is in footers; brief said leave alone. If the docs site doesn't ship before public launch, the footer link 404 is a credibility risk — consider hiding the link via a `{{DOCS_LIVE}}` flag in the templates until it's live.
5. **Cloud mobile builds — branding** — copy now says "cloud-build pipeline coming on Launch tier shortly". Is "Launch tier" the right tier for this gating (vs Growth), and is "shortly" the right tag (vs a quarter)? Re-tighten when the mobile-builds agent merges.
6. **Read-only / pause-the-app rail (Rail 04)** — copy now lists this as a roadmap item. Confirm we actually plan to ship this; if not, drop the rail entirely rather than leaving an unmet promise on the page.
7. **Backups timing** — FAQ + bundle card now say "near-term roadmap". If automation is more than a quarter out, "near-term" becomes a stretch. Decide a concrete target (e.g. "by end of Q3 2026") and tighten when it lands.
8. **Pricing accuracy** — competitor prices on vs.html are stamped "late 2025 / early 2026" and softened with "approximate / from / up to". I didn't WebSearch each vendor (the constraint was "if you can't verify, soften the claim"). When the founder next refreshes the page, a 30-min spot check against Bolt / Lovable / Replit pricing pages will keep the numbers honest.
9. **Tagline "Built for founders, not engineers"** — used in two footers. Left alone — accurate as a positioning claim. But the substrate's CLAUDE.md does presume the user has an AI coding tool, which is a soft contradiction. No change needed unless we narrow the audience further.

## Remaining marketing risks for launch day — ranked

1. **HIGH — Hatchik MCP** install.html promises a wired MCP. Until that MCP ships, anyone following the install instructions will get a 404 on `@hatchik/mcp`. Easiest fix: add a "private beta" banner to install.html and route everyone through the website wizard until the MCP is real.
2. **HIGH — docs.hatchik.com** footer link 404s. Will be the first thing anyone clicks after reading "Built for founders". Mitigate by hiding the link until the doc site is up, or pointing it temporarily at the FAQ anchor.
3. **MEDIUM — `BACKLOG.md`** in the substrate-template. CLAUDE.md now promises it. Either ship the template or make provision.py generate it.
4. **MEDIUM — Cloud mobile build** copy says "shipping shortly on Launch tier". If the mobile agent's PR hasn't landed by launch day, this is a soft promise that ages badly within weeks.
5. **MEDIUM — Browser-confirm for AI-risky actions** mentioned as roadmap in the AI-safety FAQ. Customers who skim won't read past "Sandbox-first rail" — but anyone who reads carefully will spot the gap and may worry.
6. **LOW — Sub-processor list on privacy.html** — accurate to the extent we *know* what we currently use, but launch-day post-mortem item: confirm with the founder that Infomaniak is really being used today (substrate references it; provisioning code doesn't appear to wire it yet). If not, soften §3 accordingly.
7. **LOW — "Cancel any time, take everything with you"** — terms.html §7 says we transfer server root credentials and mailbox credentials on offboarding. Confirm we actually have an offboarding script that does this on the host. If it's still manual, that's fine for launch — but write it down somewhere as "manual today, automate post-launch".

## Summary

A 200-word summary of the audit and what changed:

Hatchik's marketing copy had grown ambitious during sprint mode — across 12 files, dozens of "we'll", "automatically", "every", and "all" claims promised features that aren't wired yet (mobile builds, nightly backups, four vibe-coding safety rails, Linear-backed task tracking, browser-confirm for risky AI actions, five hosting regions, MCP-driven signup from chat). The audit reframed every one without scrapping the underlying value prop: mobile becomes "Capacitor scaffold today, cloud build shipping shortly"; backups become "on-demand snapshots today, automated nightly on the roadmap"; the four rails become two "Live" and two "Roadmap" with status pills; Linear becomes a planned Launch-tier upsell with `BACKLOG.md` as the day-one path. Pricing and Paddle MoR claims tightened to match the apex Omani-company reality. The terms.html / privacy.html legal pages got Stripe→Paddle fixes (matching the parked Stripe tech-debt note in MEMORY.md) and a single-host disclosure. The substrate-template's CLAUDE.md and both mcp.json files lost their Linear references in favour of file-based backlog working. Three credibility risks remain that need product decisions rather than copy fixes: the unfinished Hatchik MCP, the still-404 docs site, and the missing `BACKLOG.md` template — all flagged in the open-questions section.
