# Hatchik pitch deck — agent build report

**Deliverable:** `hatchik-pitch-deck.pptx` (alongside this report)
**Generated:** 2026-05-13
**Builder:** pptxgenjs (skill: anthropic-skills:pptx)
**Layout:** LAYOUT_WIDE (13.33" × 7.5")
**Slide count:** 18
**Audience:** primary — investor pitch (angel / pre-seed); secondary — partner / customer marketing
**Voice:** British English throughout. No banned words. No emojis.

---

## Design decisions (and why)

- **Palette.** Indigo `#4f46e5` + amber `#f59e0b` accents, slate `#1e293b` for headings, off-white `#f6f5f1` for backgrounds — the same palette the marketing site (`index.html`) uses. Title + closing slides flip to dark `#0b1020` (premium-feel sandwich).
- **Fonts.** Inter for headings and body, JetBrains Mono for eyebrows, code-window mocks, captions, and numeric stat sub-labels. Pairing mirrors the marketing site.
- **No accent lines under titles.** Skipped deliberately (cited as an AI-tell in the skill guide). Eyebrows handle the hierarchy.
- **Visual motif.** Every content slide has a coloured left/top accent bar on cards (indigo or amber alternating) — used across slides 2, 6, 11, 16. Carries the brand mark across the deck without becoming repetitive.
- **No emojis, no stock-photo people, no banner clipart.** Used: shape primitives (rounded rectangles, ovals, lines), a single PPT-native bar chart, a markup table on the comparison slide, and clean code-window mocks on the product-flow slide.
- **Footer.** Tiny, slate-muted, page numbers right-aligned. Not enforced on the dark title/closing slides (they have their own visual identity).

---

## Slide-by-slide content + speaker notes

> Speaker notes are also written into the .pptx file itself (each `slide.addNotes(...)` call). The bullets below are a faithful summary the founder can read alongside the deck.

### 1 — Title  (dark)
- Wordmark "Hatchik" + tagline: "The production substrate your AI coder builds on — no platform, no lock-in, no demos." (Source: MARKETING_PLAN §8.)
- Founder name (Farhan Irshad), operating entity (Omani-registered company), date (May 2026), domain.
- Background: large translucent indigo + amber blobs to suggest a gradient without using a real gradient (pptxgenjs limitation).

### 2 — The Problem
- Lead paragraph: founders ship AI-built prototypes, then plateau on auth/payments/mobile/DB.
- Three persona cards from `index.html`: PT (PrepSheet), consultant (framework demo), designer (tool-they-always-wanted). Each card has a "WHERE THEY PLATEAU" sub-section.
- Closing italic line: "Every persona above ships a working-looking thing. None of them ship a business."

### 3 — The Solution
- Left column: narrative ("we give the AI tool a real SaaS to build on"; 60s sandbox; AI reads AI_CONTEXT.md; push-to-deploy in ~30s).
- Right column: four-step numbered flow diagram (Sign up → Sandbox provisioned → AI reads handoff → Push-to-deploy).
- Pill row across the bottom: "Your AI tool / Your code / Your repo / Real production stack" — reinforces the four-pillar positioning from MARKETING_PLAN §8.

### 4 — Why now
- Three stat cards: Cursor 1M+ paid (Public 2025), Copilot 1.8M paid (MS Ignite 2024), Claude Code 100Ks (Anthropic, <12 mo).
- Dark callout strip: "~doubling YoY · none of these tools own the production substrate layer" + one-paragraph framing of the wedge.
- Source citation footer cites MARKETING_PLAN §2 explicitly.

### 5 — Product flow
- Four-column wide mockup: wizard input → sandbox URL/page → terminal (AI reads handoff + pushes) → redeploy log.
- All four "windows" share a unified mac-style chrome (red/amber/green dots) so it reads as a sequence rather than four unrelated mocks.
- Deliberately schematic rather than literal screenshots — keeps the visual legible at email-thumbnail size. Speaker note points to hatchik.com/start for the live wizard if a demo is needed.

### 6 — What's wired
- Twelve-tile 4×3 grid: Auth, Postgres, Storage, Realtime, Stripe checkout, Paddle MoR, Resend, Mailboxes, Capacitor, GitHub repo, Push-to-deploy, Status page.
- Alternating indigo/amber left accent bars across the grid.
- Footer line: "Every tile is shipping today. Cross-referenced against FIRST_CUSTOMER_RUNBOOK.md." (Faithful: every item maps to a documented section of the runbook.)

### 7 — TAM / SAM / SOM
- Left: concentric circles (indigo TAM → lighter blue SAM → amber SOM, with labels outside-circle).
- Right: three stacked blocks — TAM (~310K users / £56M/yr today → £200M+/yr by 2027), SAM (~190K / £34M/yr → £120M/yr), SOM table (Conservative 1.5K/£270K, Base 5K/£900K, Aggressive 13K/£2.4M).
- Source citation in bottom-right corner: MARKETING_PLAN §2–5.

### 8 — Competitive landscape
- Six-competitor comparison table. Hatchik column highlighted in amber background to draw the eye.
- Eight rows (from MARKETING_PLAN §9 / vs.html, with Merchant-of-record-billing added — it differentiates Hatchik from every competitor on the list).
- Legend strip: ✓ full, ~ partial, — not supported.

### 9 — Where we win
- Three big indigo-topped cards with WEDGE 01 / 02 / 03 eyebrows.
  - 01 "Your AI tool, not ours."
  - 02 "Your code, your repo, your VPS."
  - 03 "Pre-wired production stack."
- Each with a concrete proof-point paragraph.

### 10 — Business model + pricing
- Three pricing cards: Sandbox (£0 free), Launch (£79 setup + £9/mo, **dark-bg accented**), Growth (£24/mo).
- Per-tier feature bullets.
- Bottom strip "UNIT ECONOMICS": "£79 setup pays for first ~13 months of customer's VPS; Launch ongoing margin ~£0.50/mo; Growth ~£15.50/mo; Paddle handles MoR."

### 11 — Go-to-market
- Four-channel 2×2 grid: YouTube AI-coder ecosystem · r/ClaudeAI + r/cursor · X #buildinpublic · Product Hunt + Show HN.
- Dark bottom strip: "Paid ads only after CAC modelled. CAC ceiling £87 at 3:1 LTV:CAC (LTV £260)." Sets up slide 12.

### 12 — Funnel + assumptions
- Left: stylised three-stage shrinking funnel (Sandbox 100% → Convert to Launch 5% → Upgrade to Growth 1%).
- Right: three "TO VALIDATE BY SIGNUP #50" rows (conversion, churn, upgrade time).
- Bottom strip: three big stats — Blended LTV £260, CAC ceiling £87, Cohort dashboard "Live" (the admin/dashboard already exists).

### 13 — 3-year ARR projection
- Native PowerPoint clustered bar chart (Conservative / Base / Aggressive across Year 1 / 2 / 3).
- ARR-in-£K data labels above each bar (72/180/360, 140/450/1100, 216/720/1870).
- Right column: three scenario cards summarising Year-3 ARR + customer counts + posture.
- Footnote: "ARR pre-gross-margin. Margin in next slide. Customer counts (year 3): 1.5K / 5K / 13K."

### 14 — Margin reality
- Three-card honest framing: Conservative (£100K gross → £40K net, "Lifestyle"), Base (£350K → £150–200K, "Solo founder income"), Aggressive (£900K → £500K, "Needs marketing investment to materialise").
- Dark "BINDING CONSTRAINT" strip below: Launch tier ongoing margin (~£0.50/mo) is the binding constraint; £19/mo Launch pricing would roughly double it. Pricing-lever is the clearest non-cap-raise move once we have data.

### 15 — What's already built
- Two-column ticked list of 12 shipped capabilities (provisioning, account harness, lifecycle, GitHub, mobile builds, status, docs, dashboard, abuse protection, Stripe portal, idle archive + restore, AI deploy token).
- Amber footer strip: "Built solo + AI-augmented in ~3 weeks." (Estimate from the recent commit log visible in `git log`: lifecycle agent, github agent, shell agent, status agent landed in rapid succession.)

### 16 — Risks + what we're watching
- Three red-topped cards (Funnel unvalidated / Single-VPS / Mobile signing out-of-band).
- Each card has a "MITIGATION" sub-section in an indigo-tinted box.

### 17 — The Ask  *(template, founder to fill)*
- Big dark callout: "Raising £[ amount ] for [ N ] months runway".
- Three use-of-funds cards: Hire one engineer / Hire one marketer / Founder runway.
- "Target at end of round" strip: paying-customer count, MRR, validated funnel, second region live — all bracketed for founder to fill.
- **Skip this slide entirely when the deck is being used for partner / customer pitches** (Anthropic, Cursor, Windsurf, etc.). The speaker note flags this.

### 18 — Closing / contact  (dark)
- Mirror of the title slide visual.
- Tagline restated: "The production substrate your AI coder builds on. No platform. No lock-in. No demos."
- Contact card: General `hello@hatchik.com`, Web `hatchik.com`, Founder email + LinkedIn left as bracketed placeholders for the founder to fill.

---

## QA performed

1. **Visual inspection.** All 18 slides converted to JPGs (`pdftoppm -r 110`) and reviewed end-to-end.
2. **Fix-and-verify cycle.** Three rounds of fixes:
   - Slide 7: source citation was bleeding into the page footer; moved into right-column gutter.
   - Slide 12: original funnel labels "→ Growth (12mo)" + "1%" collided on the narrowest bar; rephrased labels ("Convert to Launch", "Upgrade to Growth") and widened the bar.
   - Slide 13: chart was tall enough to clip the topmost data label ("1,870") and the footnote ran into the footer; chart shrunk slightly and footnote raised.
   - Section-title block widened + height bumped to prevent two-line titles from being cut.
3. **Placeholder scan.** No accidental `Lorem ipsum`, `xxx`, or stray TODO markers. Intentional template placeholders (slide 17 / 18) are present and clearly labelled "[ founder fills ]" / "[ amount ]" etc.
4. **File integrity.** 644 KB output, 110 internal files, opens cleanly in LibreOffice (used to render the PDF for inspection).

---

## Open questions for the founder

1. **Slide 17 — the ask.** Numbers and runway target are deliberately bracketed. Worth a 15-min sit-down: typical pre-seed range £150K–£300K for 12–18 months; what's the right call given the Base scenario maths on slide 13–14?
2. **Slide 18 — founder contact.** Personal email + LinkedIn URL still bracketed. Fill before sending.
3. **Founder photo on slide 1?** Deliberately omitted — the deck reads cleaner without one and avoids the "stock-photo founder" trap. If you'd prefer a small founder portrait on the title or closing slide, easy to add.
4. **/start screenshot on slide 5?** Currently a clean schematic mock-up rather than a real screenshot. The schematic reads better at email-thumbnail size, but if the deck is going to be presented full-screen at a meeting we could swap in real screenshots from hatchik.com. Trade-off: real screenshots age fast.
5. **Margin numbers on slide 14.** I used the £0.50/mo Launch margin and ~£15.50/mo Growth margin from the prompt; the Y3 gross/net figures (£100K/£40K, £350K/£150–200K, £900K/£500K) are reasonable but worth your sanity-check before showing to anyone numerate.
6. **Partner-pitch variant.** Slide 17 is the only investor-specific slide. For partner pitches (Anthropic, Cursor, etc.), I'd save-as and delete slide 17. The rest of the deck is dual-purpose by design.

---

## Build artefacts

- Source script (pptxgenjs): `/tmp/hatchik-deck/build.js` (not checked in; left in working dir in case you want to regenerate the deck after edits).
- Intermediate PDF for inspection: `/tmp/hatchik-deck/hatchik-pitch-deck.pdf`.

To rebuild after edits:

```bash
cd /tmp/hatchik-deck
node build.js
```
