"""AI critique-and-refine loop — the differentiating feature.

After a deck is generated, this module runs a SECOND Claude call that
critiques the deck slide-by-slide as a senior consulting partner reviewing
a junior's draft. Each critique is structured (verdict, severity, reason,
suggestion) so the UI can present it as accept/reject/edit decisions.

Design principle: the critic is the partner who marks up the analyst's
work. It's confident, opinionated, and concrete. No "consider revising"
hedge — direct, specific recommendations.
"""
import time
from typing import Literal

from anthropic import Anthropic
from pydantic import BaseModel, Field
from rich.console import Console

from .config import Config
from .models import SlideDeck

console = Console()

# Bumped independently of PROMPT_VERSION (the planner's prompt). Critic has
# its own evolution.
CRITIC_VERSION = "v1.0.0"


CRITIC_SYSTEM_PROMPT = """You are a senior management consulting partner (think McKinsey EM / BCG MDP) reviewing a junior consultant's draft deck. The junior has spent the night working on this. You have 15 minutes before the client meeting.

Your job: review the deck slide by slide. For each slide, produce a structured critique. Be concrete, specific, and direct — your junior is good and respects the truth. Don't hedge with "consider" or "you might want to". Either the slide works or it doesn't, and if it doesn't you say exactly what to change.

# WHAT YOU'RE LOOKING FOR (in priority order)

1. **Insight-led titles**: Does the title state the takeaway, or just the topic? "Sales declined 12% YoY due to channel mix shift" ✓ vs "Sales results" ✗
2. **Pyramid principle**: Does the deck open with the answer? Does each slide defend it?
3. **MECE**: When the deck splits a problem into parts, are they Mutually Exclusive and Collectively Exhaustive?
4. **Specifics over vagueness**: "$2.4M, 34% YoY, 63 days" — never "significant", "considerable", "various"
5. **Layout fit**: Is the layout choice serving the argument? `two_column` only when comparing; `key_takeaway` only at inflection points
6. **Bullet quality**: 3–5 per slide, parallel structure, ≤14 words each, all defending the title
7. **Closing strength**: Are the recommendations specific actions with verb + object + (timeline / owner)?
8. **Narrative arc**: Does the deck flow? Is there filler? Is there a missing slide?

# YOUR OUTPUT FORMAT

For each slide, produce a SlideCritique. For the deck overall, produce a DeckCritique.

Severity scale:
- "fatal" = the slide undermines the deck's argument; do not ship as-is
- "major" = significantly weakens the slide; should fix before showing the client
- "minor" = polish; nice-to-fix but not critical
- "ok" = the slide does its job

For "ok" slides, still write a one-line `verdict` confirming it works. Don't pad with critique that doesn't exist.

For non-ok slides, the `suggestion` field MUST be:
- Concrete enough that a junior could implement it without asking follow-up questions
- Specific to THIS slide's content, not generic advice
- A direct rewrite when applicable ("Change title from 'Sales results' to 'Q3 sales beat plan by 4%, but enterprise carried it'")

# OVERALL DECK CRITIQUE

After scoring slides, write a DeckCritique with:
- `core_message_assessment`: is the stated core_message sharp enough to be a single-slide answer? If not, propose a sharper version.
- `narrative_arc_assessment`: does the deck actually deliver on the arc it committed to? Where does it break?
- `weakest_slide`: the single highest-leverage fix
- `missing_slide`: if there's a slide the deck NEEDS but doesn't have, describe it (title + what it should argue). Otherwise null.
- `overall_verdict`: 1-2 sentences. Is this client-ready? What needs to change first?

Be the partner you'd want reviewing your work. Pretend the junior is going to read this and decide what to fix."""


SeverityLevel = Literal["fatal", "major", "minor", "ok"]


class SlideCritique(BaseModel):
    slide_index: int = Field(description="0-based index of the slide being critiqued")
    layout: str = Field(description="The slide's layout (echoed for reference)")
    current_title: str = Field(description="The slide's current title (echoed for reference)")
    severity: SeverityLevel
    verdict: str = Field(description="1-sentence summary of how this slide is doing")
    issues: list[str] = Field(
        default_factory=list,
        description=(
            "Specific issues with this slide. Empty if severity='ok'. "
            "Each issue is a concrete observation, not generic advice. "
            "e.g., 'Title is descriptive, not insight-led — labels the topic without stating what we found.'"
        ),
    )
    suggestion: str = Field(
        default="",
        description=(
            "Concrete fix. For severity != 'ok', this MUST be specific enough that "
            "a junior could implement it. Often a literal rewrite. Empty if severity='ok'."
        ),
    )
    proposed_title: str | None = Field(
        default=None,
        description=(
            "If you're proposing a new title, put it here verbatim. "
            "The UI will surface this as a one-click 'apply' option."
        ),
    )


class DeckCritique(BaseModel):
    core_message_assessment: str = Field(
        description="Is the stated core_message sharp? If not, propose a sharper version verbatim."
    )
    narrative_arc_assessment: str = Field(
        description="Does the deck deliver on the arc it committed to? Where does it break?"
    )
    weakest_slide_index: int | None = Field(
        default=None,
        description="0-based index of the slide that most weakens the deck. Fix this first.",
    )
    missing_slide: str | None = Field(
        default=None,
        description=(
            "If there's a slide the deck NEEDS but doesn't have, describe it (title + thesis). "
            "Otherwise null. Don't invent a missing slide just to fill this field."
        ),
    )
    overall_verdict: str = Field(
        description="1-2 sentences. Is this client-ready? What needs to change first?"
    )


class CritiqueResult(BaseModel):
    critic_version: str
    model_id: str
    duration_seconds: float
    slide_critiques: list[SlideCritique]
    deck_critique: DeckCritique

    @property
    def fatal_count(self) -> int:
        return sum(1 for c in self.slide_critiques if c.severity == "fatal")

    @property
    def major_count(self) -> int:
        return sum(1 for c in self.slide_critiques if c.severity == "major")

    @property
    def minor_count(self) -> int:
        return sum(1 for c in self.slide_critiques if c.severity == "minor")

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.slide_critiques if c.severity == "ok")

    @property
    def is_client_ready(self) -> bool:
        return self.fatal_count == 0 and self.major_count == 0


class _CritiqueResponseSchema(BaseModel):
    """The shape we ask Claude to return. Wraps slide+deck critiques together
    so they're produced in one pass."""
    slide_critiques: list[SlideCritique]
    deck_critique: DeckCritique


def critique_deck(
    client: Anthropic,
    config: Config,
    deck: SlideDeck,
) -> CritiqueResult:
    """Run the critique pass on a generated deck. Returns structured critiques."""
    user_prompt = f"""Here is the draft deck to review.

# Deck-level metadata

Title: {deck.title}
Subtitle: {deck.subtitle or "(none)"}
Core message: {deck.core_message}
Narrative arc: {deck.narrative_arc}

# Slides

"""
    for i, slide in enumerate(deck.slides):
        user_prompt += f"\n## Slide {i} — layout: {slide.layout}\n"
        user_prompt += f"Title: {slide.title or '(empty)'}\n"
        if slide.body:
            user_prompt += "Body:\n"
            for b in slide.body:
                user_prompt += f"  - {b}\n"
        if slide.body_left:
            user_prompt += "Left column:\n"
            for b in slide.body_left:
                user_prompt += f"  - {b}\n"
        if slide.body_right:
            user_prompt += "Right column:\n"
            for b in slide.body_right:
                user_prompt += f"  - {b}\n"
        if slide.speaker_notes:
            user_prompt += f"Speaker notes: {slide.speaker_notes[:200]}\n"
        if slide.rationale:
            user_prompt += f"(Layout rationale: {slide.rationale})\n"

    user_prompt += "\n\nReview this deck. Produce a SlideCritique for each slide and a DeckCritique for the whole."

    started = time.monotonic()
    response = client.messages.parse(
        model=config.model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": CRITIC_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
        output_format=_CritiqueResponseSchema,
    )
    duration = time.monotonic() - started

    if response.parsed_output is None:
        raise RuntimeError("Critic output failed schema validation")

    parsed = response.parsed_output
    return CritiqueResult(
        critic_version=CRITIC_VERSION,
        model_id=config.model,
        duration_seconds=duration,
        slide_critiques=parsed.slide_critiques,
        deck_critique=parsed.deck_critique,
    )


def apply_critique_to_deck(
    deck: SlideDeck,
    critique: CritiqueResult,
    *,
    accept_severities: tuple[str, ...] = ("fatal", "major"),
) -> tuple[SlideDeck, list[int]]:
    """Apply the critic's `proposed_title` for any slide whose severity is in
    accept_severities. Returns (revised_deck, list_of_slide_indices_changed).

    More sophisticated revision (regenerating bodies, swapping layouts) requires
    re-calling the planner with the critique as guidance — that's planner.refine_deck()
    in v0.8. For now, title swaps are the safe, atomic, reviewable change.
    """
    changed: list[int] = []
    new_slides = list(deck.slides)
    for c in critique.slide_critiques:
        if c.severity not in accept_severities:
            continue
        if not c.proposed_title:
            continue
        if c.slide_index < 0 or c.slide_index >= len(new_slides):
            continue
        new_slides[c.slide_index] = new_slides[c.slide_index].model_copy(
            update={"title": c.proposed_title}
        )
        changed.append(c.slide_index)
    revised = deck.model_copy(update={"slides": new_slides})
    return revised, changed
