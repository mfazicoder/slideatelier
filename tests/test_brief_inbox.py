"""Tests for the Brief Inbox feature (Sprint W).

We mock Anthropic calls via `unittest.mock.patch` — no live API calls. The
tests cover:
- GET /brief-inbox renders 200
- POST /api/brief-inbox/ingest with empty content returns 400
- POST with valid text creates a job_id and writes brief.txt + status.json
- assemble_brief() correctly attributes paste vs URL vs attachment chunks
- Review page renders both storyboard and analysis once they exist on disk
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from slideatelier.brief_inbox import (
    BriefSource,
    assemble_brief,
    extract_attachment_text,
    extract_urls,
)
from slideatelier.metadata import GenerationMetadata
from slideatelier.models import BriefAnalysis
from slideatelier.storyboard import Storyboard
from slideatelier.web.app import app


# ---------------------------------------------------------------------------
# Helpers — fakes that match the schemas plan_storyboard / analyze_brief return.
# ---------------------------------------------------------------------------

def _fake_storyboard() -> Storyboard:
    return Storyboard.model_validate(
        {
            "title": "Test deck answer",
            "subtitle": "Demo · 2026",
            "core_message": "We should ship the brief inbox now to widen the funnel.",
            "narrative_arc": "Open with the funnel gap. Show the proof. Land the ask.",
            "slides": [
                {
                    "layout": "title",
                    "title": "Brief Inbox unlocks a 4th entry route",
                    "purpose": "Sets the frame.",
                    "rationale": "Cover.",
                },
                {
                    "layout": "key_takeaway",
                    "title": "Paste-to-deck is the highest-wow first impression",
                    "purpose": "Lands the central thesis up front.",
                    "rationale": "Pyramid principle — answer first.",
                },
            ],
        }
    )


def _fake_analysis() -> BriefAnalysis:
    return BriefAnalysis.model_validate(
        {
            "stated_goals": ["Ship a paste-to-deck flow before launch"],
            "audience": "First-time visitors evaluating the product in <30s",
            "key_messages": [
                "Brief inbox is the wow surface",
                "Re-prompt loop builds trust before any editing",
            ],
            "risks_unaddressed": [
                "No rate limit on URL fetching is a DoS vector",
            ],
        }
    )


def _fake_meta() -> GenerationMetadata:
    return GenerationMetadata(
        model_id="test",
        model_response_id="test",
        prompt_version="test",
        cache_hit=False,
        input_hash="",
        duration_seconds=0.01,
    )


# ---------------------------------------------------------------------------
# Page render
# ---------------------------------------------------------------------------

def test_intake_page_returns_200():
    c = TestClient(app)
    r = c.get("/brief-inbox")
    assert r.status_code == 200
    assert "Brief Inbox" in r.text
    assert "Paste a Slack thread" in r.text


# ---------------------------------------------------------------------------
# Ingestion endpoint
# ---------------------------------------------------------------------------

def test_ingest_rejects_empty_content():
    c = TestClient(app)
    r = c.post(
        "/api/brief-inbox/ingest",
        data={"content": ""},
        headers={"accept": "application/json"},
    )
    assert r.status_code == 400


def test_ingest_creates_job_with_text(tmp_path: Path, monkeypatch):
    """Posting valid text creates a workflow job dir + brief.txt + status.json,
    and returns a job_id that resolves to the review page. We mock the Claude
    call so no network/billing is involved.
    """
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    captured: dict = {}

    def fake_analyze(config, brief_text, requirements=""):
        captured["brief_text"] = brief_text
        captured["requirements"] = requirements
        return _fake_storyboard(), _fake_analysis(), _fake_meta()

    c = TestClient(app)
    with patch("slideatelier.web.brief_inbox_routes.analyze_brief", side_effect=fake_analyze):
        r = c.post(
            "/api/brief-inbox/ingest",
            data={
                "content": "We need a deck for the board on the Q3 results. Audience is the CFO.",
                "requirements": "Audience: CFO",
            },
            headers={"accept": "application/json"},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert "job_id" in body and len(body["job_id"]) == 12
    assert body["review_url"].startswith("/brief-inbox/")
    job_dir = tmp_path / "workflow" / body["job_id"]
    assert job_dir.exists()
    # brief.txt written synchronously
    brief_text_on_disk = (job_dir / "brief.txt").read_text()
    assert "Q3 results" in brief_text_on_disk
    # The fake Claude call should have run via background tasks and written
    # storyboard.json + brief_analysis.json. TestClient flushes background
    # tasks before returning so these are deterministic.
    assert (job_dir / "storyboard.json").exists()
    assert (job_dir / "brief_analysis.json").exists()
    status = json.loads((job_dir / "status.json").read_text())
    assert status["status"] == "done"
    # captured args confirm the brief text was passed through
    assert "Q3 results" in captured["brief_text"]


def test_ingest_records_sources_for_paste_only(tmp_path: Path, monkeypatch):
    """The paste alone produces a single 'paste' source attribution."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fake_analyze(config, brief_text, requirements=""):
        return _fake_storyboard(), _fake_analysis(), _fake_meta()

    c = TestClient(app)
    with patch("slideatelier.web.brief_inbox_routes.analyze_brief", side_effect=fake_analyze):
        r = c.post(
            "/api/brief-inbox/ingest",
            data={"content": "Pure paste, no URLs and no attachments."},
            headers={"accept": "application/json"},
        )
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    sources = json.loads((tmp_path / "workflow" / job_id / "brief_sources.json").read_text())
    assert len(sources) == 1
    assert sources[0]["kind"] == "paste"
    assert sources[0]["label"] == "pasted text"


# ---------------------------------------------------------------------------
# assemble_brief() unit tests — source attribution
# ---------------------------------------------------------------------------

def test_assemble_brief_attributes_paste_and_attachment_chunks():
    """assemble_brief should produce one source per chunk in order. URL fetches
    are skipped (fetch_urls=False) so we don't depend on the network."""
    paste = "Headline brief content here.\n\nMore paragraph text."
    attachment = ("notes.txt", b"Plain text from the upload.")
    brief, sources = assemble_brief(paste, [attachment], fetch_urls=False)

    assert "Headline brief content here." in brief
    assert "Plain text from the upload." in brief

    assert [s.kind for s in sources] == ["paste", "attachment"]
    assert sources[0].label == "pasted text"
    assert sources[0].char_count == len(paste)
    assert sources[1].label == "notes.txt"
    assert sources[1].char_count > 0
    assert sources[1].note == ""


def test_assemble_brief_attributes_skipped_attachment_with_note():
    """Unsupported file extensions are recorded in `sources` with a note,
    not silently dropped."""
    _brief, sources = assemble_brief(
        "paste",
        [("ignore.exe", b"binary"), ("notes.md", b"# heading\n\nbody")],
        fetch_urls=False,
    )
    by_label = {s.label: s for s in sources}
    assert "ignore.exe" in by_label
    assert by_label["ignore.exe"].char_count == 0
    assert "unsupported" in by_label["ignore.exe"].note
    assert by_label["notes.md"].char_count > 0


def test_assemble_brief_skips_auth_gated_urls_with_note():
    """A Google Doc URL is recorded as a source but with a 'requires
    authentication' note rather than being silently dropped."""
    paste = "See https://docs.google.com/document/d/abc123/edit for context."
    _brief, sources = assemble_brief(paste, [], fetch_urls=True)
    url_sources = [s for s in sources if s.kind == "url"]
    assert len(url_sources) == 1
    assert "authentication" in url_sources[0].note
    assert url_sources[0].char_count == 0


def test_extract_urls_dedup_and_strip_trailing_punctuation():
    text = (
        "Check https://example.com/post.\n"
        "Also https://example.com/post and https://other.example.org!"
    )
    urls = extract_urls(text)
    # First URL had a trailing dot — should be stripped. The duplicate is
    # deduped. Order preserved.
    assert urls == [
        "https://example.com/post",
        "https://other.example.org",
    ]


def test_extract_attachment_text_handles_plain_text():
    text, note = extract_attachment_text("notes.txt", b"hello world")
    assert text == "hello world"
    assert note == ""


def test_extract_attachment_text_rejects_unsupported_format():
    text, note = extract_attachment_text("song.mp3", b"\x00\x01")
    assert text == ""
    assert "unsupported" in note


# ---------------------------------------------------------------------------
# Review page
# ---------------------------------------------------------------------------

def test_review_page_renders_storyboard_and_analysis(tmp_path: Path, monkeypatch):
    """When storyboard.json + brief_analysis.json + status.json exist with
    status=done, the review page renders both side-by-side."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))

    job_id = "review-test-1"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "storyboard.json").write_text(_fake_storyboard().model_dump_json())
    (job_dir / "brief_analysis.json").write_text(_fake_analysis().model_dump_json())
    (job_dir / "brief_sources.json").write_text(
        json.dumps([BriefSource(kind="paste", label="pasted text", char_count=42).to_dict()])
    )
    (job_dir / "status.json").write_text(
        json.dumps({"stage": "storyboard", "status": "done", "message": "ready"})
    )

    c = TestClient(app)
    r = c.get(f"/brief-inbox/{job_id}/review")
    assert r.status_code == 200
    # Storyboard half
    assert "Test deck answer" in r.text
    assert "Brief Inbox unlocks a 4th entry route" in r.text
    # Analysis half
    assert "Stated goals" in r.text
    assert "Ship a paste-to-deck flow before launch" in r.text
    assert "First-time visitors evaluating the product" in r.text
    assert "DoS vector" in r.text
    # Sources rendered
    assert "pasted text" in r.text
    # Continue-to-wireframe CTA points at the same job_id used by the
    # three-stage workflow.
    assert f"/workflow/storyboard/{job_id}" in r.text


def test_review_page_404_for_unknown_job(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    c = TestClient(app)
    r = c.get("/brief-inbox/nonexistent/review")
    assert r.status_code == 404


def test_review_page_shows_running_status_before_done(tmp_path: Path, monkeypatch):
    """While the background task is mid-flight, only status.json exists. The
    page should render the running indicator (and not the storyboard half)."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))

    job_id = "running-test"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "status.json").write_text(
        json.dumps(
            {"stage": "storyboard", "status": "running", "message": "Reading the brief…"}
        )
    )
    c = TestClient(app)
    r = c.get(f"/brief-inbox/{job_id}/review")
    assert r.status_code == 200
    assert "Reading the brief…" in r.text
    # When not ready, storyboard content shouldn't appear.
    assert "Test deck answer" not in r.text
