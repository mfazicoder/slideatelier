"""Schema-validation tests for the research stage.

These tests do NOT call the live Anthropic API. They cover:
- ResearchDossier and ResearchFinding accept valid input
- The hypothesis validator rejects empty/whitespace strings
- dossier_to_storyboard_context renders cleanly with the hypothesis and
  at least one finding's insight surfaced
"""
import pytest
from pydantic import ValidationError

from slideatelier.research import (
    ResearchDossier,
    ResearchFinding,
    dossier_to_storyboard_context,
)


def _finding(**overrides) -> ResearchFinding:
    base = dict(
        topic="B2B churn cohort analysis",
        insight=(
            "B2B churn rose from 4% to 11% in 12 months, concentrated in "
            "the <$50k ARR cohort."
        ),
        source_hint="first-principles reasoning + comparable case",
        relevance="Identifies which customer segment is breaking the model.",
    )
    base.update(overrides)
    return ResearchFinding(**base)


def _dossier(**overrides) -> ResearchDossier:
    base = dict(
        brief_summary=(
            "Why is gross margin compressing despite 30% revenue growth, "
            "and what should the board approve to reverse it?"
        ),
        key_questions=[
            "Which customer segment is driving the margin compression?",
            "Is the issue pricing, mix, or cost-to-serve?",
            "What are the 2-3 highest-leverage interventions?",
        ],
        applicable_frameworks=[
            "MECE issue tree",
            "BCG growth-share matrix",
            "Jobs-To-Be-Done",
        ],
        findings=[_finding()],
        hypothesis=(
            "Cutting the SMB sales team by 40% and redirecting spend to "
            "enterprise field sales lifts gross margin 600bps in 4 quarters."
        ),
        counter_arguments=[
            "SMB serves as a top-of-funnel for enterprise; cutting it kills future pipeline.",
            "Enterprise sales cycles are 9-12 months, so the margin recovery may be back-loaded past board patience.",
        ],
    )
    base.update(overrides)
    return ResearchDossier(**base)


def test_research_dossier_validates():
    """Build a valid ResearchDossier and verify all fields populate."""
    d = _dossier()
    assert d.brief_summary.startswith("Why is gross margin")
    assert len(d.key_questions) == 3
    assert "MECE issue tree" in d.applicable_frameworks
    assert len(d.findings) == 1
    assert d.findings[0].topic == "B2B churn cohort analysis"
    assert d.hypothesis.startswith("Cutting the SMB sales team")
    assert len(d.counter_arguments) == 2


def test_research_dossier_requires_hypothesis():
    """Empty / whitespace hypothesis must be rejected by the field validator."""
    with pytest.raises(ValidationError):
        _dossier(hypothesis="")
    with pytest.raises(ValidationError):
        _dossier(hypothesis="   ")
    with pytest.raises(ValidationError):
        _dossier(hypothesis="\n\t  \n")


def test_finding_validates():
    """Build a ResearchFinding with all fields populated."""
    f = ResearchFinding(
        topic="EU regulatory shift",
        insight="DSA enforcement begins Aug 2026; fines up to 6% of global revenue.",
        source_hint="industry report",
        relevance="Frames the timeline pressure on the compliance roadmap.",
    )
    assert f.topic == "EU regulatory shift"
    assert "DSA enforcement" in f.insight
    assert f.source_hint == "industry report"
    assert f.relevance.startswith("Frames the timeline")


def test_finding_requires_all_fields():
    """ResearchFinding fields are all required (no defaults)."""
    with pytest.raises(ValidationError):
        ResearchFinding(topic="x", insight="y", source_hint="z")  # missing relevance


def test_dossier_to_storyboard_context_renders():
    """Renderer must surface the hypothesis text and at least one finding insight."""
    d = _dossier()
    rendered = dossier_to_storyboard_context(d)

    # Type and basic shape
    assert isinstance(rendered, str)
    assert len(rendered) > 0

    # Hypothesis text appears verbatim
    assert d.hypothesis in rendered

    # At least one finding's insight is surfaced
    assert d.findings[0].insight in rendered

    # Headers present so the storyboard stage can parse the structure visually
    assert "Hypothesis" in rendered
    assert "Findings" in rendered
    assert "Counter-arguments" in rendered or "counter" in rendered.lower()


def test_dossier_to_storyboard_context_includes_frameworks_and_questions():
    """Rendered context should list frameworks and key questions too."""
    d = _dossier()
    rendered = dossier_to_storyboard_context(d)
    for q in d.key_questions:
        assert q in rendered
    for fw in d.applicable_frameworks:
        assert fw in rendered
