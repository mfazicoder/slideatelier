"""Research stage — pre-storyboard analyst pass.

A ResearchDossier is the THINKING WORK done before any slide structure exists.
The model plays a senior consulting analyst: interrogating the brief,
identifying the questions the deck must answer, proposing applicable
frameworks, surfacing specific findings, committing to a hypothesis, and
stress-testing it with the strongest counter-arguments.

Design rationale: research separates "what's true / what to argue" from
"how to lay out the argument". The storyboard stage gets a richer prior —
named frameworks, named findings, a stated hypothesis — instead of having
to recover all of that from the raw brief while also choosing structure.
"""
import time

from anthropic import Anthropic
from pydantic import BaseModel, Field, field_validator

from .config import Config
from .metadata import PROMPT_VERSION, GenerationMetadata


RESEARCH_SYSTEM_PROMPT = """You are a senior management consulting analyst (think McKinsey EM / BCG project leader) doing the THINKING WORK before any slides get drafted. The brief is in front of you. You have a few hours of quiet time before the team meets to whiteboard the storyboard. Your job is to walk in with a sharp point of view.

# YOUR TASK

Given a brief, produce a ResearchDossier with:

1. `brief_summary` — a 2-line restatement of what the brief is actually asking. Strip the noise; surface the real question. If the brief asks the wrong question, restate the *right* one.

2. `key_questions` — 3-5 questions the deck must answer. These are the questions a partner would ask the junior: "Before we present, can you tell me X?" Each question should be answerable; vague exploratory questions are worthless.

3. `applicable_frameworks` — name the consulting frameworks that genuinely fit this problem. Porter's 5 Forces, BCG growth-share matrix, McKinsey 7S, Jobs-To-Be-Done, value chain analysis, MECE issue tree, 2x2 prioritization, Kotter's 8 steps, AIDA, RACI, etc. Pick the ones that ACTUALLY help; do not list frameworks for show. 2-4 named frameworks is normal.

4. `findings` — 5-10 structured findings. Each finding has a topic, a substantive insight, a source hint, and a relevance line. Findings must be SPECIFIC — numbers, names, mechanisms, dates. "Customer churn is up" is worthless. "B2B churn rose from 4% to 11% in 12 months, concentrated in the <$50k ARR cohort" is a finding. If you do not have data, reason from first principles and say so in the source_hint.

5. `hypothesis` — the strongest single answer the analyst can defend right now, given the brief and findings. This is what you would say if the partner pinned you against the wall and demanded "what do you actually think?" It must be a falsifiable claim, not mush. "Improving X" is mush. "Cutting the SMB sales team by 40% and redirecting spend to enterprise field sales will lift gross margin 600bps within 4 quarters" is a hypothesis. Hypothesis MUST be non-empty.

6. `counter_arguments` — the 2-3 strongest objections a senior partner would raise to your hypothesis. Steelman them. These are the things that, if true, would kill the hypothesis. Do not list weak strawmen. If the partner's first instinct would be "but what about regulatory risk in Europe", write that — not a polite hedge.

# CRITICAL RULES

- Findings are concrete (numbers, names, mechanisms) or they are nothing.
- Hypothesis is falsifiable, not aspirational.
- Counter-arguments are real objections, not soft hedges.
- Frameworks are chosen because they fit, not for credentialing.
- If you must reason from first principles (no data), say so honestly in source_hint — do not fabricate statistics.

# WHAT YOU DO NOT DO

- You do not write slide titles or layouts. That is the storyboard stage.
- You do not write body bullets. That is the wireframe stage.
- You do not hedge. Your job is to commit to a point of view that the storyboard can defend or revise.

Return the ResearchDossier."""


class ResearchFinding(BaseModel):
    topic: str = Field(
        description=(
            "Short topic label for this finding — a noun phrase. "
            "e.g. 'B2B churn cohort analysis', 'SMB unit economics', 'EU regulatory shift'."
        )
    )
    insight: str = Field(
        description=(
            "The substantive finding — what is true / what to know. "
            "Must be specific (numbers, names, mechanisms, dates) — not a platitude. "
            "Good: 'B2B churn rose from 4% to 11% in 12 months, concentrated in <$50k ARR cohort.' "
            "Bad: 'Customer churn is increasing.'"
        )
    )
    source_hint: str = Field(
        description=(
            "Where this could be sourced from. Examples: 'industry report', "
            "'BCG framework', 'comparable case (Acme 2023 turnaround)', "
            "'first-principles reasoning', 'SEC filings', 'expert interview'. "
            "If reasoning from first principles with no data, say so honestly."
        )
    )
    relevance: str = Field(
        description=(
            "Why this matters for the brief — 1 sentence. "
            "Connects the finding to the question the deck must answer."
        )
    )


class ResearchDossier(BaseModel):
    brief_summary: str = Field(
        description=(
            "2-line restatement of what the brief is actually asking. "
            "Strip noise; surface the real question."
        )
    )
    key_questions: list[str] = Field(
        description=(
            "3-5 questions the deck must answer. "
            "Each must be answerable; not vague exploratory prompts."
        )
    )
    applicable_frameworks: list[str] = Field(
        description=(
            "Named consulting frameworks that genuinely fit this problem. "
            "e.g. \"Porter's 5 Forces\", 'BCG growth-share matrix', 'JTBD', "
            "'McKinsey 7S', 'MECE issue tree'. 2-4 is typical."
        )
    )
    findings: list[ResearchFinding] = Field(
        description=(
            "5-10 structured findings. Each must be specific; "
            "no platitudes. See ResearchFinding fields for shape."
        )
    )
    hypothesis: str = Field(
        description=(
            "The strongest single answer hypothesis the analyst can form. "
            "Must be a falsifiable claim, not mush. Must be non-empty."
        )
    )
    counter_arguments: list[str] = Field(
        description=(
            "2-3 strongest counter-arguments / risks to the hypothesis. "
            "Steelmanned, not strawmen. The objections a senior partner would raise."
        )
    )

    @field_validator("hypothesis")
    @classmethod
    def _hypothesis_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("hypothesis must be non-empty")
        return v


def do_research(
    client: Anthropic,
    config: Config,
    brief: str,
    requirements: str = "",
) -> tuple[ResearchDossier, GenerationMetadata]:
    """Pure-LLM research (no web tools yet) — sets the analyst frame.

    Adaptive thinking, structured output. Returns the dossier + metadata. The
    dossier is intended to be passed to the storyboard stage as additional
    context via `dossier_to_storyboard_context`.
    """
    user_prompt = f"# Brief\n\n{brief}"
    if requirements:
        user_prompt += f"\n\n# Additional requirements\n\n{requirements}"
    user_prompt += (
        "\n\nDo the research pass. Restate the brief, list the key questions, "
        "name the applicable frameworks, surface the findings, commit to a "
        "hypothesis, and stress-test it with counter-arguments. Return the "
        "ResearchDossier."
    )

    started = time.monotonic()
    response = client.messages.parse(
        model=config.model,
        max_tokens=12000,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": RESEARCH_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
        output_format=ResearchDossier,
    )
    duration = time.monotonic() - started

    if response.parsed_output is None:
        raise RuntimeError("Research output failed schema validation")

    metadata = GenerationMetadata(
        model_id=config.model,
        model_response_id=getattr(response, "model", None),
        prompt_version=PROMPT_VERSION,
        cache_hit=False,
        input_hash="",  # research is upstream of caching for now
        duration_seconds=duration,
    )
    return response.parsed_output, metadata


def dossier_to_storyboard_context(dossier: ResearchDossier) -> str:
    """Render the dossier as supplemental context for the storyboard stage.

    The output is meant to be appended to a storyboard-stage user prompt so
    the model walks into the whiteboard with the analyst's prior committed.
    Format is plain Markdown-ish text with clear headers.
    """
    lines: list[str] = []
    lines.append("# Research dossier (analyst pre-work)")
    lines.append("")
    lines.append("## Brief summary")
    lines.append(dossier.brief_summary)
    lines.append("")

    lines.append("## Key questions the deck must answer")
    for q in dossier.key_questions:
        lines.append(f"- {q}")
    lines.append("")

    lines.append("## Applicable frameworks")
    for f in dossier.applicable_frameworks:
        lines.append(f"- {f}")
    lines.append("")

    lines.append("## Findings")
    for i, f in enumerate(dossier.findings, start=1):
        lines.append(f"{i}. **{f.topic}** — {f.insight}")
        lines.append(f"   - source: {f.source_hint}")
        lines.append(f"   - relevance: {f.relevance}")
    lines.append("")

    lines.append("## Hypothesis (analyst's committed answer)")
    lines.append(dossier.hypothesis)
    lines.append("")

    lines.append("## Counter-arguments / risks to stress-test")
    for c in dossier.counter_arguments:
        lines.append(f"- {c}")
    lines.append("")

    return "\n".join(lines)
