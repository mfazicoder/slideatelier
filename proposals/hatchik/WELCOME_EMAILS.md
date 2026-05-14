# Hatchik — Welcome & Provisioning Email Templates

Use as skeletons, not scripts. Always personalise — open with the
customer's first name and a sentence that proves you read what they
wrote. Send from `hello@hatchik.com` (your real address, not a
no-reply alias). British voice throughout.

---

## §1 — Sandbox signup acknowledgement (within 1 hour)

**Subject:** Welcome to Hatchik, {{first_name}} — your sandbox is being set up

```
Hi {{first_name}},

Got your signup — thanks for trying Hatchik with {{their_product_idea_in_their_words}}.

Your Hatchik sandbox is being set up now. You'll get another email
within the hour with the link to log in and start building.

Until then: have a look at https://hatchik.com#included if you want
to see what's already wired up for you.

If anything's unclear, reply to this email and we'll get back to you.

— Hatchik
```

## §2 — Launch tier signup acknowledgement (within 1 hour)

**Subject:** Thanks for the £89 — getting your Hatchik built now

```
Hi {{first_name}},

Just confirmed the £89 — thank you. {{their_product_idea_one_sentence_reflection}}.

Your Hatchik is being provisioned now. The pieces in flight:

  • {{domain}} — registered / configured ({{ETA}})
  • Your dedicated server in {{region}} — spinning up
  • 5 mailboxes on @{{domain}} — being set up
  • Stripe live mode wired into your app
  • Your code repo in your GitHub (you'll get an invite)
  • BACKLOG.md at the repo root with 20 starter tasks tailored to your idea

You'll get the full handover email within 24 hours — earlier if
nothing breaks. If anything needs your input (Google OAuth
preferences, mobile app icon, etc.) we'll ask in a separate email.

Reply to this one if you've thought of anything else since you
signed up.

— Hatchik
```

## §3 — "Your Hatchik is live" — Sandbox

**Subject:** {{product_name}} is live — here's how to log in

```
Hi {{first_name}},

Your Hatchik sandbox is up. Have a look:

  Your app:        https://{{slug}}.hatchik.com
  Log in with:     {{email}} (we'll send a magic link)
  Admin password:  {{password}}
  Your to-do list: BACKLOG.md in your repo
  Your code:       {{github_repo_url}}

What to do first:

  1. Click the link above and log in. You'll see a working app with
     sign-up, login, billing (test mode), and a placeholder dashboard.
  2. Open your favourite AI tool (Claude, Cursor, Windsurf — anything
     that supports MCP) and add the Hatchik MCP — instructions at
     https://hatchik.com/install
  3. In your AI, ask: "What's next on the backlog?" It'll read
     BACKLOG.md at the repo root and walk you through the first feature.
     Tick items off in the same commit as the feature ships.

A few things to know:

  • You're on the Sandbox tier — capped at 3 users and 100MB. Plenty
    for testing and prototyping. When you're ready for real customers,
    upgrade in the dashboard to Launch for £89.
  • There's a "Built with Hatchik" footer on your sandbox app. That
    disappears when you upgrade to Launch.
  • Your sandbox stays free as long as you're active. Idle 7 days →
    archived (we can restore it on request).

Reply to this email if anything's not working — email support is
typically same business day.

— Hatchik
```

## §4 — "Your Hatchik is live" — Launch tier

**Subject:** {{product_name}} is live at {{domain}}

```
Hi {{first_name}},

Your Hatchik is fully set up. Here's everything:

  Your live app:     https://{{domain}}
  Customer logins:   real, working (sign-up, magic link, Google OAuth)
  Payments:          Stripe LIVE — you can take real money today
  Your to-do list:   BACKLOG.md in your repo
  Your code:         {{github_repo_url}}
  Your mailboxes:    {{webmail_url}}
                     hello@{{domain}}     — general
                     support@{{domain}}   — customer support
                     billing@{{domain}}   — payments
                     noreply@{{domain}}   — automated mails
                     {{custom_inbox}}     — your personal

  Server:            {{provider}} in {{region}}, root SSH in your name
  Backups:           On-demand pg_dump from Studio + Hetzner VPS snapshots
  Monitoring:        Uptime alerts — we'll email you if anything goes wrong

What to do first:

  1. Log in at https://{{domain}}/login — your admin account is
     {{email}}. Use the magic link or reset password.
  2. Add the Hatchik MCP to your AI coder: https://hatchik.com/install
  3. Ask your AI "what's next on the backlog?" — it'll read
     BACKLOG.md at the repo root and ship the first feature. Tick
     items off in the same commit as the feature lands.
  4. Pause when you've shipped one feature, look at the preview
     deploy URL (any branch deploys to {branch}.{{domain}}
     automatically), and merge to prod when you're happy.

A few things worth knowing:

  • Domain auto-renews each year (free for years 2+ once you've
    graduated to Growth tier; £14/yr otherwise — we'll remind you).
  • Migrations don't auto-apply. When your AI suggests a database
    change, it queues for your approval in your Hatchik dashboard.
  • Snapshot before any risky migration — pg_dump from Supabase Studio
    is two clicks; or email us and we'll run one the same day.
  • £14/month billing starts on {{date}} (one month from today). After
    your 15th sign-up you graduate to £39/month — we'll email a month
    before that change.

On your statement and receipts:

  • Your bank or card statement will show "Paddle.com Market Ltd ·
    Hatchik" as the seller — Paddle handles the payment plumbing for
    us, so don't be alarmed by the unfamiliar name.
  • VAT / GST / your local sales tax was added at checkout and is
    itemised on Paddle's invoice, which they'll email you separately.
  • 14-day refund if you're not happy, no questions asked. Refunds
    are processed through Paddle and usually land within 5-10 days.

Anything not working, anything unclear, or just want to say hi —
reply to this email. Email support is typically same business day
on Launch.

Welcome to Hatchik.

— Hatchik
```

## §5 — Day-3 check-in (light touch)

**Subject:** quick check-in on {{product_name}}

```
Hi {{first_name}},

How's it going with {{product_name}}? Have you had a chance to
work through BACKLOG.md in your repo with your AI coder yet?

If you've hit anything you couldn't figure out, reply with the
specifics and we'll dig in. If everything's smooth, just write back
"all good" — we're tracking how the first cohort lands so we can
shape the experience for everyone after you.

— Hatchik
```

## §6 — Day-7 follow-up

**Subject:** week one of {{product_name}} — any thoughts?

```
Hi {{first_name}},

You've had Hatchik for a week. A handful of questions, if you have
five minutes:

  1. What did you ship in your first week?
  2. What was the worst friction you hit?
  3. Is your AI helper doing what you expected?
  4. Anything Hatchik should have done that it didn't?

You don't need to write long answers — bullets are perfect.

In return: we'll get back within a business day with anything we
can fix for you specifically, and over the coming weeks you'll see
the substrate quietly improve based on patterns from the early
cohort.

— Hatchik
```

## §7 — Payment failure (Stripe webhook → manual)

**Subject:** Your Hatchik payment didn't go through

```
Hi {{first_name}},

Stripe couldn't process your monthly payment for {{product_name}}
today. Quick update on what's happening:

  • Your app stays online for the next 7 days while we sort this out
  • All your customers, data, and code stay where they are
  • Update your card here: {{stripe_portal_url}}
  • Or reply to this email and we'll help

No drama, this happens. Most often it's an expired card or a bank
flagging an international transaction.

— Hatchik
```

## §8 — Cancellation acknowledgement (from EXIT_JOURNEY.md)

**Subject:** Sorry to see you go — here's your handover

```
Hi {{first_name}},

Got your cancellation. Here's what happens now:

What you keep:
  • Your code (already in your GitHub — nothing changes)
  • Your domain (already in your name)
  • Your server (root credentials below; rough self-hosted cost £10-15/mo)
  • Your database (full SQL dump attached)
  • Your customers (unchanged — they keep using your app)
  • Your mailboxes (Infomaniak credentials transferred to you)

Server root access:
  SSH: root@{{vps_ip}}
  Password: {{server_password}} (please change it after first login)

Your last 7 days of backups: {{b2_presigned_urls}} (valid 30 days)

What stops:
  • Hatchik monitoring on your server (you can set up your own)
  • Hatchik billing (already cancelled, no more charges)
  • Hatchik support email (still works until end of grace period)

You have 7 days to change your mind — reply and we re-enable
everything instantly. After that you're fully on your own
infrastructure, which is exactly where you signed up to be.

If there's anything specific that made you leave — even a sentence
— we'd love to hear it. Helps us build a better Hatchik for the
next person.

Thanks for trying it. Best of luck with {{product_name}}.

— Hatchik
```

## §9 — Internal: what to track per customer

After each email, log in your tracking sheet:
- Date / time
- Email type sent
- Their response (if any), summarised in one line
- Action you took
- Any follow-up needed

The pattern from these notes feeds product decisions, support
automation, and eventually trains the support agent (see
SUPPORT_JOURNEY.md).
