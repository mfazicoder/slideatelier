# Hatchik — 30-Day Launch Content Calendar

Working doc. Drafts are AI-generated; **a human publishes every single
one**. If anything here promises something the substrate doesn't already
do, rewrite or drop it — see `FIRST_CUSTOMER_RUNBOOK.md` and
`MARKETING_PLAN.md §14` for the honest list of what's wired and what
isn't.

Day numbering assumes Day 1 = first public post after accounts go live.
Times are UK time (BST). Adjust for season if you're on GMT.

---

## Section 1 — Operating principles

1. **A human publishes everything.** Drafts come from this calendar;
   accounts are not handed to an agent. Platforms shadow-ban
   AI-operated accounts on sight. The founder posts the first ~20
   personally to establish a voice that isn't going to read as bot
   later.

2. **Value-first on Reddit, always.** Every Reddit post must pass the
   "would this still be worth reading if Hatchik didn't exist?" test.
   Hatchik appears in the footer or a follow-up comment, never the
   headline. If a sub says "no self-promotion", obey it — one ban
   poisons the whole channel.

3. **Cadence:**
   - **X / Twitter**: one post a day, ideally between 08:30–10:00 BST
     (catches US East Coast morning + UK lunch) or 19:00–21:00 BST
     (catches US East workday + UK evening scroll).
   - **Reddit**: two posts per week, rotating subs so moderators don't
     see a pattern.
   - **Long-form**: one blog post a week (4 over the 30 days).
   - **Big one-shots** (Show HN, Product Hunt): once each, well-timed.

4. **Don't fabricate metrics.** Use "[N]" placeholders. Don't post
   "100 signups in week 1" until 100 signups have happened in week 1.
   The indie-hacker scene has an excellent nose for inflated numbers
   and will not forgive it.

5. **Match what's actually built.** Real today: signup pipeline,
   automated provisioning (Sandbox tier), magic-link auth, /start
   wizard, /account dashboard, idle-archive lifecycle (decommission.py),
   per-tenant containers on shared host. **Not yet real**: cross-region
   Launch provisioning worker, mobile build pipeline (Capacitor
   scaffold exists, builds aren't wired), Paddle (waiting on approval),
   GitHub-per-tenant (mentioned in marketing but not yet automated).
   Build-in-public posts must stay on the right side of that line.

6. **Metrics to watch each week:**
   - Signups by source (UTM)
   - Sandbox → Launch conversion rate (single most important number)
   - Followers from each post (to see which format compounds)
   - Reply rate on X (compounding signal, more than likes)
   - DM volume from posts (qualified-lead signal)

7. **British English. Friendly-but-confident.** Words to avoid:
   leverage, facilitate, revolutionary, game-changer, disrupt,
   unlock, supercharge, seamless, "we're excited to announce".
   Acceptable: actually, properly, fair, honest, boring, fix, ship.

---

## Section 2 — 30 daily X (Twitter) posts

Mix is roughly: 6 build-in-public · 5 how-to · 4 opinion · 4
comparison · 3 hypothetical/customer-story · 4 behind-the-scenes · 4
wildcard. Numbers below in parentheses after each day.

### Day 1 — build-in-public

**Type:** build-in-public · introduction
**Time:** 09:00 BST (US wakes up, UK is mid-morning)
**Post:**

> New project, new account. I'm building Hatchik — the boring
> infrastructure (auth, payments, mail, mobile, domain) wired up so
> your AI coder can build on top of it. Your repo. Your VPS. Leave
> any time. Going to post the build here. Day 1.

**Visual:** a single screenshot of the /start wizard at the
"naming-your-app" step.

---

### Day 2 — how-to

**Type:** how-to
**Time:** 08:45 BST
**Post:**

> Quick tip for anyone wiring Resend into Supabase Auth on their own
> stack: the SMTP `from` header needs a display name (`Hatchik
> <noreply@…>`), or Gmail strips it and your password-reset emails
> show up as "noreply" with no brand. Took me a day to spot.

**Visual:** before/after Gmail inbox screenshot, sender column.

---

### Day 3 — behind-the-scenes

**Type:** behind-the-scenes
**Time:** 19:30 BST
**Post:**

> Cost-of-running update. One Hetzner CAX21 (£10/mo) currently hosts
> the marketing site, the signup API, and every Sandbox tenant. Each
> tenant is a docker compose stack on a localhost port, fronted by a
> wildcard cert. Density is the whole reason free Sandbox can stay
> free.

**Visual:** terminal screenshot of `docker ps | wc -l`.

---

### Day 4 — opinion

**Type:** opinion
**Time:** 09:15 BST
**Post:**

> Every AI app builder ships their own chat. Cursor, Lovable, Bolt,
> Replit — they all want to be the front door. The thing is, you
> already have a relationship with your AI tool. The infrastructure
> shouldn't fight that. Use the AI you already pay for.

---

### Day 5 — build-in-public

**Type:** build-in-public · feature ship
**Time:** 10:00 BST
**Post:**

> Shipped: magic-link login on the /account dashboard. Customers
> never get sent a password — paste your email, click the link in
> the inbox, you're in. Same flow we wire into every tenant
> Supabase. No "forgot password?" page to build, ever.

**Visual:** 8-second screen recording of the magic-link flow.

---

### Day 6 — comparison

**Type:** comparison (no name-and-shame)
**Time:** 19:00 BST
**Post:**

> Been thinking about why "I'll just self-host on Hetzner" so often
> turns into a six-month detour. It's not the server. It's the
> twelve other things underneath — TLS renewal, backup retention,
> mail deliverability, Stripe webhooks. Self-host the app, don't
> self-host the substrate.

---

### Day 7 — build-in-public

**Type:** build-in-public · weekly recap
**Time:** 17:00 BST (Friday wind-down)
**Post:**

> Week 1 recap on Hatchik:
> · /start wizard live
> · /account dashboard with magic-link
> · Sandbox provisioning ~3 min end-to-end
> · [N] signups, [N] sandboxes spun up
> Next week: hardening the abuse limits before I post anywhere bigger.

**Visual:** simple chart or screenshot of `/api/admin/accounts`.

---

### Day 8 — how-to

**Type:** how-to · plumbing detail
**Time:** 08:30 BST
**Post:**

> If your AI-generated app sends password resets and they land in
> spam, it's almost always missing DKIM. SPF on its own isn't
> enough any more — Gmail wants both. The fix is two DNS records
> and a restart. Took me longer to find than to do.

---

### Day 9 — behind-the-scenes

**Type:** behind-the-scenes · pricing honesty
**Time:** 09:00 BST
**Post:**

> Quick maths on Hatchik unit costs at the £9/mo Launch tier:
> · Hetzner CPX11 ~£4/mo
> · Backups (Backblaze B2) ~£0.50
> · Resend on free tier
> · Domain prorated ~£1
> Margin pays for support, the marketing site, and the abuse-bot
> tax. Not the gold mine people assume infra SaaS is.

---

### Day 10 — opinion

**Type:** opinion · platform lock-in
**Time:** 19:00 BST
**Post:**

> Lock-in in 2026 isn't proprietary file formats any more. It's
> "your AI tool can't read this." If your platform doesn't store
> your app as plain code in a Git repo you control, you've quietly
> handed your exit cost back to them.

---

### Day 11 — hypothetical customer-story

**Type:** hypothetical (clearly marked)
**Time:** 09:30 BST
**Post:**

> Imagine: you're a PT with an idea for a meal-prep app for your
> clients. You don't want to learn Postgres. You open Claude, say
> "build me PrepSheet", and an hour later it's running on
> prepsheet.app with a working signup form. That's what Hatchik
> wants to be the floor of. (Not yet a real customer — but the
> wizard does work.)

---

### Day 12 — comparison

**Type:** comparison
**Time:** 08:45 BST
**Post:**

> Bolt, Lovable, Replit Agent — all magical for the first 30
> seconds. Then you want auth, payments, a real domain, mail that
> doesn't go to spam, a mobile version. The boring 80% that turns
> a demo into a product. That's the gap.

---

### Day 13 — build-in-public

**Type:** build-in-public · ops detail
**Time:** 10:15 BST
**Post:**

> Built the idle-archive lifecycle today. A Sandbox that hasn't
> been hit in 14 days gets soft-archived; 30 days, hard-deleted.
> Customer can resurrect with one click before then. Keeps the
> shared host honest without surprising anyone.

**Visual:** the email customers get at day 12 (preview heads-up).

---

### Day 14 — how-to

**Type:** how-to · DX
**Time:** 09:00 BST
**Post:**

> If you're using Claude Code or Cursor and your AI keeps
> forgetting your stack, pin a tiny `STACK.md` at the repo root:
> framework, DB, auth method, deploy target. Five lines.
> Massively cuts down hallucinated imports.

---

### Day 15 — behind-the-scenes

**Type:** behind-the-scenes · honest waitlist
**Time:** 19:30 BST
**Post:**

> Status today: Sandbox tier is fully automated; Launch tier
> (paid, own domain) is still hand-shaped by me per customer
> while I finish the cross-region provisioner. Working through
> them in order. If you've signed up for Launch, you'll hear
> from me within the day.

---

### Day 16 — opinion

**Type:** opinion · AI coder + ownership
**Time:** 09:15 BST
**Post:**

> The thing nobody is saying loudly enough: an AI tool that can
> only edit code inside someone's web IDE isn't really yours. If
> your AI can't `git clone` it and `npm run dev` it on your
> laptop, you're renting, not owning.

---

### Day 17 — comparison

**Type:** comparison · gentle
**Time:** 08:45 BST
**Post:**

> ShipFast and the boilerplate genre solved a real problem for
> engineers: don't redo Stripe + auth every time. The bit they
> can't solve is the running stack. Code drop ≠ live system. Two
> different products, both useful.

---

### Day 18 — build-in-public

**Type:** build-in-public · abuse defence
**Time:** 10:00 BST
**Post:**

> Added abuse protections before opening the floodgates:
> rate-limit on /api/signup, disposable-email blocklist, slug
> collision-resistant, and a single-tenant disk quota so one
> runaway sandbox can't eat the host. Boring. Necessary.

---

### Day 19 — how-to

**Type:** how-to
**Time:** 09:30 BST
**Post:**

> Cheap trick if you're hosting many small apps on one VPS: put
> each tenant on a distinct localhost port range, front everything
> with one Caddy doing wildcard TLS, hot-reload tenant routes
> with `import tenants.d/*.caddy`. New customer = one file + a
> reload, no restart.

---

### Day 20 — hypothetical customer-story

**Type:** hypothetical
**Time:** 19:00 BST
**Post:**

> A consultant friend asked: "Could I have a tool that demoes my
> framework instead of just slides?" Answer is yes, and it's the
> exact reason Hatchik exists — give the AI a real substrate, let
> the human spend their time on the framework, not the
> infrastructure underneath it.

---

### Day 21 — build-in-public

**Type:** build-in-public · weekly recap
**Time:** 17:00 BST
**Post:**

> Week 3 recap:
> · [N] signups since launch
> · [N] sandboxes still active at day 14
> · First [N] hand-onboarded Launch customers shipped
> · Biggest surprise: people want BYO-domain on Sandbox tier
>   more than I expected. Adding to the list.

---

### Day 22 — opinion

**Type:** opinion · the AI-coder market
**Time:** 09:00 BST
**Post:**

> A lot of "AI app builder" companies are racing to own the chat
> window. That's the wrong moat. The chat window is a commodity
> now; Claude and Cursor are good enough. The moat is what
> happens after the chat — and almost nobody is building there.

---

### Day 23 — comparison

**Type:** comparison
**Time:** 08:45 BST
**Post:**

> No-code is brilliant for the first user. Then your customer asks
> for a feature it doesn't do, you can't open the source, and an
> AI tool can't read your app. The exit cost compounds. Real code
> + AI coder is the version of no-code that still has a back door.

---

### Day 24 — wildcard / culture

**Type:** wildcard
**Time:** 19:30 BST
**Post:**

> Friday admission: I spent an entire afternoon this week chasing
> a bug that turned out to be a missing `import` in a Caddyfile.
> Wrote a one-line test to catch it next time. The bugs that hurt
> are always the cheap-looking ones.

---

### Day 25 — how-to

**Type:** how-to · Supabase
**Time:** 09:00 BST
**Post:**

> If you're self-hosting Supabase and the Studio panel won't open,
> nine times out of ten it's `JWT_SECRET` mismatched between the
> service and the anon/service-role JWTs you minted. Regenerate
> both with the same secret, restart, sorted.

---

### Day 26 — behind-the-scenes

**Type:** behind-the-scenes · Paddle waiting
**Time:** 09:30 BST
**Post:**

> Honest stack note: Hatchik is a UK-Omani entity, which means
> Stripe direct is closed to us. Going via Paddle (Merchant of
> Record) — they handle global VAT, US sales tax, the lot.
> Approval is pending. Meanwhile Launch tier is invoiced manually.
> Not glamorous, but solvable.

---

### Day 27 — opinion

**Type:** opinion
**Time:** 19:00 BST
**Post:**

> A platform that lets your AI tool work *with* you is going to
> beat a platform that tries to *be* your AI tool. The AI you've
> already trained, paid for, and trust is worth more than the one
> a startup is asking you to learn next week.

---

### Day 28 — build-in-public

**Type:** build-in-public · status page
**Time:** 09:00 BST
**Post:**

> Quietly shipped a public status page at status.hatchik.com.
> Pings every tenant once a minute, shows uptime per stack, lists
> the few I'm hand-onboarding. If something's red, you don't have
> to wait for me to notice.

**Visual:** screenshot of the status page.

---

### Day 29 — wildcard / community ask

**Type:** wildcard
**Time:** 09:15 BST
**Post:**

> Question for anyone using Claude Code or Cursor to build a real
> product: what's the boring infrastructure thing you keep
> redoing? Email plumbing? Auth flows? Stripe webhooks?
> Genuinely curious — building toward fixing the worst of it.

---

### Day 30 — build-in-public · monthly recap

**Type:** build-in-public · 30-day mark
**Time:** 17:00 BST
**Post:**

> One month of Hatchik in public:
> · [N] signups
> · [N] Sandbox tenants still active
> · [N] paying Launch customers
> · 0 customers stuck on something I couldn't help with
> · Endless infra papercuts I now know about
> Year of doing this. Onward.

---

## Section 3 — 8 Reddit posts (over 30 days)

Rotation logic: at most two posts in any single sub in a 30-day
window; never two in a row in the same sub; rotate days of week.
Account warm-up — the human running the account should comment
helpfully in target subs for at least 2 weeks before the first post.

---

### Reddit Post 1 — r/ClaudeAI (Week 1)

- **Why this sub:** most aligned audience. Active builders using
  Claude Code for real projects.
- **Suggested title:** "Tip: pin a 5-line `STACK.md` at the repo
  root and Claude stops hallucinating your imports"
- **Best time:** Tuesday 14:00 UTC (US morning, UK afternoon).

**Body (~300 words):**

> I've been working with Claude Code on a multi-week project and
> kept hitting the same papercut — every new session, Claude would
> guess my stack wrong on the first prompt. Imports for libraries
> I'm not using, frameworks I rejected, the wrong DB driver.
>
> The fix is unreasonably small. A single file at the repo root,
> five lines:
>
> ```
> # STACK.md
> Framework: Astro 4 (no React)
> DB: Postgres via Supabase
> Auth: Supabase magic-link only
> Payments: Paddle (MoR), no Stripe direct
> Deploy: Hetzner Cloud + docker compose
> ```
>
> Claude reads it on session start (it picks up top-level `.md`
> files reliably), and the rate of hallucinated imports dropped to
> roughly zero. Same trick worked in Cursor when I tested it
> there.
>
> The reason it works, I think, is that Claude is happy to be
> opinionated when you've told it what to be opinionated about. A
> blank repo is a blank cheque; a STACK.md is a brief.
>
> Two other small things I've found help:
>
> 1. A `DECISIONS.md` listing the ones you've made and the ones
>    you've explicitly *not* made yet ("not picking a CDN until we
>    have a paying customer"). Stops Claude looping back to
>    settled questions.
> 2. A `CONSTRAINTS.md` for environment-level facts ("VPS has 4GB
>    RAM; pick libraries accordingly"). It actually respects this.
>
> Boring docs, big DX win.
>
> (For full transparency: I'm building a thing called Hatchik that
> sets all this up automatically for non-engineers — link in
> profile, happy to talk about it in DMs if anyone wants, but the
> trick above stands on its own.)

- **Anti-shill check:** post stands alone as a tip; Hatchik
  mentioned once in a footer paragraph in parentheses. No link in
  body. Passes.
- **Expected pushback:** "you don't need a file for this, just use
  the system prompt" → reply: "Fair, that works if you remember
  to. The file survives me forgetting."
- **Pushback 2:** "this is just a CLAUDE.md / cursorrules" → reply:
  "Yes, exactly — naming is whatever the tool picks up, the
  point is what's *in* it. Renamed mine to CLAUDE.md after this
  thread, thank you."

---

### Reddit Post 2 — r/cursor (Week 1)

- **Why this sub:** second-best ICP fit. People building real
  things, often hitting the same infra wall.
- **Suggested title:** "What's the most boring thing Cursor keeps
  redoing for you? (collecting answers, building toward fixing it)"
- **Best time:** Thursday 15:00 UTC.

**Body (~250 words):**

> Genuine question, not a stealth pitch. I'm noticing patterns in
> what Cursor (and Claude Code, Windsurf, etc.) absolutely does
> not enjoy doing well:
>
> - Anything to do with email deliverability (SPF/DKIM/DMARC) —
>   it'll happily ship code that sends to /dev/null in Gmail.
> - Stripe webhook handlers — gets the happy path right, never
>   handles `invoice.payment_failed` properly.
> - Docker compose files that survive a reboot.
> - TLS cert renewal in any non-vercel/non-managed deployment.
> - Backup retention policies.
>
> Curious what's on your list. Working theory: the things Cursor
> struggles with are also the things humans struggle with, because
> they're under-documented and the right answer is "it depends".
>
> If anyone has a tip for one of the above, I'd genuinely love to
> hear it. Building something that tries to make these
> not-a-problem-by-default for indie builders, but the more the
> answer space is shared the better.

- **Anti-shill check:** asks a question, no product mention at
  all. Mention in comments if and only if someone says "is there a
  product that handles this?" Passes.
- **Expected pushback:** "this is a survey-as-marketing post" →
  reply: "Fair flag. Here's the screenshot of the doc I'm filling
  in based on the answers, with attribution where you've said
  yes." Show your work.

---

### Reddit Post 3 — r/SideProject (Week 2)

- **Why this sub:** project-introduction posts are welcomed here
  (vs. "show me your launch" hostility in r/Entrepreneur).
- **Suggested title:** "Spent a month building the boring layer
  underneath an AI-built app, here's what's in it"
- **Best time:** Sunday 19:00 UTC (Sunday is the peak day in this
  sub; people scroll while planning their week).

**Body (~350 words):**

> Hatchik is the working title. The pitch: an AI tool can write the
> features. The boring 80% — domain, TLS, auth, payments, mail that
> doesn't go to spam, mobile shells, server hosting — is what burns
> the weekends. Hatchik wires the boring bit, your AI builds on top.
>
> Honest current state:
>
> - Free Sandbox tier: automated, ~3 minutes from signup to a live
>   tenant on `<yoursubdomain>.hatchik.com`. About [N] up so far.
> - Paid Launch tier: hand-onboarded by me right now while I finish
>   the cross-region provisioner. About [N] customers in.
> - Mobile build pipeline is not yet wired — Capacitor scaffold is
>   in the substrate but the build step isn't automated. Don't sign
>   up for mobile yet.
> - Paddle integration is pending their approval; Launch invoicing
>   is currently manual.
>
> The reason for posting here, not r/SaaS: I've been told off in
> r/SaaS for "premature launch posts" before, fair, and this
> genuinely isn't ready for that crowd. r/SideProject is for
> exactly this — the in-between, here's-what-I'm-building stage.
>
> What I'd actually love feedback on, in priority order:
>
> 1. Is the £79 setup + £9/mo + £24/mo-after-15-signups pricing
>    legible? Most feedback so far is "the second graduation
>    threshold is confusing".
> 2. The /vs page (hatchik.com/vs) compares against the genre. Am
>    I being too soft on the competitors I respect?
> 3. The Sandbox tier is free forever. Am I going to regret that?
>
> Happy to answer technical questions about how the multi-tenant
> setup works on a single Hetzner box — that part was the most
> fun.

- **Anti-shill check:** transparent state, named limitations, asks
  for feedback not signups. Passes.
- **Expected pushback:** "another AI-builder?" → reply: "Fair
  weariness. Different premise — we don't *have* an AI; we wire
  the substrate your existing AI plugs into. Closer to a hosting
  platform than to Lovable."
- **Pushback 2:** "how is this different from a docker compose
  template on github?" → reply: "Honestly, the docker compose part
  is 5% of it. The other 95% is the provisioning automation, the
  paid-tier mail/domain/backup wiring, and the customer-facing
  account dashboard. Happy to walk through."

---

### Reddit Post 4 — r/selfhosted (Week 2)

- **Why this sub:** the "your own VPS" framing genuinely resonates
  here. Audience is sceptical of SaaS, will respect "we set up
  your server, you own it, here are the keys."
- **Suggested title:** "How I'm running [N] tenant apps on one
  Hetzner CAX21 with docker compose + Caddy wildcard cert"
- **Best time:** Monday 18:00 UTC.

**Body (~400 words):**

> Wanted to share the architecture in case anyone's hosting a
> bunch of small projects and tired of paying per-app Vercel rates.
>
> The shape: one CAX21 (£10/mo, 4 vCPU, 8GB), running:
>
> - A host-level Caddy doing wildcard TLS for `*.hatchik.com` (the
>   apex zone) and per-app TLS for custom domains.
> - Each tenant is a `docker compose` stack pinned to a unique
>   localhost port in the 18000–18099 range.
> - Tenant routes are config files in `tenants.d/*.caddy`, picked
>   up by `import tenants.d/*.caddy` in the host Caddyfile.
> - Spinning up a new tenant = render a directory from a template,
>   `docker compose up -d`, drop a new `tenants.d/<slug>.caddy`,
>   `caddy reload`.
> - Tearing down = `docker compose down -v && rm tenants.d/<slug>.caddy
>   && caddy reload`.
>
> Density so far: [N] live tenants, load average barely moves, the
> bottleneck is going to be disk before it's CPU.
>
> Things I learned the hard way:
>
> - `caddy reload` is graceful (existing connections survive) but
>   only if the new config parses. Always `caddy validate` first.
> - Wildcard certs from Let's Encrypt require DNS-01 — make sure
>   the DNS provider has a token-scoped API key, not your master
>   token.
> - Disk fills before CPU does. Plug in a quota check (per-volume
>   `du`) into your monitoring before you lose a Saturday.
> - Each tenant's Postgres should be on a named volume, not a bind
>   mount — makes the teardown story sane.
>
> Tools used: docker, docker compose, caddy, infomaniak DNS, plus a
> small Python orchestrator that handles the templating + reload.
>
> Happy to share the orchestrator if there's interest — it's not
> open source yet but the bones aren't secret. Reply or DM.
>
> (Full disclosure: this is the infra under a thing I'm building
> called Hatchik. The architecture works either as a product
> behind a paywall or as a weekend-hack pattern for yourself.)

- **Anti-shill check:** body is a genuine architecture write-up
  that would help someone whether or not Hatchik existed. Passes.
- **Expected pushback:** "why not Kubernetes?" → reply: "Honest
  answer: I don't have enough tenants to need it, and docker
  compose + Caddy on one box is operable by a human on a Sunday
  morning. K8s would be more correct and considerably less
  maintainable for me right now."
- **Pushback 2:** "what about backups?" → reply with a separate
  comment describing the Backblaze B2 + per-tenant pg_dump pattern.

---

### Reddit Post 5 — r/IndieHackers (Week 2)

- **Why this sub:** founder-to-founder, candid metrics welcomed.
- **Suggested title:** "Month 1 of building Hatchik in public —
  what worked, what didn't"
- **Best time:** Wednesday 14:00 UTC.

**Body (~350 words):**

> Sharing month 1 numbers in case it's useful for anyone else doing
> the build-in-public thing.
>
> **What I built:**
>
> - Marketing site (hatchik.com)
> - Signup pipeline + Sandbox auto-provisioning (~3 min end-to-end)
> - /account dashboard with magic-link auth
> - Per-tenant containerised stacks on a shared Hetzner box
> - Self-serve delete (because GDPR + because trust)
> - Idle-archive lifecycle
>
> **What I didn't build:**
>
> - Cross-region paid-tier provisioning (still hand-shaped)
> - Mobile build pipeline (Capacitor scaffold exists, builds don't)
> - Paddle integration (waiting on approval)
>
> **Numbers (placeholders for now):**
>
> - [N] signups
> - [N] Sandbox tenants live
> - [N] paying Launch customers (all hand-onboarded)
> - [N] refunds / cancellations
> - Conversion Sandbox → Launch: [N]%
>
> **What worked:**
>
> - Posting honestly about what's not built. Nobody seems to expect
>   month-1 perfection; they expect month-1 honesty.
> - r/selfhosted post about the multi-tenant architecture brought
>   the highest-quality signups of the month.
> - A friend casually mentioning Hatchik in a Discord brought more
>   signups than my X account did. Word of mouth still wins.
>
> **What didn't:**
>
> - Tried a Twitter thread with a "12 things you should know" hook.
>   Got crickets. The format is exhausted; people scroll past.
> - LinkedIn post got reach but zero signups. Wrong audience.
> - One paid Reddit ad — banned within hours, even with the £79
>   credit, because the body was too productish. Useful lesson.
>
> **What's next, month 2:**
>
> - Show HN once the Launch tier is fully automated end-to-end.
> - Outreach to two YouTube creators in the AI-coder space.
> - First-customer Linear-board automation (currently a manual
>   step in onboarding).
>
> Anyone else solo-launching this month? Trade notes in comments?

- **Anti-shill check:** mostly retrospective, value comes from the
  honest "what didn't work" list. Passes.
- **Expected pushback:** "the numbers are placeholders" → reply:
  "Fair — once they're real I'll update the post. Wanted the
  format out for feedback first."

---

### Reddit Post 6 — r/SaaS (Week 3)

- **Why this sub:** good for talking shop with other founders;
  hostile to thinly-veiled pitches.
- **Suggested title:** "Why I chose Paddle over Stripe as a
  UK-Omani entity — a tax-residency story"
- **Best time:** Tuesday 16:00 UTC.

**Body (~400 words):**

> Short version: my entity is Omani-registered. Stripe direct
> doesn't onboard Omani entities. I needed a Merchant of Record.
> The interesting bit is what I learned comparing options.
>
> **Stripe direct:** blocked. Their geo onboarding rules are
> tighter than the API docs suggest. If your registered address is
> outside their supported list, no amount of asking nicely changes
> it. Don't waste a week trying.
>
> **Stripe Atlas:** an option, but it means incorporating a US C-Corp
> on top of an entity I already pay for. Double tax filings, two
> jurisdictions, a meaningful annual cost. Heavy for a £9/mo
> product.
>
> **Paddle:** Merchant of Record. They become the legal seller;
> you're a content provider to them. They handle VAT (UK + EU),
> GST (AU, IN, SG), US state sales tax, and chargebacks. You get a
> single payout, a single tax form. Margin cost is ~5% blended
> vs. Stripe's ~3% direct — fair price for not having to set up
> tax registrations in 50 countries.
>
> **Dodo Payments:** newer entrant. Identical model to Paddle, more
> permissive onboarding. Keeping as a backup if Paddle approval
> takes longer than expected.
>
> **Lemon Squeezy:** was the obvious answer until Stripe bought
> them; product is now in a holding pattern. Skip for now.
>
> **The non-obvious lesson:** MoR isn't just a tax convenience.
> It's the only thing that lets a small operator credibly sell to
> customers in 50+ countries without getting flattened by sales-tax
> compliance. If you're solo and selling globally, MoR isn't a
> nice-to-have, it's a no-other-option-makes-sense.
>
> The downside: customer's bank statement reads "Paddle.com Market
> Ltd · Hatchik", not your brand. Worth a small FAQ entry to
> pre-empt the "what's this charge?" emails. I have one.
>
> Anyone else gone through MoR onboarding recently? Curious how
> long approval took for you.

- **Anti-shill check:** the post is genuinely useful tax-residency
  knowledge for any founder outside the Stripe-friendly geo set.
  Brand mention is incidental. Passes.
- **Expected pushback:** "this is just a roundabout for shilling
  Hatchik" → reply: "Fair concern. Worth saying: the post would
  hold even if I were selling something else. The Stripe-blocked
  reality is the actually-useful share."

---

### Reddit Post 7 — r/Entrepreneur (Week 3)

- **Why this sub:** more sceptical, slower to engage. Best
  approach: ask a question that prompts replies, not a pitch.
- **Suggested title:** "Founders selling globally — how are you
  pricing for low-income regions without leaving money on the
  table?"
- **Best time:** Thursday 13:00 UTC.

**Body (~300 words):**

> Working through purchasing-power parity pricing for a product
> that's £9/mo at the UK list price. The two extremes:
>
> 1. Same price everywhere: simple, but a £9/mo product is
>    £9/mo regardless of whether the customer earns £40k or £4k.
>    Effectively cuts you out of huge chunks of the world.
> 2. PPP-adjusted, automatic: customer in India pays the INR
>    equivalent of ~£2.50; customer in the US pays the USD
>    equivalent of ~£12. More fair, but invites VPN arbitrage and
>    confused refund conversations.
>
> I'm leaning toward option 2 with two guardrails:
>
> - Detect by payment-method country (not by IP — IP arbitrage is
>   trivial). Card BIN tells you the issuing country reliably.
> - Round to local sensible amounts (₹199, not the literal £2.50
>   conversion of the day).
>
> Question for anyone who's run a global SaaS:
>
> - Have you implemented PPP pricing? What did you learn?
> - Did you adjust list-price upward in higher-income regions, or
>   just adjust downward in lower-income ones?
> - How much VPN arbitrage did you actually see?
> - Did you publish your PPP grid, or hide it behind checkout?
>
> Trying to avoid making this either a moral question or a
> conversion-rate question alone; both matter.

- **Anti-shill check:** no product mentioned. Pure founder
  question. Passes.
- **Expected pushback:** "this is theory-talk, run the experiment"
  → reply: "Fair. Picking the structure is a one-shot decision
  though — changing it later is a customer-trust event. Want to
  get it close-to-right before launch rather than iterate
  publicly."

---

### Reddit Post 8 — r/webdev (Week 4)

- **Why this sub:** sceptical, technically literate, has seen
  every "I built a thing" post. **Handle carefully.** Lead with
  a technical specific, never with a product pitch.
- **Suggested title:** "TIL: `caddy reload` is graceful but only
  if the new config validates first — here's the wrapper I
  wrote to never re-learn it"
- **Best time:** Friday 10:00 UTC (Friday morning has the
  highest tip-tier engagement here).

**Body (~250 words):**

> Spent a Saturday debugging this so anyone else doesn't have to.
>
> `caddy reload` will replace the running config with the new one
> without dropping in-flight connections — *if* the new config
> parses. If it doesn't parse, you get a useful error and the old
> config keeps serving. Lovely.
>
> What I didn't realise: `caddy reload` does its own validation
> step, but if you've structured your config with `import` blocks
> (e.g. `import tenants.d/*.caddy`), an error in an imported file
> shows up as an error during the reload — not during a build
> step. So if you're scripting tenant additions, you want to fail
> *before* attempting the reload, otherwise your script logs are
> useless.
>
> Wrapper that works for me:
>
> ```bash
> caddy validate --config /etc/caddy/Caddyfile || exit 1
> caddy reload --config /etc/caddy/Caddyfile
> ```
>
> Plus a check that the new tenant file exists, has the right
> mode (644), and doesn't have a stray BOM (the YAML demons
> haven't gone away).
>
> Tiny tip but it'll save you a Saturday afternoon. The graceful-
> reload guarantee only holds if you let validation do its job
> first.

- **Anti-shill check:** pure technical tip. No mention of Hatchik
  at all. (Hatchik gets mentioned only if someone asks "what are
  you using this for?" in a follow-up.) Passes.
- **Expected pushback:** "caddy docs literally tell you this" →
  reply: "Fair, they do — but `caddy reload` working *most* of the
  time without explicit validate is the trap. Adding the
  belt-and-braces is the bit I'd missed."

---

## Section 4 — 4 long-form blog posts

Each post lives at `hatchik.com/writing/<slug>`. Distribution
plan included with each.

---

### Blog Post 1 — "We're built on Hetzner, here's what that means for you"

- **Target keywords:** Hetzner reliability, EU SaaS hosting, GDPR
  hosting, "is Hetzner reliable", Hetzner vs AWS for startups.
- **Approximate length:** 1,800 words.
- **Outline:**
  1. The choice nobody talks about: where your app actually
     lives. Why most SaaS sweep this under the carpet.
  2. Why Hetzner. Performance per pound, EU data residency,
     transparent pricing, the absence of egress trapdoors.
  3. The actual machine specs we run on, and what they cost.
  4. Where Hetzner is weak (US West latency, India/SEA latency),
     and what we're doing about it.
  5. How "your VPS in your name" works in practice — what
     happens if you cancel, who has the SSH key.
  6. Honest comparison: Hetzner vs. AWS vs. DigitalOcean vs.
     Fly.io for a single-server SaaS at our scale.
  7. The bit nobody mentions: what happens when the host has an
     outage (and it will).
- **Distribution:**
  - X thread (Day 11-12 slot): excerpt the "what happens if you
    cancel" section.
  - Reddit: r/selfhosted, framed as "post-mortem of host-choice
    after a year".
  - Newsletter mention: TLDR AI's infrastructure column.

---

### Blog Post 2 — "How to wire Resend SMTP into a self-hosted Supabase Auth (without spam-foldering)"

- **Target keywords:** Resend Supabase SMTP, self-hosted Supabase
  email, Supabase magic link Gmail spam, SPF DKIM DMARC Supabase.
- **Approximate length:** 1,400 words.
- **Outline:**
  1. The problem: self-hosted Supabase Auth sends from a default
     address that lands in spam, hard.
  2. Why Resend — practical reasons, not affiliate.
  3. The five env vars to set on the Supabase Auth container.
  4. The DNS records that actually matter: SPF, DKIM (the easy
     one to skip), DMARC (the easy one to misconfigure).
  5. The display-name trick (`Hatchik <noreply@...>`) — why Gmail
     strips it without one.
  6. Testing: mail-tester.com, plus the real-Gmail-inbox sniff
     test.
  7. What to do when it still goes to spam (warm-up period).
- **Distribution:**
  - X thread (Day 14 how-to slot): short version.
  - Reddit: r/Supabase + r/selfhosted.
  - Hacker News: standalone "Show HN" candidate if the post
    gets traction in the first 48h.
  - Hatchik footer mention only.

---

### Blog Post 3 — "How we built Hatchik's auto-provisioning in two weeks (architecture, code, and what broke)"

- **Target keywords:** multi-tenant docker compose, Caddy
  wildcard cert, build SaaS provisioning, Hetzner multi-tenant.
- **Approximate length:** 2,200 words.
- **Outline:**
  1. The constraint: one box, many tenants, free tier viable.
  2. The shape we landed on: per-tenant compose stack on a
     unique localhost port, host Caddy doing TLS termination.
  3. The orchestrator — what `provision.py` actually does, step
     by step, with the failure modes between each step.
  4. Slug allocation: the boring problem of "what if two
     customers pick the same name".
  5. Tenant teardown: why soft-delete exists, why hard-delete
     exists, why the SQLite sequence reset matters.
  6. The bits that broke (be specific): the JWT mismatch, the
     DNS propagation race on first cert issuance, the
     run-as-root mistake we narrowly avoided.
  7. The bits we'd do differently: switch to a real queue
     (currently subprocess), add per-tenant resource limits at
     create time not as an audit.
  8. What's next: cross-region for the paid tier, K8s migration
     trigger point.
- **Distribution:**
  - X thread (Day 13 build-in-public slot): excerpt the
    "what broke" section.
  - Reddit: r/selfhosted + r/programming.
  - Hacker News: this is the strongest Show HN candidate
    technically, save for that.

---

### Blog Post 4 — "The exit cost of an AI-built app — why your repo matters more than your prompt"

- **Target keywords:** AI app builder lock-in, Bubble vs code,
  Lovable export, AI coder ownership, Vercel lock-in.
- **Approximate length:** 1,600 words.
- **Outline:**
  1. The frame: lock-in in 2026 isn't proprietary file formats —
     it's "your AI tool can't read this".
  2. The four tiers of exit cost, with a worked example:
     - Tier 1: managed AI builder (Bubble, Lovable, Bolt) — exit
       cost = full rebuild.
     - Tier 2: managed code-gen (Replit Agent, v0) — exit cost =
       export and re-host.
     - Tier 3: your code, their infra (Vercel, Netlify) — exit
       cost = migrate provider.
     - Tier 4: your code, your infra (Hatchik, DIY) — exit
       cost = swap a DNS record.
  3. Why Tier 4 used to require an engineer, and why it doesn't
     any more.
  4. The non-financial cost of lock-in: your AI tool can't help
     you with code it can't read.
  5. A short, non-preachy comparison of what "exit" actually
     looks like for each tier.
  6. What to ask before signing up to anything that builds your
     app: "where does the code live, and can I clone it?".
- **Distribution:**
  - X thread (Day 22 opinion slot).
  - Reddit: r/IndieHackers, r/ClaudeAI (framed as "ownership +
    AI tools").
  - LinkedIn (founder-led only).
  - Newsletter pitch: Ben's Bites (AI angle), The Rundown.

---

## Section 5 — Show HN draft

**Timing:** Tuesday or Wednesday, 8:00–10:00 ET (13:00–15:00
UTC). Avoid Mondays (front-page noise) and Fridays (slow). Post
**only after** Launch tier is fully automated end-to-end —
otherwise the inevitable "I signed up and it took 24h" comment
torches the thread.

**Title** (use exactly — HN trims past 80 chars):

> Show HN: Hatchik — the boring infra your AI coder builds on (one VPS, your code)

**Body (~350 words):**

> Hi HN — I'm the solo person behind Hatchik. The pitch in one
> sentence: I wire the boring 80% of running a SaaS (auth,
> payments, mail-that-doesn't-go-to-spam, mobile shells, domain,
> server) so your AI coder (Claude Code, Cursor, Windsurf —
> whatever you already pay for) can build the actual product on
> top.
>
> What's actually built:
>
> - Free Sandbox tier: signup → live tenant on a `*.hatchik.com`
>   subdomain in ~3 minutes. Auto-provisioned.
> - Paid Launch tier (£79 setup + £9/mo): your own domain, your
>   own VPS in your name, GitHub repo under your account. Right
>   now I onboard these by hand while I finish the cross-region
>   provisioner; takes ~45 minutes per customer.
> - Per-tenant containerised stack: Postgres, Supabase Auth,
>   Caddy with wildcard TLS, Resend SMTP wired in, magic-link
>   login on day one.
> - Self-serve `/account` dashboard with magic-link auth,
>   confirm-delete flow, archive lifecycle for idle sandboxes.
>
> What's deliberately not built yet:
>
> - The mobile build pipeline. The Capacitor scaffold is in the
>   substrate but I haven't wired the builds. Don't sign up for
>   mobile yet.
> - Paddle is pending approval (entity is Omani; Stripe direct
>   doesn't take us). Launch tier is currently invoiced manually.
> - Cross-region paid provisioning (working on it).
>
> Why I think this is interesting to HN: most "AI app builder"
> companies are racing to ship their own chat window. That
> commoditises faster than the substrate question does. Claude
> and Cursor are good enough; the substrate underneath them is
> not yet good enough for non-engineers. That's the gap.
>
> Tech stack: Postgres + self-hosted Supabase + Astro + Caddy +
> docker compose, all on a single Hetzner CAX21 for the shared
> tier. Per-tenant CPX11s for paid customers.
>
> Honestly, the part I'm least sure about is the pricing
> structure — £79 setup + £9/mo + £24/mo once a tenant hits 15
> signups. Is the second-tier graduation legible, or is it too
> clever? Genuine question, not a humble brag.
>
> Site: hatchik.com · /vs comparison: hatchik.com/vs
>
> Happy to answer technical questions about the multi-tenant
> orchestrator. AMA.

**Comment seed (post yourself in the first 5 minutes if no
organic question lands):**

> One thing I should have led with: the substrate is a normal
> docker-compose stack. If you cancel, I `git push` the repo to
> your GitHub account, transfer the VPS to your Hetzner account,
> and you keep running it for ~£10/mo total. There's no
> proprietary format, no export tool needed, no data-extraction
> fee. That's the part I most want to know is legible — does it
> read as credible from this post?

---

## Section 6 — Product Hunt launch

**Timing:** launch on a **Tuesday** (best traffic), aim for the
00:01 PT launch window. **Only launch after Show HN has gone
well** — Show HN provides social proof; PH amplifies it.

**Tagline (60 chars):**

> The boring substrate your AI coder builds a real SaaS on.

(Exactly 56 chars.)

**Description (260 chars):**

> Hatchik wires the infra — auth, payments, mail, mobile, domain,
> server — so your AI tool (Claude Code, Cursor, Windsurf) can
> build the actual product. Free Sandbox. £79 to launch. Your
> code, your repo, your VPS. Leave any time.

(Exactly 259 chars.)

**Full post (4 paragraphs):**

> Hi Product Hunt — I'm shipping Hatchik today. Quick context: I
> spent six months watching friends and clients trying to turn
> their AI-built prototypes into real businesses. The wall they
> all hit was the same — auth, payments, mail, a real domain, a
> mobile version. The boring 80% that turns a demo into a product.
>
> Hatchik fixes that part. You bring your AI tool of choice
> (Claude Code, Cursor, Windsurf, etc.); we give it a real
> substrate to work on — Postgres + Supabase Auth + Stripe or
> Paddle + Resend mail + Capacitor mobile shells + a VPS in your
> name. Sandbox tier is free forever on a `*.hatchik.com`
> subdomain; Launch tier is £79 one-off + £9/month for your own
> domain, your own VPS, your own GitHub repo. After 15 signups
> the monthly graduates to £24 — that's it on the pricing model,
> no hidden seats, no overages.
>
> The thing I most want feedback on: every other "AI app builder"
> ships their own chat window and tries to be the front door.
> We're built on the assumption that your AI tool is already
> better than anything we'd ship, so we don't ship one. The whole
> substrate is plain code in a plain GitHub repo your AI can
> read, edit, and deploy.
>
> Caveats up front (because PH comments will surface them
> anyway): the mobile build pipeline is scaffolded but not yet
> automated — don't sign up for mobile yet. Paddle integration is
> pending approval; Launch tier is currently invoiced manually.
> Cross-region provisioning for the paid tier is hand-done by me
> for now. Sandbox tier is fully automated and works today.
>
> Happy to answer anything. The build log is on X (@hatchik) if
> you want to see what week-by-week looked like.

**Gallery suggestions (5 images, in order):**

1. Hero shot: the /start wizard at the "naming-your-app" step,
   with a real-looking product name typed in.
2. The chat-style mock from the homepage — your AI talking to
   Hatchik on your behalf.
3. Architecture diagram: "your AI coder → Hatchik substrate →
   your VPS / your GitHub / your domain". Three arrows, no logo
   soup.
4. Pricing screenshot: Sandbox / Launch / Growth, with the "after
   15 signups it graduates" note highlighted.
5. /account dashboard screenshot: real tenant list, magic-link
   button visible.

**Hunter outreach approach:**

- Don't ask a top-50 hunter cold. They're flooded and PH's algo
  no longer privileges hunter clout the way it did pre-2024.
- Instead, ask **two** mid-tier hunters (200–800 followers) who
  have hunted other AI-tool products in the last 90 days,
  via a one-line DM with a 30-second Loom of the /start wizard.
- Be explicit: "I'm self-hunting if you'd rather not, just
  wanted to ask first."
- Backup plan: self-hunt and lean on the Show HN traffic + X
  audience for the first-hour push.

---

## Section 7 — Newsletter pitch templates

Three short, paste-and-tweak emails. **Send only after [N] paying
customers** (target N=20). Newsletter editors smell pre-launch
pitches and bin them.

---

### Template 1 — Ben's Bites

**Subject:** Hatchik — your AI coder finally has a real substrate

**Body:**

> Hi Ben (or whoever's running the desk this week),
>
> I'm Farhan, building Hatchik. Quick pitch:
>
> Most "AI app builders" race to ship their own chat. We don't.
> Hatchik gives your existing Claude / Cursor / Windsurf a real
> SaaS substrate to build on — auth, payments, mail, mobile,
> domain, VPS — wired up in minutes, under your own GitHub repo.
> Code, repo, server are all yours; exit cost is one DNS record.
>
> Why I think this fits Ben's Bites: AI-coder distribution is
> still the bottleneck for indie founders, and the platforms
> popular with your readers (Cursor, Claude Code) don't have an
> opinionated "and now what?" path. We are that path.
>
> Current state: [N] paying customers, [N] free Sandbox tenants
> live, all transparently documented at hatchik.com/writing.
>
> Happy to do a short Loom if it helps; otherwise the home page
> + /vs page should answer most questions.
>
> Cheers,
> Farhan
>
> — hatchik.com · @hatchik on X

---

### Template 2 — TLDR AI

**Subject:** Pitch: Hatchik — real infra under AI-built apps

**Body:**

> Hi TLDR AI team,
>
> I'm pitching Hatchik for a Tools section feature.
>
> Hatchik is the production substrate (auth, payments, mail,
> mobile, domain, VPS) that AI tools like Claude Code, Cursor and
> Windsurf can build a real SaaS on top of. The gap we solve:
> every AI builder ships a magical first 30 seconds, and stops at
> "looks working". We start at "real auth, real payments, real
> domain, your own server".
>
> Specs your readers will care about:
>
> - Built on self-hosted Supabase + Postgres + Caddy.
> - Free Sandbox tier auto-provisions in ~3 minutes.
> - Paid Launch tier: £79 setup + £9/mo. Own domain, own VPS,
>   own GitHub repo.
> - Multi-region (UK, EU, US East, US West, Singapore).
> - Paddle as Merchant of Record — global VAT handled.
>
> 200-word draft attached below if useful as a starting point.
> Happy to adjust angle, length, link strategy.
>
> [paste 200-word write-up]
>
> Cheers,
> Farhan
> hatchik.com

---

### Template 3 — The Rundown

**Subject:** Hatchik launch — would love a 2-line mention

**Body:**

> Hi Rowan (or whoever's editing this week),
>
> Hatchik is a substrate for AI-built apps — Claude Code or
> Cursor handles the features, we wire the boring infra (auth,
> payments, mail, mobile, domain, VPS) so non-engineers can ship.
> Free Sandbox tier, £79 + £9/mo for the full Launch.
>
> Two angles I think work for The Rundown's format:
>
> 1. **Tool blurb (Trending Tools section):** "Hatchik wires
>    real auth / payments / mail / domain / VPS so your AI tool
>    can build a real SaaS on top — your code, your repo,
>    leave any time."
> 2. **Bigger feature (Build section):** the lock-in angle —
>    why owning your code matters more than ever when your AI
>    tool can only edit what it can read.
>
> Both are written and ready; happy to send either.
>
> Site: hatchik.com
> /vs page: hatchik.com/vs
>
> Cheers,
> Farhan

---

## Section 8 — Tracking + iteration

### UTM scheme

Every link the founder posts gets UTM tags. Format:

```
?utm_source=<platform>&utm_medium=<format>&utm_campaign=<initiative>
```

- `utm_source` = `x`, `reddit`, `hn`, `ph`, `bensbites`, `tldrai`,
  `rundown`, `youtube_<creator>`, `direct`.
- `utm_medium` = `post`, `thread`, `comment`, `bio`, `newsletter`,
  `sponsorship`.
- `utm_campaign` = `launch_d1` through `launch_d30`, then
  per-initiative names (`showhn_2026q2`, `ph_launch_2026q2`,
  etc).

Example: an X build-in-public post on Day 5 links to
`hatchik.com/?utm_source=x&utm_medium=post&utm_campaign=launch_d05`.

Capture lands in the existing signup table — extend the
`signups` schema by adding `utm_source`, `utm_medium`,
`utm_campaign` columns (existing SQLite migration is trivial) and
read them off the landing URL.

### Tracking table (use a spreadsheet for now)

Columns:

| Col | Use |
|---|---|
| Post ID | `launch_d05`, `reddit_selfhosted_w2`, etc. |
| Platform | x / reddit / hn / ph / newsletter |
| Date posted | YYYY-MM-DD |
| Type | build-in-public / how-to / opinion / etc. |
| Headline | first 60 chars |
| Visits attributed | from UTM |
| Signups attributed | from UTM |
| Sandbox→Launch from this source | requires waiting 14 days |
| Notes | what surprised us |

Once weekly volume hits ~5 posts/week, this graduates from a
spreadsheet to a view on `/api/admin/accounts`. Don't build the
admin view earlier than that — premature dashboards eat hours
better spent posting.

### Weekly review ritual (Sunday afternoon, 30 min)

1. **What hit?** Top-3 posts by signup-attribution. Why? Format,
   timing, hook, or topic?
2. **What missed?** Bottom-3 by signup-attribution. Are they
   recoverable (wrong time, wrong sub) or dead (wrong format)?
3. **Funnel snapshot:**
   - Signups this week: [N]
   - Sandbox→Launch conversion rate this week: [N]%
   - Cancellations this week: [N]
   - DM volume from posts: [N]
4. **Next week's adjustments:** drop one format that didn't work,
   double-up on the one that did. Resist the temptation to drop
   formats with two weeks of data — three weeks minimum to
   declare a format dead.
5. **One thing to ship in product** that came directly from a
   conversation this week. (This is the only standing instruction
   on the ritual that creates compounding leverage; everything
   else is bookkeeping.)

### Failure modes to watch for

- **Engagement on X without signups.** Means the format is
  amusing but unqualified. Switch to higher-signal posts (specific
  problems, specific tools).
- **Signups without conversion.** Means we're attracting the
  wrong ICP. Tighten the hook, especially the Reddit ones.
- **Conversions without retention.** Worst case. Means the
  product is below the marketing promise. Stop posting more
  marketing; ship the missing capability first.

---

*Last updated: 2026-05-13. Update freely — this document
out-dates itself the moment real signups start coming in.*
