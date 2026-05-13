# Hatchik pitch deck V2 — sprint reframe report

**Deliverable:** `hatchik-pitch-deck.pptx` (replaces V1 in place)
**Generated:** 2026-05-13
**Builder:** pptxgenjs (skill: anthropic-skills:pptx)
**Slide count:** 19 (V1 was 18 — one new "Three landing zones" slide inserted)
**Reframe brief:** AI hype-cycle 12-18 month sprint trajectory replaces 3-year SaaS-orderly framing.

---

## Slides changed (V1 → V2)

| Slide | V1 | V2 | Status |
|---|---|---|---|
| 1 | Title | Title | unchanged |
| 2 | Problem | Problem | unchanged |
| 3 | Solution | Solution | unchanged |
| **4** | **Why now (paid-user counts)** | **Why now (sprint trajectories)** | **rewritten** |
| 5 | Product flow | Product flow | unchanged |
| 6 | What's wired | What's wired | unchanged |
| **7** | **TAM/SAM/SOM (3-year)** | **TAM/SAM/SOM (12-18mo sprint SOM)** | **partially rewritten** |
| **8** | **Competitive (price £9)** | **Competitive (price £19)** | **single-cell fix for pricing consistency** |
| 9 | Where we win | Where we win | unchanged |
| **10** | **Business model (£9/£24)** | **Business model (£19/£39, repriced callout)** | **rewritten** |
| **11** | **GTM (channels)** | **GTM (sprint cadence)** | **rewritten** |
| **12** | **Funnel (signup #50)** | **Funnel (signup #100, decide by month 6)** | **rewritten** |
| **13** | **3-year ARR bar chart** | **MRR month 0-18 line chart, 2 scenarios** | **rewritten** |
| **14** | **Y3 margin reality (3 scenarios)** | **Repriced unit economics × sprint MRR** | **rewritten** |
| **15** | (was "What's already built") | **NEW: Three landing zones (acquisition / hypergrowth / lifestyle)** | **new slide inserted** |
| 16 | (was Risks) | What's already built | renumbered |
| **17** | (was Ask) | **Risks (Funnel-assumptions risk → Hype-window-closes risk)** | **renumbered + 1 risk rewritten** |
| **18** | (was Closing) | **Ask (12-month sprint capital, 60/25/15 use of funds)** | **renumbered + rewritten** |
| 19 | — | Closing / contact | renumbered |

---

## Design decisions

**Visual style preserved.** Same indigo `#4f46e5` + amber `#f59e0b` + slate `#1e293b` palette. Same Inter / JetBrains Mono pairing. Same off-white `#f6f5f1` content slides + dark `#0b1020` title/close. No emojis. British English. No banned words.

**New slide 15 (Three landing zones).** Three-card layout with coloured ribbons across the top of each card:
- **Acquisition** (amber ribbon, "MOST PLAUSIBLE UPSIDE PATH") — primary position.
- **Hypergrowth raise** (indigo, "IF METRICS COMPOUND") — middle.
- **Lifestyle landing zone** (grey, "THE FLOOR IF HYPERGROWTH MISSES") — explicitly framed as floor / consolation, not target.

Acquisition card lists six plausible acquirers (Anthropic, Cursor, Windsurf, Vercel, Supabase, Stripe) with one-line reasons each. Footnote on the slide reads: "Acquisition framing is a credible upside scenario with category precedents (Vercel ⊃ Nuxt Labs / Splitbee; GitHub ⊃ Copilot-adjacent acquisitions). Not a guarantee, not a forecast." Founder is reminded in speaker notes not to verbally promise acquisition.

**Slide 13 chart.** Replaced clustered bar chart (Y1/Y2/Y3 × 3 scenarios) with a line chart showing MRR by month for two scenarios, sampled at M0/M2/M4/M6/M9/M12/M15/M18. Sprint base curves to ~£135K MRR by M18; Sprint upside to ~£380K. Both start at zero, both compound through M2-6, both inflect at M9. Footnote: "Trajectories anchored on Cursor / Lovable / Bolt growth curves at comparable product maturity."

**Slide 10 pricing change.** Launch £9 → £19. Growth £24 → £39. Setup fee £79 unchanged. Slide includes an explicit "REPRICED · UP FROM £9 / £24" callout strip so the founder doesn't have to mentally track diffs. Slide 8 competitive table cell updated to £19 for consistency.

**Slide 14 margin reality.** Restructured around two tables/blocks: a "Before → After" margin comparison (Launch yearly margin £6 → £120, Growth £186 → £366) and a "What this means at month 18" block crossed with slide-13 MRR scenarios. The dark "Honest framing" strip explicitly says: "Sprint base = real business. Sprint upside = acquirable or fundable."

**Slide 18 ask.** Reframed from "[£amount] for [N] months runway" to "£500K-1M · 12 months to decision point". Use of funds is now 60% paid acquisition / 25% first hires / 15% infrastructure. Amount is bracketed (`[ 500K - 1M ]`) for the founder to fill the final number.

**Slide 17 risks.** Risk #1 ("Funnel assumptions unvalidated") replaced with "Hype window closes: if Anthropic / Cursor / Windsurf release their own deployment layer in the next 6 months, our wedge tightens dramatically." Mitigation: aggressive partnership outreach in months 0-4. Risks #2 and #3 unchanged.

**Slide 12 funnel.** Funnel mechanics unchanged (100% → 5% → 1%) but the urgency reframe is: "Validate by signup #100. Decide raise-or-exit by month 6." Bottom stats updated to reflect new pricing: blended LTV £520 (was £260), CAC ceiling £175 at 3:1 (was £87).

---

## QA performed

1. **Build round-trip.** pptxgenjs build → soffice PDF conversion → pdftoppm JPG render. 19 slides produced cleanly.
2. **Visual inspection.** Reviewed slides 4, 7, 8, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19 individually. Two cosmetic fixes after first render: slide-11 title wrapped to two lines → shortened ("Sprint cadence. 3× heavier in months 2-6."). Slide-8 launch-price cell still showed £9 → updated to £19 for consistency with slide-10.
3. **Placeholder scan.** Ran the prescribed grep against markitdown output: no `lorem`, `ipsum`, `xxx`, `TODO`, `[insert`, or `this slide layout` strings. Intentional template placeholders on slides 18 and 19 (`[ 500K - 1M ]`, `[ fill ]`, `[ founder fills ]`) are present and clearly bracketed.
4. **Banned-words scan.** No "leverage", "facilitate", "revolutionary", "game-changer", "disrupt", "synergy".
5. **Footer / page-number consistency.** All footers renumbered after the slide-15 insertion: slide 16-18 page numbers are correct (16, 17, 18). Slide 19 (closing) intentionally has no footer.
6. **Cross-slide consistency.** Pricing on slide 8 competitive table cross-checked with slide 10 pricing cards: both show £19.

---

## Open questions for the founder

1. **The £500K-1M raise range.** The brief specified that range; I used it on slide 18 with the actual number bracketed for you to fill. Is the range right? Is the upper bound aggressive enough for a credible 12-month sprint with one engineer + one marketer + 60% paid? Worth pressure-testing against a real burn model.
2. **Acquisition slide tone — verbal vs visual.** The deck frames acquisition as "most plausible upside path with category precedents". The slide does NOT promise an acquisition. But the visual hierarchy (amber ribbon, big $20-100M number, "PRIMARY UPSIDE" label) does signal it as the lead path. Sophisticated reader will accept this; some investors may push back on prominence. Easy to demote: shrink the amber ribbon to grey + drop "PRIMARY UPSIDE" → "PATH ONE", and the slide becomes a balanced three-zone view. Open question: how confident in the acquisition framing do you want to look on first contact?
3. **Pricing migration.** £9 → £19 is a 2× bump. Existing Sandbox→Launch funnel was on £9. Have any beta customers been quoted £9? If so, who needs honouring? Worth a comms note on the marketing site explaining the change before it goes live (recommend bumping pricing **before** Product Hunt launch, not after).
4. **MRR projection sanity.** Sprint base hits ~£135K MRR by M18, Sprint upside ~£380K. Cross-checked against £19/£39 pricing: Sprint base ~£135K MRR / £29 blended ARPU ≈ 4.7K paying customers — roughly consistent with the slide-7 SOM "5K paying by month 12" claim. Sprint upside ~£380K / £29 ≈ 13K paying — within the slide-7 "15K paying by month 18" upside band. Numbers are internally consistent. Sanity-check from your gut?
5. **Slide 7 SOM circles.** The graphical TAM/SAM/SOM ellipses on the left use the old proportions; with the new SOM framing (5K base / 15K upside paying customers), the SOM ellipse is still drawn at ~3.85% of TAM area which is roughly right for the upside scenario. Acceptable. If you'd prefer two SOM ellipses (one for base, one for upside), I can draw them.
6. **Slide 15 acquirer list specificity.** Listed six acquirers (Anthropic, Cursor, Windsurf, Vercel, Supabase, Stripe) with one-line "why each makes sense" notes. If any of these are sensitive in a specific investor conversation (e.g. an investor is already in one of those companies' captable), you might want to neutralise the names to "major AI tool vendor / major payments vendor / major platform vendor". Easy edit.
7. **Slide 19 contact details.** Founder email + LinkedIn still bracketed as `[ founder fills ]`. Fill before sending.

---

## Build artefacts

- Source script (V2 pptxgenjs): `/tmp/hatchik-deck-v2/build.js`.
- Intermediate PDF for inspection: `/tmp/hatchik-deck-v2/hatchik-pitch-deck.pdf`.
- Slide JPGs for visual review: `/tmp/hatchik-deck-v2/slide-*.jpg`.

To rebuild after edits:

```bash
cd /tmp/hatchik-deck-v2
node build.js
```
