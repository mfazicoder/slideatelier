# Hatchik — Reseller / Partner Programme Research

For every paid line in our stack, there's potentially a reseller,
partner, or volume-discount programme. Each one we activate either
reduces per-customer cost (improving margin) or improves the offering
at the same price (improving competitiveness). This doc tracks what's
worth investigating and what we've signed up for.

Last researched: 2026-05-12.

---

## Apply today (priority list)

Ranked by impact-per-hour-of-effort for a pre-revenue solo founder.
These are the only programmes worth working on this week. Everything
else gates on revenue, funding, or customer count.

| # | Programme | URL | Action item | Effort |
|---|---|---|---|---|
| 1 | **Cloudflare for Startups — Bootstrapped tier** | https://www.cloudflare.com/forstartups/ | Apply with promo code `BOOTSTRAPPED` for the $5K credit tier (no funding required) | 20–30 min |
| 2 | **Anthropic MCP / Connectors Directory** | https://claude.com/docs/connectors/building/submission | Polish the `@hatchik/mcp` server (tool annotations, privacy policy), submit form. Free distribution to Claude users. | 2–4 hr including polish |
| 3 | **Smithery.ai + Cursor MCP marketplace + mcp.so + official registry** | https://smithery.ai/ , https://cursor.com/marketplace , https://registry.modelcontextprotocol.io/ | Publish once via `smithery mcp publish`, submit GitHub issue to mcp.so, register with official MCP registry. All four are free directory listings. | 1–2 hr total |
| 4 | **Sentry for Startups** | https://sentry.io/for/startups/apply/ | Apply for the $5K credit + 6-months-free Teams plan. Eligible: under 2 years old, <$5M raised. 2–3 day response. | 15 min |
| 5 | **AWS Activate — Founders tier** | https://aws.amazon.com/startups/credits/ | $1,000 credit + $350 dev-support credit, no investor needed. Worth grabbing as a fallback / future-AGI-data-warehouse buffer. | 15 min |

The top five together = roughly half a day of work for ~$6–7K in
credits plus three free distribution surfaces. Everything else below
gates on either revenue, funding, or customer count, so deferring is
the right call until we have customers.

---

## Status legend

- 🔎 To investigate
- ✏️ Investigating
- ✓ Active
- ✓ Apply now (researched, viable today)
- ❌ Not viable (with reason)
- 📌 Deferred (revisit threshold noted)

## By layer

### Hosting / VPS

| Provider | Programme | Status | Notes |
|---|---|---|---|
| **Hetzner** | Hetzner Cloud Partner | 📌 No formal partner programme | Hetzner has only a referral programme ($10 credit per converted referral, gated on already having paid 3 invoices or $100 total). No public volume-discount or reseller tier. Third-party reseller modules exist but are unofficial. Revisit by direct outreach at ~£2k/mo Hetzner spend. |
| **Infomaniak** | Infomaniak Partner Network | 📌 Apply at €1.8k/yr volume | Up to 35% discount on flagship products + cashback on managed services. Requires registered company with active website AND minimum €1,800 / CHF 2,000 product volume per year. We won't hit that until ~25 customers on Infomaniak hosting. Apply once we cross 20 customers. |
| **DigitalOcean** | DigitalOcean Hatch | 📌 Mostly AI-native | $100K credits over 12 months (use-it-or-lose-it monthly), requires <$10M raised, prioritises AI-native startups. Hatchik is SaaS-substrate, not AI-native — would be a stretch. The "Founders" entry tier is realistic; Hatch full tier needs partner affiliation. Revisit if we need US-region DO infra. |
| **Vultr** | Vultr Partner Program | 📌 No published terms | "Industry-best margins" claimed but actual % is not disclosed publicly; requires direct outreach. Skip until we need Vultr regions. |
| **Linode (Akamai)** | Akamai Partner Program | 📌 | Revisit if customers want Linode regions. |

**Strategy:** Hetzner referral is the only no-effort win — drop a
referral link in our docs once we have customers. Infomaniak partner
is the next milestone (~20 customers). DigitalOcean Hatch is unlikely
to approve a non-AI bootstrapped SaaS substrate.

#### Findings — Infomaniak Partner Network
- URL: https://www.infomaniak.com/en/reseller-program/registration
- Eligibility: Company registered in trade register (UK Ltd works); active website; **minimum €1,800/CHF 2,000 product volume per year**
- Discount: Up to 35% on flagship products + cashback on managed services
- Application: Online form; Swiss-based support
- Effort: 30 min to apply, but pointless before €1.8k/yr volume
- Recommendation: 📌 **Apply at threshold ~20 customers** on Infomaniak hosting

#### Findings — Hetzner
- URL (referral only): https://www.hetzner.com/legal/referrals
- Eligibility for referral: paid 3 Hetzner Cloud invoices ≥ $5 each, OR $100 total paid
- Discount: $10 credit per converted referral (referee gets $20)
- No public volume / reseller tier
- Recommendation: ❌ **No formal partner programme exists**; use referral once we are a paying customer

#### Findings — DigitalOcean Hatch
- URL: https://www.digitalocean.com/startups
- Eligibility: ≤$10M raised; matching corporate website/email; **prioritises AI-native startups**; service-based businesses excluded
- Discount: Up to $100K over 12 months ($10K/month, use-it-or-lose-it); partner affiliation strongly increases approval odds
- Recommendation: 📌 **Apply later if we add a clearly-AI-flavoured story**; the entry-tier Founders/credits without partner affiliation typically lands at $1–2K not $100K

---

### Domain registration

| Provider | Programme | Status | Notes |
|---|---|---|---|
| **Infomaniak Registrar** | Reseller portal | 📌 Same gate as hosting partner | Bundled into Infomaniak Partner Network above; same €1.8k/yr threshold. |
| **Namecheap Reseller** | Namecheap Reseller | ❌ Different product than we need | Namecheap's "Reseller Hosting" plans (Nebula $19.88/mo, etc.) are cPanel hosting resale — not domain-registrar wholesale. Their domain wholesale uses the same retail rates; no formal % discount published. Skip unless we change registrar strategy. |
| **OpenSRS / Tucows** | Wholesale registrar | 📌 Apply at 100+ domains | No monthly fees, no minimum purchase. One-time non-refundable activation fee (amount not disclosed publicly — needs direct outreach). Real wholesale registry pricing. Worth pursuing once we are managing 100+ customer domains. |
| **Cloudflare Registrar** | At-cost pricing | ❌ Confirmed not resellable | Cloudflare Self-Serve Subscription Agreement (Feb 2026) explicitly prohibits "licensing, sublicensing, selling, reselling, renting, leasing, transferring, assigning, distributing, or making services available to third parties." Customers can transfer to Cloudflare Registrar themselves post-Hatchik. |

#### Findings — OpenSRS
- URL: https://opensrs.com/become-a-domain-reseller/
- Eligibility: No volume minimums, no monthly fees; pays a one-time activation fee (not publicly disclosed — needs sales contact)
- Discount: True wholesale registry pricing (typically £1–3 off retail per domain depending on TLD)
- Effort: Direct sales call to get the activation fee number
- Recommendation: 📌 **Apply at 100 customer domains** — that's when the per-domain saving outweighs activation fee and integration effort

#### Findings — Cloudflare Registrar
- URL: https://www.cloudflare.com/terms/ §4 (resale prohibition)
- Recommendation: ❌ **Not viable.** Confirmed by Feb 2026 self-serve terms

---

### Mail

| Provider | Programme | Status | Notes |
|---|---|---|---|
| **Infomaniak Mail** | Reseller / wholesale | 📌 Same gate as hosting partner | Bundled into Infomaniak partner programme. |
| **Migadu** | Affiliate programme | 📌 | Small affiliate kickback (~10–20% of first-year revenue). Marginal at our scale. |
| **Resend** | Volume pricing (no formal partner programme) | ❌ Auto-applied | No partner programme that we could verify. Volume discount is automatic via published Scale plan tiers ($0.90/1k at 100k, drops to $0.65/1k at 1M). Enterprise (sales-led) starts at 2.5M+ emails/mo. Just sign up and use it. |
| **Amazon SES** | None (commodity) | ❌ | No partner programme; volume discounts apply automatically. Already cheapest at scale. |

#### Findings — Resend
- URL: https://resend.com/pricing
- No public partner programme. Volume scaling is automatic on the Scale plan.
- Pricing curve: $0.90/1k emails at 100k/mo, $0.65/1k at 1M/mo
- Dedicated IPs available at 500+/day on Scale plan
- Recommendation: ❌ **Nothing to apply for** — automatic tier discount. Revisit Enterprise at 2.5M emails/mo.

---

### CDN / DNS / WAF

| Provider | Programme | Status | Notes |
|---|---|---|---|
| **Cloudflare** | Cloudflare for Startups — Bootstrapped tier | ✓ Apply now | **Bootstrapped tier exists.** $5K in credits for self-funded founders using promo code `BOOTSTRAPPED`. Hatchik qualifies. |
| **Cloudflare** | Cloudflare Partner Network | ❌ Resale prohibited | Cloudflare Self-Serve terms (Feb 2026) explicitly prohibit reselling Cloudflare services. Partner Network is for system integrators offering implementation services, not resale. |
| **Cloudflare R2** | Volume pricing | ✓ Auto-applied | Nothing to apply for. Zero-egress pricing already structurally advantageous. |
| **Bunny.net** | Reseller programme | 📌 | Cheaper than Cloudflare for some workloads; revisit if egress costs matter. |

#### Findings — Cloudflare for Startups (Bootstrapped tier)
- URL: https://www.cloudflare.com/forstartups/
- Eligibility (Bootstrapped/$5K tier): "Just getting started," <5 years old, valid website and email, building software. **No funding required.** Hatchik qualifies.
- Higher tiers gate on funding: $25K at <$1M raised, $100K at $1M–$5M raised, $250K at Tier-1-VC-backed.
- Apply with promo code: `BOOTSTRAPPED`
- Credits valid 12 months; capped at $10K for R2/Cache Reserve and $50K for Workers AI within the credit pool
- Effort: 20–30 min form
- Recommendation: ✓ **Apply now.** This is the single highest-value programme available to us today.

#### Surprising finding
The Cloudflare for Startups Bootstrapped tier (introduced 2025) makes
prior notes in this doc ("VC-backed only") stale. Self-funded solo
founders do qualify for $5K of credits — apply this week.

---

### Object storage / Backups

| Provider | Programme | Status | Notes |
|---|---|---|---|
| **Backblaze B2** | Backblaze Partner Programme | ❌ No B2 revenue share | Backblaze explicitly does **not** pay commissions on B2 Cloud Storage; resellers must build margin through their own markup (which we already do). The 10% revenue share applies only to Computer Backup, not B2. B2 Reserve (annualised capacity SKU) requires channel partner relationship for enhanced margins. |
| **Cloudflare R2** | Volume pricing | ✓ Auto-applied | Zero-egress is the structural advantage; no programme to apply for. |
| **Wasabi** | Reseller | 📌 | Alternative to B2; comparable pricing. |

#### Findings — Backblaze B2
- URL: https://www.backblaze.com/b2/resellers.html
- Eligibility: Open
- Discount: **No commission on B2.** 10% only on Computer Backup product line. B2 Reserve (capacity-based, annualised) is channel-only and requires direct sales contact.
- Recommendation: ❌ **Marketed as a partner programme but offers no actual B2 discount** for our use case. Just mark up the standard B2 rate. Revisit B2 Reserve only at multi-PB scale.

---

### Payments

| Provider | Programme | Status | Notes |
|---|---|---|---|
| **Stripe Connect** | Standard / Express | ✓ | Already in use. |
| **Stripe Atlas** | Perks partner / referral | 📌 Unclear mechanic | Stripe doesn't publicly document the partner-side compensation. "Atlas perks partners" appears to mean *we offer a perk* (discount on Hatchik to Atlas-incorporated startups) in exchange for distribution into Atlas's $50K perks bundle — i.e. it's a customer-acquisition channel, not a referral kickback. Worth investigating once Hatchik is launched and we can offer e.g. 50% off first 6 months to Atlas founders. |
| **Paddle** | Not applicable | ❌ | Paddle is merchant-of-record; doesn't fit Hatchik's pass-through model. Confirmed. |

#### Findings — Stripe Atlas Perks Partner
- URL: https://support.stripe.com/questions/suggest-new-perks-or-become-an-atlas-partner
- Eligibility: Unclear; appears to be curated (Vouch, Mercury are examples)
- Discount mechanic: **We provide the discount**, Atlas distributes us into the perks bundle to 60k+ Atlas founders. There is no kickback to us — the value is qualified distribution.
- Effort: Submission form + likely negotiation
- Recommendation: 📌 **Apply post-launch** when we can credibly offer "50% off first 6 months for Atlas founders." Skip now — nothing to apply with pre-launch.

---

### Monitoring / Errors

| Provider | Programme | Status | Notes |
|---|---|---|---|
| **Sentry** | Startup programme | ✓ Apply now | $5K credits + 6 months free Teams plan. Eligibility: founded <2 years ago, <$5M raised, new to paid Sentry. We qualify. |
| **BetterStack** | Reseller / affiliate | 📌 | Affiliate programme exists; could offer customers premium uptime monitoring as upsell. |
| **Grafana Cloud** | Free tier + startup credits | 📌 | Free tier covers most of v1. |
| **Self-hosted (Uptime Kuma, Sentry OSS)** | Free | ✓ | Our default. |

#### Findings — Sentry for Startups
- URL: https://sentry.io/for/startups/apply/
- Eligibility: <2 years old (Hatchik qualifies); <$5M raised (qualifies — we are pre-revenue); new to paying for Sentry (free plan use is fine)
- Discount: Up to $5,000 in credits + priority support. Separate promo offers 6 months free on Teams plan.
- Application: Web form; 2–3 business day response
- Effort: 15 min
- Recommendation: ✓ **Apply now.** Fast form, decent credit, low risk.

---

### AI providers

| Provider | Programme | Status | Notes |
|---|---|---|---|
| **OpenRouter** | Volume discount (negotiated) | 📌 Volume-gated | OpenRouter's standard fee is 5.5%; negotiable to 4–5% at $2M+ annual spend. Below that, no published tier. Volume discounts 10–30% available for "larger teams" but not publicly defined. Direct outreach needed; gate at ~£15k/mo AI spend. |
| **Anthropic** | Direct API reseller | 📌 | Requires £20k+/mo consumption. Revisit at that volume. |
| **OpenAI** | Direct API reseller | 📌 | Similar threshold. |
| **Google Vertex / Gemini** | Standard | 📌 | Use via OpenRouter for now. |
| **Together / Replicate / Modal** | Open-source model reseller | 📌 | Revisit if we expose OSS model passthrough. |

#### Findings — OpenRouter
- URL: https://openrouter.ai/pricing
- Eligibility: Negotiated above ~$2M annual spend
- Discount: Platform fee negotiable from 5.5% to 4–5% at enterprise volume; 10–30% volume discount for "larger teams" (not publicly tiered)
- Application: Email sales
- Recommendation: 📌 **Apply at $2M annual spend** (~£15k/mo). Until then we get the auto rates.

---

### Backlog / Project tools

| Provider | Programme | Status | Notes |
|---|---|---|---|
| **Linear** | Linear for Startups | 📌 Partner-affiliation gated | Linear's Startup Program offers **6 months free** of Basic or Business plan, but only with a partner code from an approved Linear Startup partner (accelerators, VCs, etc.). Standard solo-founder path: use the Free plan (unlimited members, 2 teams, 250 issues) which is genuinely generous. |
| **Linear** | Become-a-partner | 📌 Distribution-side | Linear's "become a partner" form is for investors/accelerators offering Linear to portfolio companies — not for tool integrators. Not applicable to Hatchik. |
| **Notion** | Partner programme | 📌 | Only if we add Notion as backlog option in v2. |

#### Findings — Linear for Startups
- URL: https://linear.app/startups
- Eligibility: <50 employees AND must have a partner code from an approved Linear partner (accelerator, VC, etc.)
- Discount: 6 months free on Basic ($10/seat) or Business ($16/seat)
- Standard alternative: Free plan = unlimited members, 250 issues, 2 teams — sufficient for solo founder
- Recommendation: 📌 **Use Free plan now.** Revisit Startup Program only if we join an accelerator that's a Linear partner. The "Linear Partners" listing for integrators doesn't exist as we initially assumed.

---

### Other / cross-cutting

| Provider | Programme | Status | Notes |
|---|---|---|---|
| **GitHub** | GitHub for Startups | ❌ Funding + partner gated | Requires (a) outside funding (up to Series B), (b) affiliation with an approved GitHub for Startups partner (accelerators / VCs / VC firms), (c) new to GitHub Enterprise. Hatchik fails (a) and (b). Bootstrapped solo founders do **not** qualify. |
| **AWS** | AWS Activate Founders | ✓ Apply now | $1,000 credits + $350 dev-support credit. No funding or partner needed for Founders tier. Pre-Series B, <10 years old, paid AWS account. Hatchik qualifies. |
| **Google Cloud** | Google for Startups Cloud — Start tier | ✓ Apply now (low priority) | $2,000 credits for bootstrapped startups (no equity funding). <5 years old, <Series A. Hatchik qualifies. Same effort as AWS Activate. |
| **Stripe Atlas** | Perks partner | 📌 Post-launch | See Payments section. |
| **Anthropic Connectors Directory** | App showcase | ✓ Apply now | Submit MCP server to Claude's Connectors Directory. Requires tool annotations (30% of rejections are missing these), privacy policy, public documentation, OAuth if auth needed. Free distribution to Claude users. |
| **Cursor Marketplace** | MCP server listing | ✓ Apply now | Cursor Marketplace accepts MCP servers as plugins. Listed alongside skills, subagents, rules, hooks, commands. |
| **Smithery.ai** | MCP server registry | ✓ Apply now | `smithery mcp publish` CLI command or web dashboard submission. Largest MCP directory by traffic. |
| **mcp.so** | MCP server registry | ✓ Apply now | Submit via GitHub issue or Submit button in nav. Trivial. |
| **Official MCP Registry** | registry.modelcontextprotocol.io | ✓ Apply now | The official community-driven registry. Servers identified by stable UUIDs with authenticated namespaces. |
| **HackerNews / Product Hunt** | Free launch surfaces | 📌 | Not partner programmes; coordinate at launch. |

#### Findings — GitHub for Startups
- URL: https://github.com/enterprise/startups
- Eligibility: outside funding required (up to Series B), partner affiliation required, new to GitHub Enterprise
- Discount: $10,000 in GitHub credits over 12 months for Enterprise + Copilot + Advanced Security
- Recommendation: ❌ **Hatchik does not qualify** as bootstrapped solo founder. Revisit only if we take outside funding via a partnered accelerator/VC.

#### Findings — AWS Activate Founders
- URL: https://aws.amazon.com/startups/credits/
- Eligibility: Pre-Series B, <10 years old, active AWS paid account, business email matching website domain. **No investor required for Founders tier.**
- Discount: $1,000 AWS credits + $350 Developer Support credits
- Application: 2–7 business day response
- Effort: 15 min
- Recommendation: ✓ **Apply now.** Low effort, future option value (e.g. data warehouse).

#### Findings — Google for Startups Cloud (Start tier)
- URL: https://cloud.google.com/startup/apply
- Eligibility: <5 years old (incorporation date), pre-Series A, working website. Bootstrapped/grants/F&F money OK for Start tier.
- Discount: $2,000 in Google Cloud credits
- Effort: 15 min
- Recommendation: ✓ **Apply now if we anticipate using any GCP service** (Vertex AI, Gemini, Workspace). Defer if not.

#### Findings — Anthropic Connectors / MCP Directory
- URL: https://claude.com/docs/connectors/building/submission
- Eligibility: Open; submission form always open
- Requirements:
  - Server basics: name, URL, tagline, description, use cases
  - Connection details: auth type, transport, read/write caps
  - **Tool annotations** (read vs modify) — 30% of rejections are for missing these
  - Privacy policy (immediate rejection if missing/incomplete)
  - Public documentation by publish date (blog post or help article is fine)
  - OAuth if auth required
  - MCP Apps additionally require screenshots
- Effort: 2–4 hr (most of that is polishing annotations and writing privacy policy)
- Recommendation: ✓ **Apply now.** Free distribution to Claude's user base.

#### Findings — Smithery.ai
- URL: https://smithery.ai/
- Submission: `smithery mcp publish "https://your-server.com" -n yourorg/your-server` (CLI) or web dashboard
- Effort: 15 min once MCP server is hosted
- Recommendation: ✓ **Apply now.**

#### Findings — Cursor Marketplace
- URL: https://cursor.com/marketplace
- Submission: Marketplace launched May 2026, accepts plugins (skills, subagents, rules, hooks, commands, MCP servers). Specific submission flow not fully documented publicly; check current docs at https://cursor.com/docs/mcp
- Effort: 30–60 min
- Recommendation: ✓ **Apply now.** Cursor users are an excellent ICP overlap for Hatchik.

#### Findings — Official MCP Registry & mcp.so
- URLs: https://registry.modelcontextprotocol.io/ , https://mcp.so/
- Submission to mcp.so: GitHub issue or Submit button in nav
- Effort: 15 min each
- Recommendation: ✓ **Apply now to both.** Trivial effort, broad reach.

---

## Application tracker

| Programme | URL | Status | Applied | Expected response | Outcome |
|---|---|---|---|---|---|
| Cloudflare for Startups (Bootstrapped tier) | https://www.cloudflare.com/forstartups/ | Not applied | – | 1–2 weeks | – |
| Sentry for Startups | https://sentry.io/for/startups/apply/ | Not applied | – | 2–3 business days | – |
| AWS Activate Founders | https://aws.amazon.com/startups/credits/ | Not applied | – | 2–7 business days | – |
| Google for Startups Cloud (Start) | https://cloud.google.com/startup/apply | Not applied | – | 1–2 weeks | – |
| Anthropic Connectors Directory | https://claude.com/docs/connectors/building/submission | Not applied | – | Variable | – |
| Smithery.ai (MCP) | https://smithery.ai/ | Not applied | – | Immediate (auto) | – |
| Cursor Marketplace (MCP) | https://cursor.com/marketplace | Not applied | – | Variable | – |
| mcp.so | https://mcp.so/ | Not applied | – | Days | – |
| Official MCP Registry | https://registry.modelcontextprotocol.io/ | Not applied | – | Immediate (auto) | – |
| Infomaniak Partner Network | https://www.infomaniak.com/en/reseller-program/registration | Deferred — at €1.8k/yr volume | – | – | – |
| OpenSRS reseller | https://opensrs.com/become-a-domain-reseller/ | Deferred — at 100 domains | – | – | – |
| OpenRouter volume | (direct sales) | Deferred — at ~$2M/yr | – | – | – |
| Stripe Atlas perks partner | https://support.stripe.com/questions/suggest-new-perks-or-become-an-atlas-partner | Deferred — post-launch | – | – | – |
| GitHub for Startups | https://github.com/enterprise/startups/partner-application | Not viable (funding gate) | – | – | – |
| DigitalOcean Hatch | https://www.digitalocean.com/startups | Deferred — AI-native gate | – | – | – |
| Hetzner Partner | — | Not viable (no formal programme) | – | – | – |
| Cloudflare Registrar resale | — | Not viable (terms prohibit) | – | – | – |
| Paddle | — | Not viable (model mismatch) | – | – | – |

---

## Prioritisation matrix

**Apply now (low effort, viable today):**
1. Cloudflare for Startups Bootstrapped tier — $5K credits — 20 min
2. Anthropic Connectors / MCP Directory — free distribution — 2–4 hr
3. Smithery + Cursor + mcp.so + official MCP registry — free distribution — 1–2 hr
4. Sentry for Startups — $5K credits + 6mo free — 15 min
5. AWS Activate Founders — $1K + $350 — 15 min
6. Google for Startups Cloud (Start) — $2K — 15 min (low priority — only if we'll use GCP)

**Pursue at customer-count milestones:**
- Infomaniak Partner Network — at €1.8k/yr volume (~20 customers)
- OpenSRS wholesale registrar — at 100 customer domains
- Stripe Atlas perks partner — post-launch when we can offer founder discount
- OpenRouter negotiated volume — at $2M/yr AI spend
- Anthropic / OpenAI direct API — at £20k+/mo consumption
- Backblaze B2 Reserve — at multi-PB scale (likely never relevant)

**Watchlist (defer indefinitely):**
- GitHub for Startups (need outside funding + partner)
- Hetzner formal partner (doesn't exist)
- DigitalOcean Hatch (AI-native gate; also we use Hetzner)

**Not viable:**
- Cloudflare Registrar resale (terms prohibit)
- Cloudflare Partner Network for resale (terms prohibit)
- Paddle (merchant-of-record mismatch)

---

## Application templates

For each programme, capture:
- Application URL
- Required documentation (incorporation, revenue, customer count)
- Application date
- Status
- Outcome
- Renewal / expiry date

See Application Tracker table above for live state.

---

## Cost impact modelling

For each active programme, model:
- Per-customer cost reduction (£/customer/month or one-time)
- Total reduction at 100, 1,000, 10,000 customer scale
- Effort to activate / maintain (hours)

A 5% cost reduction per customer at 1,000 customers = £400/month
saving. A 5% reduction at 10,000 customers = £4k/month. The point of
this doc is to compound those small percentages.

**One-time credit value (apply-now set):**
- Cloudflare $5K + Sentry $5K + AWS $1.35K + GCP $2K = **~$13.35K / ~£10.5K**
  of one-time credit available without funding or partner gate.
  Effort to capture: ~1 hour total.

**Recurring savings unlock at:**
- ~20 customers → Infomaniak 35% off hosting + mail (~£0.50–1.00/customer/mo)
- 100 customer domains → OpenSRS wholesale (~£1–2/domain/year)
- $2M/yr AI spend → OpenRouter fee cut from 5.5% to ~4.5% (~£20k+/yr)

---

## Strategic note

Our customer-facing pricing (£89 / £14 / £39) does NOT change with these
savings. The margin gain is ours to invest — into product, into CAC
reduction (we can be more generous with referrals, free trials, ambassador
discounts), or to extend runway.

Resellers are a back-office lever for an honest front-office offering.
The customer never sees these arrangements.

---

## Notes for follow-up (direct outreach required)

A handful of programmes have details that can't be confirmed from
public web research. Resolve these by direct sales contact:

- **OpenSRS activation fee** — one-time non-refundable, amount not disclosed; call before integrating
- **Vultr Partner Program margins** — "industry best" claimed but no public %
- **OpenRouter exact volume tier bands** — only the $2M+ enterprise rate is known publicly
- **Stripe Atlas perks partner application process** — unclear whether self-serve or curated; needs email to atlas@stripe.com once we have a launched product
- **Cloudflare Partner Network revenue share** — terms don't mention reseller resale at all (which suggests it doesn't exist for self-serve customers); confirm at https://www.cloudflare.com/partners/
