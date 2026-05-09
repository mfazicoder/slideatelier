"""QA pass — sanity tests added during launch readiness review.

Verifies the highest-risk launch surfaces actually work end-to-end at the
HTTP layer:
  - hi-fi page surfaces Render and Download buttons correctly,
  - empty-deck wireframe renders a friendly empty-state hint,
  - bogus workflow / web URLs serve a friendly HTML 404 to browsers but
    keep JSON for API clients,
  - HTMX swaps still receive JSON details (so event.detail.successful logic
    on the client doesn't get derailed).

Each test is independent. They double as regression coverage for the
inline fixes documented in /LAUNCH_BLOCKERS.md.
"""
from __future__ import annotations

import json as _json
import os
from fastapi.testclient import TestClient

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)

from slideatelier.web.app import app  # noqa: E402

c = TestClient(app)


def _seed_workflow(tmp_path, monkeypatch, *, slides=None, with_pptx=False, status=None):
    """Helper — seed a workflow dir under tmp_path/workflow/<job_id>/."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_id = "qa-test-" + os.urandom(2).hex()
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    deck = {
        "title": "T",
        "subtitle": "",
        "core_message": "Test core message stating an answer in one sentence.",
        "narrative_arc": "Open. Defend. Close.",
        "slides": slides if slides is not None else [],
    }
    (job_dir / "deck.json").write_text(_json.dumps(deck))
    (job_dir / "status.json").write_text(_json.dumps(status or {
        "stage": "wireframe", "status": "done",
        "message": "ready", "updated_at": "2026-05-01T00:00:00+00:00",
    }))
    if with_pptx:
        (job_dir / "deck.pptx").write_bytes(b"PK\x03\x04 stub pptx")
    return job_id, job_dir


def test_hi_fi_page_shows_render_button_when_pptx_missing(tmp_path, monkeypatch):
    """hi-fi route must pass pptx_ready/pptx_fresh/tpl/template_name to the
    template; otherwise the page can't decide between Render and Download."""
    job_id, _ = _seed_workflow(tmp_path, monkeypatch, with_pptx=False)
    r = TestClient(app).get(f"/workflow/hi-fi/{job_id}")
    assert r.status_code == 200, r.text
    assert "Render to .pptx" in r.text
    assert "Download .pptx" not in r.text
    # Template-applied panel renders only when tpl is present.
    assert "Template applied" in r.text


def test_hi_fi_page_shows_download_when_pptx_present(tmp_path, monkeypatch):
    """When deck.pptx exists, the page must surface a Download button — this
    was the bug the inline fix addresses (route was not passing pptx_ready)."""
    job_id, job_dir = _seed_workflow(tmp_path, monkeypatch, with_pptx=True)
    # The route gates ready on status.stage == hi_fi & status.status == done,
    # so write that status to flip the page into post-render state.
    (job_dir / "status.json").write_text(_json.dumps({
        "stage": "hi_fi", "status": "done",
        "message": "rendered", "updated_at": "2026-05-01T00:00:00+00:00",
    }))
    r = TestClient(app).get(f"/workflow/hi-fi/{job_id}")
    assert r.status_code == 200, r.text
    assert "Download .pptx" in r.text


def test_empty_deck_wireframe_shows_friendly_empty_state(tmp_path, monkeypatch):
    """Wireframe must not render an empty void when the deck has zero slides."""
    job_id, _ = _seed_workflow(tmp_path, monkeypatch, slides=[])
    r = TestClient(app).get(f"/workflow/wireframe/{job_id}")
    assert r.status_code == 200, r.text
    assert "No slides yet" in r.text


def test_404_workflow_serves_html_to_browser(tmp_path, monkeypatch):
    """A browser navigating to a bogus workflow id should land on a styled
    HTML 404 page — not raw JSON."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    r = TestClient(app).get(
        "/workflow/wireframe/badjobid",
        headers={"accept": "text/html,application/xhtml+xml"},
    )
    assert r.status_code == 404
    assert "<html" in r.text.lower()
    assert "Error 404" in r.text


def test_404_workflow_keeps_json_for_api_clients(tmp_path, monkeypatch):
    """API clients (Accept: application/json) must keep getting JSON detail."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    r = TestClient(app).get(
        "/workflow/wireframe/badjobid",
        headers={"accept": "application/json"},
    )
    assert r.status_code == 404
    assert r.headers.get("content-type", "").startswith("application/json")
    # Body includes detail + observability fields (request_id, status_code).
    assert r.json().get("detail") == "workflow not found"


def test_404_workflow_keeps_json_for_htmx(tmp_path, monkeypatch):
    """HTMX requests check event.detail.successful. They must still receive
    a non-HTML response so swaps don't accidentally inject the 404 page."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    r = TestClient(app).get(
        "/workflow/wireframe/badjobid",
        headers={"hx-request": "true"},
    )
    assert r.status_code == 404
    assert r.headers.get("content-type", "").startswith("application/json")


def test_404_web_slug_serves_html_to_browser():
    """Public deck viewer must show a friendly HTML 404, not JSON."""
    r = TestClient(app).get("/web/totallybogusslug")
    # Accept default is text/html, so this should be HTML.
    assert r.status_code == 404
    assert "<html" in r.text.lower()
