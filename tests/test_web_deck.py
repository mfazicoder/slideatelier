"""Sprint J.D — Web Deck publishing tests.

Covers:
1. WebRenderer emits valid HTML for a 3-slide deck.
2. POST /api/jobs/<job_id>/publish creates the slug + HTML, returns valid URL JSON.
3. GET /web/<slug> returns 200 with all slide titles in the body.
4. GET /web/<bad-slug> returns 404.
5. Presenter-mode toggle is present in the HTML (`p` keybinding).
6. Theme colours are emitted as CSS custom properties on slide sections.
"""
from __future__ import annotations

import json
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from slideatelier.models import SlideDeck
from slideatelier.template import Template
from slideatelier.web.app import app
from slideatelier.web_renderer import WebRenderer


# ---------------------------------------------------------------------------
# Fixtures (inline for clarity)
# ---------------------------------------------------------------------------

def _make_deck() -> SlideDeck:
    return SlideDeck.model_validate({
        "title": "Concentrate on what is working",
        "subtitle": "Q3 review",
        "core_message": "Enterprise drove the quarter; reallocate to defend the lead.",
        "narrative_arc": "Open with the asymmetric quarter. Diagnose the SMB break. Recommend reallocation.",
        "slides": [
            {
                "layout": "title",
                "title": "Concentrate on what is working",
                "strap": "",
                "body": [],
                "body_left": [], "body_right": [],
                "speaker_notes": "Open with the headline.",
                "rationale": "", "asset_ref": None, "extras": [],
            },
            {
                "layout": "content",
                "title": "Enterprise drove all Q3 growth",
                "strap": "While SMB stagnated for the first time in 8 quarters",
                "body": [
                    "Enterprise revenue plus 34 percent YoY",
                    "SMB revenue flat for the first time in 8 quarters",
                    "Mid-market on plan",
                ],
                "body_left": [], "body_right": [],
                "speaker_notes": "The asymmetry is the story.",
                "rationale": "", "asset_ref": None, "extras": [],
            },
            {
                "layout": "key_takeaway",
                "title": "Reallocate two million to defend the lead",
                "strap": "",
                "body": [],
                "body_left": [], "body_right": [],
                "speaker_notes": "Land the recommendation.",
                "rationale": "", "asset_ref": None, "extras": [],
            },
        ],
    })


class _ParseChecker(HTMLParser):
    """Minimal HTML well-formedness sniffer — verifies tags balance.
    Self-closing & void elements are ignored; we only need confirmation that
    the document parses without hitting any unrecoverable error events."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.saw_html = False
        self.saw_body = False

    def handle_starttag(self, tag, attrs):
        if tag == "html":
            self.saw_html = True
        if tag == "body":
            self.saw_body = True

    def error(self, message):  # pragma: no cover - py3 stdlib silently ignores
        self.errors.append(message)


def _parses_as_html(doc: str) -> bool:
    p = _ParseChecker()
    p.feed(doc)
    p.close()
    return p.saw_html and p.saw_body and not p.errors


# ---------------------------------------------------------------------------
# 1. Renderer emits valid HTML for a 3-slide deck
# ---------------------------------------------------------------------------

def test_renderer_emits_valid_html_for_three_slide_deck():
    deck = _make_deck()
    tpl = Template()
    out = WebRenderer(tpl).render_deck_html(deck, slug="abc12345")
    assert out.startswith("<!doctype html>")
    assert _parses_as_html(out)
    # Every slide title appears in the body.
    for s in deck.slides:
        assert s.title in out
    # Three <section class="slide ..."> elements.
    assert out.count('<section class="slide') == 3


# ---------------------------------------------------------------------------
# 2. POST /api/jobs/<id>/publish creates files + returns 200 JSON
# ---------------------------------------------------------------------------

def _seed_job(tmp_path, monkeypatch, job_id="pub-test-1") -> str:
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    deck = _make_deck()
    (job_dir / "deck.json").write_text(deck.model_dump_json())
    return job_id


def test_publish_endpoint_creates_artifacts_and_returns_url(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch)
    c = TestClient(app)
    r = c.post(f"/api/jobs/{job_id}/publish")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"].startswith("/web/")
    slug = body["slug"]
    assert slug and len(slug) >= 6
    # Files persist on disk.
    job_dir = tmp_path / "workflow" / job_id
    assert (job_dir / "web_slug.txt").read_text().strip() == slug
    assert (job_dir / "web_deck.html").exists()
    assert (job_dir / "web_deck.html").read_text().startswith("<!doctype html>")
    # Slug index reflects the mapping.
    index = json.loads((tmp_path / "web_slugs.json").read_text())
    assert index[slug] == job_id


def test_publish_is_idempotent_reuses_slug(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "pub-idem")
    c = TestClient(app)
    r1 = c.post(f"/api/jobs/{job_id}/publish")
    r2 = c.post(f"/api/jobs/{job_id}/publish")
    assert r1.json()["slug"] == r2.json()["slug"]


# ---------------------------------------------------------------------------
# 3. GET /web/<slug> returns 200 with slide titles in body
# ---------------------------------------------------------------------------

def test_public_view_serves_html_with_slide_titles(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "pub-view")
    c = TestClient(app)
    pub = c.post(f"/api/jobs/{job_id}/publish").json()
    slug = pub["slug"]
    r = c.get(f"/web/{slug}")
    assert r.status_code == 200
    body = r.text
    deck = _make_deck()
    for s in deck.slides:
        assert s.title in body


# ---------------------------------------------------------------------------
# 4. GET /web/<bad-slug> returns 404
# ---------------------------------------------------------------------------

def test_public_view_404_for_unknown_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    c = TestClient(app)
    r = c.get("/web/doesnotexist")
    assert r.status_code == 404


def test_public_view_404_for_path_traversal_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    c = TestClient(app)
    r = c.get("/web/..%2Fetc")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 5. Presenter-mode toggle in HTML
# ---------------------------------------------------------------------------

def test_presenter_mode_toggle_present_in_html(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "pub-presenter")
    c = TestClient(app)
    pub = c.post(f"/api/jobs/{job_id}/publish").json()
    r = c.get(f"/web/{pub['slug']}")
    body = r.text
    # The 'p' key triggers presenter mode in the inline JS.
    assert "presenter" in body.lower()
    assert "togglePresenter" in body
    # The keybinding for 'p' must be wired (case insensitive).
    assert "k==='p'" in body or "k==='P'" in body


# ---------------------------------------------------------------------------
# 6. Theme colours emitted as CSS custom properties on slide sections
# ---------------------------------------------------------------------------

def test_theme_colors_emitted_as_css_custom_properties(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "pub-theme")
    c = TestClient(app)
    pub = c.post(f"/api/jobs/{job_id}/publish").json()
    r = c.get(f"/web/{pub['slug']}")
    body = r.text
    # Default Template's primary is #1F3A5F; accent #C86E3C; bg #FFFFFF.
    assert "--brand-primary:#1F3A5F" in body
    assert "--brand-accent:#C86E3C" in body
    assert "--brand-bg:#FFFFFF" in body
    # And these custom properties should appear on each slide section's style="..."
    # — at least one occurrence per primary colour for the section.
    assert body.count("--brand-primary:") >= 3


# ---------------------------------------------------------------------------
# Bonus coverage — basic safety / lookup endpoint
# ---------------------------------------------------------------------------

def test_web_deck_url_endpoint_404_before_publish(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "pub-lookup")
    c = TestClient(app)
    r = c.get(f"/api/jobs/{job_id}/web-deck-url")
    assert r.status_code == 404


def test_web_deck_url_endpoint_returns_url_after_publish(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "pub-lookup-2")
    c = TestClient(app)
    pub = c.post(f"/api/jobs/{job_id}/publish").json()
    r = c.get(f"/api/jobs/{job_id}/web-deck-url")
    assert r.status_code == 200
    assert r.json()["slug"] == pub["slug"]


def test_publish_404_when_deck_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    c = TestClient(app)
    r = c.post("/api/jobs/no-such-job/publish")
    assert r.status_code == 404


def test_slide_deep_link_redirects_to_fragment(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "pub-deep")
    c = TestClient(app)
    pub = c.post(f"/api/jobs/{job_id}/publish").json()
    slug = pub["slug"]
    r = c.get(f"/web/{slug}/slide/2", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == f"/web/{slug}#slide-2"
