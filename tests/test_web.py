"""Smoke tests for the FastAPI web layer. No real Anthropic calls — those are
gated by ANTHROPIC_API_KEY at request time."""
from fastapi.testclient import TestClient

from slideatelier.web.app import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_index_page_renders():
    r = client.get("/")
    assert r.status_code == 200
    assert "slide" in r.text.lower()
    assert "Generate" in r.text


def test_list_templates_endpoint():
    r = client.get("/api/templates")
    assert r.status_code == 200
    body = r.json()
    assert "templates" in body
    assert isinstance(body["templates"], list)


def test_generate_rejects_empty_content():
    r = client.post("/api/generate", data={"content": "", "template": "default"})
    assert r.status_code == 400


def test_job_not_found():
    r = client.get("/api/jobs/nonexistent")
    assert r.status_code == 404


def test_register_template_rejects_non_pptx(tmp_path):
    fake = tmp_path / "not-a-pptx.txt"
    fake.write_text("hello")
    with fake.open("rb") as f:
        r = client.post(
            "/api/templates/register",
            data={"name": "bad", "description": ""},
            files={"master": ("not-a-pptx.txt", f, "text/plain")},
        )
    assert r.status_code == 400


def test_update_extra_persists_bbox_position_and_color(tmp_path, monkeypatch):
    """Sprint D: /update-extra/<idx>/<extra_idx> mutates slide.extras[i].config
    to reflect the dragged-bbox / picked-color / chosen-position the user set
    in the WYSIWYG overlay."""
    import json as _json
    from slideatelier.web.app import app as _app

    # Point the config's output_dir at a tmp dir for the duration of this test
    # so the deck.json roundtrip writes/reads from a clean location. The web
    # layer reads SLIDEATELIER_OUTPUT_DIR via Config.from_env().
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))

    job_id = "sprint-d-test"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    deck = {
        "title": "T",
        "subtitle": "",
        "core_message": "Test core message stating an answer in one sentence.",
        "narrative_arc": "Open. Defend. Close.",
        "slides": [
            {
                "layout": "content",
                "title": "Has an extra",
                "strap": "",
                "body": ["one"],
                "body_left": [],
                "body_right": [],
                "speaker_notes": "",
                "rationale": "",
                "asset_ref": None,
                "extras": [
                    {
                        "type": "library_asset",
                        "position": "right",
                        "config": {"asset_ref": "fake/diagram/1"},
                    }
                ],
            }
        ],
    }
    (job_dir / "deck.json").write_text(_json.dumps(deck))

    c = TestClient(_app)
    bbox = {"left": 0.10, "top": 0.20, "width": 0.30, "height": 0.40}
    r = c.post(
        f"/workflow/wireframe/{job_id}/update-extra/0/0",
        data={
            "bbox": _json.dumps(bbox),
            "color_override": "#ff8800",
            "position": "left",
        },
    )
    assert r.status_code == 200, r.text
    saved = _json.loads((job_dir / "deck.json").read_text())
    cfg = saved["slides"][0]["extras"][0]["config"]
    assert cfg["bbox"] == bbox
    assert cfg["color_override"] == "#ff8800"
    assert saved["slides"][0]["extras"][0]["position"] == "left"

    # Clearing color_override drops the key.
    r2 = c.post(
        f"/workflow/wireframe/{job_id}/update-extra/0/0",
        data={"color_override": ""},
    )
    assert r2.status_code == 200
    saved2 = _json.loads((job_dir / "deck.json").read_text())
    assert "color_override" not in saved2["slides"][0]["extras"][0]["config"]

    # Clearing bbox (empty) drops the key — used when the user picks a named
    # position from the dropdown after dragging.
    r3 = c.post(
        f"/workflow/wireframe/{job_id}/update-extra/0/0",
        data={"bbox": ""},
    )
    assert r3.status_code == 200
    saved3 = _json.loads((job_dir / "deck.json").read_text())
    assert "bbox" not in saved3["slides"][0]["extras"][0]["config"]


def test_update_block_persists_and_clears_bbox(tmp_path, monkeypatch):
    """Sprint Y v1: /update-block/<idx>/<block> mutates slide.block_bbox[name]
    so the wireframe lifts the text block out of layout flow into a freeform
    overlay. POSTing an empty bbox string clears the entry.
    """
    import json as _json
    from slideatelier.web.app import app as _app

    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))

    job_id = "block-bbox-test"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    deck = {
        "title": "T", "subtitle": "",
        "core_message": "Test.", "narrative_arc": "Open.",
        "slides": [{
            "layout": "content", "title": "X", "strap": "",
            "body": ["a", "b"], "body_left": [], "body_right": [],
            "speaker_notes": "", "rationale": "", "asset_ref": None, "extras": [],
        }],
    }
    (job_dir / "deck.json").write_text(_json.dumps(deck))

    c = TestClient(_app)
    bbox = {"left": 0.10, "top": 0.55, "width": 0.80, "height": 0.35}

    # Set a body block_bbox
    r = c.post(
        f"/workflow/wireframe/{job_id}/update-block/0/body",
        data={"bbox": _json.dumps(bbox)},
    )
    assert r.status_code == 200, r.text
    saved = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert saved["block_bbox"]["body"] == bbox

    # Re-render emits the freeform overlay (data-block="body" data-has-bbox="1")
    assert 'data-block="body"' in r.text
    assert 'data-has-bbox="1"' in r.text

    # Clear it
    r2 = c.post(
        f"/workflow/wireframe/{job_id}/update-block/0/body",
        data={"bbox": ""},
    )
    assert r2.status_code == 200
    saved2 = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert "body" not in (saved2.get("block_bbox") or {})


def test_update_block_style_persists_individual_fields(tmp_path, monkeypatch):
    """Sprint Y.3: /update-block-style/<idx>/<block> mutates
    slide.block_style[name] one field at a time. Empty string clears.
    """
    import json as _json
    from slideatelier.web.app import app as _app

    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_id = "block-style-test"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "deck.json").write_text(_json.dumps({
        "title": "T", "subtitle": "",
        "core_message": "Test.", "narrative_arc": "Open.",
        "slides": [{
            "layout": "content", "title": "X", "strap": "",
            "body": ["a"], "body_left": [], "body_right": [],
            "speaker_notes": "", "rationale": "", "asset_ref": None,
            "extras": [],
        }],
    }))
    c = TestClient(_app)
    base = f"/workflow/wireframe/{job_id}/update-block-style/0/title"

    # Set font_size
    r = c.post(base, data={"font_size": "24"})
    assert r.status_code == 200, r.text
    saved = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert saved["block_style"]["title"] == {"font_size": 24}

    # Layer color on top
    r = c.post(base, data={"color": "#ff8800"})
    assert r.status_code == 200
    saved = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert saved["block_style"]["title"]["font_size"] == 24
    assert saved["block_style"]["title"]["color"] == "#ff8800"

    # Toggle bold on
    r = c.post(base, data={"bold": "1"})
    assert r.status_code == 200
    saved = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert saved["block_style"]["title"]["bold"] is True

    # Set align
    r = c.post(base, data={"align": "center"})
    assert r.status_code == 200
    saved = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert saved["block_style"]["title"]["align"] == "center"

    # Bad align rejected
    r = c.post(base, data={"align": "diagonal"})
    assert r.status_code == 400

    # font_size out of range gets clamped
    r = c.post(base, data={"font_size": "200"})
    assert r.status_code == 200
    saved = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert saved["block_style"]["title"]["font_size"] == 96

    # Clear all (every key empty-string) drops the entry entirely.
    r = c.post(base, data={
        "font_family": "", "font_size": "", "color": "",
        "bold": "", "italic": "", "align": "",
    })
    assert r.status_code == 200
    saved = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert "title" not in (saved.get("block_style") or {})


def test_update_block_style_rejects_unknown_block_name(tmp_path, monkeypatch):
    import json as _json
    from slideatelier.web.app import app as _app
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_id = "bad-style-block"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "deck.json").write_text(_json.dumps({
        "title": "T", "subtitle": "",
        "core_message": "Test.", "narrative_arc": "Open.",
        "slides": [{"layout": "content", "title": "X", "strap": "",
                    "body": [], "body_left": [], "body_right": [],
                    "speaker_notes": "", "rationale": "",
                    "asset_ref": None, "extras": []}],
    }))
    c = TestClient(_app)
    r = c.post(
        f"/workflow/wireframe/{job_id}/update-block-style/0/notarealblock",
        data={"font_size": "12"},
    )
    assert r.status_code == 400


def test_update_block_rejects_unknown_name(tmp_path, monkeypatch):
    import json as _json
    from slideatelier.web.app import app as _app

    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_id = "bad-block-name"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "deck.json").write_text(_json.dumps({
        "title": "T", "subtitle": "",
        "core_message": "Test.", "narrative_arc": "Open.",
        "slides": [{"layout": "content", "title": "X", "strap": "",
                    "body": [], "body_left": [], "body_right": [],
                    "speaker_notes": "", "rationale": "",
                    "asset_ref": None, "extras": []}],
    }))
    c = TestClient(_app)
    r = c.post(
        f"/workflow/wireframe/{job_id}/update-block/0/notarealfield",
        data={"bbox": _json.dumps({"left": 0.1, "top": 0.1, "width": 0.5, "height": 0.5})},
    )
    assert r.status_code == 400


def test_layout_change_to_title_auto_promotes_body_to_freeform(tmp_path, monkeypatch):
    """When a content slide with body content is changed to `title` (which
    has no visible body slot), the server auto-creates block_bbox['body'] so
    the body is preserved AND visible as a freeform overlay — matching the
    user requirement that content is never silently stranded.
    Conversely, switching back to a body-rendering layout drops the
    auto-created bbox so body returns to its normal flex-flow position.
    """
    import json as _json
    from slideatelier.web.app import app as _app

    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_id = "auto-promote-test"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    deck = {
        "title": "T", "subtitle": "",
        "core_message": "Test.", "narrative_arc": "Open.",
        "slides": [{
            "layout": "content", "title": "Cover",
            "strap": "Sub", "body": ["one", "two"],
            "body_left": [], "body_right": [],
            "speaker_notes": "", "rationale": "",
            "asset_ref": None, "extras": [],
        }],
    }
    (job_dir / "deck.json").write_text(_json.dumps(deck))

    c = TestClient(_app)

    # content -> title: body should be auto-promoted to a freeform block.
    r = c.post(
        f"/workflow/wireframe/{job_id}/save-slide/0",
        data={
            "layout": "title", "title": "Cover", "strap": "Sub",
            "body": "one\ntwo", "body_left": "", "body_right": "",
            "speaker_notes": "",
        },
    )
    assert r.status_code == 200
    saved = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert saved["layout"] == "title"
    assert saved["body"] == ["one", "two"]
    assert "body" in saved["block_bbox"]
    bb = saved["block_bbox"]["body"]
    assert 0 < bb["top"] < 1 and 0 < bb["left"] < 1
    # The rendered card should include the freeform overlay textarea.
    assert 'data-block="body"' in r.text
    assert 'data-has-bbox="1"' in r.text

    # title -> content: auto-created bbox is dropped; body returns to flex flow.
    r2 = c.post(
        f"/workflow/wireframe/{job_id}/save-slide/0",
        data={
            "layout": "content", "title": "Cover", "strap": "Sub",
            "body": "one\ntwo", "body_left": "", "body_right": "",
            "speaker_notes": "",
        },
    )
    assert r2.status_code == 200
    saved2 = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert saved2["layout"] == "content"
    assert "body" not in (saved2.get("block_bbox") or {})


def test_layout_change_redistributes_content_no_loss(tmp_path, monkeypatch):
    """When the user changes a slide's layout, body content must NOT visibly
    disappear. Specifically: content→two_column splits body bullets across
    body_left/body_right; two_column→content merges them back. Conservative
    rule: never overwrite a non-empty destination.
    """
    import json as _json
    from slideatelier.web.app import app as _app

    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))

    job_id = "layout-redistribute-test"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    deck = {
        "title": "T",
        "subtitle": "",
        "core_message": "Test core message stating an answer in one sentence.",
        "narrative_arc": "Open. Defend. Close.",
        "slides": [
            {
                "layout": "content",
                "title": "Total cost to incorporate",
                "strap": "",
                "body": [
                    "Incorporation and license: ~$25K one-time",
                    "Office co-working: ~$18K Year 1",
                    "Visas: ~$12K",
                    "Legal retainer: ~$35K Year 1",
                    "Total Year 1: ~$90K",
                ],
                "body_left": [],
                "body_right": [],
                "speaker_notes": "",
                "rationale": "",
                "asset_ref": None,
                "extras": [],
            }
        ],
    }
    (job_dir / "deck.json").write_text(_json.dumps(deck))

    c = TestClient(_app)

    # 1) content -> two_column: body should split into body_left + body_right.
    r = c.post(
        f"/workflow/wireframe/{job_id}/save-slide/0",
        data={
            "layout": "two_column",
            "title": "Total cost to incorporate",
            "strap": "",
            "body": "\n".join([
                "Incorporation and license: ~$25K one-time",
                "Office co-working: ~$18K Year 1",
                "Visas: ~$12K",
                "Legal retainer: ~$35K Year 1",
                "Total Year 1: ~$90K",
            ]),
            "body_left": "",
            "body_right": "",
            "speaker_notes": "",
        },
    )
    assert r.status_code == 200, r.text
    saved = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert saved["layout"] == "two_column"
    # 5 bullets → left gets 3, right gets 2 (left gets the larger half if odd)
    assert saved["body"] == []
    assert saved["body_left"] == [
        "Incorporation and license: ~$25K one-time",
        "Office co-working: ~$18K Year 1",
        "Visas: ~$12K",
    ]
    assert saved["body_right"] == [
        "Legal retainer: ~$35K Year 1",
        "Total Year 1: ~$90K",
    ]

    # 2) two_column -> content: columns merge back into body.
    r = c.post(
        f"/workflow/wireframe/{job_id}/save-slide/0",
        data={
            "layout": "content",
            "title": "Total cost to incorporate",
            "strap": "",
            "body": "",
            "body_left": "\n".join(saved["body_left"]),
            "body_right": "\n".join(saved["body_right"]),
            "speaker_notes": "",
        },
    )
    assert r.status_code == 200
    merged = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    assert merged["layout"] == "content"
    assert merged["body"] == [
        "Incorporation and license: ~$25K one-time",
        "Office co-working: ~$18K Year 1",
        "Visas: ~$12K",
        "Legal retainer: ~$35K Year 1",
        "Total Year 1: ~$90K",
    ]
    assert merged["body_left"] == []
    assert merged["body_right"] == []


def test_layout_change_does_not_overwrite_existing_destination(tmp_path, monkeypatch):
    """Conservative redistribution rule: if the destination slot already has
    content, don't overwrite. Existing data is sacred.
    """
    import json as _json
    from slideatelier.web.app import app as _app

    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))

    job_id = "layout-conservative-test"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    # Slide already has BOTH body and body_left populated. Going to two_column
    # must NOT clobber body_left with body's content.
    deck = {
        "title": "T", "subtitle": "",
        "core_message": "Test.", "narrative_arc": "Open. Defend. Close.",
        "slides": [{
            "layout": "content",
            "title": "Mixed",
            "strap": "",
            "body": ["a", "b", "c"],
            "body_left": ["existing left"],
            "body_right": [],
            "speaker_notes": "", "rationale": "", "asset_ref": None, "extras": [],
        }],
    }
    (job_dir / "deck.json").write_text(_json.dumps(deck))

    c = TestClient(_app)
    r = c.post(
        f"/workflow/wireframe/{job_id}/save-slide/0",
        data={
            "layout": "two_column",
            "title": "Mixed",
            "strap": "",
            "body": "a\nb\nc",
            "body_left": "existing left",
            "body_right": "",
            "speaker_notes": "",
        },
    )
    assert r.status_code == 200
    saved = _json.loads((job_dir / "deck.json").read_text())["slides"][0]
    # body_left was non-empty: redistribution skipped, all three slots keep their content.
    assert saved["body"] == ["a", "b", "c"]
    assert saved["body_left"] == ["existing left"]
    assert saved["body_right"] == []


def test_hidden_content_badge_renders_when_layout_hides_field(tmp_path, monkeypatch):
    """When a layout doesn't render a populated body slot, the slide card must
    surface a 'hidden content preserved' badge so the user knows nothing was
    silently lost.
    """
    import json as _json
    from slideatelier.web.app import app as _app

    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))

    job_id = "hidden-badge-test"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    # title layout with body content → body is invisible → badge must render.
    deck = {
        "title": "T", "subtitle": "",
        "core_message": "Test.", "narrative_arc": "Open. Defend. Close.",
        "slides": [{
            "layout": "title",
            "title": "Cover",
            "strap": "",
            "body": ["hidden line one", "hidden line two"],
            "body_left": [], "body_right": [],
            "speaker_notes": "", "rationale": "", "asset_ref": None, "extras": [],
        }],
    }
    (job_dir / "deck.json").write_text(_json.dumps(deck))

    # The wireframe page only renders slide cards when the workflow's status
    # is wireframe-done. Easier to re-render the card via /save-slide which
    # echoes the rendered partial — same template, same context.
    c = TestClient(_app)
    r = c.post(
        f"/workflow/wireframe/{job_id}/save-slide/0",
        data={
            "layout": "title",
            "title": "Cover",
            "strap": "",
            "body": "hidden line one\nhidden line two",
            "body_left": "",
            "body_right": "",
            "speaker_notes": "",
        },
    )
    assert r.status_code == 200, r.text
    assert "hidden content preserved" in r.text
    assert "body (2 lines)" in r.text


def test_slide_card_preview_has_unique_data_attr():
    """Regression: the slide preview canvas must be queryable by an unambiguous
    selector — NOT `.aspect-[16/9]`, because the toolbar contains a tiny
    layout-icon wrapper with the same class earlier in the DOM. If the JS picks
    that 48px box up by mistake, drag math computes bbox values way > 1.0, the
    server clamps them to {1,1,1,1}, and the overlay renders at left:100%;
    top:100% — i.e. vanishes off-screen. We pin the selector by enforcing that
    every rendered slide-edit card has a `[data-slide-preview]` element AND that
    the wireframe page's drag JS uses that selector (not `.aspect-[16/9]`).
    """
    from pathlib import Path
    from slideatelier.web.app import app as _app

    # The card template lives inside a Jinja partial; assert against its
    # source directly so we don't need a full deck-render fixture.
    tpl = Path(__file__).resolve().parent.parent / "src" / "slideatelier" / "web" / "templates" / "workflow" / "_slide_edit_card.html"
    src = tpl.read_text()
    assert "data-slide-preview=" in src, "slide preview canvas must carry data-slide-preview attribute"
    # The drag JS must NOT resolve the preview via the ambiguous class selector.
    assert ".aspect-\\\\[16\\\\/9\\\\]" not in src or "data-slide-preview" in src
    assert "querySelector('[data-slide-preview]')" in src, "drag JS must select preview via data-slide-preview"

    # Smoke: the wireframe page itself loads without erroring.
    c = TestClient(_app)
    r = c.get("/")
    assert r.status_code == 200
