# Agent Report — Content Calendar (2026-05-13)

Short note on the editorial decisions taken, what was deliberately
*not* included, and the open questions worth surfacing for the
founder before any post goes live.

---

## Editorial decisions

1. **Human publishes everything.** The calendar treats Day 1 as
   the founder's first public post and never assumes any agent
   posting. This is explicit in §1 and shapes every cadence
   recommendation. (Per `MARKETING_PLAN.md §12`.)

2. **No fabricated metrics.** Every numeric claim is a `[N]`
   placeholder. The `[N]`-pattern is deliberate, easy to grep,
   and signals the founder needs to fill them in before posting.
   This was applied even to weekly recap posts and the Show HN /
   PH drafts — those are kept low-claim on purpose.

3. **Tone discipline.** British English throughout. Vocabulary
   banlist enforced (no "leverage", "facilitate", "unlock",
   "revolutionary", "game-changer", "disrupt", "supercharge",
   "seamless", "we're excited to announce"). Replaced with:
   "actually", "properly", "fair", "honest", "boring", "fix",
   "ship". Matches the voice on `index.html` and FAQ.

4. **Build-in-public posts are anchored in real ship-events from
   recent commits.** Cross-referenced against the cranky-nash
   commit log: /start wizard, /account dashboard, magic-link
   auth, Resend-into-Supabase SMTP wiring, idle-archive
   lifecycle, decommission CLI, status page, abuse protections.
   Nothing in the calendar promises capability outside that set.

5. **What's deliberately not promised:**
   - **Mobile builds.** Capacitor scaffold exists per
     `MARKETING_PLAN.md §14`, build pipeline isn't wired. Every
     mention of mobile in the calendar carries a "don't sign up
     for mobile yet" qualifier — including Show HN and PH.
   - **Paddle integration.** Per the Hatchik MoR memory note,
     Paddle is pending approval and Launch tier is currently
     invoiced manually. Calendar §15 (Day 26) admits this
     directly; Show HN admits it; PH admits it.
   - **GitHub-per-tenant automation.** The marketing site
     promises it, the substrate doesn't auto-create the repo
     yet (it's a manual step in `FIRST_CUSTOMER_RUNBOOK.md`).
     The calendar references the GitHub repo only in contexts
     where the manual onboarding bridge is acceptable (Show HN
     comment seed, blog post 4).
   - **Cross-region paid provisioning.** Still hand-shaped.
     Calendar Day 15 admits this openly.

6. **Anti-shill on Reddit.** Each of the 8 Reddit posts has an
   explicit "anti-shill check" line. The test applied: would
   this post still be worth reading if Hatchik did not exist?
   Three of the eight (r/cursor, r/Entrepreneur, r/webdev) make
   no product mention at all in the body — Hatchik only appears
   if someone asks in comments. Two (r/ClaudeAI, r/selfhosted)
   mention Hatchik in a single footer paragraph in parentheses.
   Three (r/SideProject, r/IndieHackers, r/SaaS) lead with
   Hatchik because those subs explicitly welcome
   project-introduction posts — but each leads with substantive,
   value-bearing content, not a pitch.

7. **No naming-and-shaming in comparison posts.** Per the brief,
   /vs material on social is framed as "I've been thinking about
   why X is hard" rather than "Lovable is bad". Day 6, Day 12,
   and Day 17 X posts all stay on the right side of this line.
   Day 22 names "Bolt, Lovable, Replit Agent" but only as a
   neutral cohort, not as comparative criticism.

8. **Reddit cadence is 2/week, not daily.** Brief said "twice
   weekly Reddit"; 8 posts over 30 days is ~2/week. Distributed
   so no single sub gets two posts within the 30-day window. The
   subs hostile-to-shilling (r/webdev, r/Entrepreneur) get
   pure-value posts; the founder-friendly subs (r/IndieHackers,
   r/SideProject) get the more direct project-introductions.

9. **Show HN before Product Hunt.** Sequencing decision: HN
   first, PH only after HN goes well. HN gives technical social
   proof; PH amplifies it. Inverse order tends to produce a
   weaker PH and a Show HN that arrives sounding stale.

10. **Show HN gated on Launch-tier automation.** Explicit note
    that Show HN should *not* go up until cross-region Launch
    provisioning is fully automated end-to-end — because the
    inevitable "I signed up and it took 24h" comment will tank
    the thread. Per `FIRST_CUSTOMER_RUNBOOK.md`, that's still in
    progress.

11. **Newsletter pitches gated on 20+ paying customers.** Per
    `MARKETING_PLAN.md §11` which says "pitch after 50 real
    customers" — softened to 20 in the calendar because the
    smaller newsletters (Ben's Bites, Rundown) will accept
    pitches with smaller customer counts than the biggest list
    fish would. Founder should override toward 50 if early
    pitches are bouncing.

---

## What I did *not* include and why

- **A YouTube outreach script.** The brief listed it implicitly
  (channels in `MARKETING_PLAN.md §11`), but YouTube sponsorship
  outreach is its own document — needs creator-by-creator pitch
  customisation, budget conversations, and timeline negotiation.
  Flagged as next-document material.

- **LinkedIn content.** `MARKETING_PLAN.md §11` says
  "founder-led posts only". I didn't draft LinkedIn specifically
  because the X build-in-public posts are 90% translatable
  to LinkedIn with light edits (longer, less terse, more
  context-setting). Adding a separate LinkedIn track was a
  fabricated cadence given the brief said "daily on X, twice
  weekly Reddit, weekly long-form".

- **TikTok / Instagram / Threads.** Per
  `MARKETING_PLAN.md §11` "don't waste energy yet". Honoured.

- **Paid ads.** Same reason; CAC tolerance unknown.

- **Influencer / community partnerships beyond newsletters.**
  Treated as out-of-scope for a 30-day content calendar; this
  is partnership-development work, not content work.

- **A second wave of long-form posts beyond #4.** Brief said
  "4 long-form blog posts (titles + outlines)" — kept to the
  exact 4 requested.

---

## Open questions for the founder

1. **What's the real handle on X?** The calendar refers to
   `@hatchik` (consistent with `MARKETING_PLAN.md §12`'s
   open question about the handle). If that's taken, switch
   globally and update mentions in Day 30 + the Show HN.

2. **Should Day 26 admit Paddle is pending?** The post is
   useful as transparency, but if the founder feels admitting
   "Stripe direct is closed to us" sounds like a credibility
   risk to a casual reader, the post can be reworked into a
   generic "why MoR matters for global SaaS" piece. I'd lean
   toward leaving it — the indie-hacker scene rewards honest
   tax-residency stories — but it's the founder's call.

3. **Is the £79 + £9/mo + £24/mo-after-15-signups pricing
   final?** Several posts reference it; if you change pricing,
   posts #1, #7 (recap), #11 (hypothetical), #21 (recap), #29
   (community ask), #30 (recap), Show HN, PH, and Reddit #3
   all need a sweep.

4. **Launch-tier wait — what do you actually want to promise?**
   The FAQ on the homepage says "within 24h"; the runbook says
   "45-90 min per Launch customer once you've done a few"; the
   honest reality is "as soon as I get to it". Day 15
   build-in-public post threads this carefully but a tightening
   would help.

5. **Is `hatchik.com/writing/<slug>` the right URL pattern for
   the blog?** Calendar assumes that path. If you'd rather
   `/blog/<slug>` or `/articles/<slug>`, find-and-replace.

6. **Show HN timing.** Calendar gates Show HN on Launch-tier
   automation. If that automation slips past Day 30, the Show
   HN slides too — which means the PH gating slides as well.
   Worth tracking explicitly.

7. **First customer real metrics.** Once you have actual
   numbers, do a one-pass edit through the calendar replacing
   `[N]` placeholders. Don't wait for a complete picture — even
   one real number is more persuasive than a polished
   placeholder.

8. **Should we draft a `/writing` index page now?** Four blog
   posts is enough to justify a writing index; the marketing
   site doesn't currently link out to one. Worth a 30-minute
   wire-up.

9. **The /vs page — is it built?** The calendar references
   `hatchik.com/vs` in Show HN, PH, and Reddit #3. If it's not
   live yet, that's a single highest-priority pre-launch task.
   (Per `MARKETING_PLAN.md §12` it's listed as drafted but I
   couldn't verify a live page in the worktree this agent had
   access to.)

---

## What the founder should do next

1. Read the calendar end-to-end in one sitting. Edit voice
   anywhere it doesn't sound like you.
2. Fill in the `[N]` placeholders for any numbers you already
   have.
3. Decide the Day 1 date and schedule the first three posts
   (Days 1-3) so the cadence has a running start before
   improvisation pressure kicks in.
4. Stand up the UTM tracking table (Section 8) as a Google
   Sheet **before** Day 1, not after.
5. Don't post the Show HN or PH drafts yet — they're keyed to
   Launch-tier automation landing. Day-30 review is the
   earliest sensible moment.

---

*Generated: 2026-05-13. Edit freely.*
