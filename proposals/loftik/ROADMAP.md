# Hatchik — Build Roadmap

Phased plan from "marketing page locked" to "first paying customer."
Estimated 10-12 weeks total to v1 launch, single-founder-led with
contractor support as needed.

---

## Phase 0 — Done ✓

- Marketing page (`proposals/launchkit/index.html`)
- Product offering spec (`PRODUCT_OFFERING.md`)
- Technical stack definition (`STACK.md`)
- Brand name (Hatchik) + positioning
- Pricing model (£0 / £79+£7 / £19, 15-sign-up graduation)
- Architecture specs:
  - `linear-provisioning.md` (Linear integration)
  - `backlog-prompt.md` (LLM prompt for starter backlog)
  - `mcp-signup-flow.md` (MCP architecture)

## Phase 1 — Substrate template (Weeks 1-3)

**Goal:** A fully-working "Hello, world" SaaS that runs end-to-end in
docker-compose, deploys to a single VPS, and is the source we'll
provision from per customer.

### Week 1 — Foundation
- [x] Monorepo scaffolding (pnpm workspaces)
- [x] `CLAUDE.md` and `.cursorrules` with curated context
- [x] `HANDOFF.md` template
- [x] `docker-compose.yml` for local dev (Postgres, Supabase stack, web, api)
- [x] `Caddyfile` for prod (TLS + reverse proxy)
- [ ] Initial Supabase schema (users, subscriptions, audit_log)
- [ ] `apps/web` scaffold (Vite + React + Tailwind + shadcn + Supabase client)
- [ ] `apps/api` scaffold (FastAPI + SQLAlchemy + Supabase JWT verification)

### Week 2 — Business layer
- [ ] Auth flows wired: email/password, magic link, Google OAuth
- [ ] Stripe Checkout integration (test mode)
- [ ] Stripe webhooks → subscription state in Supabase
- [ ] Subscription gating helper (`requirePaid` route guard)
- [ ] Customer Portal embed
- [ ] Email templates (welcome, receipt, magic link)
- [ ] Transactional email via Resend

### Week 3 — Polish + mobile
- [ ] Mobile shells (Capacitor iOS + Android), build pipeline
- [ ] PWA manifest + service worker
- [ ] CI/CD GitHub Actions (test + deploy)
- [ ] Preview deploys per branch
- [ ] Migration gate (review before apply)
- [ ] One-click backup restore
- [ ] Read-only prod toggle
- [ ] `docs/` written (architecture, adding-a-table, adding-a-page)
- [ ] AI integration docs and code-generation patterns documented

**Phase 1 success criteria:** `git clone` + `docker compose up` + 30 seconds
later, working SaaS app accessible at `localhost:8080` with signup, login,
billing, and a "Hello, world" dashboard. Same code deploys to a VPS via
`./deploy.sh` and serves at a real domain.

## Phase 2 — Wizard (Week 4)

**Goal:** 4-question web form that takes a customer's product details
and queues a provisioning job.

- [ ] Next.js app at `app.hatchik.com`
- [ ] 4-question wizard:
  1. Product name + description
  2. Domain search (Infomaniak Registry API)
  3. Hosting region picker
  4. Email + Stripe connect (or skip)
- [ ] Free Sandbox path (skip domain + region, get subdomain)
- [ ] Stripe Checkout for paid Launch tier
- [ ] Wizard session persistence (Postgres)
- [ ] Email confirmations
- [ ] "Your app is being provisioned" status page with live updates

**Success criteria:** Customer can complete signup in <3 minutes. Wizard
generates a complete provisioning manifest and queues the job. Customer
sees progress in real time.

## Phase 3 — Provisioning orchestrator (Weeks 5-7)

**Goal:** Deterministic state machine that consumes a wizard manifest and
ends with a fully-provisioned customer instance.

### Week 5 — Infrastructure lanes
- [ ] Job orchestrator (Python + Celery + Postgres-backed queue)
- [ ] Idempotent step framework (retry, rollback, idempotency keys)
- [ ] Infomaniak Domain API integration (register, configure DNS)
- [ ] Cloudflare DNS API integration
- [ ] Hetzner Cloud API integration (provision VPS)
- [ ] Vultr / DigitalOcean / Infomaniak Public Cloud fallbacks
- [ ] SSH key management + initial server bootstrap

### Week 6 — Application provisioning
- [ ] GitHub API integration (create repo, push template)
- [ ] Template rendering (substitute customer values into substrate)
- [ ] SSH-deploy of substrate to customer VPS
- [ ] Caddy + Supabase + Postgres bootstrap via docker-compose
- [ ] Infomaniak Mail provisioning (5 inboxes + DKIM/SPF/DMARC)
- [ ] Stripe OAuth + product creation
- [ ] Resend domain verification
- [ ] Backup destination setup (Backblaze B2 bucket per customer)

### Week 7 — AI integrations + finalization
- [ ] Linear OAuth flow + workspace bootstrap
- [ ] Backlog generation via Claude API (using `backlog-prompt.md`)
- [ ] Linear project + 20-task seeding
- [ ] MCP config injection into customer's repo
- [ ] GitHub Actions secrets configured
- [ ] First deploy + smoke test
- [ ] Welcome email + handover credentials

**Success criteria:** Wizard manifest → fully working customer app at
their domain in <10 minutes, end-to-end automated, with rollback on any
step failure.

## Phase 4 — Customer dashboard (Week 8)

**Goal:** Customer's home base for ongoing operations.

- [ ] Next.js app at `app.hatchik.com`, multi-tenant
- [ ] Billing (Stripe Customer Portal embed)
- [ ] Deploy status (recent deploys, prod health, preview URLs)
- [ ] Backups list + one-click restore
- [ ] Pending migrations review/approve
- [ ] Logs + recent errors (Sentry events surfaced)
- [ ] AI usage (if passthrough enabled)
- [ ] Team invites (basic)
- [ ] Danger zone (cancel, export data, transfer ownership)

**Success criteria:** Customer can self-serve 95% of post-launch ops
without contacting support.

## Phase 5 — MCP signup server (Week 9)

**Goal:** The differentiating "sign up inside your AI tool" experience.

- [ ] `@loftik/mcp` npm package scaffold
- [ ] MCP server (Node + `@modelcontextprotocol/sdk`)
- [ ] Signup-mode tools (start_signup, suggest_domains, set_choices,
      checkout, status, complete)
- [ ] Ops-mode tools (project_info, deploy_status, preview_url,
      pending_migrations, apply_migration, deploy_to_prod, rollback,
      logs, recent_errors)
- [ ] Browser confirmation token system for destructive actions
- [ ] Install-token handoff (browser → MCP local config)
- [ ] Self-rewriting MCP config (writes project ID + API key after signup)
- [ ] Open-source release (MIT license)
- [ ] Submit to MCP marketplaces:
  - Cursor MCP marketplace
  - Anthropic MCP registry
  - Smithery.ai
  - mcp-servers GitHub list

**Success criteria:** Customer can install MCP, chat with their AI, and
have a working Hatchik deployment without ever opening the web wizard.

## Phase 6 — AI credits passthrough (Week 10)

**Goal:** The one optional revenue stream beyond core hosting.

- [ ] OpenRouter API integration
- [ ] AI proxy endpoint (`proxy.hatchik.com`)
- [ ] Per-customer API key generation + scoping
- [ ] Usage metering (request count, token count, cost)
- [ ] Cost caps + alerts ("you're at £80, on track for £200")
- [ ] Monthly billing reconciliation
- [ ] Stripe invoice line items for AI usage
- [ ] Dashboard UI for usage / cap / settings
- [ ] Provider switching (Claude / GPT / Gemini / open-source) via env var

**Success criteria:** Customer flips a toggle and their app's AI calls
route through Hatchik with metered billing. Monthly invoice includes AI
line.

## Phase 7 — Launch prep (Weeks 11-12)

### Week 11 — Hardening
- [ ] Load testing (100 simultaneous wizard submissions, 1000 concurrent
      customers)
- [ ] Failure mode testing (provider API outages, payment failures,
      half-provisioned recovery)
- [ ] Security audit (own + external pentester)
- [ ] GDPR documentation (data processing, DPA template, sub-processor
      list)
- [ ] Terms of Service + Privacy Policy (lawyer review)
- [ ] Refund / dispute / abuse policy
- [ ] Status page set up at `status.hatchik.com`

### Week 12 — Marketing + first customers
- [ ] Final marketing page polish + demo video recording
- [ ] Open-source MCP repo on GitHub
- [ ] First-100-customers offer (£0 setup waived) — outreach to:
  - Indie Hackers community
  - r/SaaS, r/IndieHackers
  - Build-in-public crowd on X / Bluesky
  - Personal network
- [ ] Pre-launch email list activated
- [ ] Anthropic featured app submission
- [ ] Linear "complementary tools" outreach
- [ ] HackerNews launch post draft
- [ ] Product Hunt launch scheduled
- [ ] First 10 paying customers shipped

**Success criteria:** 10 paying customers signed up in week 12, no
critical incidents during signup or first 7 days of operation.

## Beyond v1 — Watchlist

Things that are not in scope but tracked for v2 evaluation:

| Item | Trigger to revisit |
|---|---|
| Multi-tenant team support | First 5 customer requests for team seats |
| White-label / agency tier | First serious agency outreach |
| Builder tier (sub-Launch, hobbyist-priced) | Sandbox-to-Launch conversion <20% |
| Additional backlog tools (Notion, GitHub Projects, Plane) | First 10 requests for non-Linear |
| Voice signup via MCP | When MCP voice support matures |
| Self-hosted Hatchik Server edition | Compelling "data sovereignty" customer request |
| Concierge launch (paid premium tier) | Repeated customer asks for "do it for me" |

## Capacity and risk

**Single-founder pace assumes:**
- ~40 hours/week on Hatchik
- Contractor or designer for 1-2 weeks for marketing site polish + demo video
- Possible legal contractor for ToS/Privacy review

**Highest-risk steps (most likely to slip):**
1. Provisioning orchestrator (Week 5-7) — many third-party APIs, all
   must be reliable
2. AI credits passthrough billing (Week 10) — usage-based billing has
   gotchas
3. First-customer hardening (Week 11) — discovers issues we missed

**De-risking moves:**
- Build orchestrator with retry/rollback from day one
- Use OpenRouter as billing layer (don't build wholesale billing v1)
- Recruit 5-10 friendly beta customers for week 9-10 testing before
  public launch

## What we cut to make this 12 weeks (not 24)

Deferred to v2 explicitly:
- Multi-tenant team support (single-developer per project in v1)
- White-label features
- Builder tier
- Marketing automation (email sequences, drip campaigns)
- Affiliate / referral program
- Customer success dashboards (analytics, milestones)
- Advanced monitoring (custom Grafana dashboards per customer)
- Compliance attestations (SOC 2, HIPAA, ISO)
- Multi-language support
- Voice / native mobile MCP clients

Each of these can be 2-4 weeks. v1 is a focused launch.

## Total estimated effort

- **Build (Phases 1-6):** ~10 weeks engineering
- **Launch prep (Phase 7):** ~2 weeks
- **Total:** 12 weeks

Assumes one full-time engineer (founder). Add 30% buffer for unknowns =
**15 weeks realistic.** Aim for v1 launch in 16 weeks (4 months).
