# Hatchik — First-Customer Runbook

For the concierge-MVP phase (signups before the wizard + provisioning
worker exist). When someone signs up via `hatchik.com`, do this. End to
end: 30–45 minutes per customer, more if you're learning the steps.

The goal during this phase is to deliver the same experience the
automated pipeline will eventually deliver — just slower and by hand.
Customers shouldn't feel they're a beta; they should feel they're
hand-shaped.

---

## Trigger

A signup form submission arrives at `hello@hatchik.com` (or in your
signup backend). It contains:

- Customer email
- Product name / description (what they want to build)
- Tier (Sandbox / Launch)
- For Launch: Stripe payment confirmation, region preference, domain
  preference (BYO existing or new)

## Within 1 hour — Acknowledge

Reply personally. Don't use a canned template raw — open with their
name and a sentence that proves you read what they sent.

Use the template at `WELCOME_EMAILS.md §1` as your skeleton. Aim for
3–4 sentences. Set expectation: "I'll have your Hatchik live within
24h. I'll email you again when it's ready."

If Launch tier: the £79 charge is in Stripe. Confirm payment landed,
note the amount and customer ID in your tracking sheet.

## Provisioning — Sandbox tier (free)

1. SSH to your Hatchik infrastructure VPS (the shared sandbox host).
2. Clone the substrate template into a per-customer directory:
   ```bash
   cd /opt/loftik/sandboxes
   git clone /opt/loftik/substrate-template <customer-slug>
   cd <customer-slug>
   ```
3. Generate the `.env` from `.env.example`, substituting:
   - `PRODUCT_NAME` = customer's chosen name
   - `DOMAIN` = `<customer-slug>.hatchik.com`
   - `JWT_SECRET` = `openssl rand -hex 32`
   - `POSTGRES_PASSWORD` = `openssl rand -hex 24`
   - `SITE_URL` = `https://<customer-slug>.hatchik.com`
   - All other secrets left empty / test mode
4. Update the Caddyfile on the sandbox VPS to add a new subdomain
   block routing `<customer-slug>.hatchik.com` → this customer's docker
   containers
5. `docker compose up -d` from the customer's directory
6. Wait ~30s, verify `https://<customer-slug>.hatchik.com` loads
7. Trigger a quick smoke test: sign-up flow works, login works,
   Supabase Studio accessible at `/studio`
8. Create the customer's Linear board (next section)
9. Send the welcome email (§4 below)

Time: 10–15 minutes once you've done it twice.

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
   mkdir -p /opt/loftik
   cd /opt/loftik
   git clone /opt/loftik/substrate-template-mirror <customer-slug>
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
   cd /opt/loftik/<customer-slug>
   # Uncomment the production block in Caddyfile, fill in {{DOMAIN}}
   docker compose up -d
   ```

9. **Smoke test** the deployed app at `https://<customer-domain>`

10. **Send the "your Hatchik is live" email** (§5 below)

Time: 45–90 minutes for the first one; 30–45 once you've done it
three times.

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
