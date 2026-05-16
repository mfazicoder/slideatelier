# /vs page — agent build report

Built `proposals/hatchik/vs.html` against `MARKETING_PLAN.md` §8–§10 and the
visual language in `index.html` / `start.html`. This is a working draft —
review before publishing.

---

## What got built

Six sections in this order:

1. **Hero** — "How Hatchik compares" plus an honest framing line and a
   no-strawman disclaimer. Two CTAs: jump to matrix, or start free.
2. **Comparison matrix** — 11 rows × 8 columns (Capability + Hatchik + 6
   competitors). ✓ / ~ / ✗ symbols, colour-coded green / amber / rose.
   Hatchik column visually emphasised with a subtle indigo ring and
   background tint. Includes footnoted citations (superscripts → bottom-of-
   page footnotes).
3. **Per-competitor deep dives** — 6 cards (Bolt, Lovable, Replit, Bubble,
   ShipFast, DIY Vercel+Supabase). Each has: name + tagline, "Where they
   shine", "Where Hatchik wins", "Pick them if…" / "Pick Hatchik if…",
   pricing line.
4. **Pricing snapshot** — 8 small cards (Hatchik + 7 alternatives + Hatchik
   Growth) showing entry-tier price and annual all-in. Hatchik card
   visually emphasised.
5. **FAQ** — 5 entries: why cheaper, what if I outgrow, should I switch,
   is Hatchik really not an AI builder, where does this data come from.
6. **Footnotes** — 29 numbered citations linking out to each competitor's
   own pricing/docs page where available.
7. **CTA + footer** — matches `index.html` exactly (same nav, same footer,
   same Paddle MoR disclosure). Adds the "accurate as of late 2025 / early
   2026 — ping us when stale" line per the brief.

Design system reuse: same Tailwind CDN, same `--ink` / `--indigo` / `--amber`
custom properties, same `chip` / `card` / `btn-primary` / `btn-ghost` /
`gradient-text` / `grain` classes from `index.html`. No new dependencies.
British English throughout. No emojis.

---

## Key decisions

- **Treated "DIY on Hetzner / Vercel / Supabase" as one column called
  "DIY Vercel + Supabase"** for headline brevity, but the deep-dive card
  covers the same ground the brief asked for. Vercel + Supabase is by far
  the most common DIY stack our ICP picks.
- **No straw-manning enforced**: every "Where they shine" paragraph is
  written so a current Bolt/Lovable/Replit/Bubble user would nod along
  before reading the Hatchik counter.
- **Lock-in row is inverted** (✓ means *no* lock-in) — I added an explicit
  parenthetical ("none" / "some" / "heavy" / "heaviest") to avoid the
  semantic flip confusing anyone scanning the row.
- **Pricing in approximate GBP** with USD source values in footnotes. Used
  ~1.25 USD/GBP for ballpark conversion; noted as approximate.
- **Free-tier row** marks Replit ~ rather than ✓ because Replit free is
  meaningfully different from "free Agent usage" — Agent itself is gated.
- **Bubble free tier** marked ✗ because their indefinite free plan was
  retired in 2023 — new accounts only get a trial.
- **Paddle MoR row** is a Hatchik-only ✓ — accurate AFAIK, but flagged
  below as worth double-checking.

---

## Open questions / things to verify before publishing

1. **Lovable pricing band.** I wrote "$25–$100/mo depending on plan" per
   the brief. Their public pricing page evolved through 2025 (multiple
   Pro/Teams/Scale variants); verify the band is still right and pick a
   single representative number if not.
2. **Replit Agent pricing.** "$25/mo Core + Agent usage on top" is the
   shape, but the exact metered Agent rate isn't quoted on the page.
   If it's stable now (effort/credit-based), it might be worth a more
   specific number.
3. **ShipFast lifetime.** $199 is the headline; Marc Lou has run
   sales and added "lifetime upgrade" tiers. Verify $199 is still the
   public entry.
4. **Bubble Starter price.** Bubble has shifted pricing repeatedly. I
   used $39/$119; if they've moved, refresh.
5. **Paddle MoR claim** for competitors. I asserted none of them use
   Paddle MoR. Bolt/Lovable/Replit appear to use Stripe direct or similar
   PSP-direct setups, not MoR — but worth a 5-minute sanity check on
   each before publishing, because it's a strong claim.
6. **Mobile builds for ShipFast.** Marked as ~ because Marc Lou has a
   separate mobile boilerplate; double-check it isn't bundled in newer
   ShipFast versions.
7. **Hatchik Growth in the snapshot** — I included it as a comparison
   anchor to show even our top tier beats Bubble Starter and DIY. That
   gives 8 cards in a 4-column grid which wraps to two rows; visually OK
   but design review may prefer to drop one.
8. **Hatchik mobile builds** — the matrix says ✓ but `index.html` FAQ
   notes the build pipeline isn't fully wired (Capacitor scaffold exists,
   builds are pending). The marketing page already claims this; we're
   consistent, but it's a known credibility risk the marketing plan
   already flags (§14). Don't let this page run far ahead of reality.
9. **Custom domain on Sandbox tier** — marked ✓ overall for Hatchik but
   custom domains today are Launch-only (Sandbox is `<slug>.hatchik.com`).
   The matrix is correct at the *product* level; a sharp-eyed reader might
   pick at it. Acceptable for now; revisit when Sandbox-domain question
   is resolved (open in marketing plan §14).

---

## What this page does NOT do

- No JavaScript beyond the existing Tailwind CDN — purely static page.
- No analytics / event tracking wired (the brief didn't ask).
- No A/B variants — single canonical page.
- Not linked from `index.html` nav yet. Worth adding `"Compare"` to the
  nav once this is approved, but that's an `index.html` edit.

---

## Files touched

- `proposals/hatchik/vs.html` (new, ~600 lines)
- `proposals/hatchik/AGENT_VS_REPORT.md` (this file)

Nothing deployed. Worktree only.
