# Hatchik — Technical Stack

What we use, why, what it costs us per customer, and what we'd switch to
if things change. Source of truth for the provisioning worker and for any
internal "which provider for X" conversation.

---

## Per-customer monthly cost target

| Layer | Provider | Cost/customer/month | Notes |
|---|---|---|---|
| Server | Hetzner CPX11 / Infomaniak Public Cloud A2 | £3.85 | Dedicated, smallest viable tier |
| Database, Auth, Storage, Realtime | Supabase (self-hosted on customer VPS) | £0 | Runs in the same docker-compose as the app |
| Mail (5 inboxes) | Infomaniak Mail | £0 | Free with domain registration |
| Transactional email | Resend free tier → Amazon SES at scale | £0 (Resend free covers ≤3k/mo) | Switch to SES at ~£0.10/1k beyond |
| Backups | Backblaze B2 | £0.50 | Free 10GB tier covers most apps |
| Edge / CDN / DNS / WAF | Cloudflare Free | £0 | Free tier comprehensive |
| Uptime monitoring | Uptime Kuma (self-hosted, shared) | ~£0.10 | One small server monitors all customers |
| Error tracking | Sentry self-hosted (shared) | ~£0.20 | One small server, shared |
| Domain renewal (amortized) | Infomaniak / Namecheap | £1.00 | ~£12/yr ÷ 12 |
| Support amortization | Founder/team time | £1.00 | Assumes ~30 min lifetime per customer |
| **Total per customer** | | **~£6.65/month** | |

For Sandbox (free tier): co-tenanted on shared infra, marginal cost
~£0.30/customer/month.

## Infrastructure topology — three distinct workload classes

Hatchik runs three categorically different workloads. Each gets its own
infrastructure boundary; nothing crosses tiers.

### 1. Hatchik's own infrastructure (single VPS, founder-owned)

The marketing site, the customer dashboard, the wizard, the MCP server,
the provisioning worker, and Hatchik's internal Postgres all run on a
single VPS the Hatchik team controls. This is the existing Infomaniak
VPS at the founder's account (currently shared with slideAtelier,
Stackr, ThreadLine, Nextcloud — Hatchik joins as another tenant).

- **Provider:** Infomaniak (existing account)
- **Why this one:** zero incremental cost, EU-resident, founder already has
  SSH access and operational familiarity
- **What's on it:** hatchik.com (marketing site, waitlist form), app.hatchik.com
  (customer dashboard, when built), proxy.hatchik.com (AI passthrough, when
  built), the provisioning worker + Postgres for Hatchik's customer
  records, the shared Sentry/Uptime Kuma instances
- **Failure mode:** if this VPS goes down, Hatchik's marketing, wizard, and
  dashboard go down — but customer apps stay up (they're on their own
  VPSs). Acceptable for v1.
- **Growth trigger to migrate off:** when Hatchik's own load exceeds ~50%
  of VPS capacity, or when slideAtelier/other tenants need the space,
  Hatchik gets its own dedicated VPS.

### 2. Sandbox tier (single shared VPS, multi-tenant)

All free Sandbox customers share one VPS. Each customer's "app" is an
isolated docker-compose deployment routed by Caddy subdomain mapping
(`<customer>.hatchik.com`). Resource caps via cgroups + container limits
keep noisy neighbours under control.

- **Provider:** Hetzner CCX23 or similar (~£20/mo total, hosts ~50–100
  sandboxes)
- **Cost per Sandbox customer:** ~£0.30/month
- **Isolation:** containers, no shared Postgres (each sandbox has its
  own small Postgres container), 3-user / 100MB cap enforced at app
  layer
- **Trade-off:** lower cost, weaker isolation. Acceptable for free tier;
  customers who want stronger isolation upgrade to Launch.
- **Scaling:** add another shared VPS when first one hits ~80 sandboxes.

### 3. Launch + Growth tiers (dedicated VPS per customer)

Every paid customer gets a fresh, dedicated VPS provisioned at signup.
Their full stack (Caddy + Supabase + their app) runs in isolation. The
provisioning worker bootstraps each new VPS via the provider's cloud
API.

- **Provider per region:**
  - UK / Germany / Finland → Hetzner CPX11 (£3.85/mo)
  - Switzerland → Infomaniak Public Cloud A2 (£4.30/mo)
  - US East / US West → DigitalOcean Basic 2GB (£8.00/mo)
  - Singapore / Tokyo / Sydney → Vultr Cloud Compute 2GB (£6.50/mo)
- **Customer keeps the VPS** on exit — root credentials transferred,
  customer continues billing the provider directly. No data migration
  needed.
- **Why dedicated:** matches the "you own your infrastructure" brand
  promise, prevents noisy-neighbour problems for paying customers, lets
  each customer install whatever they want.

### Summary diagram

```
        ┌────────────────────────────────────────────────────┐
        │  Founder's Infomaniak VPS (existing, shared with   │
        │  slideAtelier, Stackr, ThreadLine, Nextcloud)      │
        │                                                    │
        │  ─ hatchik.com (marketing)                           │
        │  ─ app.hatchik.com (customer dashboard, when built)  │
        │  ─ proxy.hatchik.com (AI passthrough, when built)    │
        │  ─ Hatchik's provisioning worker + Postgres          │
        │  ─ Shared Sentry / Uptime Kuma                     │
        └────────────────────────────────────────────────────┘
                              │
                              │ provisions
                              ▼
        ┌────────────────────────────────────────────────────┐
        │  Shared Sandbox VPS (Hetzner CCX23, ~£20/mo)       │
        │                                                    │
        │  ─ sandbox-1.hatchik.com  ─ sandbox-2.hatchik.com  …   │
        │  (~50–100 Sandbox customers, container-isolated)   │
        └────────────────────────────────────────────────────┘

                              + (when customer upgrades)

        ┌─── Launch / Growth customer ────────────────────────┐
        │                                                    │
        │  Dedicated VPS in customer's region                │
        │  Their domain → their Caddy → their Supabase stack │
        │  Provisioned at signup, owned by customer          │
        └────────────────────────────────────────────────────┘
```

This three-tier topology is the source of truth. The provisioning
worker reads it to decide where to deploy each new signup. The
marketing page reflects it (Sandbox is shared, paid is dedicated, Hatchik
runs on Hatchik's own infra).

## Architecture (per customer)

```
                customer's-domain.com
                       │
                  Cloudflare (DNS + edge)
                       │
                Hetzner / Infomaniak VPS
                       │
            ┌──────────┴──────────┐
            │  Caddy (TLS + proxy)│
            └────┬────────┬───────┘
                 │        │
       ┌─────────▼──┐  ┌──▼──────────────┐
       │ apps/web   │  │ apps/api        │
       │ (static)   │  │ (FastAPI)       │
       └────────────┘  └────┬────────────┘
                            │
                      ┌─────▼──────────────────┐
                      │ Supabase stack         │
                      │ ┌────────────────────┐ │
                      │ │ Postgres           │ │
                      │ │ GoTrue (auth)      │ │
                      │ │ Storage            │ │
                      │ │ Realtime           │ │
                      │ └────────────────────┘ │
                      └────────────────────────┘

                  +─── Infomaniak Mail (off-VPS, customer's domain)
                  +─── Stripe (off-VPS, customer's account)
                  +─── Backblaze B2 (off-VPS, nightly snapshots)
                  +─── Sentry self-hosted (off-VPS, shared)
                  +─── Uptime Kuma self-hosted (off-VPS, shared)
```

Single VPS per customer keeps the architecture comprehensible. Customer
can SSH in, see all services running, take it elsewhere.

## Layer-by-layer

### Hosting / Server

| Region | Provider | Tier | Cost |
|---|---|---|---|
| UK | Hetzner | CPX11 (Falkenstein/Helsinki, closest to UK) | £3.85/mo |
| Germany | Hetzner | CPX11 (Falkenstein/Nuremberg) | £3.85/mo |
| Switzerland | Infomaniak Public Cloud | A2 (Geneva) | £4.30/mo |
| US East | DigitalOcean | Basic 2GB (NYC) | £8.00/mo |
| US West | DigitalOcean | Basic 2GB (SFO) | £8.00/mo |
| Singapore | Vultr | Cloud Compute 2GB (Singapore) | £6.50/mo |
| Other (Sydney, Tokyo, São Paulo, Mumbai, Dubai) | Vultr | Cloud Compute 2GB | £6.50-8/mo |

Default region selection logic (in wizard / MCP):
- Customer's billing country → nearest region with same data-residency laws
- UK customer → UK or Germany (GDPR)
- EU customer → Germany
- US customer → US East
- Asia customer → Singapore

Provider rotation strategy: monitor pricing and availability quarterly.
Hetzner is the cost leader and our default. Infomaniak premium positions
Switzerland as a privacy-premium option (small premium charge in v2).

### Application stack

**Frontend (`apps/web`)**
- React 18 + Vite
- TypeScript (strict mode)
- Tailwind CSS + shadcn/ui components
- TanStack Router (file-based routing)
- TanStack Query (server state)
- Supabase JS client
- Stripe.js for checkout

**Backend (`apps/api`)**
- Python 3.12
- FastAPI
- SQLAlchemy 2.x (async)
- Pydantic v2
- Supabase Python client
- Stripe Python SDK
- Celery + Redis for background jobs (when needed)

**Mobile (`apps/mobile`)**
- Capacitor 6
- Same React codebase as `apps/web`, wrapped natively
- iOS and Android targets pre-configured

**Database, Auth, Storage**
- Supabase self-hosted (Postgres 15 + GoTrue + Storage + Realtime)
- Runs in customer's VPS docker-compose
- Migrations via the `packages/db` shared workspace

**Cross-package**
- `packages/db` — migrations + generated TypeScript types
- `packages/ui` — shared React components beyond shadcn primitives
- `packages/config` — shared TS / Python config helpers

Why these choices:
- **React** — largest pool of AI-coder familiarity, every AI tool grasps it
- **FastAPI** — readable Python, excellent OpenAPI generation, async-native
- **Supabase self-hosted** — replaces 5 SaaS services with one bundle the
  customer owns and pays no per-row fees on
- **Tailwind + shadcn** — best-in-class for AI tools generating UI
- **TypeScript strict** — fewer bugs from AI-generated code
- **Capacitor over React Native** — single codebase, simpler build chain

### Payments

- **Stripe** for all paid customer flows
  - Stripe Checkout (Hatchik bills the customer)
  - Stripe Connect (customer bills their end users) — Standard model in
    v1, Express considered for v2
  - Stripe Customer Portal exposed to end customers for billing
    self-service
  - Webhooks via the customer's `apps/api` for subscription state sync

### Mail

**Inbound (customer's mailboxes)**
- Infomaniak Mail, 5 inboxes per registered domain, free
- IMAP / SMTP / webmail
- Anti-spam, anti-virus included

**Outbound (transactional)**
- Resend free tier (3,000 emails/month, 100/day max)
- Auto-upgrade to Amazon SES at threshold (much cheaper at scale)
- SPF, DKIM, DMARC configured automatically by provisioning worker

### Code repository

- **GitHub** — Private repo created under customer's account at
  provisioning time
- **Branch model** — `main` deploys to prod; any other branch deploys to
  preview environment automatically
- **CI/CD** — GitHub Actions, push-to-deploy via SSH to customer VPS

### Domain & DNS

- **Registration** — Infomaniak (preferred) or Namecheap (fallback). 
  Customer is the legal registrant.
- **DNS** — Cloudflare (free tier). Transferred to customer's Cloudflare
  account on offboarding.
- **TLS** — Caddy + Let's Encrypt, automatic renewal.

### Monitoring & observability

- **Uptime** — Uptime Kuma self-hosted, one instance shared across all
  customers, monitors HTTP/TCP/ping
- **Errors** — Sentry self-hosted (open-source edition), one instance
  shared, customers' apps report to per-customer Sentry projects
- **Logs** — Application logs to local file + structured JSON to a
  central log aggregator (Grafana Loki, free tier)
- **Metrics** — Prometheus + Grafana, optional per-customer dashboard
  panel
- **Alerts** — Email to customer + ops on downtime / error rate spikes

### Backups

- **Database** — Nightly `pg_dump`, encrypted with customer-specific key,
  uploaded to Backblaze B2
- **Storage (Supabase bucket)** — Daily snapshot to B2
- **Retention** — Sandbox: 24h; Launch: 7 days; Growth: 30 days
- **Restoration** — One-click from customer dashboard, takes ~30 seconds

### AI integration

- **For development (the customer's AI coder)** — MCP server hosted at
  `npm:@hatchik/mcp` for Cursor / Claude Code / Windsurf / Cline. Lists
  the customer's project ID + provides tools for ops.
- **For runtime (AI inside the customer's app)** — Optional AI credits
  passthrough via OpenRouter. Customer flips a switch in dashboard, their
  app's AI calls route through `proxy.hatchik.com` with metered billing.
- **For BACKLOG.md generation** — Claude Sonnet 4.6 generates the
  starter backlog (~20 tasks) from customer's product description at
  provisioning time and commits it as `BACKLOG.md` at the repo root.

### Customer dashboard

Hosted at `app.hatchik.com`, single dashboard for all paying customers:

- Billing (Stripe Customer Portal embed)
- Deploy status (recent deploys, prod health, preview URLs)
- Backups (list snapshots, one-click restore)
- Migrations (review pending DB changes from their AI, approve/reject)
- Logs (recent errors, app logs)
- Team (invite collaborators, manage seats)
- AI usage (if passthrough enabled)
- Settings (region, mail config, danger zone)

Built with Next.js (Hatchik's own dashboard ≠ customer's app substrate).
Different stack from substrate because dashboard is single-codebase
multi-tenant, where substrate is single-tenant per customer.

## Hatchik's own internal stack

Not delivered to customers. What we use to run Hatchik itself.

| Component | Stack |
|---|---|
| Marketing site | Next.js + Tailwind (the current proposals/launchkit/index.html, eventually moved to hatchik.com) |
| Customer dashboard | Next.js + Tailwind + tRPC |
| Wizard | Embedded in marketing site (Next.js) |
| Provisioning worker | Python + Celery + Postgres |
| MCP server | Node + @modelcontextprotocol/sdk |
| AI proxy (for passthrough) | Python + LiteLLM or direct OpenRouter SDK |
| Marketing analytics | Plausible (self-hosted) |
| Product analytics | PostHog (self-hosted), free tier |
| CRM | Linear (we drink our own champagne) for customer issues |
| Internal docs | Notion |
| Status page | Statping (self-hosted) at `status.hatchik.com` |
| Internal hosting | One Hetzner CCX23 (£20/mo) for all of the above |

Total Hatchik internal infrastructure: ~£60/month at v1. Scales horizontally
as needed.

## Provider rotation policy

Reviewed quarterly:

- **VPS provider per region** — keep watching pricing; switch if cheaper
  with >20% saving and similar reliability
- **Mail provider** — Infomaniak is sticky because of the free 5
  mailboxes / domain. Hard to beat.
- **CDN** — Cloudflare Free is unmatched. No realistic alternative.
- **Object storage** — Backblaze B2 vs Cloudflare R2 (R2 has no egress
  fees, B2 is cheapest storage). Use R2 for high-egress customers, B2
  default.
- **Transactional email** — Resend → SES at scale. Postmark / Sendgrid
  as fallbacks for deliverability problems.
- **AI passthrough wholesale** — OpenRouter is the v1 layer. Direct
  Anthropic/OpenAI reseller agreements become viable at ~£10k+/month
  consumption.

## Security posture

- **Secrets** — Stored in customer's VPS `.env`, never committed to repo.
  Hatchik's central secrets in Infisical (self-hosted).
- **HTTPS everywhere** — Caddy auto-TLS, HSTS enabled, modern cipher
  suites only.
- **Database** — Local Postgres in customer's VPS, port 5432 firewalled
  to localhost. No external DB exposure.
- **OAuth tokens** (GitHub, Stripe, Paddle) — KMS-encrypted in Hatchik's
  central DB, decrypted just-in-time for API calls.
- **AI proxy** — Per-customer API key, rate-limited, cost-capped.
- **Backups** — Encrypted at rest with per-customer key.
- **Compliance** — GDPR by default. HIPAA region available on request
  (US BAA territory). PCI scope minimized via Stripe-hosted Checkout.

## What we explicitly don't use (and why)

- **Vercel / Netlify** — Their pricing eats our margin and the customer
  doesn't own infra.
- **AWS / GCP / Azure** — Overkill complexity and cost for our customers'
  scale. Re-evaluate at >5k MAU per customer.
- **Heroku / Render** — Similar reasons; pricing doesn't fit our economics.
- **MongoDB / DynamoDB** — Postgres is enough and the AI tools handle
  SQL better than NoSQL APIs.
- **Auth0 / Clerk** — Supabase Auth is good enough and free.
- **Firebase** — Vendor lock-in we don't want for customers.
- **PlanetScale / Neon / Crunchbridge** — All make sense as managed
  Postgres alternatives, but we want Postgres co-located with the app
  on the customer's VPS for cost and ownership reasons. Re-evaluate when
  customers outgrow a single VPS.
- **Next.js for customer apps** — React + Vite gives us a SPA that runs
  identically on web and Capacitor mobile. Next.js SSR doesn't translate
  to Capacitor. We use Next.js for Hatchik's own marketing site / dashboard,
  but not for what we hand to customers.
