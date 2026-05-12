# Hatchik — Exit Journey (Agent-Driven)

How a customer leaves Hatchik with everything they're owed, gracefully,
without ever feeling like they hit a retention dark pattern. The journey
is driven by an AI agent throughout, with human escalation only at
customer request.

---

## Design principles

1. **No friction for leaving.** Cancellation is one click in the dashboard.
   We don't make the customer call us, click through "are you sure?"
   gauntlets, or fill out a survey.
2. **Customer keeps everything.** Repo, domain, server, data, customers,
   mailboxes. Hatchik holds nothing hostage.
3. **The handover is a service, not a chore.** Customer gets a clean
   credentials packet, a quick "you can keep running it yourself" guide,
   and a follow-up at 7 days.
4. **Honesty over retention.** If the customer is leaving because Hatchik
   genuinely isn't right for them, we accept that and help them go well.
   We don't try to win them back with discounts.
5. **The agent never argues.** If the customer says they want to leave,
   the agent confirms, then helps. No "are you sure?" beyond one
   confirmation.

## The journey, end to end

```
Customer intent signal
        │
        ▼
   AGENT: Confirm intent + explain what they'll keep
        │
        ▼
   AGENT: Offer voluntary feedback (skippable)
        │
        ▼
   AGENT: Generate handover package (repo unchanged, server keys,
          domain transfer, data export, mailbox transfer)
        │
        ▼
   BROWSER: Customer confirms exit (one-time URL)
        │
        ▼
   PROVISIONING WORKER: Initiates 7-day grace period
        │
        ▼
   AGENT: Walks customer through self-hosting (if customer wants)
        │
        ▼
   AGENT: Day-3 check-in: "anything you can't find?"
        │
        ▼
   AGENT: Day-7 final check-in + offboarding completion
        │
        ▼
   PROVISIONING WORKER: Deprovisions Hatchik-side
   (server access removed from Hatchik, monitoring disabled, billing
   stops, customer's resources continue to function under their
   own ownership)
```

## Step-by-step

### 1. Intent signal

Customer triggers the exit journey in any of three ways:

- **Dashboard:** Settings → Danger zone → "Cancel and offboard"
- **Chat with their AI:** `/launchkit cancel` slash command, or just
  telling Claude/Cursor "I want to cancel Hatchik"
- **Email:** Replies to any Hatchik email with "cancel"

All three paths route to the same agent flow.

### 2. Agent confirms + sets expectations

The agent (Claude, running inside Hatchik's support layer) responds:

> "Got it. Before we start, here's what you'll keep when this is done:
>
> - Your code (already in your GitHub — nothing changes)
> - Your domain (already in your name — we'll initiate transfer to your
>   own registrar if you want, or you can stay on Infomaniak)
> - Your server (we'll hand over root credentials; rough self-hosted
>   cost going forward is £10-15/month)
> - Your database and data (full SQL dump + JSON exports to your email)
> - Your mailboxes (Infomaniak account credentials transferred to you)
> - Your customers (unchanged — they keep using your app)
>
> Two questions before we proceed:
>
> 1. Do you want help self-hosting after we offboard, or are you good?
> 2. Any feedback that would help us improve? (Skippable.)
>
> Once you confirm in your browser, you have a 7-day grace period
> before Hatchik's hand comes off. Cancel anytime in that window if you
> change your mind."

The agent answers any clarifying questions ("does my server keep
running?", "do my customers notice anything?", "can I come back?")
using the canonical FAQ knowledge base.

### 3. Browser confirmation

The agent returns a one-time confirmation URL: `https://app.hatchik.com/exit/confirm/{token}?ttl=600`.

Page shows:
- The list of what they're keeping (same as above)
- The handover timeline (today → +7 days → deprovisioned)
- An itemised "what stops" list (Hatchik-managed backups, monitoring,
  support, billing)
- One big confirm button
- An "I changed my mind" button that returns them to the dashboard

Token is single-use, 10-minute TTL. Defence against prompt-injection
auto-cancellation.

### 4. Provisioning worker: 7-day grace

On confirmation, the orchestrator:

- Marks the subscription as `pending_cancellation` in Hatchik's DB
- Schedules the deprovisioning job for `now() + 7 days`
- Generates the handover package:
  - GitHub repo: noop (already customer's)
  - Domain transfer auth code (Infomaniak)
  - Server root credentials reset to a customer-chosen password
  - Database export (`pg_dump` of full DB, encrypted)
  - Storage export (Supabase storage bucket as tar.gz)
  - Mailbox handover (Infomaniak account control transferred)
  - Stripe Connect customer can keep running their subscriptions —
    nothing changes for them
- Emails customer with the package within 2h
- Pauses Hatchik's invoicing immediately (pro-rated refund issued)

### 5. Self-host walkthrough (if customer wants it)

If customer indicated they want help self-hosting, the agent provides:

- A `docs/self-hosting.md` quickstart for what to do next:
  1. Change passwords on the VPS root account
  2. Update Cloudflare DNS to remove Hatchik's records (we leave a stub
     for backwards compatibility)
  3. Set up your own backup destination (Backblaze B2 / S3)
  4. Disable Hatchik's `LOFTIK_*` env vars (or leave them — they fail
     silently once we deprovision)
  5. Optional: switch transactional email from Resend to your own
  6. Optional: switch monitoring to your own Sentry / Uptime Kuma
- A `self-host-checklist.md` PR opened against their repo
- An offer to jump on a 15-minute call (Growth tier only)

### 6. Day-3 check-in

Agent emails the customer:

> "Three days into your offboarding. Quick check: is anything missing or
> unclear? Reply if so. If not, you don't need to do anything — your
> deprovisioning lands on {{DATE}}."

Replies route back to the agent. Agent can extend the grace period if
asked.

### 7. Day-7 deprovisioning

Provisioning worker:

- Sends final email with credentials packet (in case earlier email was
  missed) and offboarding completion confirmation
- Disables Hatchik monitoring on customer's server (Sentry, Uptime
  Kuma, log forwarding)
- Removes Hatchik's SSH key from the customer's VPS
- Stops Hatchik-managed backups (customer's last 7-14 days of snapshots
  emailed as B2 presigned URLs valid for 30 days)
- Closes any open support tickets
- Cancels the subscription in Hatchik's DB
- Sends one final email: "You're free. Your server is yours. Hatchik is
  off. Best of luck."

### 8. Aftermath

- Customer keeps everything operational under their own ownership.
- They can re-onboard within 90 days with full data restoration — same
  setup fee waived, no penalty for trying-then-coming-back.
- Hatchik retains anonymised exit feedback in customer-research database.

## Edge cases

| Scenario | Handling |
|---|---|
| Customer cancels within 14 days of signup | Full refund of £79 setup, pro-rated refund of any monthly. Same 7-day grace period applies. |
| Customer has outstanding AI passthrough invoice | Settled before exit. Agent confirms balance and processes. |
| Customer wants to keep using Hatchik for some things (e.g. mail) but not others | Not supported in v1. They keep everything or nothing. Suggest they self-host then BYO Hatchik's mail config to their new setup. |
| Customer is mid-graduation (about to hit 15 sign-ups) | Cancellation precedes graduation. They stay on £9/mo for the grace period, then offboard. |
| Customer hasn't backed up customer-data in years | Agent generates a full archive: SQL dump + Storage + audit log. Emailed as B2 presigned URL. |
| Customer wants their domain transferred but doesn't know how | Agent provides registrar-specific instructions (Namecheap, GoDaddy, Cloudflare Registrar, etc.) |
| Customer leaves and the VPS provider charges them directly afterwards | Expected. Agent reminds them: "Hatchik stopped billing you; the VPS provider (Hetzner / Infomaniak) starts billing you directly. Their account is in your name; their charge appears on your card." |
| Customer wants to be deleted entirely (GDPR right to erasure) | Standard GDPR flow. Email security@hatchik.com. We retain only what's legally required for tax/accounting (7 years). |
| Customer asks for refund beyond 14-day window | "We can refund the unused portion of your most recent month. Setup is non-refundable after 14 days, unless there's a service issue we caused." |
| Customer wants to keep Hatchik's Linear board after leaving | Linear is the customer's anyway — they keep it. We just stop adding to it. |
| Customer wants to return | Welcome. Within 90 days: full data restored, setup fee waived. After 90 days: re-onboard from scratch (data they exported is still theirs to import). |

## Why agent-driven

A human-led offboarding would cost ~30 minutes of support time per
customer, of which 25 minutes is information delivery the agent does
better. We use the human time for the actual judgement calls (refund
edge cases, unusual data requests, escalations).

A side benefit: agent-driven offboarding scales linearly with customers,
human-driven doesn't. We can have 1,000 cancellations the same week
without lag.

## Agent prompt sketch (for the support agent)

The exit-journey agent is a specialised system prompt within the
support agent (see SUPPORT_JOURNEY.md). Key behavioural rules:

- Never try to retain. If customer says they want to leave, help them
  leave.
- Always confirm what they'll keep, before they confirm exit.
- If they ask "what if I change my mind?", explain the 7-day grace
  period clearly.
- Don't ask for feedback more than once. Skippable.
- Offer the self-host walkthrough proactively if they didn't already
  signal interest.
- Escalate to human only on: refund disputes, custom contract
  cancellations, suspected fraud, or customer explicit request.

## Metrics

Track per quarter:
- Cancellation count
- Days-from-signup-to-cancellation (cohort analysis)
- Return-after-90-days rate
- Exit-feedback completion rate
- Top exit reasons (anonymised)
- Time-to-handover (from intent → handover packet sent; target: <2h)
- Time-to-deprovision (from confirmation → final email; target: 7d ± 4h)
- Customer NPS at point of exit (5-point scale, single question)
