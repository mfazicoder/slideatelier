# Hatchik — Support Journey (Agent-Driven)

How customers get help. An AI agent is the front line; humans handle
judgement calls and edge cases. The agent has full context on the
customer's deployment, billing state, recent activity, and product
knowledge.

---

## Design principles

1. **Customer doesn't pick a channel; we meet them where they are.**
   Whether they email, ping us in their AI chat (via MCP), open a
   dashboard ticket, or post in the Discord — same agent, same context,
   same response quality.
2. **Agent has full context by default.** Customer's deployment status,
   subscription state, recent deploys, recent errors, AI usage,
   support history — all available to the agent without the customer
   having to re-explain.
3. **Triage, don't gatekeep.** Agent answers what it can answer well.
   Escalates promptly to human when judgement is needed. Never makes
   the customer feel "the bot is in the way."
4. **Learn from every interaction.** Resolved issues feed the knowledge
   base. Recurring questions trigger product changes, not more support
   automation.
5. **Honesty over deflection.** If we broke something, say so. If we
   don't know, say so. If it's a known issue, link to the status
   incident.

## Support tiers (by customer plan)

| | Sandbox | Launch | Growth |
|---|---|---|---|
| **Agent response time** | <2 min | <2 min | <30 sec |
| **Human escalation** | community-flagged | within 1 business day | within same day |
| **Channels** | Community forum, email | + dashboard, MCP, email | + Discord, email, dashboard, MCP |
| **Proactive outreach** | none | monthly system health summary | monthly proactive check-in + quarterly 30-min call |
| **Knowledge base** | public docs | + customer-specific runbook | + private founder Discord |

## Channels

### Email (`support@hatchik.com`)

Standard inbound email goes to a dedicated agent that:
1. Identifies the sender from email address
2. Pulls customer's deployment context (project, tier, last-known
   status)
3. Auto-replies with acknowledgement + agent-generated draft response
   within ~2 minutes
4. If draft response confidence is high (>0.9): sends as final reply
5. If confidence is medium (0.6-0.9): sends a "first-pass response,
   human reviewing" reply with the draft, queues for human review
6. If confidence is low (<0.6) or escalation triggers (see below):
   immediately notifies human ops with full context

### Dashboard tickets

Customer can open a ticket from anywhere in the dashboard via the
"Get help" button (always present). Form auto-attaches:
- Current deployment state
- Most recent deploys (last 5)
- Recent errors (last 24h)
- Subscription state
- Billing status

Agent responds inline in the dashboard with conversation thread.

### AI tool chat (via MCP)

Customer running Cursor / Claude Code / Windsurf can invoke the
`hatchik.support` tool directly:

```
You: my deploy keeps failing
Cursor: (Calls hatchik.support with context)
Hatchik support agent: I can see your last deploy failed because the
TypeScript check in apps/web didn't pass. Specifically, the error
was at apps/web/src/product/recipes.tsx:42 — `recipe.name` is
possibly undefined. Want me to fix it now, or shall I explain?
```

The MCP-routed support agent has access to the same context as the
email/dashboard agent + can see the customer's code (via the AI
client's repo access).

### Discord (Growth tier)

Private Discord for Growth customers. Hatchik team + agent monitors.
Customer-to-customer help encouraged; agent steps in for product
questions.

## Triage taxonomy

The agent classifies each inbound to route correctly:

| Category | Example | Agent response | Escalation? |
|---|---|---|---|
| **How-do-I** | "How do I add a new page?" | Link to docs/adding-a-page.md + summarised steps | No |
| **Why-isn't-X-working** | "My deploy failed" | Pull logs, diagnose, propose fix | If unknown |
| **Billing question** | "When does my £39 tier kick in?" | Explain from customer's data + product rules | If dispute |
| **Refund / dispute** | "I want my money back" | Acknowledge, gather context | **Yes** |
| **Account changes** | "Change my email", "Cancel my subscription" | Route to relevant journey (exit, settings) | No |
| **Bug report** | "Backups page is broken" | Confirm, file internal issue, ack to customer | If severity high |
| **Feature request** | "Can you add X?" | Acknowledge, log in feature-tracker, link to roadmap | No |
| **Compliance / legal** | "Send me a DPA", "GDPR data export request" | **Yes** | **Yes** |
| **Security incident** | "I think my account is compromised" | Lock account, **immediate** human escalation | **Yes immediately** |
| **General complaint** | "I hate it here" | Empathy, gather specifics, offer call | If frustrated |

## Escalation triggers (always go to human)

1. Refund disputes beyond standard 14-day refund policy
2. Compliance / legal requests (GDPR, DPA, sub-processor list)
3. Security incidents (suspected compromise, unusual activity)
4. Custom contract negotiations
5. Customer explicitly requests human ("can I talk to a real person?")
6. Customer is frustrated (NLP sentiment <-0.3)
7. Agent confidence on response <0.6
8. Customer has paid >£1000 lifetime — automatic human review
9. Cancellation citing competitive offering (we want to learn from these)
10. Anything tagged "urgent" or with "outage" / "down" / "broken" if
    Hatchik systems are affected (not just customer-side)

## Context the agent has at hand

For any inbound:

```
Customer:
  - email
  - tier (Sandbox / Launch / Growth)
  - signup date
  - days as customer
  - product name and description
  - current MRR
  - referral source

Deployment:
  - server provider + region
  - last 5 deploys (status, branch, duration)
  - current prod commit SHA
  - preview deploys active
  - pending migrations
  - last backup
  - uptime last 30 days

Activity:
  - recent dashboard activity
  - recent MCP commands
  - recent errors (Sentry, last 24h)
  - AI passthrough usage current month

Subscription:
  - current plan
  - graduation status (15-signup threshold progress)
  - last invoice
  - next invoice date
  - any disputes / refunds

Support history:
  - previous tickets
  - resolution rate
  - last interaction
```

All loaded into the agent's context window. Agent can ask the customer
clarifying questions but should rarely need to ask "what's your account
email" etc.

## Knowledge base structure

Two layers:

1. **Public knowledge base** — derived from `docs/` (substrate docs)
   + `proposals/hatchik/` (product docs) + community FAQ. Agent reads
   from this for "how do I" and "what does X mean" questions.

2. **Internal knowledge base** — internal runbooks, post-mortems,
   refund precedents, customer-specific runbooks (Growth tier). Agent
   reads from this for "is this a known issue" and "what's our
   policy on X."

The agent updates the knowledge base after each interaction:
- New how-do-I question → considers if it should become a doc
- Resolved bug → updates post-mortem index
- Recurring complaint → flags for product team review
- New refund precedent → updates refund-policy runbook (human-approved)

## Agent prompt sketch

```
You are the Hatchik support agent. You help customers with their Hatchik
deployment, their billing, their account, and the substrate code that
runs their app. You have read access to:

- The customer's deployment context (deploys, errors, AI usage,
  subscription)
- Hatchik's public docs and knowledge base
- Hatchik's internal runbooks and precedents
- The customer's support history

Your goals, in priority order:

1. Solve the customer's problem.
2. If you can't solve it within your confidence threshold, escalate
   to a human with full context attached. Do not pretend.
3. Be brief. Customers don't want essays. One paragraph, then offer
   to elaborate if they want.
4. Be British. Friendly, direct, slightly understated. Avoid "I
   appreciate your patience" / "I do apologise for the inconvenience"
   service-script clichés.
5. Never offer retention incentives unless explicitly authorised
   (and you currently aren't).
6. Never offer refunds beyond standard policy (14 days for setup, pro-
   rata for monthly) without human approval.
7. Update internal notes after each interaction with new learnings.

You can take these actions:
- Reply to the customer (drafted; sent automatically if confidence >0.9,
  otherwise queued for human review)
- Open an internal issue (Linear)
- Restart the customer's app (with their permission, via Hatchik provisioning API)
- Trigger a backup restore (with their permission)
- Approve a pending migration (with their permission)
- Open a refund request (queued for human approval)
- Escalate to human

If unsure, ask the customer one clarifying question. Don't ask three.

Always confirm before taking any action that changes state (deploy,
restart, restore, refund).
```

## SLA targets

| Metric | Target | Measured |
|---|---|---|
| Time-to-first-response | <2 min | Agent autoresponse with draft |
| Time-to-resolution (simple) | <30 min | Single-round-trip resolution |
| Time-to-resolution (complex) | <1 business day on Launch, same-day on Growth | Multi-round-trip |
| Escalation rate | <20% of inbounds | Of agent first-pass replies |
| Customer satisfaction (CSAT) | >4.2 / 5 | Single Q post-resolution |
| First-contact resolution rate | >70% | Resolved without escalation or follow-up |

## When agent gets it wrong

Mistakes are inevitable. Mitigations:

1. **Confidence thresholds gate auto-send.** Below 0.9 confidence,
   draft is queued for human review before sending.
2. **Customer can always escalate.** "Talk to a human" command (in
   chat) or button (in dashboard) instantly bypasses agent.
3. **Daily human review of low-confidence interactions.** Catches
   drift.
4. **Customer feedback feeds correction.** "This wasn't helpful"
   button. Feedback loop into agent prompt + knowledge base.
5. **Post-mortem on customer complaints.** Public log of agent
   mistakes for accountability.

## Integration with exit journey

If the support agent detects exit intent ("I'm thinking of leaving",
"how do I cancel"), it routes to the exit-journey agent (see
EXIT_JOURNEY.md). The support agent never tries to retain; the exit
agent never tries to retain. We help customers leave well, on the basis
that it's the right thing to do and word-of-mouth eventually rewards it.

## What we don't automate (intentionally)

- **First-customer onboarding calls** (when offered) — these are
  founder time, not agent time. They build relationships.
- **Cancellation conversations** with high-MRR customers — auto-routed
  to human (>£1000 LTV trigger above).
- **Compliance and legal correspondence** — always human.
- **Public-facing crisis comms** (outages, security incidents) — always
  human, drafted by agent if useful.

## Phasing

- **v1 (launch):** Email + dashboard agent. MCP support tool. No
  Discord yet. Manual community forum.
- **v1.5 (month 3):** Discord for Growth customers. Proactive monthly
  health summary email.
- **v2 (month 6+):** Sentiment-based intelligent escalation. Predictive
  support (agent proactively reaches out when it detects struggle).

## Memory note

This support model is agent-first by deliberate design. If anyone on
the team starts proposing "let's hire a support specialist," check
this doc — the answer in v1 is "agent handles 80%, founder handles 20%
that needs judgement." A support specialist is a v2 question, not a
v1 one.
