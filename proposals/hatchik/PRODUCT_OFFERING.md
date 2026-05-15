# Hatchik — Product Offering

The authoritative spec of what Hatchik sells, in what tier, with what included.
Single source of truth for the marketing page, the wizard, the dashboard,
support documentation, and customer contracts.

---

## 1. The product, in one sentence

Hatchik is a wired-up SaaS substrate — hosting, payments, mail, mobile, auth,
backups — delivered to the customer's own infrastructure with their AI coding
tool already configured. Customer brings the product idea. Hatchik handles
everything else.

## 2. Tiers

Three tiers. One product. The price ladder is tied to customer success, not
gated features. **Every tier ships the same substrate.**

### 2.1 Sandbox (Free, forever)

| | |
|---|---|
| **Price** | £0 |
| **URL** | `<customer-subdomain>.hatchik.com` |
| **Server** | Co-tenanted shared infrastructure |
| **Database** | **3 users, 100 MB database storage** |
| **Payments** | Stripe **or** Paddle, test mode only (customer picks at signup) |
| **Mailboxes** | Single shared sender (`noreply@hatchik.com`) |
| **Repo** | Private repo in **hatchik-sandboxes** org, customer added as collaborator with write access |
| **Backups** | 24h retention |
| **Mobile shells** | iOS + Android, store-submittable (customer handles submission — see §5.3). Cloud builds via GitHub Actions, **up to 3 builds per hour per tenant** |
| **AI tool integration** | Full MCP setup |
| **AI token allowance** | **One-off £0.50** of Claude Haiku (~50k tokens) — a taster, deliberately enough to wire up one AI-powered feature and see it run. Not a monthly subscription; spent once, gone once. Customer can BYO key any time or upgrade to a tier with a monthly allowance. |
| **Branding** | "Built with Hatchik" footer (non-removable) |
| **Idle policy** | Archived after 30 consecutive days idle; restorable on request |
| **Support** | Community forum |

Purpose: derisk the purchase decision. Sandbox is the "try it" path that
costs nothing and never expires while the customer is active. The 3-user
cap prevents serious commercial use while letting the customer (plus a
co-founder and a friend) properly test the experience.

### 2.2 Launch — £89 one-time + £14/month (starting month 2)

| | |
|---|---|
| **Setup fee** | £89 once (covers everything for the first month) |
| **Monthly** | £14/month **from month 2 onwards** |
| **Domain** | **Customer's choice**: bring your own existing domain (we configure it), OR get a new domain registered (first year included in the £89) |
| **Server** | Dedicated VPS in customer's chosen region |
| **Database** | Unlimited size, unlimited users |
| **Payments** | Stripe **or** Paddle, live mode (customer's choice — see §4.3) |
| **Mailboxes** | 3 inboxes on customer's domain |
| **Repo** | Same private repo from Sandbox carries over (in **hatchik-sandboxes** org, customer keeps collaborator write access) |
| **Backups** | Tiered by environment — see §6 |
| **Mobile shells** | Same scaffold as Sandbox; same up to 3 cloud builds per hour per tenant — Launch differs in support/SLA, not mobile capability |
| **AI tool integration** | Full MCP setup, both signup and ongoing ops |
| **AI token allowance** | **~£3/month included** (~150k tokens at avg model rates) for runtime AI in customer's app; passthrough for overage at provider cost (no Hatchik margin shown on invoice); or bring your own key for direct provider billing |
| **Branding** | Customer's own |
| **Substrate updates** | Delivered as opt-in PRs to customer's repo |
| **Support** | Email, 1 business day response |

This is where most customers start.

**The £89 covers:**
- Domain registration (year 1) OR existing-domain configuration
- VPS provisioning in customer's chosen region
- Caddy + TLS + Cloudflare wiring
- Supabase stack deployment
- 3 mailboxes + SPF/DKIM/DMARC
- Stripe Connect handshake + product creation
- GitHub repo + CI/CD + MCP config
- Linear board with 20 seed tasks
- First month of hosting + backups + monitoring (included)

**The £14/month (from month 2) covers:** ongoing VPS, backups, monitoring,
mail, edge network, included AI token allowance, substrate update PRs.

**Your sandbox stays alive as a dev environment.** When you upgrade
from Sandbox to Launch, we don't tear down your original sandbox —
we keep it running alongside your new production stack so you have
a real dev/prod split out of the box. The substrate code, repo and
data on the sandbox side stay yours to experiment with. We cover the
infrastructure cost as part of the monthly fee; the idle-archive
policy that applies to free Sandbox tenants is disabled for promoted
sandboxes.

### 2.3 Growth — £39/month

Automatic graduation from Launch after the customer's 15th sign-up to
their app.

| | |
|---|---|
| **Monthly** | £39/month (replaces £14/month from Launch) |
| **Setup fee** | n/a (already paid at Launch) |
| **Everything in Launch** | ✓ |
| **Server** | Same VPS, more headroom included (CPU bursting allowance, +RAM if needed) |
| **Backups** | Extended retention — see §6 |
| **Mobile shells** | Same |
| **AI tool integration** | Same |
| **AI token allowance** | **~£10/month included** (~500k tokens at avg model rates), generous overage threshold before billing kicks in |
| **Domain renewal** | Free annual domain renewal (years 2+) |
| **Substrate updates** | **Early access** — Growth customers see substrate features 2 weeks before they roll out to Launch |
| **Custom email rules** | Forwards, auto-responders, mail filters configurable from dashboard |
| **Support** | Email, same-day response, monthly proactive health check |
| **Priority on new features** | Vote on roadmap; early access to beta features |
| **Community access** | Hatchik founders' Discord — direct line to other customers and team |

Graduation trigger: 15 customer-app sign-ups (whether free or paid users
of the customer's app). Customer gets an email a month before the
change. No surprise charges.

**Your sandbox stays alive as a dev environment.** Same arrangement
as Launch — the original sandbox you started on stays running as your
dev environment alongside the Growth production stack. We cover the
infrastructure as part of the £39/mo fee; the idle-archive policy
doesn't apply once you've upgraded.

## 3. Add-ons

The only add-on that is part of the offering. Everything else is out of
scope (see §5).

### 3.1 AI credits passthrough (optional convenience)

**The model:** customer's app calls AI providers (Claude, GPT, Gemini,
Grok, open-source via OpenRouter) for runtime features. By default the customer
brings their own API key and is billed directly by the provider. Optionally,
they can route AI usage through Hatchik for unified billing.

**Two paths to AI in the customer's app:**

| | **Bring your own key (default)** | **Hatchik passthrough (optional)** |
|---|---|---|
| Setup | Customer adds their Anthropic / OpenAI / OpenRouter key to their `.env` | Customer flips toggle in dashboard; no key needed |
| Billing | Provider bills customer directly | Hatchik bills customer; we pay providers |
| Invoice line | n/a | Single line: "AI usage — £X" (no margin breakdown shown) |
| Margin | None — customer pays provider rate | Hatchik applies a small markup over provider cost on tokens past the included allowance.[^overage-markup] |
| Provider switching | Customer's choice | One config change, switch between Claude/GPT/Gemini/Grok |
| Token allowance included | n/a | £3/mo on Launch, £10/mo on Growth |
| Cost caps | Customer's own setup | Built-in spend caps in dashboard |
| Best for | Customers with existing AI provider accounts; cost-sensitive | Customers wanting unified billing, vendor flexibility, runaway-prompt protection |

[^overage-markup]: The exact markup is a strategic lever, not a published rate. Default modelled at 30% in `proposals/hatchik/AI_COGS_SENSITIVITY.xlsx` (cell `overage_markup` on the Inputs sheet — tunable to model alternative values). The bundled-invoice presentation does not surface it line-by-line; the customer sees only the all-in usage figure.

**No lock-in either way.** Customer can switch from passthrough to BYO key
(or vice versa) at any time. The substrate code reads from environment
variables that work with either path.

**Note on invoice transparency:** Per customer preference, AI passthrough
invoices show a single bundled cost (e.g. "AI usage — £49.00") without
exposing Hatchik's margin breakdown. The customer always sees the total
they pay. The margin is internal accounting only.

## 4. What's included, in plain English

Cross-references the bundle rows on the marketing page. Each appears in
the substrate from day one.

1. **A place to store your app's data** — Supabase Postgres
2. **Sign-up and login** — Email, magic link, Google OAuth
3. **A way to charge customers** — **Stripe** Checkout + Customer Portal +
   subscription gating, **or Paddle** as Merchant of Record (handles global
   tax + supports non-Stripe countries). Customer picks at signup. See
   §4.3 for the trade-offs.
4. **A real website address** — Customer's choice: bring an existing
   domain (we configure DNS + TLS) OR register a new one (year 1 included
   in £89)
5. **Mailboxes on your domain** — 3 inboxes via Infomaniak Mail, plus
   SPF/DKIM/DMARC for transactional email
6. **Your server, in your region** — Dedicated Hetzner Cloud VPS in the
   customer's chosen city. **All hosting is Hetzner** to keep ops simple
   (one provider, one API, one billing relationship). Offered regions:

   | Region | City | Hetzner DC |
   |---|---|---|
   | 🇩🇪 Germany | Falkenstein | FSN1 |
   | 🇩🇪 Germany | Nuremberg | NBG1 |
   | 🇫🇮 Finland | Helsinki | HEL1 |
   | 🇺🇸 US East | Ashburn, VA | ASH |
   | 🇺🇸 US West | Hillsboro, OR | HIL |
   | 🇸🇬 Singapore | Singapore | SIN |

   Marketing copy says "6 cities across 5 regions" (counting
   datacentres). Customer picks one at signup, defaults to
   nearest by IP. Switching region post-launch requires a migration
   ticket (Growth tier: included; Launch tier: £49 one-off).

   **UK customers** default to Falkenstein/Frankfurt — ~20ms RTT to
   London, well within SaaS norms.
   **Swiss / Canadian / Sydney / Indian customers** requesting regional
   hosting receive their nearest Hetzner option (Falkenstein for CH/CA;
   Singapore for IN/AU). On-request bespoke hosting is *out of scope*.
7. **iOS + Android app shells** — Capacitor scaffolds from the same
   codebase, cloud-built and signed (customer handles store accounts
   and submission — see §5.3)
8. **Code you actually own** — Private GitHub repo under customer's
   account
9. **A safety net** — Tiered backups (see §6) + uptime monitoring + error
   tracking (Sentry) + downtime alerts
10. **A clear list of what to build next** — Linear board (free for solo
    founders), pre-populated with ~20 starter tasks generated from
    customer's product description; AI coder reads/writes the board via
    Linear MCP
11. **AI features inside your app (if you want them)** — Pre-wired
    integration with Claude / GPT / Gemini / Grok / open-source models;
    **customer's choice**: bring your own API key OR use Hatchik passthrough
    (with included token allowance — **one-off £0.50 of Claude Haiku** on Sandbox to wire up your first AI feature, then £3/mo on Launch, £10/mo on Growth)

### 4.3 Substrate payment provider — Stripe vs Paddle

Customer picks one at signup. Substrate ships with both wired in; the choice
flips a config flag that drives which SDK + checkout flow is active.

| | **Stripe** | **Paddle** |
|---|---|---|
| **Customer-facing fee** | ~2.9% + 30p | ~5% + £0.40 |
| **Tax handling** | Customer's responsibility (Stripe Tax add-on £$$) | Built-in MoR — Paddle handles VAT / GST / sales tax in 100+ jurisdictions |
| **Customer eligibility** | Must be in one of ~50 supported countries | Any country Paddle accepts (broader, includes Middle East, parts of Africa, etc.) |
| **Brand recognition** | High (Apple / Spotify use it) | High in SaaS, less consumer-facing |
| **Best for** | UK / EU / US founders with simple tax obligations | Global SaaS, non-Stripe-supported countries, founders who don't want to handle tax |

**Rationale for offering both:** Hatchik already uses Paddle for its own
checkout (forced by Oman-residency — see §8.1). Wiring Paddle into the
substrate adds ~1 week of dev (substrate-template work) but unblocks
non-Stripe-country customers from using Hatchik at all. The substrate's
payment-abstraction layer presents a common `Payments` interface to the
customer's app code; switching providers post-launch is a config change,
not a rewrite.

**Customer journey for each:**
- *Stripe path:* customer creates Stripe account, goes through Connect
  onboarding from a link we email, we receive the OAuth handshake, their
  app starts taking cards. Stripe fees deducted at transaction time;
  payouts to customer's bank direct.
- *Paddle path:* customer signs up to Paddle, completes their KYC, gets
  API keys, pastes them into their app's settings (or we paste for them
  during concierge). Paddle takes the cut + remits taxes; payouts to
  customer's bank.

### 4.4 Why Sandbox includes a small AI passthrough allowance

The one-off £0.50 Sandbox allowance is a deliberate acquisition lever, not
a recurring giveaway. If Sandbox users default to BYO key during the free
phase, they build a habit + provider relationship that survives the
Launch upgrade — making Hatchik's passthrough product permanently
optional even at Growth.

By seeding ~50k Haiku tokens on Sandbox, the customer is routing through
Hatchik's passthrough infrastructure when they wire up their first AI
feature — they see it work, they see the unified invoice line. When they
decide whether to graduate, the default path becomes "stay on
passthrough, upgrade to a monthly allowance" rather than "swap to BYO".

**Why one-off, not monthly.** A recurring £0.50/month allowance reads
to the customer as "an ongoing freebie I should ration" — they
under-use it and never build the muscle memory. A one-off taster
reads as "I have £0.50 of free AI; let me actually spend it." Higher
activation, cleaner mental model, removes the ambient cost from the
free tier (we want Sandbox marginal cost to round to zero per
customer-month after the initial taster is used). Customers who run
out either BYO key (most likely outcome) or feel the actual cost of
their app's AI usage and upgrade to a tier with a real monthly
allowance — exactly the conversion event we want.

Estimated marginal cost: a one-time £0.25-£0.40 per Sandbox customer
(some never wire up AI; some use only half the taster), with zero
recurring drag.

## 5. Out of scope (explicit non-offering)

We will not, in this product line, offer:

### 5.1 Custom development

We don't write the customer's app code. The starter substrate is
delivered "Hello, world." The customer builds the product, with their AI
tool, by hand, or by hiring a developer separately.

### 5.2 Marketing copy or branding services

We don't write the customer's marketing site, design their logo, or
build their content strategy.

### 5.3 App store submission service

We **build** the iOS/Android shells from the customer's code, ready to
submit. The customer is responsible for:

- Apple Developer Program account ($99/year)
- Google Play Console account ($25 one-time)
- App Store / Play Store metadata (description, screenshots, keywords,
  category, age rating)
- Compliance with each store's review guidelines (Apple's App Review
  Guidelines, Google Play's Developer Programme Policies)
- App icon design and marketing assets per store specs
- Privacy declarations (App Tracking Transparency, Data Safety form)
- Pricing tier selection and in-app purchase setup if monetising via
  store payments instead of Stripe
- Submission, review responses, and approval timeline (Apple typically
  24-72h, Google typically same-day to 7 days)
- Compliance with App Store rollout phases (staged release, phased
  release on Play Store)
- Ongoing updates / responding to store policy changes

We can advise on common rejection reasons, but we don't submit on behalf
of the customer. This is non-negotiable — the apps live on the
customer's store accounts, billed to their cards, under their legal
identity.

### 5.4 Legal templates / compliance services

We don't draft ToS, Privacy Policies, DPA agreements, or compliance
documentation. We provide pointers to good templates and remind customers
that they're responsible for their own legal compliance.

### 5.5 Concierge launch / agency services

No "let us handle everything" premium tier. The £89 + £14/mo and
graduation to £39/mo are the only paid options.

### 5.6 White-label / agency reseller program

v1 scope is end-customer direct. May add in future based on demand
signal — see §12.

### 5.7 Multi-environment beyond what's bundled

Substrate ships with prod + preview branches. Customers can optionally
spin up a "test" environment (see §6); we don't sell separate "staging"
environments as a standalone product.

### 5.8 24/7 phone support / SLAs beyond email

Email-first support, with same-day response on Growth tier and
1-business-day response on Launch. No on-call.

The clarity of these "no"s is part of the product. We're not a managed
services company.

## 6. Backups model

Differentiated by environment to balance protection against cost. (Storage
cost dominates the backup line; less retention = lower cost. The
differentiated model is ~30% cheaper than nightly-everything-14-days.)

| Environment | Frequency | Retention | Restoration time |
|---|---|---|---|
| **Sandbox** | None / on-demand | n/a | n/a |
| **Launch — Preview/Dev branches** | Nightly | 7 days | 4h target |
| **Launch — Test environment** (when customer opts to add it) | Weekly | 2 weeks | 4h target |
| **Launch — Prod** | Nightly | 14 days | 1h target |
| **Growth — Prod** | Nightly | 30 days | 1h target |

Customer can trigger an on-demand backup from the dashboard at any time
(stays for retention period of their tier). One-click restore from any
snapshot.

Backups encrypted at rest with customer-specific key, stored at
Backblaze B2 (free tier covers most apps; we pass storage cost through
on overage at provider rate, capped at ~£0.50/month for typical app size).

## 7. Service-level commitments

| | Sandbox | Launch | Growth |
|---|---|---|---|
| Uptime target | best-effort | 99.5% | 99.9% |
| Support response | community | 1 business day | same day |
| Substrate security patches | when severe | within 14 days | within 7 days |
| Migration off (handover) | self-serve | 1 business day | priority |

No financial credits for missed SLAs in v1 — we're a small operation
and won't pretend otherwise. Persistent issues = full refund of the
most recent month, no questions.

## 8. Pricing details

- **Setup fee (£89)** — non-refundable after 14 days. Within 14 days,
  full refund if customer cancels before substrate is fully provisioned;
  prorated refund minus domain registration cost (£12-15, if a new domain
  was registered) if provisioned. The £89 covers month 1 ops entirely.
- **Monthly fees** — billed in advance starting month 2 for Launch tier.
  Cancellable any time. Pro-rata refund for unused portion on cancellation.
- **Currency** — GBP primary list price. Customer sees their local currency
  at checkout (auto-detected by IP). USD / EUR / OMR / AED / INR / BRL / etc.
  all supported via the MoR (see §8.1). Regional purchasing-power adjustments
  (PPP) applied automatically for LATAM, SEA, Africa, South Asia — handled
  by the MoR layer, no per-region pricing maintenance.
- **Tax** — VAT (UK / EU), GST (AU / IN / SG / etc.), state sales tax (US),
  and all other jurisdictional taxes handled by the MoR. Hatchik does not
  register for tax in customer jurisdictions; the MoR is the legal seller
  and remits taxes on Hatchik's behalf.

### 8.1 Merchant of Record (MoR) architecture

Hatchik's legal selling entity is registered in **Oman**, which is outside
Stripe's supported countries. To operate globally, Hatchik uses a Merchant
of Record service:

| | Decision |
|---|---|
| **Primary MoR** | Paddle (apply in parallel to DodoPayments as backup) |
| **Fee** | ~5% + £0.40 / transaction (Paddle) |
| **Settles to** | Hatchik's Omani company bank account (SWIFT, GBP→OMR FX at Paddle's rate) |
| **Tax handled** | 100+ jurisdictions, automatic by Paddle |
| **Localized pricing** | PPP-aware, 30+ currencies, regional payment methods (SEPA / iDEAL / Klarna / UPI / Pix / Konbini / etc.) |
| **Customer-facing entity** | Paddle.com Market Ltd is the seller of record. Hatchik appears as "Paddle.com Market Ltd · Hatchik" on customer's bank statement |
| **Refunds / chargebacks** | Paddle's responsibility (not Hatchik's) |

**Rationale:** lets Hatchik be a global product sold from Oman without
maintaining tax registration in 50+ countries, handling FX, or processing
chargebacks. Trade-off is ~2% higher fee vs Stripe direct, accepted as the
cost of global reach + compliance abstraction.

**Eliminated alternatives:**
- *Stripe direct* — does not support Omani business entities
- *Lemon Squeezy / Polar.sh* — payout via Stripe Connect, same Oman restriction
- *Regional gateways (Tap, PayTabs, Telr)* — no global localized-pricing /
  multi-jurisdiction tax handling
- *FastSpring* — viable but higher fees and slower onboarding

**Approval timeline:** 1–2 weeks Paddle, 3–5 days Dodo. Until approved,
concierge MVP customers pay by **bank transfer (Wise)** against an invoice
issued from the Omani entity.

**Customer-facing implications on the marketing page:**
- Footer must show "Hatchik is a service of [Omani entity name], registered in Muscat"
- Checkout messaging should say "secure checkout in your local currency" rather than naming a specific processor
- Prices shown on the page are GBP reference; actual checkout displays local currency + tax

## 9. Reseller-sourced services (in progress)

Hatchik's per-customer cost is sensitive to the providers we use. Where
reseller / partner / referral programs exist that reduce our cost (or
increase customer value at the same price), we should use them. Active
investigation — see [RESELLER_RESEARCH.md](./RESELLER_RESEARCH.md):

Candidate reseller / partner programs:
- **Infomaniak** — domain registrar reseller program, mail reseller (we
  bundle 5 free per domain anyway, but mass-account pricing matters at
  scale)
- **Hetzner** — partner program for high-volume customers
- **Cloudflare** — Cloudflare for Startups / Cloudflare Partner Network
- **Stripe** — already partner via Stripe Connect; explore Stripe Atlas
  referral
- **Sentry** — startup discount / partner pricing
- **Linear** — solopreneur / startup pricing; complementary-tools listing
- **Backblaze B2** — partner / volume discount
- **Resend / SES** — volume tier discounts (SES is already lowest-cost
  per email at scale)
- **OpenRouter** — wholesale / volume tier for AI passthrough
- **Anthropic / OpenAI direct** — reseller agreements become viable at
  £20k+/mo consumption (deferred to v2)

Each reduces our per-customer cost or improves the offering without
increasing customer price. Compounds over the customer base.

## 10. Migration & exit

Customer can leave at any time with everything. The exit journey is
agent-driven — see [EXIT_JOURNEY.md](./EXIT_JOURNEY.md).

Summary: customer signals exit intent (dashboard or chat), AI confirms
intent and explains the handover, AI generates the handover package
(credentials, repo unchanged, server keys handed over, domain transfer
initiated, data exported), AI walks customer through self-hosting if
desired, 7-day grace period before deprovisioning.

What customer keeps:
- **GitHub repo** — already under their account
- **Domain** — already in their name, transferable
- **Server** — root credentials handed over, can keep running on same VPS
  (rough self-hosted cost ~£10-15/mo)
- **Database** — exported as standard SQL dump
- **Customer data** — JSON / CSV export from dashboard
- **Mailboxes** — Infomaniak account credentials transferred

No exit fee, no data extraction fee. Walk away anytime.

## 11. Support journey

Support is also agent-driven by default — see
[SUPPORT_JOURNEY.md](./SUPPORT_JOURNEY.md).

Summary: customer asks a question (email, dashboard, MCP), AI triages
(simple → answers directly using product knowledge + customer's
deployment context; complex → escalates to human with full context
attached). AI updates the support knowledge base from each interaction.

## 12. v2 watchlist (institutional memory)

Items NOT in v1 but tracked for v2 evaluation based on signal. These
are also persisted in long-term memory and not lost between sessions:

- White-label / agency tier (for consultants running multiple client apps)
- Builder tier (lower-priced for hobbyists / internal tools)
- Self-hosted alternative (Hatchik Server, OSS edition)
- Additional backlog tools (Notion, GitHub Projects, Plane)
- Voice signup via MCP
- Multi-tenant team support (currently single-developer per project)
- Concierge launch service as priced add-on (£500-1500 range)
- App store submission service (paid add-on, £290 per platform)
- Direct Anthropic / OpenAI reseller relationships (volume-gated)
- Marketplace of "ready-to-launch" templates (Restaurant POS, Booking,
  Marketplace, Course platform — £49 each)
- Compliance attestations (SOC 2, HIPAA, ISO)
- Annual prepay discount (~10% off for 12 months upfront)
- Region pricing premiums (Switzerland +10%, premium markets up)
- Multi-region failover for Growth tier (UK + EU + US as primary/secondary)
- Free annual security review (Growth tier)
- Customer success milestones / growth playbook content
- Affiliate / referral program with lifetime commission
- Newsletter sponsorships and indie-hacker community partnerships

These are watchlist items, not promises.

## 13. Brand alignment

Across all materials (marketing site, wizard copy, dashboard, support
emails, MCP responses), Hatchik speaks in these voice attributes:

- **Plain English first, technical names as small chips alongside.** The
  busy professional reads the left side, the developer reads the right.
- **Honest about what we are.** No "transform your business" language.
  No hype. Specifics over abstractions.
- **Confident without being smug.** We make hard things easy; we don't
  make easy things sound hard.
- **British rather than Silicon Valley.** "Get it launched" over "ship
  your dreams". "Boring bit" over "infrastructure complexity".
- **AI-first without being AI-obsessed.** Customers are vibe-coding;
  we enable that. But we don't pretend to be an AI builder, and we don't
  hide the rails that keep AI from breaking things.

The marketing page is the canonical voice reference.
