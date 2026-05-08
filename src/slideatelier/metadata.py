"""Generation metadata — wraps a SlideDeck with provenance info so we can trace
any deck back to the exact code/prompt/model that produced it."""
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .models import SlideDeck

# Bump this every time SYSTEM_PROMPT, the SlideDeck schema, or layout semantics
# change. The cache key includes this so old cache entries are invalidated when
# we change behavior.
#
# Version log:
#   v1.0.0 — initial release; minimal SYSTEM_PROMPT, no narrative metadata.
#   v1.1.0 — added core_message + narrative_arc + rationale; rewrote SYSTEM_PROMPT
#            with detailed per-layout rules, deck patterns, authoring rules,
#            and a worked Acme example. Crosses the 4096-token threshold so
#            Anthropic's prompt cache activates.
#   v1.2.0 — added asset_ref to Slide schema (optional). Asset library system
#            in place; LLM not yet asset-aware (manual asset_ref usage only).
#   v1.3.0 — three-stage workflow foundation. Added Storyboard primitive
#            (Stage 1) and expand_storyboard_to_deck (Stage 2). Added critic.py
#            (parked, ready for Stage 2 use). slideAtelier pivots to SaaS
#            three-stage editor architecture.
#   v1.3.1 — added optional `strap` field to Slide (sub-headline under title).
#            Renderer renders strap below title in muted color when present.
#            Wireframe edit card exposes strap as editable; layout selector now
#            shows a visual schematic icon.
#   v1.4.0 — wireframe stage redesigned: WYSIWYG 16:9 slide cards with template-
#            aware in-place editing (title/strap/body/columns are styled inputs
#            that look like the rendered output). Sidebar with storyline summary,
#            slide TOC, add-slide CTAs. Drag-and-drop reordering via SortableJS.
#            Added SlideExtra schema (rendering integration in v1.5).
#   v1.4.1 — sidebar polished: storyline editor uses click-to-edit AJAX inline
#            pattern (no duplicate right-side form); SLIDES TOC wraps titles,
#            shows layout type as text, supports drag handle + delete, and
#            absorbs the add-slide controls.
#   v1.5.0 — growable extras: library assets attachable to any slide as add-ons
#            via "+ Attach diagram" picker modal. Renderer stamps the asset on
#            the configured position (right/below/left/inline) and reflows body
#            width for right-positioned extras. Chart_bar / chart_donut /
#            matrix_2x2 schemas reserved for v1.6.
PROMPT_VERSION = "v1.5.0"


class GenerationMetadata(BaseModel):
    model_id: str = Field(description="Model alias used (e.g., 'claude-opus-4-7')")
    model_response_id: str | None = Field(
        default=None,
        description="The exact model string returned by Anthropic (may include date suffix on dated snapshots)",
    )
    prompt_version: str = Field(description="slideAtelier prompt/schema version")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cache_hit: bool = False
    input_hash: str = Field(description="SHA256 of (prompt + brief + requirements + model + prompt_version)")
    duration_seconds: float | None = Field(
        default=None,
        description="Wall-clock time for the API call (None for cache hits)",
    )


class GeneratedDeck(BaseModel):
    """The wrapper written to disk via --spec-out. Couples the model output
    with the metadata of how it was generated."""
    metadata: GenerationMetadata
    deck: SlideDeck
