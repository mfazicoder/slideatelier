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

## 5. SOM — Sprint trajectory (12-18 months, not 3 years)

The original 3-year scenarios anchored on steady-state SaaS funnel math
(5% conversion, 30% churn, linear growth). That's the wrong frame for
this market. AI coding tools are compounding 30-50% MoM through their
hype window: **Cursor** went 0 → 1M+ paid users in ~24 months,
**Lovable** hit $5M+ MRR in ~12 months, **Bolt** crossed 1M users in
<12 months. The right frame is a 12-18 month sprint, not a marathon.

### Reference trajectories (real, public)

| Product | Time to milestone | Driver |
|---|---|---|
| Cursor | 0 → 1M+ paid in ~24mo | Sustained 30-50% MoM through year 1, content + word-of-mouth |
| Lovable.dev | 0 → $5M+ MRR in ~12mo | Hype + Anthropic Claude integration + Twitter founder loop |
| Bolt.new | 0 → 1M users in <12mo | Product Hunt + Show HN + repeated viral moments |

These aren't 3-year curves — they're 12-18 month sprints where the
founders either capitalised at peak (raised at high multiples) or got
acquired. Slow growth in this window is *actively expensive*: by the
time you'd hit 5K paying customers on a 3-year cadence, someone else
owns the mindshare.

### Two sprint scenarios (replacing Conservative/Base/Aggressive)

| Scenario | Month-12 state | Month-18 state | Required posture |
|---|---|---|---|
| **Sprint base** | ~5K paying, £40-80K MRR | ~8K paying, £100-150K MRR | Aggressive launch (PH + Show HN + 5× YouTube sponsorships in months 2-4), then sustained content engine |
| **Sprint upside** | ~10-15K paying, £100-200K MRR | ~15-25K paying, £200-400K MRR | All of Sprint base + one major partnership (Anthropic / Cursor / Windsurf featuring), or successful pre-seed unlocking paid acquisition |

The **lifestyle landing zone** (~£30-80K MRR plateaued) is a
consolation outcome if hypergrowth misses — fine, but not the target.

### Five-phase sprint cadence

| Phase | Months | Target | Driver |
|---|---|---|---|
| **Beta + launch** | 0-2 | 100 friendly users, polished, pricing tested | Founder-led; tight feedback loop on first 50 customers |
| **Hype injection** | 2-4 | 5K signups, 250-500 paying | Product Hunt + Show HN + 5× YouTube creator sponsorships |
| **Compound** | 4-6 | 15K signups, 1.5-2K paying | Reddit value content, X #buildinpublic, newsletter pitches, partnership outreach |
| **Decision point** | 6-9 | 30K signups, 4-5K paying. £40-80K MRR | Raise pre-seed OR signal availability to acquirers OR stay lean |
| **Capitalise** | 9-15 | 50-80K signups, 8-15K paying. £100-300K MRR | Major partnership OR exit OR full marketing engine |

---

## 6. Funnel assumptions

For every 100 Sandbox signups:
- ~10 convert to Launch within 30 days (10% ceiling, 3-5% more realistic)
- Of Launch, ~20% upgrade to Growth within 12 months
- Annual churn at Launch: ~30%; at Growth: ~15%

These need revalidation against actual signups; in a hype window
conversion can be 2-3× higher than baseline SaaS.

### Pricing recommendation: confirmed

The original £9/mo Launch / £24/mo Growth was founder-empathy
underpricing for this window. **Final pricing**: Launch **£89 setup
+ £14/mo**, Growth **£39/mo**. Comparable hype-window products
(Cursor Pro $25/mo, Lovable $25-100/mo, Replit Core $25/mo) sit in
the £20-30/mo entry band. We deliberately sit just under that band
on Launch to keep the "first SaaS" affordability story while still
clearing a real margin once the setup fee is folded in.

Margin impact at the new pricing — **now with AI COGS subtracted**
(cost-to-serve breakdown: infra £8.50/mo, **AI passthrough COGS
£1.50/mo Launch / £5/mo Growth mid-range**, Paddle fees ~5%+£0.40/txn).
The AI line is elastic — it scales with customer model mix (Haiku vs
Sonnet vs GPT-4o), allowance utilisation, and the BYO-key share. Modelled
end-to-end in `proposals/hatchik/AI_COGS_SENSITIVITY.xlsx`.

- **Launch ongoing margin (mid-range): ~£2.14/mo at £14** (sensitivity
  range −£0.75 pessimistic to £4.28 optimistic). Pessimistic = all
  customers max allowance on expensive models; optimistic = high BYO-key
  share + low utilisation + cheap-model mix.
- **Growth ongoing margin (mid-range): ~£21.45/mo at £39** (sensitivity
  range £12.79 pessimistic to £27.75 optimistic).
- Year-1 Launch revenue: £89 + 11 × £14 = £243 (with passthrough overage
  uplift the realised figure is closer to £247 per customer).
- Year-1 Launch net (mid-range, AI COGS included): ~£108/customer.
- Year-1 Growth net (mid-range, AI COGS included): ~£257/customer (down
  from the pre-AI-COGS £338 cited in earlier drafts of this plan).
- 1000-customer cohort at 80/20 Launch/Growth: Y1 gross margin
  ~£137K (vs ~£154K pre-AI-COGS — a ~11% haircut, materially smaller
  than I feared before running the model).
- Blended LTV at 80/20 mix and 24-month tenure remains in the £450–£500
  range — AI COGS trims the top of the range but not the CAC headroom.
- CAC ceiling at 3:1 LTV:CAC: ~£150–£170 — still usable room for paid
  acquisition.

**Overage-margin uplift.** Once a customer is on Hatchik's passthrough,
they're already in our billing flow; tokens past the included allowance
flow through us at a markup rather than strict zero-margin passthrough.
The spreadsheet models a 30% markup as default (tunable lever). This adds
roughly £0.40–£1.00/customer/month on Launch and £1.00–£2.00 on Growth
for heavy-usage customers — net contribution to margin already baked
into the figures above.

---

## 7. Three numbers to validate first

These will move estimates by 2-5×:

1. **Actual Sandbox→Launch conversion rate.** Industry says 3-10% for
   free-to-paid; hype-window can be higher. The business case lives or
   dies on this.
2. **Churn at Launch tier.** £14/mo is more committed pricing than the
   original £9/mo; expect
   churn similar to or lower than Cursor's ~5-10% monthly at peak.
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
> up — under your own GitHub repo on your own server. Free to try, £89
> to launch, £14/month to run. Leave any time, your code comes with you.

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
| Price (£/mo at launch tier) | £14 | $20+ | $25+ | $25+ | $39+ | one-off |

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

## 13. Sprint cadence — months 0-15

### Phase 0 — Beta + launch (months 0-2, where we are now)

- Polished product live (signup → sandbox → push-to-deploy → mobile builds)
- 50-100 friendly-beta users, founder-driven onboarding
- Pricing tested at £89 setup + £14/£39 (not £9/£24)
- Show HN draft + Product Hunt assets ready
- /vs page + content arsenal already drafted

### Phase 1 — Hype injection (months 2-4)

The single highest-leverage window. Goal: 5K signups, 250-500 paying.

- **Week 1**: Show HN (Tue-Thu morning, well-written write-up referencing
  Cursor / Lovable / Bolt and our differentiation)
- **Week 2**: Product Hunt launch (#1 product target — schedule a Tuesday,
  pre-line up upvotes from network, hunter outreach to 3-4 well-known PH
  people in the dev-tools space)
- **Weeks 3-12**: 5× YouTube creator sponsorships (£500-2K each):
  Theo Browne, Beyond Fireship, indie-hacker channels, AI-coding-focused
  creators. **Burn 2-3× the £170 CAC ceiling in this window** — the cost
  of slow growth right now is much higher than the cost of overspending
- **Throughout**: weekly build-in-public X thread; Reddit value content
  in r/ClaudeAI, r/cursor (one substantive post per week, not shills);
  Indie Hackers community presence

### Phase 2 — Compound (months 4-6)

Goal: 15K signups, 1.5-2K paying, £20-40K MRR.

- Reddit value-content cadence becomes routine (3× per week across subs)
- Newsletter pitches: Ben's Bites, TLDR AI, The Rundown (only AFTER we
  hit 50 paying customers — premature pitches get rejected)
- First **partnership outreach** to Anthropic / Cursor / Windsurf — not
  for distribution yet, just relationship-building. Position as "the
  deployment layer your users need"
- Build attribution dashboard so we know what's working
- Hire 1 part-time customer-success person at £40-60K signups/week —
  founder shouldn't be the bottleneck

### Phase 3 — Decision point (months 6-9)

Goal: 30K signups, 4-5K paying, £40-80K MRR. **This is the inflection.**

Three paths from here. Pick one based on what the metrics say:

- **A. Raise a pre-seed (£500K-1M).** Hire engineering + marketing. Push
  for £200K MRR by month 12. Position as a venture-scale opportunity.
  Valuation: £8-20M depending on growth rate.
- **B. Signal availability to acquirers.** Talk to Anthropic / Cursor /
  Vercel / Supabase. A clean $20-50M strategic acquisition at this stage
  is plausible — especially for whoever needs a "deployment layer for
  non-tech founders" piece.
- **C. Stay lean.** Don't raise. Don't sell. Optimise for founder income
  £15-25K/mo. This is the fallback if hypergrowth misses, not the goal.

### Phase 4 — Capitalise (months 9-15)

Goal: 50-80K signups, 8-15K paying, £100-300K MRR. **Whichever path was
chosen in phase 3, execute it hard.**

- If raised: paid acquisition at scale, partnership announcement, content
  team in place
- If selling: clean up financial story, due diligence prep, multiple-buyer
  process
- If lean: optimise unit economics, automate as much as possible, reduce
  founder hours

### Phase 5 — Beyond month 15

Either:
- **Acquired** — $20-100M exit. Most plausible upside path.
- **Hypergrowing** — £500K-1M MRR by month 24, raising Series A
- **Plateaued** — solo lifestyle business at £30-80K MRR. Fine. Not the target.

---

## 14. Operating principles for the sprint

1. **Pricing is a lever, not a constraint.** Test £29/mo Launch and
   £59/mo Growth on a cohort in month 4. If conversion holds, raise.
2. **Acquisition optionality is not the same as actively selling.**
   Build the product as if you're staying solo forever. Talk to acquirers
   in parallel because some of them will be the highest-value outcome.
3. **Founder bandwidth is the bottleneck, not the business model.** Hire
   pre-emptively at the first sustained week of >100 signups.
4. **The hype window is finite.** Cursor, Lovable, Bolt all peaked
   somewhere — we don't know where in the cycle we are. Treat months 2-9
   as "act now or miss it" rather than "build to last".
5. **Quality wins compound interest in this market.** Customer-told-a-friend
   is the highest-margin growth channel. Optimise relentlessly for the
   first 100 customers' day-one experience — what they tell their network
   determines the next 1000.

---

## 15. Open questions to revisit

- **Pricing**: confirmed at £89 setup + £14 Launch / £39 Growth in this
  revision. A/B test against £29/£59 ongoing in month 4 to see if conversion
  holds.
- **Mobile builds**: cloud-build pipeline shipped (commit `6edf9f1`).
  Marketing claim is now real.
- **Custom domains on Sandbox tier**: today everyone gets `<slug>.hatchik.com`.
  Do we offer BYO domain at Launch only, or sometimes at Sandbox for
  power users?
- **Geographic expansion**: India/SEA latency is the biggest infra blocker
  to growing SAM. Spin up a second host region by month 6?
- **Partnerships**: official integrations with Anthropic / Cursor /
  Windsurf? Co-marketing with Supabase (we're built on it)? Anthropic
  outreach should start month 4 latest — relationships take time.
- **Raise-or-acquire decision criteria**: what specific metrics at month
  6 tip the decision? Suggest: >30% MoM growth AND >£40K MRR AND
  >2K paying = raise; otherwise signal to acquirers.

---

*Last updated: 2026-05-13. Edit freely as we learn.*
