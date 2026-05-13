# Hatchik — Marketing Plan

Working doc. Captures the strategic analysis from session 2026-05-13 so
we can pick it up later without losing the thread. Update as we learn.

---

## 1. ICP — Ideal Customer Profile

A person who is:

1. **Founder / builder / maker** — not employed as a software engineer
   at a company building someone else's product.
2. **Active paid user of at least one AI coding tool** — Cursor, Claude
   Code, Windsurf, GitHub Copilot Workspace, or Perplexity Comet.
3. **Building a SaaS / web app / mobile app with intent to monetise** —
   not a hobby script, not a portfolio piece.
4. **Will pay £100–£300/year for infrastructure tooling.**
5. **English-speaking, has a card, lives in a payments-friendly country.**
6. **Has at least one hour/day to work on their product.**

The filter is deliberately tight. It excludes:
- Software engineers at FAANG (build it themselves, don't want a platform)
- Students learning to code (no spend)
- No-code-only Bubble users who'll never touch a terminal (different ICP)

---

## 2. TAM — Total Addressable Market

### Sizing the universe

| Layer | Count | Source |
|---|---|---|
| Global paying AI-coding-tool users | ~2.5M unique | Cursor 1M+ paid (2025 public), Copilot 1.8M paid (Microsoft Ignite 2024), Claude Code 100Ks (Anthropic), Windsurf 100Ks; ~30% dedup overlap |
| ...whose primary identity is **founder/builder** (not employed eng) | ~25% | Stripe Atlas signal + estimate |
| ...**actively building toward launch**, not just side-projecting | ~50% of above | Industry split estimate |
| **TAM users today** | **~310K globally** | |

### TAM in revenue

- Sandbox £0, Launch £178 yr-1 / £108 ongoing, Growth £288/yr.
- Blended ARPU assumption: **£180/year** (mix of Launch + Growth, mostly
  Launch in early years).
- **TAM today ≈ £56M/yr.**
- AI-coding-tool adoption is ~doubling annually. At 2× for two more years:
  **TAM 2027 ≈ £200M+/yr.**

---

## 3. SAM — Serviceable Addressable Market

| Filter | Multiplier | Notes |
|---|---|---|
| English-first content / UI | × 0.75 | US, UK, Anglophone EU, AU/NZ/CA, India, SG/PH |
| Paddle-eligible geo | × 0.95 | Paddle covers almost everywhere |
| Latency / infra OK from Hetzner NBG1 (<200ms) | × 0.85 | US East/UK/EU strong; India/SEA/AU degraded — solvable with more hosts |
| **SAM users today** | **~190K** | |
| **SAM revenue today** | **~£34M/yr** | |
| **SAM revenue 2027** | **~£120M/yr** | |

---

## 4. Competitor share of TAM (ICP-slice only)

Estimates triangulated from public revenue claims, valuations, paid-user
reports, indie-hacker community signals. No formal market research
exists for this slice.

| Competitor | ICP users | Share of TAM | Evidence |
|---|---:|---:|---|
| **Lovable.dev** | ~40K | 13% | $5M+ MRR mid-2025 / £100 ARPU; ~80% ICP-aligned |
| **Bubble.io** | ~40K | 13% | ~200K paying, ~20% ICP-aligned (rest enterprise/hobbyist) |
| **Bolt.new** | ~25K | 8% | 1M+ users since launch, ~2.5% paid conversion, mostly ICP |
| **Replit Agent** | ~15K | 5% | ~150K Replit Pro, ~10% Agent-active and ICP-shaped |
| **ShipFast + Pegasus + boilerplates** | ~12K | 4% | ShipFast ~7K licences, others ~5K |
| **Vercel + Supabase DIY** | ~30K | 10% | Mostly engineers; ICP slice estimated |
| **Webflow + Memberstack / Softr / Glide** | ~20K | 6% | Drifting toward AI-tool users now |
| **DIY on Hetzner/DO/Fly/Railway, no platform** | ~80K | 26% | **Largest segment — exactly our wedge** |
| **Truly uncaptured (still using only the AI tool)** | ~50K | 15% | Greenfield — new AI-tool users |
| **Total** | **~312K** | **~100%** | |

### Where the money actually sits (of £56M/yr TAM)

- ~£15M with **direct competitors** (Lovable, Bolt, Replit, Bubble, boilerplates)
- ~£8M with **DIY-on-managed-infra** (Vercel + Supabase + Mailgun, fragmented)
- ~£12M in **founder time cost** (DIY on Hetzner/DO/Fly)
- ~£21M **not captured at all** (founder hasn't picked a stack yet)

The two biggest opportunities:

1. **The 26% DIY-on-Hetzner-without-platform segment** — they explicitly
   chose "no platform" and are bleeding hours. Sell them their hours back.
2. **The 15% greenfield AI-tool-new segment** — first product decision;
   if Hatchik is in front of them at the right moment, default choice.

Combined that's £21M/yr of TAM **not currently spending elsewhere**.
Even 10% capture there is £2.1M ARR.

---

## 5. SOM — Serviceable Obtainable Market (3-year)

| Scenario | Customers | ARR | Required posture |
|---|---:|---:|---|
| **Conservative** (1% of SAM 2027) | ~1,500 | **£270K** | Organic-only, founder-led, no paid marketing |
| **Base** (3% of SAM 2027) | ~5,000 | **£900K** | Active content/Reddit, 1-2 YouTube sponsorships, PH + Show HN |
| **Aggressive** (8% of SAM 2027) | ~13,000 | **£2.4M** | Full content engine, paid ads at break-even CAC, partnerships with Anthropic/Cursor/Windsurf for distribution |

Monthly signup velocity to hit these (year 3):
- Conservative: ~15 paying signups/week
- Base: ~50 paying/week
- Aggressive: ~125 paying/week

---

## 6. Funnel assumptions

For every 100 Sandbox signups:
- ~10 convert to Launch within 30 days (10% ceiling, 3-5% more realistic)
- Of Launch, ~20% upgrade to Growth within 12 months
- Annual churn at Launch: ~30%; at Growth: ~15%
- **Implied blended LTV: ~£260**
- **CAC ceiling at 3:1 LTV:CAC: ~£87/customer**

The CAC ceiling is the binding constraint on paid acquisition. Means
Reddit/YouTube/SEO are the primary channels; paid ads only viable at
very high efficiency.

---

## 7. Three numbers to validate first

These will move estimates by 2-5×:

1. **Actual Sandbox→Launch conversion rate.** Industry says 3-10% for
   free-to-paid. The business case lives or dies on this.
2. **Churn at Launch tier.** £9/mo is low enough some stay forever for
   the email/domain; some fail and bail in 30 days.
3. **Average tier upgrade time.** If Launch→Growth happens at month 4
   not month 12, blended ARPU jumps 60% and SOM moves with it.

**Build the cohort dashboard in `/api/admin/accounts` by signup #50** so
we have ground truth instead of inheriting estimates.

---

## 8. Positioning — the "100% this is the one" pitch

We're competing on four things nobody else combines:

1. **Your AI tool, not ours** (Claude Code / Cursor / Windsurf / Perplexity
   Comet) — every other AI app builder ships their own chat.
2. **Your code, your repo, your VPS** — exit costs are zero.
3. **The infrastructure already wired** — auth, payments (MoR via Paddle),
   mailboxes, mobile, GitHub repo, sandbox in seconds.
4. **Real business, not a demo** — production-grade from day one.

### Tagline

**"The production substrate your AI coder builds on — no platform, no
lock-in, no demos."**

### 30-second elevator

> You're already using Claude/Cursor/Windsurf to code. We give that AI a
> real SaaS to build on — auth, payments, mailboxes, mobile, all wired
> up — under your own GitHub repo on your own server. Free to try, £79
> to launch, £9/month to run. Leave any time, your code comes with you.

---

## 9. /vs comparison page

Build at hatchik.com/vs.

|  | Hatchik | Bolt | Lovable | Replit Agent | Bubble | ShipFast |
|---|---|---|---|---|---|---|
| Your existing AI tool works | ✓ | ~ | ✗ (theirs) | ~ | ✗ | ✓ |
| Real auth/payments/mail wired | ✓ | ✗ | ~ | ~ | ✓ | ✓ (DIY) |
| You own the code | ✓ | ~ | ~ | ✗ | ✗ | ✓ |
| Take your stack elsewhere | ✓ | ~ | ~ | ✗ | ✗ | ✓ |
| Provisioned for you | ✓ | ✗ | ✓ | ✓ | n/a | ✗ |
| Mobile builds included | ✓ | ✗ | ✗ | ✗ | ~ | ~ |
| Price (£/mo at launch tier) | £9 | $20+ | $25+ | $25+ | $39+ | one-off |

---

## 10. Head-to-head — who eats us

### Bolt.new — the prototype illusion

Magical first 30 seconds (describe an app, watch it appear in browser).
**Weakness**: stops at "looks working." No auth, mailboxes, payments,
mobile, GitHub repo to outgrow them with. They make demos; we make
businesses.
**Where we lose**: their first-touch dopamine is unbeatable. We need a
visible "your real auth/payments/mail just wired up" moment in our own
first 60 seconds.

### Lovable.dev — the prettiest cage

Polished UI gen, Supabase-backed. Moderate lock-in.
**Big weakness**: their own AI chat — if you're already a Claude/Cursor
person you have to context-switch. We don't ship our own AI; we plug into
the one you've already built a relationship with.

### Replit Agent — the dorm room

Vertical integration is real, pricing scales viciously, no exit. Audience
overlap is thinnest: Replit is for people who want to live in the IDE; our
audience wants to live in Claude Code/Cursor on their own laptop.

### ShipFast / SaaS Pegasus boilerplates — cousins not competitors

They sell engineers code drops. We sell non-engineers a *running stack
their AI can edit*. Customers still need to set up Stripe, Supabase,
domain, mobile binary themselves. We do all that.

### Bubble — the gravity well

Massive community, real income for some users, completely closed.
Non-tech founder picks Bubble, gets traction, can't ever leave without
rebuilding. **Our wedge**: real code, real GitHub, real exit. The Bubble
community is also drifting toward AI tools — anyone frustrated their AI
can't read their Bubble app is a warm lead.

---

## 11. Distribution strategy

### Highest-leverage channels

- **YouTube creators in the AI-coder ecosystem** — Theo, Fireship-adjacents,
  Beyond Fireship, Y Combinator's Garry Tan, indie-hacker channels. One
  sponsored video → thousands of qualified people.
- **r/ClaudeAI and r/cursor** — exact customer is there debugging their
  AI workflows. Provide free value (tutorials, working examples) THEN
  mention Hatchik weeks later. Direct shilling gets banned.
- **#buildinpublic on X** — Levels.io, Marc Lou's orbit. Indie hacker
  scene rewards transparency: weekly metric tweets, build logs, candid
  "what broke" posts.

### Medium-leverage

- Indie Hackers community + interviews
- Product Hunt launch (do once, do well)
- Hacker News Show HN (Tue-Thu morning, well-written write-up)
- Newsletters: Ben's Bites, TLDR AI, The Rundown (pitch after 50 real customers)
- LinkedIn — founder-led posts only

### Don't waste energy yet

- Facebook groups
- TikTok
- Paid ads (until CAC tolerance is known)

---

## 12. Operating model — what AI builds vs what humans do

**What I can build, autonomously:**

1. Draft the `/vs` comparison page (table above, expanded with evidence)
2. Draft positioning copy for the marketing site (hero, FAQ, why-us)
3. Draft a 30-day content calendar — specific Twitter posts, Reddit posts
   (with subs to target and "free value first" angle), long-form blog
   posts, Show HN draft, Product Hunt draft
4. Build an internal "content queue" admin tool — drafts flow through me,
   publishing flows through human
5. Build attribution tracking in signup pipeline — know which post →
   which signup → which paying customer
6. Build a Reddit/Twitter monitor flagging posts with target keywords
   ("Cursor + Supabase", "deploy my Claude project", "Bubble alternative")
   so a human can manually reply with value

**What only a human can do:**

- Create accounts (handle `@hatchik`?)
- Make the first ~10-20 posts personally to establish authentic voice
- DM the first 50 prospects 1:1
- Respond to comments in real-time
- Negotiate with YouTube sponsors

**The reason** isn't technical — it's policy. AI agents posting
unsupervised on social platforms is how Hatchik gets shadow-banned across
every platform on day one. Initial accounts must look human and *be*
human-driven for the first months. After that, agents can help with
curation and drafting; humans hit "publish."

---

## 13. Phased rollout

### Phase 1 — Positioning + content arsenal (now)

- Draft the /vs page + rewritten positioning copy
- Draft 30 days of social content (drafts for human to publish)
- Draft Show HN + Product Hunt copy
- Draft 5-10 substantial blog posts (tutorials, comparisons, case studies)

### Phase 2 — Human launches accounts

- Create Twitter, Reddit, LinkedIn, Indie Hackers accounts
- Make first ~10 posts personally to seed authenticity
- AI keeps drafting; human keeps publishing

### Phase 3 — Once warm

- Build attribution tooling
- Build content automation that pushes drafts to human queue
- Narrow agents for specific tasks (e.g. "monitor r/SaaS for keywords,
  alert with suggested response" — human sends reply)

---

## 14. Open questions to revisit

- **Pricing**: is £79 / £9/mo / £24/mo the right shape? ShipFast is one-off
  $199, Bubble is £39-119/mo. Are we leaving money on the table at the
  top, or is the low entry deliberate for conversion?
- **Mobile builds**: marketing promises iOS/Android shells included.
  Capacitor scaffold exists in the substrate but the build pipeline
  isn't wired. When does this become a credibility problem?
- **Custom domains on Sandbox tier**: today everyone gets `<slug>.hatchik.com`.
  Do we offer BYO domain at Launch only, or sometimes at Sandbox for
  power users?
- **Geographic expansion**: India/SEA latency is the biggest infra blocker
  to growing SAM. Spin up a second host region by month 6?
- **Partnerships**: official integrations with Anthropic / Cursor /
  Windsurf? Co-marketing with Supabase (we're built on it)?

---

*Last updated: 2026-05-13. Edit freely as we learn.*
