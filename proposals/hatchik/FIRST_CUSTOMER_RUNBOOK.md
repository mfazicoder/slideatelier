# Hatchik — First-Customer Runbook

For the concierge-MVP phase. Sandbox tier is now **fully automated** by
the orchestrator on the sandbox host — see `sandbox-orchestrator/` for
internals. Launch tier remains manual until the cross-region
provisioning worker is built. End to end: ~5 minutes per Sandbox
customer (mostly waiting for DNS + container boot), 45–90 minutes per
Launch customer.

The goal during this phase is to deliver the same experience the
automated pipeline will eventually deliver. Customers shouldn't feel
they're a beta; they should feel they're hand-shaped.

---

## Infrastructure summary (where things live)

Everything Sandbox-tier runs on one VPS — the **sandbox host** at
`178.105.139.144` (Hetzner CAX21, Nuremberg). On that box:

- **Apex `hatchik.com`** — static marketing files at `/var/www/hatchik`,
  served by host Caddy with wildcard TLS
- **Signup API `hatchik.com/api/signup`** — `hatchik-signup.service`
  (systemd, FastAPI on `127.0.0.1:8090`) at `/opt/hatchik-signup/`
- **Sandbox orchestrator** — `provision.py` at
  `/opt/hatchik-orchestrator/` (called by signup-service via subprocess)
- **Per-tenant compose stacks** — `/opt/hatchik-orchestrator/sandboxes/<slug>/`,
  each bound to a unique localhost port 18000–18099
- **Host Caddy** — `/opt/hatchik-host-caddy/` (compose stack with
  wildcard cert + `import tenants.d/*.caddy` for per-tenant routes)

The Resend `from` header is set in `/etc/hatchik-signup.env` as
`HATCHIK_FROM_EMAIL=Hatchik <noreply@hatchik.com>`. The display-name
prefix (`Hatchik <...>`) is what Gmail shows in the inbox sender column;
without it Gmail extracts the local-part and shows `noreply`. After any
change here, `systemctl restart hatchik-signup`.

## Trigger

A signup form submission posts to `https://hatchik.com/api/signup`. The
signup-service:

1. Records the signup in `/var/lib/hatchik/signups.json`
2. Sends the welcome acknowledgement email via Resend
3. For Sandbox tier: subprocess-spawns `provision.py <email> <slug>
   <product>` which mints JWTs, renders the substrate, writes a tenant
   Caddyfile, runs `docker compose up`, reloads host Caddy, and emails
   the customer when their sandbox is live

Payload contains:

- Customer email
- Product name / description (what they want to build)
- Tier (Sandbox / Launch)
- For Launch: Paddle payment confirmation, region preference, domain
  preference (BYO existing or new)

## Within 1 hour — Acknowledge

Reply personally. Don't use a canned template raw — open with their
name and a sentence that proves you read what they sent.

Use the template at `WELCOME_EMAILS.md §1` as your skeleton. Aim for
3–4 sentences. Set expectation: "I'll have your Hatchik live within
24h. I'll email you again when it's ready."

If Launch tier: the £79 charge is in Stripe. Confirm payment landed,
note the amount and customer ID in your tracking sheet.

## Provisioning — Sandbox tier (free, automated)

The orchestrator handles steps 1–6 automatically when the signup
arrives. Your job is to verify it worked and finish off the human-only
bits (Linear board, personalised welcome).

1. **Verify provisioning landed.** SSH into the sandbox host
   (`ssh -i ~/.ssh/hatchik-deploy root@178.105.139.144`) and check:
   ```bash
   tail -20 /var/lib/hatchik/signups.json    # confirm signup was recorded
   ls /opt/hatchik-orchestrator/sandboxes/    # confirm tenant directory exists
   docker ps --format "{{.Names}}" | grep <slug>    # confirm 9 containers up
   curl -sk -o /dev/null -w "%{http_code}\n" https://<slug>.hatchik.com/
   ```
2. **Smoke-test the sandbox:** sign-up flow works, login works,
   Supabase Studio accessible at `/studio`, the static marketing copy
   on `/` shows `{{PRODUCT_NAME}}` substituted to the customer's name.
3. **If provisioning failed** (no containers, 404 from `<slug>.hatchik.com`):
   re-run manually:
   ```bash
   cd /opt/hatchik-orchestrator
   python3 provision.py <email> <slug> "<product description>"
   ```
   Watch the log for the failing step. Most failures are DNS
   propagation (transient) or substrate boot bugs (fix in the local
   template, rsync to host).
4. **Create the customer's Linear board** (next section).
5. **Send the welcome email** (§4 below) — personalise on top of the
   automated "your sandbox is live" Resend email.

Time: 5 minutes if the orchestrator worked, ~15 if you need to debug.

## Provisioning — Launch tier (paid)

1. **Provision a fresh VPS in the customer's region**
   - UK/EU customer → Hetzner CPX11 in Falkenstein or Helsinki (use
     Hetzner Cloud Console or `hcloud server create`)
   - US East → DigitalOcean Basic 2GB in NYC
   - US West → DO Basic 2GB in SFO
   - Singapore → Vultr Cloud Compute 2GB
   - Note the new VPS IP

2. **Register or configure the domain**
   - If customer is bringing their own domain: add it as a Cloudflare
     zone in your Cloudflare account, give them the nameservers to
     update at their registrar
   - If they need a new domain: register via Infomaniak Domain Manager
     or Cloudflare Registrar in the customer's name (use their email +
     a payment method they've authorised — typically the same Stripe
     card via Stripe Connect, or invoice separately)
   - Add an A record pointing the domain to the new VPS IP

3. **Bootstrap the VPS**
   ```bash
   ssh root@<new-vps-ip>

   # Install Docker + docker-compose plugin
   curl -fsSL https://get.docker.com | sh
   apt update && apt install -y caddy git python3-venv

   # Clone the substrate template
   mkdir -p /opt/hatchik
   cd /opt/hatchik
   git clone /opt/hatchik/substrate-template-mirror <customer-slug>
   # (or scp the substrate-template directory from your dev machine
   # since the git repo isn't pushed anywhere public yet)
   ```

4. **Generate `.env` with real values**
   - `PRODUCT_NAME` = customer's product name
   - `DOMAIN` = customer's domain
   - `JWT_SECRET` = `openssl rand -hex 32`
   - `POSTGRES_PASSWORD` = `openssl rand -hex 24`
   - `STRIPE_*` = customer's Stripe Connect keys (if they've connected
     Stripe; otherwise leave empty for now and update later)
   - `RESEND_API_KEY` = your Resend account key (you can issue a
     subkey scoped to their domain)
   - `LINEAR_*` = filled after Linear bootstrap (next step)

5. **Set up the customer's mailboxes**
   - Log into Infomaniak Mail console
   - Add the customer's domain
   - Create 5 inboxes: `hello@`, `support@`, `noreply@`, `billing@`,
     plus one of the customer's choice
   - Set MX records on Cloudflare DNS
   - Configure SPF, DKIM, DMARC (Infomaniak Mail provides the values;
     paste into Cloudflare DNS)
   - Send the customer their mailbox credentials in a separate email

6. **Set up Linear board** (see §"Linear bootstrap" below)

7. **Set up GitHub repo**
   - In your GitHub Organization (or a dedicated "Hatchik-customers"
     org), create a new private repo `customer-<slug>`
   - Push the customer's substrate (with `.env` excluded)
   - Add the customer as an owner / admin of the repo via their email
   - Send them the invite

8. **Configure Caddyfile and start the stack**
   ```bash
   cd /opt/hatchik/<customer-slug>
   # Uncomment the production block in Caddyfile, fill in {{DOMAIN}}
   docker compose up -d
   ```

9. **Smoke test** the deployed app at `https://<customer-domain>`

10. **Send the "your Hatchik is live" email** (§5 below)

Time: 45–90 minutes for the first one; 30–45 once you've done it
three times.

## Account harness — create + delete

Three layers, all live as of commit history (see `decommission.py`,
admin/self-serve endpoints in `signup-service/main.py`).

### 1. Self-serve (what customers see)

- **Sign up:** the form on `https://hatchik.com/#signup` posts to
  `POST /api/signup`. Provisioning is automatic.
- **Delete:** the footer link or
  `https://hatchik.com/delete-sandbox` posts to
  `POST /api/account/request-deletion {email}`. Customer receives a
  one-time confirmation link valid 24h. Clicking the link fires
  `GET /api/account/confirm-deletion?token=...` which subprocess-runs
  `decommission.py <slug> --hard` — tears down containers + volumes,
  removes the registry entry, and DELETEs the signup row. Anti-
  enumeration: the request endpoint always returns 202, never reveals
  whether the email matched.

### One active Sandbox per email

`POST /api/signup` enforces a single active Sandbox-tier tenant per
email. If a customer tries to create a second Sandbox while one is
still live (signup row status NOT IN
`deleted/cancelled/archived_purged` AND registry status NOT
`decommissioned`), the endpoint responds with **409 Conflict**:

```json
{
  "ok": false,
  "error": "sandbox_exists",
  "message": "You already have a Sandbox running at https://<slug>.hatchik.com. Delete it (https://hatchik.com/delete-sandbox) first if you want to start fresh."
}
```

Both the wizard at `/start` and the inline form on the marketing page
render this as a red banner with a one-click link to `/delete-sandbox`.
After the customer confirms deletion, the row flips inactive and a fresh
sign-up succeeds.

The cap **does not** apply to Launch/Growth tier — those can have
multiple per email (one per product the customer is launching).

### 2. Admin API (HTTP)

Header `X-Admin-Token: $HATCHIK_ADMIN_TOKEN` is required (the token
lives in `/etc/hatchik-signup.env` on the sandbox host).

- `GET /api/admin/accounts` — list all signups + joined tenant state
  (url, status, port).
- `DELETE /api/admin/account/{slug}` — soft decommission (keeps
  registry + signup row for audit). Append `?hard=true` to fully purge.
- `GET /api/admin/metrics/cohorts?granularity=week|month&since=YYYY-MM-DD`
  — per-cohort funnel breakdown (signups, currently live, conversion to
  Launch, mean days-to-upgrade, churn rates).
- `GET /api/admin/metrics/funnel` — all-time funnel rollup
  (sandbox→launch %, launch→growth %, overall churn %).
- `GET /api/admin/metrics/distribution` — current tier distribution
  across live tenants.

### 3. Admin CLI (`decommission.py`)

For when you're SSH'd into the sandbox host. Same teardown logic that
the API endpoints subprocess, but interactive:

```bash
python3 /opt/hatchik-orchestrator/decommission.py --list
python3 /opt/hatchik-orchestrator/decommission.py <slug>          # soft
python3 /opt/hatchik-orchestrator/decommission.py <slug> --hard   # purge
python3 /opt/hatchik-orchestrator/decommission.py --signup <id>   # lookup-by-id
```

### Metrics dashboard

Cohort-funnel dashboard at `https://hatchik.com/admin/dashboard`
(static page, served by Caddy from the marketing site). Paste your
`HATCHIK_ADMIN_TOKEN` into the header input once — it's stored in
localStorage so subsequent visits skip the prompt. Click Refresh to
pull fresh numbers; auto-refresh is intentionally disabled.

What the dashboard surfaces:

- **Top-line tiles** — total signups, currently live tenants, all-time
  Sandbox→Launch conversion %, all-time churn %.
- **Cohort table** — one row per weekly (or monthly, via toggle)
  cohort. Click a column header to sort.
- **Cohort funnel chart** — bar chart of each cohort's Sandbox → Launch
  → Growth progression (Chart.js, served from jsDelivr CDN).
- **Tier distribution donut** — how live tenants split across Sandbox /
  Launch / Growth today.

Backed by three admin API endpoints (see §2 above). All three accept
an optional `?since=YYYY-MM-DD` filter. The dashboard hits each
endpoint in parallel on every Refresh — fail-gracefully on empty DB
("no cohort data yet — come back after signup #1").

These numbers are what we use to **retire the assumed conversion /
churn figures from `MARKETING_PLAN.md` §7** by the time we hit signup
#50.

### Resetting the SQLite signup sequence

Soft-deleting individual rows leaves the autoincrement counter intact
(next signup gets `id = max(deleted) + 1`). To start fresh from `#1`:

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('/var/lib/hatchik/signups.db')
c.execute('DELETE FROM signups')
c.execute(\"DELETE FROM sqlite_sequence WHERE name='signups'\")
c.commit()
"
```

## Linear bootstrap (both tiers)

1. Send the customer a Linear invite link to a new workspace
   - Or, if they already have a Linear workspace, ask them to add you
     as an admin
2. Create a new Linear project for their product
3. Run the backlog-generation prompt (see `backlog-prompt.md`) using
   Claude with the customer's product description as input
4. Take the JSON output and create the ~20 starter issues in their
   Linear project via Linear's GraphQL API (`issueCreate` batched
   mutations)
5. Note the team ID and project ID — paste into the customer's `.env`
   as `LINEAR_TEAM_ID` and `LINEAR_PROJECT_ID`

Until the provisioning worker exists, this step is manual but takes
~10 minutes per customer.

## Send the welcome / activation email

Use the template at `WELCOME_EMAILS.md §3` — fill in:
- Customer's name
- Hatchik URL (sandbox subdomain or their custom domain)
- GitHub repo URL
- Linear project URL
- Login credentials (or magic link)
- Mailbox webmail URL + credentials

Include a 10-minute offer: "Reply to this email if you're stuck on
anything in the first hour — I'll jump in personally."

## Day-3 check-in

Reply on the same email thread:

> "Just checking in — anything you've tried that hasn't worked, or
> anywhere you need help? Also if you've used the Linear backlog with
> your AI coder yet, I'd love to hear how it went."

Customers don't expect this; it's why concierge-MVP wins early
retention.

## Day-7 follow-up

Open a Linear issue in your internal tracker tagged with this
customer's name. Note:
- What did they ship in 7 days?
- What did they struggle with?
- Did they ask any questions the FAQ should answer?
- Did they hit any substrate bugs?

Roll all of this back into product changes.

## Tracking sheet

Until you build a proper customer dashboard, keep a simple spreadsheet
or Airtable with one row per customer:

| Field | |
|---|---|
| Signup date | |
| Email | |
| Product name | |
| Product description | |
| Tier | Sandbox / Launch |
| Domain | |
| Region | |
| VPS IP | |
| Stripe customer ID | |
| GitHub repo | |
| Linear project URL | |
| Sandbox URL or live URL | |
| Status | Provisioning / Live / Cancelled |
| Last interaction | |
| Notes | |

This becomes the seed data for the eventual customer dashboard.

## Launch/Growth shell — what tenants ship with on day one

Every provisioned tenant (Sandbox, Launch and Growth) now boots with two
extra out-of-the-box surfaces on top of the existing auth + billing +
settings substrate. Customers don't have to design these from scratch —
they edit copy.

### 1. Marketing landing template (`apps/web/src/routes/index.tsx`)

Replaces the old "Hello, world. This is a freshly-provisioned SaaS"
placeholder. Pulls two values from the signup row via `provision.py`'s
template substitution:

- `VITE_PRODUCT_NAME` (was already wired) — hero headline, footer,
  copy throughout
- `VITE_PRODUCT_IDEA` (new) — hero subtitle, FAQ first answer; sourced
  from the signup's `description` column, trimmed to the first sentence
  and capped at 160 chars

Sections shipped: hero with CTA → `/login`, three feature cards (copy
templated on the product name), three-step "how it works", FAQ
accordion (3 generic SaaS questions), and a footer with privacy +
terms links.

The footer also shows a **"Built with Hatchik"** referral link on
Sandbox-tier tenants only. The flag is `VITE_BUILT_WITH_HATCHIK`,
defaulting to `"true"`; Launch/Growth provisioning flips it to
`"false"` post-deploy so paying customers don't have to display it.

### 2. End-user account dashboard (`apps/web/src/routes/account.tsx`)

A new tabbed dashboard at `/account` — separate from `/settings` (which
remains for app-level preferences). Tabs:

- **Profile** — display name (writes to `public.user_profiles`),
  email change (calls `supabase.auth.updateUser({email})`, fires the
  confirmation flow)
- **Security** — password change via `supabase.auth.updateUser({password})`,
  shows last-changed date as `user.updated_at`
- **Billing** — if `VITE_STRIPE_PUBLISHABLE_KEY` is set, "Open billing
  portal" button posts to `/api/billing/portal`. The endpoint is a stub
  (returns a placeholder URL) for the customer to wire up to Stripe
  customer portal config. If the key isn't set, the tab shows
  "Billing not configured yet — your founder will set this up before
  launching."
- **Sessions** — current session card + "Sign out from other devices"
  (uses Supabase's `signOut({scope:'others'})`). Full session listing
  needs a server-side endpoint that we haven't shipped yet.
- **Danger zone** — "Delete account" with type-your-email confirmation;
  deletes the `user_profiles` row (RLS-scoped to self), signs out, and
  redirects to `/login`. Auth-user purge needs a server endpoint —
  founders add when they need it.

A new migration `packages/db/migrations/0011_user_profiles.sql`
creates the `public.user_profiles` table (RLS-scoped, idempotent).
The header nav in `__root.tsx` adds an "Account" link when the user
is signed in.

### What customers still build themselves

- Their actual product (everything in `*/product/` directories)
- Real screenshots / pricing / testimonials on the landing page
- The Stripe customer-portal config + the
  `/api/billing/portal` implementation (currently a stub)
- Any session-listing UI beyond the current session

## When you hit problems

- **Customer paid but provisioning is taking too long**: email them
  immediately with an honest update. "Hit an unexpected issue with X,
  here's what I'm doing, you'll have your Hatchik by Y."
- **Customer wants a refund within 14 days**: Stripe dashboard → find
  payment → refund. Don't argue. Reply: "Done, refund issued. If
  there's anything specific that pushed you away, I'd love to hear it
  — feedback shapes what we build next."
- **Customer wants a refund after 14 days**: per PRODUCT_OFFERING.md
  §8, setup is non-refundable after 14 days but you can refund the
  unused portion of the most recent month. Be generous if the
  customer is unhappy — first 100 customers are evangelists or they're
  detractors; choose evangelist.

## Cap on concierge scale

Realistically, you can hand-provision **5–15 customers per week**
before this becomes painful. Track signup rate carefully. When
weekly signup rate exceeds 5, start treating wizard + orchestrator
build as the #1 priority — see ROADMAP.md Phases 2–3.

If signups exceed 25/week, throttle Stripe Checkout (return a "queue"
state) and use the waitlist temporarily until the wizard ships. Don't
overpromise.

## What customers should never see

- Frustration / apology beyond a single sentence
- Mention that the wizard isn't built yet (it's fine for them to know
  it's hand-onboarded; it's not fine for them to feel they're getting
  a worse experience)
- Different pricing / setup process than the marketing page promises
- Inconsistencies between this and PRODUCT_OFFERING.md

When in doubt, deliver more than the marketing page promises.
