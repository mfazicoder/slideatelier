# slideAtelier — requirements backlog

> **How this works**: User drops requirements as they come up. PM (Claude) appends them here, organises into sprints, executes the current sprint without interruption. New requirements go in the **inbox** section and get triaged at sprint boundaries.

## North star (2026-05-08)

**slideAtelier = a Framer alternative, but better, plus presentation-specific superpowers.**
Native-editable code-defined primitives are the moat (squares stay squares, charts stay native PowerPoint charts). Cross-format output (.pptx today; hosted Web Deck soon). AI generation via Anthropic Claude as the conversational core. Three-pronged entry: Design-led / Idea-led / Content-led.

---

## Status

| Sprint | Status |
|---|---|
| A — Native asset framework foundation | ✅ done |
| B — Library UX upgrade | ✅ done |
| C — Comprehensive font system | ✅ done |
| D — Visual manipulation of extras | ✅ done |
| E — User-created custom templates + brand kit | queued |
| F — Cross-stage history | ✅ done |
| G — More native templates (14 shapes × 4 themes) | ✅ done |
| H — Storyboard creative whiteboard | queued |
| I — Three-pronged entry chooser | queued |
| Y — Freeform text blocks + typography | ✅ done (Y.1+Y.2+Y.3) — bbox + typography overrides; renderer honors both |
| J — Hosted Web Deck publishing | ✅ done — SVG renderer for all 14 shapes + WebRenderer + routes + viewer + presenter mode + Publish button |
| **K–X — Competitive-analysis gap-fill (below)** | newly queued |
| Z — Selective text stripping (Z.v1 .pptx + Z.v2 Web Deck SVG) | ✅ done — chrome_only at attach time + live SVG translation in Web Deck |

---

## Inbox (newly captured, awaiting triage)

### Decisions log (resolved, no action)
- **Refused: Dribbble scraping** — ToS violation + copyright derivatives. Substitutes: Slidesgo / Adobe Stock / Behance CC / Unsplash / native shapes + themes / user-uploaded brand decks (Sprint E).
- **Refused: Dribbble screengrab + clone variant** — same copyright/derivative-work problem as scraping, just slower. Same substitutes apply.
- **DM Sans default for wireframing UI** ✅ shipped in-flight.
- **Visual rendering of attached extras in WYSIWYG** ✅ shipped in-flight.

### Open inbox items
- **Tiered AI access as monetisation lever** (Site-builders cluster). Free = 5 generations/mo + watermarked Web Deck; Pro = unlimited gen + custom domain + marketplace selling; Team = multi-seat + brand kit lock + audit trail. → Pricing track, not a sprint feature; revisit when commercial work begins.
- **Quiz-driven entry refinement** (Wix-style 4-question audience/length/tone/artifact branch). → Folds into Sprint I as I.0.

---

## Queued sprints — original backlog

### Sprint E — User-created custom templates + brand kit
- User uploads logo, brand colors/fonts via /design-system (partial)
- User uploads custom shape SVGs → converted to native PowerPoint primitives where possible
- User-defined templates combining: brand colors + fonts + logos + custom shapes
- Per-tenant template library
- User-uploaded asset classes (fonts, shapes, graphics, images, logos)
- Now superseded in part by **Sprint K — Brand Kit + Brand Tokens** (which is the cleaner abstraction); E focuses on custom shape uploads and per-tenant template library.

### Sprint H — Storyboard creative whiteboard (multi-sprint group)
- Infinite digital whiteboard / mindmap-style canvas, NOT slide-shaped
- Accept structured AND unstructured input (text + file uploads, security-filtered)
- Continuous AI categorization: groups thoughts, suggests narrative arc, surfaces themes
- Backlog/queue view + visual mindmap/flowchart view (toggle)
- Drag/edit/delete/rearrange thought-cards
- Dynamic parameters (audience, style, purpose) inferred from content and shown as editable filters along the screen — NOT as a wizard
- Single initial prompt: "what are you trying to create?" + optional parameters + attachment upload
- AI uses initial input to generate the prompt/context for the storyboard generation
- Output feeds into the existing storyboard.json schema (so wireframe stage continues to work)

### Sprint I — Three-pronged front-page entry chooser
- I.0 — Quiz-driven branch (4 questions: audience / length / tone / artifact) bias defaults
- I.1 — Front page entry chooser (full-width 3-column, hero photographs, draggable separators)
- I.2 — Idea-led journey: AI chat + real-time mindmap (overlaps Sprint H whiteboard)
- I.3 — Design-led journey: curated style gallery (royalty-free + native themes; **no** Dribbble derivatives)

---

## Queued sprints — competitive-analysis gap-fill (J–X)

> Generated 2026-05-08 from parallel competitive analysis of Framer / Figma / Ceros / Readymag / Squarespace / Webflow / Wix / WordPress / Claude Code / Codex / Lovable / Unbounce / Contentful. Each sprint cites the cluster that surfaced it. Recommended ordering top-to-bottom — J–N are the structural Framer-killers; O–R unlock team/enterprise tier; S–X are ecosystem and polish.

### Sprint J — Hosted Web Deck publishing (✅ shipped 2026-05-08)
*Source: Site-builders cluster (consensus structural feature).*

The structural feature that converts slideAtelier from "PPTX generator" to "Framer for decks." Shipped via 4 parallel agents in worktree isolation (J.A/J.B/J.C shapes + J.D infrastructure).

**What landed:**
- `WebRenderer` class (`src/slideatelier/web_renderer.py`) emits a complete `<html>` doc with each slide as a 16:9 `<section>`. Theme palette/typography → CSS custom properties (`--brand-primary`, `--type-display`, etc.) ready for Sprint K brand-token swap-in. Honors `block_bbox` + `block_style` from Sprint Y.
- All 14 native AssetShapes have a `render_svg(ctx, w, h)` method using native primitives only — `<rect>`, `<circle>`, `<polygon>`, `<line>`. Donut chart uses `<path>` arc commands (`A` elliptical-arc — the SVG-native curve, no polyline approximations). All 14 × 4 themes = 56 renders parse as valid XML.
- Routes: `POST /api/jobs/<job_id>/publish` (idempotent, generates URL-safe 8-char slug, writes `web_slug.txt` + `web_deck.html` + slug-index), `GET /web/<slug>`, `GET /web/<slug>/slide/<idx>` (deeplink redirect), `GET /api/jobs/<job_id>/web-deck-url` (lookup).
- Viewer: keyboard nav (← → space PgUp/PgDn Home/End Backspace), `f` for fullscreen, `p` for presenter mode (overlays speaker notes), `esc` exits, IntersectionObserver keeps slide counter in sync.
- "🌐 Publish to Web" button on the hi-fi page; copy-to-clipboard chip; auto-loads existing URL on page open.

**Test count**: +31 (5+5+6 shape SVG tests + 12 web-deck tests + 3 from value_chain regression fix). Total 98/98 passing.

**Bug fixed during integration**: ValueChain SVG had unescaped `&` in "Marketing & sales" label breaking XML parse. Added `_xml_escape` helper and applied to all label interpolations.

**Out of scope (queued for J.v2)**:
- Custom domains
- Password gate
- Multi-tenant slug-index storage (currently a single shared `output/web_slugs.json`; needs SQLite + write lock for multi-worker)

### Sprint K — Brand Kit + Brand Tokens
*Source: Design/creative cluster (Figma variables) + Site-builders cluster (Squarespace Blueprint).*

Per-workspace token store overrides the 4 themes; every existing AssetShape and Theme reads tokens at render time.
- 60-sec onboarding wizard captures logo, primary/secondary color, type pair, audience tone → emits a custom `Theme` row.
- JSON token store: colors, type families, spacing scale, logo asset.
- Reusable across all future decks; one-click rebrand.
- Largest enterprise-buyer gap closer; partially supersedes Sprint E.

### Sprint L — Atelier Copilot (selection-aware AI assistant) + Slide Visual Edits
*Source: AI-codegen cluster (Lovable Visual Edits) + Site-builders cluster (Squarespace Beacon).*

Sticky right-rail chat anchored to current selection (slide / shape / theme) emits **scoped diff edits**, not full regenerations.
- Click any rendered AssetShape → contextual toolbar (resize, recolor, swap-template, edit-label).
- "Ask AI" button issues a *narrowly scoped* call ("change hexagon labels to customer-facing tone") returning only the affected shape group via `hx-swap-oob` with a highlight pulse.
- Folds in **AI Organize / Shape Re-suggester** ("this list of 4 reads like a 2x2 matrix").
- Slash shortcuts: `/shape`, `/theme`, `/slide N` to scope without prose.
- Surgical HTMX targeting via stable shape-group `id`s.
- Persists context across stage transitions; primary AI monetisation meter.

### Sprint M — Outline Plan Mode + Streaming Per-slide Steering
*Source: AI-codegen cluster (Claude Code Plan Mode + Codex inline approvals).*

Gate every regen behind a cheap planning pass; let the user steer mid-generation.
- Before any deck regen, surface an **editable plan card**: each slide's title, chosen AssetShape, key bullets, theme deltas. User edits inline (drag-reorder, swap shape via dropdown, edit copy) before clicking Generate.
- **Streaming generation**: slides emitted one-at-a-time; each carries Accept / Skip / Re-prompt / Swap-shape buttons before the next slide begins.
- Three approval tiers (auto-all / per-slide / per-shape) configurable per project.
- Saves Opus tokens; eliminates wholesale-regen weakness.

### Sprint N — Named Version Cards + branching history
*Source: AI-codegen cluster (Lovable Versioning 2.0).*

Replace linear undo/redo with a version-card timeline.
- Every AI turn and every visual edit creates a **labelled card** in a right-side history rail (e.g. "Switched theme to Onyx", "Replaced funnel with pyramid").
- Restoring an old card **forks** rather than overwrites; sidebar shows tree, not stack.
- Side-by-side compare two cards.
- Each card stores snapshot id + prompt + AI-generated one-line summary.
- Builds on existing `workflow_history` snapshot machinery.

### Sprint O — Slide Collections (CMS-for-decks) + read-only Decks API
*Source: Site-builders cluster (Webflow Next-Gen CMS) + Marketing/CMS cluster (Contentful CDA).*

Bind AssetShape fields to columns of a user-supplied Collection.
- Connectors: CSV upload, Notion DB, Airtable, Google Sheet (ship 5 + manual CSV first).
- Templates bind shape fields to columns: "Portfolio Logos" → one tile per row; "KPI Hero" → value/label/delta; "Comparison Columns" → iterates rows.
- One refresh re-renders every bound slide.
- **Headless API**: `/v1/decks`, `/v1/decks/:id/slides/:idx` returning versioned JSON (theme, shapes, text, assets); query params `?format=json|pptx|pdf|html`, `?locale=`, `?variant=`. API-key auth, per-key rate limit.
- Unlocks board updates, fund quarterlies, monthly reports — the killer use-case slideAtelier currently can't serve.

### Sprint P — Deck Variants + Localisation
*Source: Marketing/CMS cluster (Unbounce Smart Traffic + Contentful localisation).*

One canonical deck → N output forms.
- **Variants**: `DeckVariant` table FK to parent `Deck`. Variants share theme + asset library + fonts; each owns its own slide list. Tab strip across top of Wireframe stage; "Duplicate as variant" on any slide. Public share link takes `?audience=board|sales|investor`; default served if absent.
- **Localisation**: refactor text fields from `str` to `LocalisedString({en, es, fr, …})` with default + fallback chain. Export menu picks locale → renders pptx in that language. AI "Translate deck to {locale}" action runs Claude over text blocks.
- v1: text only (assets/charts use fallback); RTL deferred.
- Composes cleanly: `(variant, locale)` tuple addresses each rendering.
- v2: ML routing rules ("if `utm_source=linkedin` → sales").

### Sprint Q — Slide Analytics
*Source: Design/creative cluster (Ceros) + Marketing/CMS cluster (Unbounce dashboard).*

View-side observability for Web Decks.
- Beacon in public web embed posts `{deck_id, variant_id, slide_id, event, ts, anon_session}` to `/v1/events`.
- Events: `slide_enter`, `slide_exit`, `cta_click`, `share`.
- Aggregate nightly into `slide_metrics` table.
- Dashboard: deck-level funnel (slide 1 → N retention), per-slide dwell median + p90, CTA CTR on linked shapes.
- Privacy: anon session IDs, IP-stripped, opt-out toggle per deck. No heatmaps v1.

### Sprint R — Roles + Draft/Review/Publish workflow
*Source: Marketing/CMS cluster (Contentful Workflows, scaled down).*

Lightweight collaboration without Contentful's enterprise weight.
- `DeckRole` enum: Owner / Editor / Reviewer / Viewer.
- Deck `state` field: `draft | in_review | published`.
- Per-slide threaded comments; reviewers approve or request changes.
- N approvals required to publish (configurable, default 0 for solo users).
- Publishing freezes the canonical variant URL; subsequent edits create a new draft revision.
- SSO + audit log deferred to enterprise tier.

### Sprint S — Marketplace + Recipe Library + slideAtelier Skills
*Source: Design/creative cluster (Framer 0%-cut marketplace) + Site-builders cluster (Webflow 95% creator share + recipe remixing) + AI-codegen cluster (Claude Code Skills/MCP).*

Three converging extension surfaces shipped together because they share one creator economy.
- **Marketplace**: sellable bundles of AssetShape sequences + Theme + sample narrative. Tiers: $19 single deck / $49 pack / $99 industry kit. **90% creator share**, fulfillment-link export for off-platform sales. Curated submission queue enforces native-primitive output.
- **Recipe Library**: every public deck export becomes (with consent) a remixable recipe = AssetShape sequence + theme + narrative skeleton. Target 500 in 6 months via opt-in.
- **Skills**: `/skills` directory of markdown+JSON folders defining custom AssetShapes, brand-guideline checkers, content-source connectors (Notion/GDocs/Confluence ingest). Auto-discovered by intent, MCP-style tool-search to avoid context bloat.
- Strategic moat: every contributed shape/skill is a lock-in artifact rivals can't replicate without rebuilding the framework.

### Sprint T — Slide Motion Layer (.pptx-native animation export)
*Source: Design/creative cluster (Framer animation system).*

Closes Framer's biggest superiority lever without breaking native-editability.
- Build-in / build-out / emphasis animations on shapes.
- Scroll-trigger reveals.
- Mapped one-to-one onto python-pptx animation primitives (PowerPoint Fade / Wipe / Appear / Emphasize).
- Same timeline rendered as CSS / Framer-Motion preview in browser for share links.
- **Hard rule**: no freeform animation hacks. If it can't be expressed in pptx primitives, it doesn't ship.

### Sprint U — Real-time collaborative editing
*Source: Design/creative cluster (Figma + Ceros).*

Table-stakes for team-tier upsell.
- Y.js or Liveblocks-backed presence cursors.
- Concurrent editing in Wireframe stage.
- Composes with Sprint R roles.

### Sprint V — Deck Audit (linter)
*Source: Site-builders cluster (Webflow AI SEO/AEO audits).*

Sitewide-equivalent linter for decks.
- Flags: slides >40 words, heading-hierarchy breaks, off-theme color overrides, unused asset slots, duplicated content, missing speaker notes on critical slides, accessibility (contrast, alt text).
- Per-issue "Fix" button + bulk "Fix all" action.
- Runs on demand and as a publish gate (configurable per workspace).

### Sprint W — Brief Inbox (paste-to-deck inbound entry)
*Source: AI-codegen cluster (Codex GitHub-issue-to-PR pattern).*

Fourth entry-point alongside the three-pronged chooser, optimised for "brief lands in your DMs."
- Paste a Slack thread / Notion brief / email / Google Doc URL.
- slideAtelier emits a **draft deck** PR-style with a **diff against the brief's stated goals**, ready for stakeholder review.
- Same Outline Plan Mode (Sprint M) gates the actual generation.
- Reuses Sprint K brand tokens for instant brand fit.

### Sprint Z — Selective text stripping on library_asset copy (✅ shipped 2026-05-08)
*Source: user — observed mid-Sprint-J browser test 2026-05-08.*

When attaching a library .pptx slide as an extra, current behaviour
(Sprint A's `copy_slide_shapes_onto(strip_text=True)`) strips **every**
text frame indiscriminately. That's blunter than the user wants: it
deletes diagram annotations the user came for (quadrant labels, funnel
step names, axis labels, callouts) along with the irrelevant outer chrome
(original slide title, subtitle, source caption, page footer).

**The fix**: distinguish *outer chrome* from *inline-to-diagram* text.

Heuristics for v1:
- **Strip**: text frames whose shape is at the slide periphery (top 12% / bottom 8% / outside the bounding box of the slide's "main" shape group), or whose font size ≥ 28pt and word count ≤ 8 (slide-title-like), or whose shape name matches `Title 1`/`Subtitle 1`/`Footer 1` placeholders.
- **Keep**: text frames whose shape is INSIDE another shape's bounding rect (annotation on top of a primitive), or part of a group, or font size < 24pt and short (single-line callout), or whose shape is a `TEXT_BOX` adjacent to a primitive (within ~1cm).

Implementation (shipped):
- `copy_slide_shapes_onto` now accepts `strip_text: bool | str` with three
  modes — `"none"` / `"chrome_only"` (default) / `"all"`. Backward-compat:
  `True` → `"all"`, `False` → `"none"`.
- New helpers in `asset_copier.py`: `_is_chrome_text(shape, w, h)` (4-layer
  heuristic) and `_blank_shape_text(shape)`. Heuristic checks: (1) placeholder
  type — TITLE/SUBTITLE/FOOTER/SLIDE_NUMBER/HEADER/DATE; (2) top-12% +
  titlish-text (font ≥24pt OR ≤8 words); (3) vertical centre below 92%;
  (4) shape-name pattern match.
- Strip happens on python-pptx shape objects pre-scaling, so position
  percentages reference the source slide's dimensions correctly.
- `tests/test_strip_text_modes.py`: 11 tests building a synthetic source
  slide with title + footer + page-number + 4 quadrant annotations + axis
  label, verifying each strip mode behaves correctly + heuristic unit tests.

**Z.v2 (✅ shipped same day)** — live SVG translation of library_asset
extras in the Web Deck viewer. Replaces the baked thumbnail PNG with native
inline SVG so chrome stripping applies, text reflows, and theme tokens hot-
swap on the published web view (matching the .pptx attach path bit-for-bit).
- New module `src/slideatelier/library_to_svg.py` — walks the source slide's
  shapes recursively (descends into GROUPs), dispatches on shape_type /
  auto_shape_type, emits native SVG primitives (`<rect>`, `<ellipse>` /
  `<circle>`, `<polygon>`, `<line>`, `<image>` for pictures, `<text>` /
  `<tspan>` for text frames). Outer `<svg>` uses `viewBox="0 0 W H"` in
  source EMU so each shape uses native coords directly.
- Handles common AutoShape geometries: RECTANGLE, ROUNDED_RECTANGLE, OVAL,
  RIGHT_TRIANGLE, ISOCELES_TRIANGLE, DIAMOND, CHEVRON, PENTAGON, HEXAGON.
  Other AutoShape types fall back to a bounding-box `<rect>`.
- Pictures: base64-embedded as `data:` URLs so the SVG is self-contained.
- Charts / tables / freeform custGeom: low-key bounding-box placeholder so
  the layout footprint stays correct (worst case is identical to v1
  thumbnail).
- Reuses `_is_chrome_text` from `asset_copier.py` so the heuristic is shared.
- WebRenderer's `_extra_svg` calls `library_asset_to_svg` first; on any
  exception falls through to the legacy thumbnail `<img>` (worst case
  unchanged).
- Routes wired to construct the WebRenderer with the loaded LibraryCatalog.
- 9 new tests in `test_library_to_svg.py` covering well-formed XML, native
  primitive emission, all three strip modes, viewBox in source EMU,
  invalid-index error, unknown shape fallback, group recursion. End-to-end
  publish test passed against the user's actual DIFC deck (8 inline `<svg>`s,
  0 thumbnails, 0 placeholders).

Total tests: 118 (was 98 pre-Sprint-Z; +20 across Z.v1 + Z.v2).

### Sprint X — Typography depth
*Source: Design/creative cluster (Readymag 5,000 fonts + Adobe Fonts).*

Upgrade Sprint C's font system to design-tool parity.
- OpenType feature toggles: ligatures, small caps, stylistic sets, contextual alternates, fractions.
- Variable-font axis controls (weight, width, slant, optical size).
- Adobe Fonts integration alongside the existing Google Fonts catalog.
- Per-shape type overrides surfaced in the Atelier Copilot.

---

## Sprint Y — Freeform text blocks (in flight)

**Y.1 (✅ shipped 2026-05-08)** — wireframe-side body block can be lifted out of layout flow.
- New `slide.block_bbox: dict[str, dict]` field stores 0..1 normalised `{left,top,width,height}` overrides keyed by block name.
- New endpoint `/workflow/wireframe/<job_id>/update-block/<idx>/<name>` persists / clears a block bbox.
- WYSIWYG card renders body as an absolute-positioned overlay when bbox is set, with drag handle, 4-corner resize, and ↺ reset. Same UX as diagram extras.
- "📐 free position" hover-button on the body textarea promotes it to freeform on demand.
- Auto-promote on layout change: switching to `title` / `section_divider` (no visible body slot) auto-creates a sensible body bbox so content stays visible. Switching back drops the auto bbox so body returns to flex flow.
- Smart redistribute: `content↔two_column` swaps body↔body_left/body_right, conservative (never overwrites non-empty destinations).
- "📎 hidden content preserved" badge on cards where a layout doesn't render a populated slot.

**Y.3 (✅ shipped 2026-05-08)** — per-block typography overrides.
- New `slide.block_style: dict[str, dict]` field stores sparse style dicts keyed by block name. Supported keys: `font_family`, `font_size` (8..96), `color` (#RRGGBB), `bold`, `italic`, `align` (left|center|right|justify).
- New endpoint `/workflow/wireframe/<job_id>/update-block-style/<idx>/<name>` — each form field is independent; empty string clears that key; clearing all keys drops the entry.
- Hover toolbar inside every freeform-block overlay: font family dropdown (15 curated families), size input, color picker, B / I toggles, align cycle button, × clear-all.
- Renderer's `_apply_block_style_textbox()` layers block_style overrides on top of layout defaults; both `_add_textbox` and `_add_bullets` now accept the full styling palette (font_family, color, bold, italic, align, font_size) — previously bullets were locked to template body color/font.
- Round-trip test in `test_renderer_honors_block_style` verifies font.size/name/color/bold/italic + paragraph.alignment all reflect overrides in the exported .pptx.

**Y.2 (✅ shipped 2026-05-08)** — extended freeform to all text blocks AND wired .pptx renderer.
- Title / strap / body / body_left / body_right all support freeform (📐 free → drag → resize → ↺ reset). Two helper macros: `block_flow_wrap` for in-flow rendering with the 📐 button, `block_overlay` for absolute-positioned overlay rendering.
- python-pptx renderer's `_block_rect(slide, name)` projects 0..1 normalised bbox onto slide_width × slide_height in EMU. Each `_render_*` method uses the bbox rect if set, else its hardcoded `Inches(...)` default.
- Auto-promoted body bboxes (set by save endpoint when switching to title/section_divider) are now rendered into the .pptx — content survives the layout switch end-to-end.
- Test `test_renderer_honors_block_bbox` verifies round-trip via inspecting placed shape coordinates ±1% slop.

---

## Inflight bug fixes & polish

These get folded into sprints opportunistically:
- Library asset thumbnails still show placeholder text (lorem ipsum) — clean text-stripped thumbnails would be nicer (low priority since strip-on-copy ships in Sprint A).
- Need richer preview rendering when slide has multiple extras (currently only first-of-position renders).

---

## How requirements get added

1. User drops a requirement in chat
2. PM acknowledges briefly (1-2 sentences) without breaking from current sprint
3. PM appends to **Inbox** section above
4. At sprint boundary, PM triages inbox: assigns each item to an existing sprint OR creates a new sprint
5. PM proposes the next sprint to the user; user approves or redirects
