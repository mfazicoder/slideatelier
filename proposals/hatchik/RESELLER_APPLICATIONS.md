# Hatchik — Reseller programme application drafts

Pre-written copy for the top five "apply-today" programmes identified
in RESELLER_RESEARCH.md. Combined potential: ~$13K of credits + 4
free distribution channels in ~1 hour of form-filling.

For each programme: the direct URL, what to say in each field, and
any prerequisites you need to satisfy first.

---

## Order of attack (fastest to most-prep-required)

1. **AWS Activate Founders** (15 min, no review delay) — $1,350 credits
2. **Sentry for Startups** (15 min, 2-3 day review) — $5K credits
3. **Cloudflare for Startups — Bootstrapped tier** (20-30 min, ~7 day review) — $5K credits
4. **Smithery + Cursor + mcp.so + MCP registry** (1-2 hr, instant) — free distribution
5. **Anthropic MCP / Connectors Directory** (2-4 hr including MCP polish, 1-2 week review) — free distribution + Claude integration

Total credit value: ~$11,350 + featured-distribution upside.

---

## 1. AWS Activate Founders ($1,000 + $350 dev support)

**URL:** https://aws.amazon.com/startups/credits/

**Eligibility:** Founders tier — no investor required, just self-attest
as an early-stage startup. We qualify.

**Application fields you'll see (drafted answers):**

> **Company name:** Hatchik
>
> **Company URL:** https://hatchik.com
>
> **Stage:** Idea / Pre-revenue
>
> **Year founded:** {{YEAR_HATCHIK_INCORPORATED}}
>
> **Number of employees:** 1
>
> **Funding raised:** £0 (bootstrapped)
>
> **What does your company do? (1-2 paragraphs):**
> Hatchik is a SaaS-substrate-as-a-service for non-technical founders.
> We provision a fully-wired SaaS — hosting, payments, email, mobile,
> auth, backups, AI-tool integration — to the customer's own
> infrastructure. They use AI coders like Claude and Cursor to build
> their actual product on top; we operate the foundation. Each
> customer keeps their code, server, domain, and data — Hatchik is
> not a lock-in platform.
>
> **How will you use AWS credits?**
> Two purposes. First, as a backup VPS provider for customer
> deployments in regions where Hetzner/Infomaniak don't have
> coverage (specifically Asia-Pacific and South America). Second,
> for Hatchik's internal infrastructure: a managed Postgres for
> Atelo's customer-records DB, an S3-equivalent bucket for the
> shared backup destination beyond our Backblaze B2 free tier, and
> a small EC2 instance for the AI proxy service (proxy.hatchik.com)
> when we activate the AI credits passthrough feature.
>
> **Tech stack:** Python (FastAPI), React + Vite, Postgres (Supabase),
> Stripe, Cloudflare, Resend, OpenRouter. Deployments via Caddy +
> Docker Compose.

**After submission:** Credits land in your AWS account within ~24
hours. No call required.

---

## 2. Sentry for Startups ($5K credits + 6 months free Teams plan)

**URL:** https://sentry.io/for/startups/apply/

**Eligibility:** Under 2 years old, raised under $5M, fewer than 50
employees. We qualify on all three.

**Application fields:**

> **Company name:** Hatchik
>
> **Website:** https://hatchik.com
>
> **Date founded:** {{HATCHIK_FOUNDED_DATE}}
>
> **Funding raised:** $0
>
> **Number of employees:** 1
>
> **What does your company build?**
> Hatchik is a SaaS-substrate-as-a-service. We provision a complete
> working SaaS — hosting, payments, mail, mobile, auth, backups, AI
> tool integration — on customer-owned infrastructure. Customers
> build their actual product on top using AI coders like Claude and
> Cursor.
>
> **Why do you need Sentry?**
> Two flows:
> (1) Our own product — the marketing site, customer dashboard,
> wizard, MCP server, and provisioning worker need error tracking.
> (2) Each customer's deployed app — Sentry catches errors in the
> substrate-shipped code, so we can patch substrate bugs across all
> customer instances when something goes wrong centrally. We
> currently self-host Sentry's open-source edition for the customer
> side; Sentry Cloud Teams plan would let us consolidate both
> internal + customer-facing flows into one place with better
> performance.
>
> **Estimated monthly events (errors):**
> Internal: <10K/month (small operation).
> Customer-aggregated: scales with customer count — projected 50-500K
> events/month at 100 customers.
>
> **Anything else we should know?**
> Hatchik is pre-launch, soft-launching this month. Customer-facing
> infrastructure is the unique angle — Sentry credits effectively
> support the customers we're enabling, not just our own operations.

**After submission:** 2-3 business days. Email response with credit
code + sign-up link for Teams plan.

---

## 3. Cloudflare for Startups — Bootstrapped tier ($5K credits)

**URL:** https://www.cloudflare.com/forstartups/

**Eligibility:** Bootstrapped (no outside funding) startup. **Use
promo code `BOOTSTRAPPED`** when prompted — that unlocks the tier
without requiring VC-portfolio affiliation.

**Application fields:**

> **Company name:** Hatchik
>
> **Website:** https://hatchik.com
>
> **Promo code:** BOOTSTRAPPED
>
> **Industry:** SaaS / Developer Tools
>
> **Stage:** Pre-launch / pre-revenue
>
> **Funding:** Bootstrapped (zero outside funding)
>
> **What does your company do?**
> Hatchik is a SaaS-substrate-as-a-service. We provision a wired-up
> SaaS — hosting, payments, mail, mobile, auth, backups, AI-tool
> integration — to infrastructure customers own. Customers build
> their product on top using AI coders (Claude, Cursor, Windsurf).
> Each customer gets their own Cloudflare DNS zone + edge layer + WAF
> as part of the bundle.
>
> **Which Cloudflare products do you use today?**
> Cloudflare Free tier: DNS, CDN, Universal SSL, basic WAF for the
> Hatchik marketing site at hatchik.com and for each customer
> deployment we provision.
>
> **What credits would unlock for you?**
> Workers and Workers KV for the signup flow and AI proxy service.
> R2 for customer backup destinations (zero-egress fees significantly
> reduce our cost vs Backblaze B2 for customers with heavy egress).
> Cloudflare Pages for the customer dashboard. Turnstile for the
> signup form. WAF Pro for premium customer tiers.
>
> **Estimated traffic:** Currently <100 requests/day to the marketing
> site; expected to scale 10-100x in the next 6 months as the
> customer base grows.

**After submission:** Approximately 5-7 business days. They may
schedule a 15-min call to verify legitimacy.

---

## 4. MCP Directory Submissions (Smithery + Cursor + mcp.so + official registry)

**Prerequisite:** The `@hatchik/mcp` server needs to be published to
npm first (or be runnable via `npx`). This means:
1. Polish the MCP server (the one specced in `mcp-signup-flow.md`)
2. Publish to npm: `npm publish --access=public`
3. Then submit to the directories

For the launch-today path, you can submit to the directories with a
"coming soon" listing even before the MCP itself is fully polished —
just point at the GitHub repo.

### 4a. Smithery

**URL:** https://smithery.ai/submit

**Steps:** Go to smithery.ai, click "Submit a server," fill in:

> **Name:** Hatchik
>
> **Author:** {{YOUR_NAME / Hatchik}}
>
> **GitHub URL:** https://github.com/{{YOUR_ORG}}/hatchik-mcp
>
> **NPM package:** @hatchik/mcp
>
> **Description:** Sign up for Hatchik (a SaaS substrate-as-a-service)
> directly inside your AI tool, and manage your deployment ongoing.
> Tools include domain search, signup wizard, deployment status,
> preview URLs, migration approval, and rollback — all via your
> existing Claude / Cursor / Windsurf session.
>
> **Tags:** deployment, saas, hosting, ai-coding, infrastructure

**Result:** Listed in the Smithery directory within ~24 hours after
their review.

### 4b. Cursor MCP Marketplace

**URL:** https://cursor.com/marketplace/mcp (look for "Submit a server")

**Steps:** Same content as Smithery; Cursor's submission form is
nearly identical.

### 4c. mcp.so

**URL:** https://mcp.so (look for the GitHub link "submit an MCP
server")

**Steps:** mcp.so is a community-maintained list. Submit via GitHub
PR or issue with the same content.

### 4d. Official MCP Registry

**URL:** https://registry.modelcontextprotocol.io/

**Steps:** The official registry accepts submissions via GitHub. PR
your server's metadata into the public registry repo. Anthropic's
team reviews; typically merged within a few days.

**Result of all four:** Hatchik MCP shows up in every major directory
where Claude / Cursor / Windsurf users discover MCPs. Customer
acquisition cost via this channel: effectively £0.

---

## 5. Anthropic MCP / Connectors Directory

**URL:** https://claude.com/docs/connectors/building/submission

**Eligibility:** Anthropic-curated. Bar is higher than the directory
listings above. Need: polished MCP with proper tool annotations,
privacy policy, public docs, working demo.

**Prerequisites before applying:**

- [ ] `@hatchik/mcp` published to npm
- [ ] Privacy policy live at https://hatchik.com/privacy ✓
- [ ] MCP docs page (at hatchik.com/install or docs subdomain)
- [ ] Demo video showing the MCP in use (60-90s screen recording)
- [ ] Tool annotations: each tool in the MCP needs a clear description
      and example (this is where 30% of submissions get rejected)

**Application fields (drafted):**

> **Connector name:** Hatchik
>
> **Submission URL:** https://github.com/{{YOUR_ORG}}/hatchik-mcp
>
> **Description (1-2 sentences):**
> Hatchik lets you launch and operate a complete SaaS — domain, server,
> payments, mail, mobile, auth, AI — directly from inside Claude. Sign
> up via chat, then use the same MCP to deploy features, preview
> changes, restore backups, and approve migrations.
>
> **What problem does it solve?**
> Non-technical founders with software ideas can vibe-code an app
> using Claude or another AI tool, but the operational stack
> (hosting, payments, email, backups, mobile builds, security) is a
> wall they hit immediately. Hatchik handles that wall. The MCP makes
> the entire signup + ops experience native to Claude — no
> context-switching to web dashboards.
>
> **Target users:** Solo founders, indie hackers, small-business
> owners with software ideas. Anyone vibe-coding with Claude who
> wants their app actually live.
>
> **Tools the MCP exposes:**
> - Signup mode: start_signup, suggest_domains, check_domain,
>   set_choices, quote, checkout, status, complete
> - Ops mode: project_info, deploy_status, preview_url,
>   pending_migrations, apply_migration (browser-confirmed),
>   deploy_to_prod (browser-confirmed), rollback (browser-confirmed),
>   read_logs, recent_errors
>
> **Security model:** All destructive actions (payments, deploys to
> prod, rollbacks, migrations) require browser-confirmation via
> one-time URL. Prompt-injection-safe by design. Open-source MCP code
> on GitHub.
>
> **Public docs:** https://hatchik.com/install
> **Demo video:** {{YOUTUBE_OR_LOOM_URL}}
> **Privacy policy:** https://hatchik.com/privacy

**After submission:** 1-2 weeks review. If approved, Hatchik shows up
in Claude's built-in connectors directory — Claude users can install
with one click without npm.

---

## Application tracker

Copy this into a Google Sheet or Notion table:

| # | Programme | Applied | Status | Outcome |
|---|---|---|---|---|
| 1 | AWS Activate Founders | | | |
| 2 | Sentry for Startups | | | |
| 3 | Cloudflare for Startups (Bootstrapped) | | | |
| 4a | Smithery | | | |
| 4b | Cursor MCP Marketplace | | | |
| 4c | mcp.so | | | |
| 4d | Official MCP Registry | | | |
| 5 | Anthropic MCP / Connectors | | | |

Update after each submission. Re-check status weekly until all five
land.

---

## Notes that aren't in the application copy

- **Don't lie or stretch.** Pre-revenue is fine. Bootstrapped is fine.
  Sole founder is fine. The credits exist precisely for this profile —
  no need to embellish.
- **One application per programme.** Don't reapply if rejected; some
  programmes have one-shot rules.
- **Read each programme's full terms** before clicking submit. The
  credit value listed here is approximate; small print may add
  conditions (e.g. AWS credits expire 12 months from issue).
- **Anthropic submission is highest-value but highest-bar.** Apply
  fourth, after you've nailed the MCP server and have a demo video.
  Quality matters more than speed here.

---

## Beyond the apply-today list

Per RESELLER_RESEARCH.md, the following gate on revenue / volume and
should be revisited later:

- Infomaniak Partner Network (apply at ~20 Hatchik customers on
  Infomaniak hosting)
- Hetzner Cloud Partner (no formal programme — use the referral
  programme once you're a paying customer)
- OpenSRS Registrar Reseller (apply at 100+ customer domains)
- OpenRouter volume pricing (apply at £10k+/month AI passthrough
  consumption)
- Direct Anthropic / OpenAI reseller (apply at £20k+/month
  consumption)

Track in RESELLER_RESEARCH.md and revisit quarterly.
