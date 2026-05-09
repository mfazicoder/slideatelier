"""Sprint V — Deck Audit (linter) tests.

Each lint code gets a dedicated synthetic deck that triggers it, and a paired
assertion that the code is absent on a clean deck. Severity classification is
verified against AUDIT_CODES, and apply_audit_fixes is round-tripped on the
auto-fixable codes.

The CLI command is exercised via Typer's runner: clean deck → exit 0,
deck with errors → exit 1.
"""
from __future__ import annotations

import json as _json
from pathlib import Path

from typer.testing import CliRunner

from slideatelier.audit import (
    AUDIT_CODES,
    AUTO_FIXABLE_CODES,
    apply_audit_fixes,
    audit_deck,
)
from slideatelier.cli import app as cli_app
from slideatelier.models import Slide, SlideDeck
from slideatelier.template import Template, TemplateColors

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CORE = "Test core message stating the answer in one sentence."
_ARC = "Open with X. Defend with Y. Close with Z."


def _deck(*slides: Slide, title: str = "Test deck") -> SlideDeck:
    return SlideDeck(
        title=title,
        core_message=_CORE,
        narrative_arc=_ARC,
        slides=list(slides) or [Slide(layout="title", title=title)],
    )


def _codes(issues) -> list[str]:
    return [i.code for i in issues]


def _has(issues, code: str, slide_idx: int | None = None) -> bool:
    for i in issues:
        if i.code == code and (slide_idx is None or i.slide_idx == slide_idx):
            return True
    return False


# ---------------------------------------------------------------------------
# Code registry
# ---------------------------------------------------------------------------

def test_audit_codes_registry_complete():
    """The 9 v1 codes are all registered with severities the brief specifies."""
    expected = {
        "BODY_TOO_LONG": "warning",
        "HEADING_HIERARCHY": "info",
        "OFF_THEME_COLOR": "warning",
        "UNUSED_ASSET_SLOT": "warning",
        "DUPLICATE_CONTENT": "warning",
        "MISSING_SPEAKER_NOTES": "info",
        "LOW_CONTRAST": "error",
        "EMPTY_BODY": "error",
        "OVERFLOWING_BBOX": "warning",
    }
    for code, sev in expected.items():
        assert code in AUDIT_CODES, f"missing code {code}"
        assert AUDIT_CODES[code][0] == sev, f"{code} severity mismatch"


# ---------------------------------------------------------------------------
# Per-code triggers
# ---------------------------------------------------------------------------

def test_body_too_long_triggers():
    long_body = ["one two three four five six seven eight"] * 6  # 48 words
    deck = _deck(
        Slide(layout="title", title="Hi"),
        Slide(layout="content", title="Wordy", body=long_body),
    )
    issues = audit_deck(deck, Template())
    assert _has(issues, "BODY_TOO_LONG", slide_idx=1)


def test_body_too_long_clean():
    deck = _deck(
        Slide(layout="content", title="Tight", body=["short", "items", "only"])
    )
    assert not _has(audit_deck(deck, Template()), "BODY_TOO_LONG")


def test_heading_hierarchy_triggers():
    """Body slide → title slide jump without a divider is a hierarchy issue."""
    deck = _deck(
        Slide(layout="title", title="Cover"),
        Slide(layout="content", title="Insight", body=["a"]),
        Slide(layout="title", title="Wait, another title?"),  # jump
    )
    issues = audit_deck(deck, Template())
    assert _has(issues, "HEADING_HIERARCHY", slide_idx=2)


def test_heading_hierarchy_clean_with_divider():
    deck = _deck(
        Slide(layout="title", title="Cover"),
        Slide(layout="section_divider", title="Part one"),
        Slide(layout="content", title="Body", body=["a"]),
    )
    assert not _has(audit_deck(deck, Template()), "HEADING_HIERARCHY")


def test_off_theme_color_triggers():
    """A block_style color outside the theme palette triggers the warning."""
    deck = _deck(
        Slide(
            layout="content",
            title="Coloured",
            body=["a"],
            block_style={"title": {"color": "#ABCDEF"}},  # not in theme
        )
    )
    issues = audit_deck(deck, Template())
    assert _has(issues, "OFF_THEME_COLOR", slide_idx=0)


def test_off_theme_color_clean_when_in_palette():
    tpl = Template()
    deck = _deck(
        Slide(
            layout="content",
            title="Coloured",
            body=["a"],
            block_style={"title": {"color": tpl.colors.primary}},
        )
    )
    assert not _has(audit_deck(deck, tpl), "OFF_THEME_COLOR")


def test_unused_asset_slot_triggers():
    deck = _deck(
        Slide(
            layout="two_column",
            title="Compare",
            body_left=["only left"],  # right empty
        )
    )
    issues = audit_deck(deck, Template())
    assert _has(issues, "UNUSED_ASSET_SLOT", slide_idx=0)


def test_unused_asset_slot_clean_when_both_filled():
    deck = _deck(
        Slide(
            layout="two_column",
            title="Compare",
            body_left=["L"], body_right=["R"],
        )
    )
    assert not _has(audit_deck(deck, Template()), "UNUSED_ASSET_SLOT")


def test_duplicate_content_triggers_on_title():
    deck = _deck(
        Slide(layout="content", title="Same title", body=["a"]),
        Slide(layout="content", title="Same title", body=["b"]),
    )
    issues = audit_deck(deck, Template())
    assert _has(issues, "DUPLICATE_CONTENT", slide_idx=1)


def test_duplicate_content_triggers_on_first_bullet():
    deck = _deck(
        Slide(layout="content", title="A", body=["Repeated lead bullet"]),
        Slide(layout="content", title="B", body=["Repeated lead bullet"]),
    )
    issues = audit_deck(deck, Template())
    assert _has(issues, "DUPLICATE_CONTENT", slide_idx=1)


def test_missing_speaker_notes_on_closing_triggers():
    deck = _deck(
        Slide(layout="title", title="Hi"),
        Slide(layout="content", title="Body", body=["a"]),
        Slide(layout="closing", title="The end"),  # no notes
    )
    issues = audit_deck(deck, Template())
    assert _has(issues, "MISSING_SPEAKER_NOTES", slide_idx=2)


def test_missing_speaker_notes_clean_when_present():
    deck = _deck(
        Slide(layout="title", title="Hi"),
        Slide(layout="closing", title="The end", speaker_notes="Wrap up the deck."),
    )
    assert not _has(audit_deck(deck, Template()), "MISSING_SPEAKER_NOTES")


def test_low_contrast_triggers():
    """Light-grey text on white background fails WCAG AA."""
    deck = _deck(
        Slide(
            layout="content",
            title="Faint",
            body=["a"],
            block_style={"title": {"color": "#CCCCCC"}},  # very low contrast on white
        )
    )
    issues = audit_deck(deck, Template())
    assert _has(issues, "LOW_CONTRAST", slide_idx=0)


def test_low_contrast_clean_with_strong_color():
    deck = _deck(
        Slide(
            layout="content",
            title="Strong",
            body=["a"],
            block_style={"title": {"color": "#000000"}},
        )
    )
    assert not _has(audit_deck(deck, Template()), "LOW_CONTRAST")


def test_empty_body_triggers():
    deck = _deck(
        Slide(layout="content", title="Empty", body=[]),
    )
    issues = audit_deck(deck, Template())
    assert _has(issues, "EMPTY_BODY", slide_idx=0)


def test_empty_body_clean_when_extras_present():
    from slideatelier.models import SlideExtra
    deck = _deck(
        Slide(
            layout="content",
            title="Has extra",
            extras=[SlideExtra(type="library_asset", config={"asset_ref": "x"})],
        )
    )
    assert not _has(audit_deck(deck, Template()), "EMPTY_BODY")


def test_overflowing_bbox_triggers():
    """Tiny bbox + lots of body text => overflow flag."""
    very_long = "x " * 400  # ~800 chars per bullet
    deck = _deck(
        Slide(
            layout="content",
            title="Cramped",
            body=[very_long],
            block_bbox={"body": {"left": 0.05, "top": 0.05, "width": 0.1, "height": 0.05}},
        )
    )
    issues = audit_deck(deck, Template())
    assert _has(issues, "OVERFLOWING_BBOX", slide_idx=0)


def test_overflowing_bbox_clean_when_room():
    deck = _deck(
        Slide(
            layout="content",
            title="Roomy",
            body=["short"],
            block_bbox={"body": {"left": 0.05, "top": 0.05, "width": 0.9, "height": 0.8}},
        )
    )
    assert not _has(audit_deck(deck, Template()), "OVERFLOWING_BBOX")


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

def test_severity_classification_is_correct():
    """Every emitted issue's severity matches AUDIT_CODES[code][0]."""
    long_body = ["one two three four five six seven eight"] * 6
    deck = _deck(
        Slide(layout="title", title="Cover"),
        Slide(layout="content", title="Wordy", body=long_body),  # BODY_TOO_LONG
        Slide(
            layout="content",
            title="Faint",
            body=["a"],
            block_style={"title": {"color": "#CCCCCC"}},  # LOW_CONTRAST + OFF_THEME_COLOR
        ),
        Slide(layout="content", title="Empty body"),  # EMPTY_BODY
        Slide(layout="closing", title="End"),  # MISSING_SPEAKER_NOTES
    )
    for issue in audit_deck(deck, Template()):
        assert issue.severity == AUDIT_CODES[issue.code][0], (
            f"{issue.code} emitted with {issue.severity}, expected "
            f"{AUDIT_CODES[issue.code][0]}"
        )


# ---------------------------------------------------------------------------
# Auto-fix
# ---------------------------------------------------------------------------

def test_apply_fix_missing_speaker_notes_adds_stub():
    deck = _deck(
        Slide(layout="closing", title="Wrap"),
    )
    issues = audit_deck(deck, Template())
    assert any(i.code == "MISSING_SPEAKER_NOTES" for i in issues)

    new_deck, applied = apply_audit_fixes(deck, issues)
    assert applied  # at least one fix applied
    assert new_deck.slides[0].speaker_notes  # stub added
    # Re-audit: that issue is gone
    assert not _has(audit_deck(new_deck, Template()), "MISSING_SPEAKER_NOTES")


def test_apply_fix_duplicate_title_appends_suffix():
    deck = _deck(
        Slide(layout="content", title="Repeat", body=["a"]),
        Slide(layout="content", title="Repeat", body=["b"]),
    )
    issues = audit_deck(deck, Template())
    new_deck, applied = apply_audit_fixes(deck, issues)
    assert applied
    assert new_deck.slides[1].title != "Repeat"


def test_apply_fix_unused_asset_slot_converts_to_content():
    deck = _deck(
        Slide(layout="two_column", title="Lonely", body_left=["L1", "L2"]),
    )
    issues = audit_deck(deck, Template())
    new_deck, _applied = apply_audit_fixes(deck, issues)
    assert new_deck.slides[0].layout == "content"
    assert new_deck.slides[0].body == ["L1", "L2"]
    assert new_deck.slides[0].body_left == []


def test_apply_fix_does_not_touch_non_auto_fixable_codes():
    """EMPTY_BODY and LOW_CONTRAST should remain after fix-all."""
    deck = _deck(
        Slide(layout="content", title="Empty"),  # EMPTY_BODY
    )
    issues = audit_deck(deck, Template())
    new_deck, applied = apply_audit_fixes(deck, issues)
    # No applied fixes for EMPTY_BODY (not auto-fixable)
    assert "EMPTY_BODY" not in {issues[i].code for i in applied}
    assert _has(audit_deck(new_deck, Template()), "EMPTY_BODY")


def test_apply_fix_is_pure_does_not_mutate_input():
    deck = _deck(Slide(layout="closing", title="Wrap"))
    before = deck.model_dump_json()
    issues = audit_deck(deck, Template())
    apply_audit_fixes(deck, issues)
    after = deck.model_dump_json()
    assert before == after, "apply_audit_fixes must not mutate the input deck"


def test_auto_fixable_codes_subset_of_registry():
    for code in AUTO_FIXABLE_CODES:
        assert code in AUDIT_CODES, f"AUTO_FIXABLE_CODES contains unknown code {code}"


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

def _write_workflow_deck(tmp_path: Path, deck: SlideDeck, job_id: str) -> Path:
    """Lay out a deck.json the way three_stage_routes does."""
    job_dir = tmp_path / "output" / "workflow" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "deck.json").write_text(deck.model_dump_json(indent=2))
    return job_dir


def test_cli_audit_clean_exits_zero(tmp_path, monkeypatch):
    deck = _deck(
        Slide(layout="title", title="Hi"),
        Slide(layout="content", title="Insight", body=["bullet a"], speaker_notes="n"),
        Slide(
            layout="closing",
            title="End",
            body=["Decide today", "Execute next week"],
            speaker_notes="wrap",
        ),
    )
    # Sanity-check the synthetic deck is actually clean before driving the CLI.
    assert audit_deck(deck, Template()) == [], (
        f"Test fixture not clean: {audit_deck(deck, Template())}"
    )
    _write_workflow_deck(tmp_path, deck, "cleanjob")
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")  # not used but config wants it

    runner = CliRunner()
    result = runner.invoke(cli_app, ["audit", "cleanjob"])
    assert result.exit_code == 0, f"stdout: {result.stdout}\nexc: {result.exception}"


def test_cli_audit_with_errors_exits_one(tmp_path, monkeypatch):
    deck = _deck(
        Slide(layout="title", title="Hi"),
        Slide(layout="content", title="Empty"),  # EMPTY_BODY -> error
    )
    _write_workflow_deck(tmp_path, deck, "errorjob")
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    runner = CliRunner()
    result = runner.invoke(cli_app, ["audit", "errorjob"])
    assert result.exit_code == 1
    assert "EMPTY_BODY" in result.stdout


def test_cli_audit_json_mode(tmp_path, monkeypatch):
    deck = _deck(
        Slide(layout="title", title="Hi"),
        Slide(layout="content", title="Empty"),
    )
    _write_workflow_deck(tmp_path, deck, "jsonjob")
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    runner = CliRunner()
    result = runner.invoke(cli_app, ["audit", "jsonjob", "--json"])
    assert result.exit_code == 1, f"stdout: {result.stdout!r}\nexc: {result.exception}"
    # The JSON branch prints exactly one JSON document via print() — no Rich
    # decoration. Strip trailing whitespace and parse.
    payload = _json.loads(result.stdout.strip())
    assert payload["error_count"] >= 1
    assert any(i["code"] == "EMPTY_BODY" for i in payload["issues"])


def test_cli_audit_missing_job_exits_two(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    runner = CliRunner()
    result = runner.invoke(cli_app, ["audit", "doesnotexist"])
    assert result.exit_code == 2
