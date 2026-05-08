"""Storyboard stage — Stage 1 of the three-stage workflow.

A Storyboard is a deck's ARGUMENT STRUCTURE — title, core_message, narrative_arc,
and slide titles + layouts + purposes. NO body content yet. This is the
"whiteboard" stage — the consulting partner sketches the argument before any
prose gets written.

Design rationale: separating storyboard from full deck has three advantages:
1. Cheaper API call (~30% the tokens of a full deck) — fast iteration on
   structure
2. Forces commitment to the argument BEFORE writing prose (pyramid principle)
3. Editable as a discrete artifact — the user can rearrange / rename slides
   without touching content
"""
import time

from anthropic import Anthropic
from pydantic import BaseModel, Field
from rich.console import Console

from .cache import DeckCache, compute_input_hash
from .config import Config
from .metadata import PROMPT_VERSION, GenerationMetadata
from .models import LayoutType, Slide, SlideDeck

console = Console()

STORYBOARD_SYSTEM_PROMPT = """You are a senior management consulting partner sketching the ARGUMENT STRUCTURE of a deck before any prose gets written. This is the whiteboard stage — you commit to the thesis and the structure of the defense, but you do not write body content. That comes later.

# YOUR TASK

Given a brief, produce a Storyboard with:
- A `title` that states the deck's ANSWER (not the topic)
- A `subtitle` for audience/date/decision-required context
- A `core_message` — the single sentence the deck delivers if the audience reads only one thing
- A `narrative_arc` — 2–3 sentences explaining how the slides connect to defend core_message
- Slides — each with layout, title (insight-led), and a 1-line `purpose` describing what this slide accomplishes for the argument

# CRITICAL RULES

1. **Insight-led titles only.** "Sales declined 12% YoY due to channel mix shift" ✓ vs "Sales results" ✗. Every title must be an argument, not a label.

2. **Pyramid principle.** Open with the answer (the deck's title and an early key_takeaway both restate core_message). Defend it.

3. **MECE structure.** When the deck splits a problem into parts, no overlap, no gaps.

4. **One idea per slide.** If the slide's purpose can't be stated in one sentence, split it.

5. **Layout fits the argument.** Use `two_column` only for genuine comparisons. Use `key_takeaway` only at narrative inflection points (1–2 per deck max).

6. **Length matches density.** 8–12 slides for a 30-min board slot, 4–7 for a 10-min update, 16–25 for a 60-min deep-dive. Don't pad with filler.

# WHAT YOU DO NOT WRITE

- No body bullets (those are wireframe stage)
- No speaker notes (wireframe stage)
- No specific data points or quotes (wireframe stage)
- No design choices (hi-fi stage)

You're committing to the SHAPE of the argument. Body and prose come next.

# OUTPUT FORMAT

For each slide, write:
- `layout`: which layout type
- `title`: the slide's takeaway, insight-led
- `purpose`: 1 sentence explaining what this slide accomplishes (e.g., "Diagnoses the three structural weaknesses in SMB", "Lands the financial impact before the asks")
- `rationale`: 1 sentence explaining WHY this layout suits this slide's purpose

Return the Storyboard."""


class StoryboardSlide(BaseModel):
    layout: LayoutType = Field(
        description="Which layout type — see SYSTEM_PROMPT for layout reference"
    )
    title: str = Field(
        description=(
            "The slide's takeaway, NOT its topic. Insight-led. "
            "Good: 'Enterprise drove all Q3 growth; SMB is breaking'. "
            "Bad: 'Q3 segment performance'. ≤14 words. No trailing punctuation."
        )
    )
    purpose: str = Field(
        description=(
            "1 sentence explaining what this slide accomplishes for the argument. "
            "Example: 'Diagnoses the three structural weaknesses in SMB' or "
            "'Lands the financial impact before asking for the decisions'. "
            "Used by the wireframe stage to know what body content to write."
        )
    )
    rationale: str = Field(
        default="",
        description="1 sentence explaining WHY this layout suits this slide's purpose."
    )


class Storyboard(BaseModel):
    title: str = Field(
        description="Deck title — should state the ANSWER, not the topic. ≤12 words."
    )
    subtitle: str = Field(
        default="",
        description="Audience, date, or context line. e.g. 'Board review · Q3 2026 · Decision required'."
    )
    core_message: str = Field(
        description=(
            "The single sentence the deck delivers if the audience reads only one thing. "
            "Forces pyramid-principle commitment. ≤25 words."
        )
    )
    narrative_arc: str = Field(
        description=(
            "2–3 sentences explaining how slides connect to defend core_message. "
            "Where the deck starts, what it diagnoses or argues, where it lands."
        )
    )
    slides: list[StoryboardSlide] = Field(
        description=(
            "Slides in presentation order. Aim for 8–12 for a 30-min slot, 4–7 for a 10-min update, "
            "16–25 for a 60-min deep-dive. Match length to the brief's density."
        )
    )


def plan_storyboard(
    client: Anthropic,
    config: Config,
    content: str,
    requirements: str = "",
    *,
    regenerate: bool = False,
) -> tuple[Storyboard, GenerationMetadata]:
    """Stage 1: brief → storyboard. Cheaper and faster than full deck planning
    because we skip body content. Used as the editable input to Stage 2 (wireframe).
    """
    cache_key_extra = "STORYBOARD"  # ensures storyboard cache keys don't collide with full-deck keys
    input_hash = compute_input_hash(
        system_prompt=STORYBOARD_SYSTEM_PROMPT + "::" + cache_key_extra,
        brief=content,
        requirements=requirements,
        model_id=config.model,
        prompt_version=PROMPT_VERSION,
    )

    # Use a separate cache namespace for storyboards
    cache = DeckCache(config.cache_dir / "storyboards", enabled=config.cache_enabled)

    if not regenerate:
        cached = cache.get(input_hash)
        if cached is not None:
            # cache stores SlideDeck-shaped JSON; we hijack its file storage by
            # serializing Storyboard with the same shape via model_validate_json
            # but actually we want a separate cache type. Let me reload as Storyboard.
            # Simpler path: just re-call the API on cache miss; storyboard is cheap.
            pass  # noqa — see note below

    user_prompt = f"Source content:\n\n{content}"
    if requirements:
        user_prompt += f"\n\nAdditional requirements:\n{requirements}"
    user_prompt += "\n\nSketch the storyboard. Title + core_message + narrative_arc + slide list. No body content."

    started = time.monotonic()
    response = client.messages.parse(
        model=config.model,
        max_tokens=8000,  # storyboards are short
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": STORYBOARD_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
        output_format=Storyboard,
    )
    duration = time.monotonic() - started

    if response.parsed_output is None:
        raise RuntimeError("Storyboard output failed schema validation")

    metadata = GenerationMetadata(
        model_id=config.model,
        model_response_id=getattr(response, "model", None),
        prompt_version=PROMPT_VERSION,
        cache_hit=False,
        input_hash=input_hash,
        duration_seconds=duration,
    )
    return response.parsed_output, metadata


# ----------------------------------------------------------------------------
# Stage 1 → Stage 2 expansion
# ----------------------------------------------------------------------------

EXPAND_SYSTEM_PROMPT = """You are flesh-ing out a draft deck from its storyboard. The argument structure is committed (storyboard given). Your job: write the body content — bullets, columns, speaker notes — that DEFENDS each slide's title.

# CRITICAL RULES

1. The storyboard is FIXED. Do not change titles, layouts, or core_message. You write body content only.
2. Each bullet must defend its slide's title. If you can't argue for a bullet, drop it.
3. 3–5 bullets per slide max. ≤14 words each. Parallel sentence structure within a slide.
4. Use specifics. Cite numbers, dates, names. No "significant", "considerable", "various".
5. For two_column slides: populate body_left AND body_right with parallel structure (same item count, same level of detail, mirrored framing).
6. For key_takeaway slides: title carries the weight; body is at most a one-line evidence reference.
7. For section_divider slides: no body. The title is the whole slide.
8. For closing slides: each bullet is an action — verb + object + (timeline / owner where possible).
9. Speaker notes for non-trivial slides: explain the slide's role, the supporting data to reference, likely audience pushback.

Use the slide's `purpose` field as your guide for what to argue.

Return a SlideDeck where the structural fields (title, subtitle, core_message, narrative_arc, slide titles, slide layouts) match the storyboard exactly, and the body content is filled in."""


def expand_storyboard_to_deck(
    client: Anthropic,
    config: Config,
    storyboard: Storyboard,
    content: str,
    requirements: str = "",
) -> tuple[SlideDeck, GenerationMetadata]:
    """Stage 2: storyboard + brief → full SlideDeck with body content.

    Preserves the storyboard's argument structure (titles, layouts, core_message,
    narrative_arc) and fills in the body. The user has approved the structure;
    we don't second-guess it.
    """
    user_prompt = f"""# Source brief

{content}
"""
    if requirements:
        user_prompt += f"\n# Additional requirements\n\n{requirements}\n"

    user_prompt += f"""
# Approved storyboard (DO NOT CHANGE titles, layouts, core_message, or narrative_arc)

deck.title: {storyboard.title}
deck.subtitle: {storyboard.subtitle}
core_message: {storyboard.core_message}
narrative_arc: {storyboard.narrative_arc}

slides:
"""
    for i, s in enumerate(storyboard.slides):
        user_prompt += f"""  [{i}] layout={s.layout}
       title: {s.title}
       purpose: {s.purpose}
"""

    user_prompt += "\n\nFlesh out the body content for each slide. Return the full SlideDeck."

    started = time.monotonic()
    response = client.messages.parse(
        model=config.model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": EXPAND_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
        output_format=SlideDeck,
    )
    duration = time.monotonic() - started

    if response.parsed_output is None:
        raise RuntimeError("Expand-storyboard output failed schema validation")

    metadata = GenerationMetadata(
        model_id=config.model,
        model_response_id=getattr(response, "model", None),
        prompt_version=PROMPT_VERSION,
        cache_hit=False,
        input_hash="",  # no caching for the expansion step (storyboard input may have been edited)
        duration_seconds=duration,
    )

    deck = response.parsed_output

    # Defensive: if the model accidentally renamed a slide title or changed a layout,
    # restore from storyboard so the user's approved structure wins.
    if len(deck.slides) == len(storyboard.slides):
        new_slides = []
        for storyboard_slide, deck_slide in zip(storyboard.slides, deck.slides):
            new_slides.append(deck_slide.model_copy(update={
                "layout": storyboard_slide.layout,
                "title": storyboard_slide.title,
            }))
        deck = deck.model_copy(update={
            "slides": new_slides,
            "title": storyboard.title,
            "subtitle": storyboard.subtitle,
            "core_message": storyboard.core_message,
            "narrative_arc": storyboard.narrative_arc,
        })

    return deck, metadata
