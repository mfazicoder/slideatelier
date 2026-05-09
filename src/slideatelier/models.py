import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

LayoutType = Literal[
    "title",
    "section_divider",
    "content",
    "two_column",
    "bullet_list",
    "key_takeaway",
    "closing",
]


class SlideExtra(BaseModel):
    """A growable add-on attached to a slide. Rendered alongside the slide's
    normal layout content. Supported `type` values grow over time; the renderer
    no-ops gracefully on unknown types."""
    type: str = Field(
        description=(
            "Extra kind. Currently supported: "
            "'library_asset' (stamp a slide from the asset library at a smaller scale). "
            "Coming next: 'chart_bar', 'chart_donut', 'chart_line', 'matrix_2x2', 'metric_callout'."
        )
    )
    position: Literal["right", "below", "left", "inline"] = Field(
        default="right",
        description="Where on the slide to render the extra. 'right' is most common for diagrams.",
    )
    config: dict = Field(
        default_factory=dict,
        description=(
            "Type-specific config. For library_asset: {'asset_ref': '<id>'}. "
            "For chart_bar: {'categories': [...], 'series': [...]}. "
            "For matrix_2x2: {'x_label': '...', 'y_label': '...', 'quadrants': [...]}. "
            "Optional WYSIWYG-edit keys (any extra type): "
            "'bbox' = {left, top, width, height} as floats in 0..1 (slide-relative); "
            "when present the renderer paints the extra at that exact rectangle, "
            "overriding the position-based defaults. "
            "'color_override' = '#RRGGBB' that recolors the dominant solid fill of "
            "the extra's shapes after stamping (preserves accent colors)."
        ),
    )


class Slide(BaseModel):
    layout: LayoutType = Field(
        description=(
            "Which layout to use. Pick by what the slide is *for*: "
            "'title' for the deck cover; "
            "'section_divider' to chapter-break the argument; "
            "'content' for the standard title+bullets workhorse; "
            "'bullet_list' as a semantic alias for content when the body is a parallel list; "
            "'two_column' ONLY when comparison IS the point (Before/After, Option A/B, Pros/Cons); "
            "'key_takeaway' for an emphasized single insight at a narrative inflection point (1–2 per deck max); "
            "'closing' for the final slide with specific actions."
        )
    )
    title: str = Field(
        description=(
            "The slide's takeaway, NOT its topic. State the insight, not the subject. "
            "Good: 'Sales declined 12% YoY due to channel mix shift'. "
            "Bad: 'Sales results' (just a topic). "
            "Bad: 'Q3 Analysis' (vague). "
            "Each title should be a complete thought a reader could grasp in 3 seconds. "
            "≤14 words. No trailing punctuation."
        )
    )
    strap: str = Field(
        default="",
        description=(
            "Optional strapline shown directly under the title — adds nuance, qualifies the headline, "
            "or anchors evidence. Like a deck under a newspaper headline. Keep it tight (≤18 words). "
            "Example: title='Enterprise drove all Q3 growth' / strap='While SMB stagnated for the first "
            "time in 8 quarters'. Leave empty for slides that don't need it."
        ),
    )
    body: list[str] = Field(
        default_factory=list,
        description=(
            "3–5 bullets, each ≤14 words. Each bullet must defend or elaborate the title's takeaway. "
            "Use parallel sentence structure across bullets (start them all the same way: noun-led, or verb-led). "
            "Prefer specifics ('$2.4M', '34% YoY') over vague claims ('significant', 'meaningful'). "
            "Empty for layouts that don't need body content (e.g., section_divider). "
            "For two_column layouts: leave empty and use body_left/body_right instead."
        ),
    )
    body_left: list[str] = Field(
        default_factory=list,
        description=(
            "Left column for two_column layout ONLY. Must be parallel in length and framing to body_right "
            "(same number of items, same level of detail, mirrored sentence structure)."
        ),
    )
    body_right: list[str] = Field(
        default_factory=list,
        description="Right column for two_column layout ONLY. Must mirror body_left's structure.",
    )
    speaker_notes: str = Field(
        default="",
        description=(
            "Notes for the presenter — explain the slide's role in the larger argument, "
            "the supporting data the presenter should reference, and likely audience questions. "
            "Not visible to the audience; visible in PowerPoint's notes pane."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "1-sentence explanation of WHY this layout was chosen for this content. "
            "Visible only in the spec JSON for debugging — not rendered to the deck. "
            "Helps iterate on the model's layout-selection logic."
        ),
    )
    asset_ref: str | None = Field(
        default=None,
        description=(
            "Legacy: reference to a library slide that REPLACES this slide entirely. "
            "When set, the renderer stamps the library slide instead of building from scratch. "
            "For ADDING a diagram alongside the slide's normal content, use `extras` instead — that's "
            "the growable composition path. asset_ref is preserved for the case where you want a slide "
            "to BE a library slide (no title/body of our own)."
        ),
    )
    extras: list[SlideExtra] = Field(
        default_factory=list,
        description=(
            "Growable add-ons rendered ALONGSIDE this slide's normal layout content. "
            "Each extra has a type (library_asset, chart_bar, chart_donut, matrix_2x2, metric_callout, …) "
            "and a position hint (right, below, inline). Use to attach a diagram or chart to a content "
            "slide without replacing the slide's title/body."
        ),
    )
    block_bbox: dict[str, dict] = Field(
        default_factory=dict,
        description=(
            "Per-block bbox overrides for freeform layout in the wireframe. Keys are block names "
            "('title', 'strap', 'body', 'body_left', 'body_right'); values are normalized 0..1 "
            "{left, top, width, height} relative to the 16:9 slide area. When present, the wireframe "
            "renders the block at that exact bbox instead of using the layout-template's default flow. "
            "Empty dict = use layout defaults. Set by the WYSIWYG drag/resize UI."
        ),
    )
    block_style: dict[str, dict] = Field(
        default_factory=dict,
        description=(
            "Per-block typography overrides for freeform text blocks. Keys are block names "
            "('title', 'strap', 'body', 'body_left', 'body_right'); values are a sparse style "
            "dict with any of: 'font_family' (str), 'font_size' (int 8..96), 'color' (#RRGGBB), "
            "'bold' (bool), 'italic' (bool), 'align' ('left'|'center'|'right'|'justify'). "
            "Missing keys fall back to the layout's defaults; empty dict = full default styling. "
            "Set by the per-block typography toolbar in the wireframe."
        ),
    )


_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _normalize_hex(value: str) -> str:
    """Normalize a hex string to '#RRGGBB' uppercase form. Raises ValueError on malformed input."""
    m = _HEX_RE.match(value.strip())
    if not m:
        raise ValueError(
            f"Invalid hex color: {value!r}. Expected '#RRGGBB' or 'RRGGBB'."
        )
    return "#" + m.group(1).upper()


class BrandKit(BaseModel):
    """Per-workspace brand tokens. When set, override the active theme's
    palette + typography at render time so every existing AssetShape and
    SlideTemplate rebrands without re-running generation.

    Storage is JSON at output/brand_kit.json (anonymous) or
    output/users/<user_id>/brand_kit.json (authenticated). The renderer reads
    the kit once per render via ShapeRenderContext.brand_kit and the
    effective_*() helpers fall back to theme values when a field is None.
    """

    logo_path: str | None = None
    color_primary: str = "#0F172A"
    color_secondary: str = "#3B82F6"
    color_accent: str | None = None
    color_bg: str = "#FFFFFF"
    color_text: str = "#0F172A"
    type_display: str = "Inter"
    type_body: str = "Inter"
    type_mono: str = "JetBrains Mono"
    audience_tone: Literal["formal", "approachable", "bold", "minimal"] = "approachable"

    @field_validator("color_primary", "color_secondary", "color_bg", "color_text")
    @classmethod
    def _validate_required_hex(cls, v: str) -> str:
        return _normalize_hex(v)

    @field_validator("color_accent")
    @classmethod
    def _validate_optional_hex(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _normalize_hex(v)


class BriefAnalysis(BaseModel):
    """Structured read-out of a raw brief — what the deck author actually
    asked for vs. what the brief leaves implicit. Sprint W (Brief Inbox).

    Used as the second return value from a Claude call that ALSO produces a
    Storyboard. The pair lets the user see the deck draft side-by-side with
    a critique of the brief itself, then decide whether to continue or
    re-prompt with corrections.
    """
    stated_goals: list[str] = Field(
        default_factory=list,
        description=(
            "What the brief explicitly says it wants the deck to accomplish. "
            "Quote-or-paraphrase short statements (≤14 words each). 1–5 items."
        ),
    )
    audience: str = Field(
        default="",
        description=(
            "Who the deck is for, as inferred from the brief. Job role / "
            "decision-making context / time pressure if known. ≤30 words. "
            "Empty if the brief gives no signal."
        ),
    )
    key_messages: list[str] = Field(
        default_factory=list,
        description=(
            "The argument lines the deck must land. Each is the takeaway form "
            "(verb + object), not the topic. 2–5 items, ≤18 words each."
        ),
    )
    risks_unaddressed: list[str] = Field(
        default_factory=list,
        description=(
            "Gaps, ambiguities, or pushbacks the brief leaves unanswered that "
            "the deck author should resolve before final review. 0–5 items, "
            "≤25 words each. Empty list is fine if the brief is tight."
        ),
    )


class SlideDeck(BaseModel):
    title: str = Field(
        description=(
            "The deck's overall title — should state the deck's ANSWER or recommendation, not its topic. "
            "Good: 'Concentrate on what's working: Q3 review'. "
            "Bad: 'Q3 Business Review' (just topic). "
            "≤12 words."
        )
    )
    subtitle: str = Field(
        default="",
        description="Audience, date, or context line shown on the title slide. e.g., 'Board review · Q3 2026'.",
    )
    core_message: str = Field(
        description=(
            "The single-sentence answer this deck delivers. If the audience could only read ONE thing, "
            "this is it. Forces pyramid-principle commitment before slide-by-slide writing. "
            "≤25 words. Must contain the recommendation or core insight, not just the topic. "
            "Example: 'Enterprise drove all Q3 growth while SMB broke; we should reallocate $2M to defend the lead.' "
            "Used as the foundation of the narrative arc — every slide must support this message."
        )
    )
    narrative_arc: str = Field(
        description=(
            "2–3 sentences explaining how the slides connect to defend core_message. "
            "Walks through: where the deck starts, what it diagnoses or argues in the middle, "
            "where it lands. Forces structural commitment before writing slides. "
            "Example: 'Open with the asymmetric quarter — one segment carrying us. Diagnose the three signals "
            "showing SMB is structurally breaking. Recommend a $2M reallocation and a $50K floor. Close with "
            "the specific decisions needed today.'"
        )
    )
    slides: list[Slide] = Field(
        description=(
            "Slides in presentation order. Aim for 8–15 slides for a 30-minute slot, 4–8 for a 10-minute slot, "
            "16–25 for a 60-minute deep-dive. Length should match content density of the brief — don't pad, don't cram."
        )
    )


class AuditIssue(BaseModel):
    """A single deck-audit (linter) finding.

    Sprint V — produced by slideatelier.audit.audit_deck. Defined here so the
    schema is shared with downstream consumers (CLI, web, tests) without
    pulling in the audit module's dependencies.
    """

    slide_idx: int = Field(
        description="0-based index of the offending slide. -1 for deck-level issues."
    )
    severity: Literal["error", "warning", "info"]
    code: str = Field(
        description="Stable code, e.g. 'BODY_TOO_LONG'. See audit.AUDIT_CODES."
    )
    message: str
    fix: dict | None = Field(
        default=None,
        description=(
            "Sparse {action: ...} dict describing how apply_audit_fixes() can resolve "
            "the issue, or None if the issue requires user action."
        ),
    )
