"""Tests for Sprint L — Atelier Copilot.

Covers:
- Slash-shortcut prompt parser (/shape, /theme, /slide N)
- Focused-slice builder scopes the prompt to the right slice
- POST /copilot/ask returns an HTMX swap and persists changes
- Selection scoping: only the targeted slide is in the prompt sent to Claude
- Snapshot is taken before mutation
- "Rethink this slide" returns shape suggestions with rationale

All Anthropic calls are mocked. NO live API in tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from slideatelier.copilot import (
    CopilotPatch,
    apply_patch,
    ask_copilot,
    build_focused_slice,
    parse_prompt,
    rethink_slide,
)


# ---------------------------------------------------------------------------
# Slash-shortcut parser
# ---------------------------------------------------------------------------

def test_parse_prompt_no_slash():
    p = parse_prompt("Sharpen the title")
    assert p.body == "Sharpen the title"
    assert p.selection_override is None


def test_parse_prompt_slide_shortcut():
    p = parse_prompt("/slide 3 sharpen the title")
    assert p.selection_override == {"kind": "slide", "id": 2}  # 1-based to 0-based
    assert p.body == "sharpen the title"


def test_parse_prompt_shape_shortcut():
    p = parse_prompt("/shape funnel/3-stage make it 4 stages")
    assert p.selection_override is not None
    assert p.selection_override["kind"] == "shape"
    assert p.selection_override["id"] == "funnel/3-stage make it 4 stages"
    # Shape id absorbs the rest of the line; body empty without explicit newline.
    assert p.body == ""


def test_parse_prompt_shape_with_body():
    p = parse_prompt("/shape funnel/3-stage\nmake it 4 stages")
    assert p.selection_override == {"kind": "shape", "id": "funnel/3-stage"}
    assert p.body == "make it 4 stages"


def test_parse_prompt_theme_shortcut():
    p = parse_prompt("/theme make it warmer")
    assert p.selection_override == {"kind": "theme", "id": None}
    assert p.body == "make it warmer"


def test_parse_prompt_unknown_slash_passes_through():
    p = parse_prompt("/wat is this even")
    assert p.selection_override is None
    assert p.body == "/wat is this even"


def test_parse_prompt_bad_slide_number_passes_through():
    """A malformed /slide token shouldn't crash; keep the user's text intact."""
    p = parse_prompt("/slide foo bar")
    assert p.selection_override is None
    assert p.body == "/slide foo bar"


def test_parse_prompt_empty():
    p = parse_prompt("")
    assert p.body == ""
    assert p.selection_override is None


# ---------------------------------------------------------------------------
# Focused-slice builder
# ---------------------------------------------------------------------------

def test_focused_slice_slide_only_includes_target_slide():
    deck = {
        "title": "T",
        "core_message": "msg",
        "slides": [
            {"layout": "title", "title": "Cover", "body": []},
            {"layout": "content", "title": "Detail A", "body": ["x"]},
            {"layout": "content", "title": "Detail B", "body": ["y"]},
        ],
    }
    sliced = build_focused_slice(deck, {"kind": "slide", "id": 1})
    assert sliced["kind"] == "slide"
    assert sliced["slide_index"] == 1
    assert sliced["slide"]["title"] == "Detail A"
    blob = json.dumps(sliced)
    # Only the targeted slide's content should be in the slice.
    assert "Detail A" in blob
    assert "Detail B" not in blob
    assert "Cover" not in blob


def test_focused_slice_theme_excludes_slides():
    deck = {"title": "T", "subtitle": "S", "core_message": "m", "narrative_arc": "n",
            "slides": [{"title": "x", "body": ["secret"]}]}
    sliced = build_focused_slice(deck, {"kind": "theme", "id": None})
    assert sliced["kind"] == "theme"
    assert "secret" not in json.dumps(sliced)


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------

def test_apply_patch_slide_scope():
    deck = {
        "title": "T",
        "slides": [
            {"layout": "content", "title": "old", "body": ["a"]},
        ],
    }
    patch = CopilotPatch(
        scope="slide",
        target=0,
        set={"title": "Insight-led replacement", "body": ["b1", "b2"]},
        rationale="r",
    )
    apply_patch(deck, patch)
    assert deck["slides"][0]["title"] == "Insight-led replacement"
    assert deck["slides"][0]["body"] == ["b1", "b2"]


def test_apply_patch_theme_scope_writes_root_paths():
    deck = {"title": "T", "subtitle": "old", "slides": []}
    patch = CopilotPatch(
        scope="theme",
        target=None,
        set={"subtitle": "Board review · Q3 2026"},
        rationale="r",
    )
    apply_patch(deck, patch)
    assert deck["subtitle"] == "Board review · Q3 2026"


def test_apply_patch_shape_scope_no_op_on_deck():
    deck = {"title": "T", "slides": [{"title": "x"}]}
    patch = CopilotPatch(scope="shape", target="some/id", set={"foo": "bar"})
    apply_patch(deck, patch)
    # No deck mutation expected for shape-scoped patches.
    assert deck == {"title": "T", "slides": [{"title": "x"}]}


# ---------------------------------------------------------------------------
# ask_copilot — selection scoping (mock the Anthropic client)
# ---------------------------------------------------------------------------

def _make_mock_client(json_payload: dict):
    """Build a stub Anthropic client whose .messages.create returns one text
    block containing `json_payload` serialized."""
    fake = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(json_payload))]
    fake.messages.create.return_value = msg
    return fake


def test_ask_copilot_only_sends_targeted_slide_in_prompt():
    """Selection-scoping check: the user message Claude sees must contain the
    target slide's content but NOT content from sibling slides."""
    deck = {
        "title": "T",
        "core_message": "m",
        "slides": [
            {"layout": "title", "title": "Cover", "body": []},
            {"layout": "content", "title": "Targeted slide", "body": ["target body"]},
            {"layout": "content", "title": "Sibling slide", "body": ["sibling body"]},
        ],
    }
    selection = {"kind": "slide", "id": 1}
    focused = build_focused_slice(deck, selection)

    fake = _make_mock_client({
        "scope": "slide",
        "target": 1,
        "set": {"title": "New title"},
        "rationale": "Sharpen the headline",
    })
    patch = ask_copilot(
        fake,
        model="claude-fake",
        prompt="Sharpen the title",
        selection=selection,
        focused_slice=focused,
    )
    assert patch.scope == "slide"
    assert patch.target == 1
    assert patch.set == {"title": "New title"}

    # Inspect the user message that was sent to Claude.
    args, kwargs = fake.messages.create.call_args
    sent = kwargs.get("messages") or args[0]
    user_content = sent[0]["content"]
    assert "Targeted slide" in user_content
    assert "target body" in user_content
    assert "Sibling slide" not in user_content
    assert "sibling body" not in user_content


def test_ask_copilot_handles_fenced_json_response():
    """Some models wrap JSON in ```json fences. The extractor must still parse."""
    fake = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text='```json\n{"scope":"slide","target":0,"set":{"title":"X"},"rationale":""}\n```')]
    fake.messages.create.return_value = msg
    patch = ask_copilot(
        fake,
        model="claude-fake",
        prompt="x",
        selection={"kind": "slide", "id": 0},
        focused_slice={"kind": "slide", "slide_index": 0, "slide": {"title": "old"}},
    )
    assert patch.scope == "slide"
    assert patch.set == {"title": "X"}


# ---------------------------------------------------------------------------
# rethink_slide — shape suggestions
# ---------------------------------------------------------------------------

def test_rethink_slide_returns_suggestions_with_rationale():
    fake = _make_mock_client({
        "suggestions": [
            {"shape_id": "matrix_2x2", "rationale": "Four bullets read as a 2x2", "confidence": "high"},
            {"shape_id": "process_funnel", "rationale": "Sequential stages", "confidence": "medium"},
        ]
    })
    out = rethink_slide(
        fake,
        model="claude-fake",
        slide={"layout": "content", "title": "Q3", "body": ["a", "b", "c", "d"]},
        available_shape_ids=["matrix_2x2", "process_funnel", "donut"],
    )
    assert len(out) == 2
    assert out[0]["shape_id"] == "matrix_2x2"
    assert out[0]["rationale"] == "Four bullets read as a 2x2"
    assert out[0]["confidence"] == "high"
    assert out[1]["shape_id"] == "process_funnel"


def test_rethink_slide_handles_malformed_response():
    fake = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text="not json at all")]
    fake.messages.create.return_value = msg
    out = rethink_slide(
        fake, model="x", slide={"title": "x"}, available_shape_ids=["a"],
    )
    assert out == []


# ---------------------------------------------------------------------------
# Route tests — POST /copilot/ask + /copilot/rethink/<idx>
# ---------------------------------------------------------------------------

def _write_minimal_deck(job_dir: Path) -> dict:
    deck = {
        "title": "Acme Q3",
        "subtitle": "",
        "core_message": "Enterprise drove all Q3 growth; SMB structurally broke; reallocate $2M.",
        "narrative_arc": "Open. Diagnose. Recommend. Close.",
        "slides": [
            {
                "layout": "title", "title": "Cover", "strap": "",
                "body": [], "body_left": [], "body_right": [],
                "speaker_notes": "", "rationale": "",
                "asset_ref": None, "extras": [],
            },
            {
                "layout": "content", "title": "Old vague title", "strap": "",
                "body": ["b1", "b2"], "body_left": [], "body_right": [],
                "speaker_notes": "", "rationale": "",
                "asset_ref": None, "extras": [],
            },
        ],
    }
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "deck.json").write_text(json.dumps(deck, indent=2))
    return deck


def _patched_make_client(monkeypatch, json_payload: dict):
    """Patch claude_client.make_client to return a stub Anthropic client."""
    fake = _make_mock_client(json_payload)
    import slideatelier.claude_client as cc_mod
    monkeypatch.setattr(cc_mod, "make_client", lambda config: fake)
    return fake


def test_copilot_ask_returns_htmx_swap_and_persists_change(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-for-copilot")

    job_id = "copilot-test-1"
    job_dir = tmp_path / "workflow" / job_id
    _write_minimal_deck(job_dir)

    fake = _patched_make_client(monkeypatch, {
        "scope": "slide",
        "target": 1,
        "set": {"title": "Sharper insight-led title"},
        "rationale": "Stated the topic, not the takeaway.",
    })

    from slideatelier.web.app import app
    c = TestClient(app)
    r = c.post(
        f"/workflow/wireframe/{job_id}/copilot/ask",
        data={
            "prompt": "Sharpen the title",
            "selection_kind": "slide",
            "selection_id": "1",
        },
    )
    assert r.status_code == 200, r.text

    # Returned HTML contains both the chat bubble AND an OOB-swap slide-card.
    body = r.text
    assert "copilot-turn" in body
    assert "Sharper insight-led title" in body
    assert 'hx-swap-oob="outerHTML"' in body  # OOB swap of the slide card

    # Deck on disk reflects the patch.
    saved = json.loads((job_dir / "deck.json").read_text())
    assert saved["slides"][1]["title"] == "Sharper insight-led title"

    # Anthropic was called once.
    assert fake.messages.create.call_count == 1


def test_copilot_ask_takes_snapshot_before_mutation(tmp_path, monkeypatch):
    """Pre-mutation snapshot is required so undo restores the prior state.

    We assert that workflow_history.snapshot was called with the deck artifact
    BEFORE the deck.json on disk was rewritten. The order is verified by
    capturing the deck title at snapshot time (it should be the OLD title).
    """
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    job_id = "copilot-snapshot-test"
    job_dir = tmp_path / "workflow" / job_id
    _write_minimal_deck(job_dir)

    _patched_make_client(monkeypatch, {
        "scope": "slide", "target": 1,
        "set": {"title": "After"}, "rationale": "r",
    })

    captured: list[str] = []
    import slideatelier.web.copilot_routes as cr_mod
    real_snapshot = cr_mod.snapshot if hasattr(cr_mod, "snapshot") else None
    # The route imports snapshot lazily from .workflow_history; patch THAT.
    import slideatelier.web.workflow_history as wh_mod
    real = wh_mod.snapshot

    def spy(job_dir_arg, kind):
        # At snapshot time, the deck.json should still hold the OLD title.
        deck_path = job_dir_arg / "deck.json"
        if deck_path.exists():
            captured.append(json.loads(deck_path.read_text())["slides"][1]["title"])
        return real(job_dir_arg, kind)

    monkeypatch.setattr(wh_mod, "snapshot", spy)

    from slideatelier.web.app import app
    c = TestClient(app)
    r = c.post(
        f"/workflow/wireframe/{job_id}/copilot/ask",
        data={"prompt": "x", "selection_kind": "slide", "selection_id": "1"},
    )
    assert r.status_code == 200, r.text

    # Snapshot ran AT LEAST once and saw the pre-mutation title.
    assert "Old vague title" in captured, (
        f"snapshot must run before deck rewrite; saw {captured}"
    )


def test_copilot_ask_logs_turn_to_jsonl(tmp_path, monkeypatch):
    """Each turn appends one JSONL record to <job>/copilot/turns.jsonl."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    job_id = "copilot-log-test"
    job_dir = tmp_path / "workflow" / job_id
    _write_minimal_deck(job_dir)
    _patched_make_client(monkeypatch, {
        "scope": "slide", "target": 1, "set": {"title": "X"}, "rationale": "r",
    })

    from slideatelier.web.app import app
    c = TestClient(app)
    c.post(
        f"/workflow/wireframe/{job_id}/copilot/ask",
        data={"prompt": "a", "selection_kind": "slide", "selection_id": "1"},
    )
    c.post(
        f"/workflow/wireframe/{job_id}/copilot/ask",
        data={"prompt": "b", "selection_kind": "slide", "selection_id": "1"},
    )
    log_path = job_dir / "copilot" / "turns.jsonl"
    assert log_path.exists()
    lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["prompt"] == "a"
    assert rec["selection"]["kind"] == "slide"
    assert rec["success"] is True


def test_copilot_ask_slash_shortcut_overrides_form_selection(tmp_path, monkeypatch):
    """Slash shortcut /slide N takes precedence over the form's selection_id."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    job_id = "copilot-shortcut-test"
    job_dir = tmp_path / "workflow" / job_id
    _write_minimal_deck(job_dir)

    fake = _patched_make_client(monkeypatch, {
        "scope": "slide", "target": 0,
        "set": {"title": "Patched-via-shortcut"}, "rationale": "r",
    })

    from slideatelier.web.app import app
    c = TestClient(app)
    # selection_id=1 in the form, but /slide 1 in the prompt should redirect to slide 0.
    r = c.post(
        f"/workflow/wireframe/{job_id}/copilot/ask",
        data={
            "prompt": "/slide 1 update",
            "selection_kind": "slide",
            "selection_id": "1",  # would otherwise target slide #1 (index 1)
        },
    )
    assert r.status_code == 200, r.text

    # The prompt sent to Claude should be scoped to slide 0 (the shortcut won).
    args, kwargs = fake.messages.create.call_args
    user_content = (kwargs.get("messages") or args[0])[0]["content"]
    assert "Cover" in user_content  # slide 0's title
    assert "Old vague title" not in user_content  # slide 1's title


def test_copilot_rethink_returns_suggestions_html(tmp_path, monkeypatch):
    """POST /copilot/rethink/<idx> returns suggestion items with shape ids and rationale."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    job_id = "copilot-rethink-test"
    job_dir = tmp_path / "workflow" / job_id
    _write_minimal_deck(job_dir)

    _patched_make_client(monkeypatch, {
        "suggestions": [
            {"shape_id": "matrix_2x2",
             "rationale": "Four bullets group naturally into a 2x2",
             "confidence": "high"},
        ]
    })

    from slideatelier.web.app import app
    c = TestClient(app)
    r = c.post(f"/workflow/wireframe/{job_id}/copilot/rethink/1")
    assert r.status_code == 200, r.text
    assert "matrix_2x2" in r.text
    assert "Four bullets group naturally into a 2x2" in r.text
    # The suggestion list should be wired to attach-extra so user can apply.
    assert f"/workflow/wireframe/{job_id}/attach-extra/1" in r.text


def test_copilot_log_endpoint_returns_recorded_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    job_id = "copilot-log-get-test"
    job_dir = tmp_path / "workflow" / job_id
    _write_minimal_deck(job_dir)
    _patched_make_client(monkeypatch, {
        "scope": "slide", "target": 1, "set": {"title": "X"}, "rationale": "",
    })

    from slideatelier.web.app import app
    c = TestClient(app)
    c.post(
        f"/workflow/wireframe/{job_id}/copilot/ask",
        data={"prompt": "first turn", "selection_kind": "slide", "selection_id": "1"},
    )
    r = c.get(f"/workflow/wireframe/{job_id}/copilot/log")
    assert r.status_code == 200
    body = r.json()
    assert "turns" in body
    assert len(body["turns"]) == 1
    assert body["turns"][0]["prompt"] == "first turn"


def test_copilot_ask_rejects_empty_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    job_id = "copilot-empty-test"
    job_dir = tmp_path / "workflow" / job_id
    _write_minimal_deck(job_dir)
    from slideatelier.web.app import app
    c = TestClient(app)
    r = c.post(
        f"/workflow/wireframe/{job_id}/copilot/ask",
        data={"prompt": "   ", "selection_kind": "deck"},
    )
    assert r.status_code == 400


def test_wireframe_template_includes_copilot_rail():
    """Smoke check: the wireframe.html template renders the copilot rail markup
    and a toolbar button. We don't render a full deck here; we just sanity-check
    the source includes the expected ids."""
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parent.parent
           / "src" / "slideatelier" / "web" / "templates"
           / "workflow" / "wireframe.html").read_text()
    assert "copilot-rail" in src
    assert "🤖 Atelier" in src
    assert "/workflow/wireframe/{{ job_id }}/copilot/ask" in src


def test_slide_card_template_includes_rethink_button():
    """Smoke check: the slide-edit card shows the 💡 Rethink button + target panel."""
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parent.parent
           / "src" / "slideatelier" / "web" / "templates"
           / "workflow" / "_slide_edit_card.html").read_text()
    assert "💡 Rethink" in src
    assert "rethink-panel-{{ slide_idx }}" in src
    assert "/copilot/rethink/{{ slide_idx }}" in src
