"""Sprint Q — slide analytics tests.

Covers:
  * POST /api/events with a valid payload inserts a row.
  * Bad payloads (missing fields, oversized, bogus event types) return 400.
  * Per-deck opt-out flag suppresses beacon emission in the rendered HTML.
  * Dashboard route renders aggregate stats.
  * anon_session is preserved across requests for the same visitor.
  * extras_json is scrubbed of obvious PII.
  * record_event aggregations (funnel, dwell, CTR) compute correctly.
"""
from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

from slideatelier import analytics
from slideatelier.models import SlideDeck
from slideatelier.template import Template
from slideatelier.web.app import app
from slideatelier.web_renderer import WebRenderer


# ---------------------------------------------------------------------------
# Fixtures — keep deck creation lightweight.
# ---------------------------------------------------------------------------


def _make_deck() -> SlideDeck:
    return SlideDeck.model_validate({
        "title": "Q3 Review",
        "subtitle": "",
        "core_message": "x",
        "narrative_arc": "x",
        "slides": [
            {"layout": "title", "title": "Hello world", "strap": "",
             "body": [], "body_left": [], "body_right": [],
             "speaker_notes": "", "rationale": "", "asset_ref": None, "extras": []},
            {"layout": "content", "title": "Why it matters", "strap": "",
             "body": ["a", "b"], "body_left": [], "body_right": [],
             "speaker_notes": "", "rationale": "", "asset_ref": None, "extras": []},
            {"layout": "key_takeaway", "title": "Do this", "strap": "",
             "body": [], "body_left": [], "body_right": [],
             "speaker_notes": "", "rationale": "", "asset_ref": None, "extras": []},
        ],
    })


def _seed_job(tmp_path, monkeypatch, job_id: str = "q-1") -> str:
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "deck.json").write_text(_make_deck().model_dump_json())
    return job_id


def _ev(deck_id: str, slug: str, idx: int, event: str, **extra) -> dict:
    out = {
        "deck_id": deck_id,
        "slug": slug,
        "slide_id": idx,
        "event": event,
        "ts": 1700000000.0 + idx,
        "anon_session": "abcd1234efgh5678",
    }
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# 1. Valid payload inserts.
# ---------------------------------------------------------------------------


def test_valid_event_inserts_into_db(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "ev-ok")
    c = TestClient(app)
    payload = _ev(job_id, "slug-ok", 0, "slide_enter")
    r = c.post("/api/events", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("stored") is True

    # Row landed in DB.
    rows = analytics.all_events_for(job_id, output_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0]["event"] == "slide_enter"
    assert rows[0]["anon_session"] == "abcd1234efgh5678"
    assert rows[0]["slide_idx"] == 0


def test_slide_exit_with_duration_persists(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "ev-exit")
    c = TestClient(app)
    payload = _ev(job_id, "slug-exit", 1, "slide_exit", duration_ms=4500)
    r = c.post("/api/events", json=payload)
    assert r.status_code == 200
    rows = analytics.all_events_for(job_id, output_dir=tmp_path)
    assert rows[0]["duration_ms"] == 4500


# ---------------------------------------------------------------------------
# 2. Bad payloads -> 400.
# ---------------------------------------------------------------------------


def test_missing_event_returns_400(tmp_path, monkeypatch):
    _seed_job(tmp_path, monkeypatch, "ev-bad-1")
    c = TestClient(app)
    p = _ev("ev-bad-1", "x", 0, "slide_enter")
    p.pop("event")
    r = c.post("/api/events", json=p)
    assert r.status_code == 400


def test_unknown_event_type_returns_400(tmp_path, monkeypatch):
    _seed_job(tmp_path, monkeypatch, "ev-bad-2")
    c = TestClient(app)
    p = _ev("ev-bad-2", "x", 0, "yolo_event")
    r = c.post("/api/events", json=p)
    assert r.status_code == 400


def test_oversized_payload_returns_400(tmp_path, monkeypatch):
    _seed_job(tmp_path, monkeypatch, "ev-big")
    c = TestClient(app)
    p = _ev("ev-big", "x", 0, "slide_enter")
    p["extras"] = {"junk": "x" * 5000}
    r = c.post("/api/events", json=p)
    assert r.status_code == 400


def test_invalid_anon_session_returns_400(tmp_path, monkeypatch):
    _seed_job(tmp_path, monkeypatch, "ev-bad-sess")
    c = TestClient(app)
    p = _ev("ev-bad-sess", "x", 0, "slide_enter")
    p["anon_session"] = "x"  # too short
    r = c.post("/api/events", json=p)
    assert r.status_code == 400


def test_negative_slide_idx_returns_400(tmp_path, monkeypatch):
    _seed_job(tmp_path, monkeypatch, "ev-bad-idx")
    c = TestClient(app)
    p = _ev("ev-bad-idx", "x", -5, "slide_enter")
    r = c.post("/api/events", json=p)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 3. Opt-out suppresses beacon emission.
# ---------------------------------------------------------------------------


def test_opt_out_flag_suppresses_beacon_in_rendered_html(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "ev-optout")
    job_dir = tmp_path / "workflow" / job_id
    analytics.save_settings(job_dir, {"analytics_enabled": False})

    c = TestClient(app)
    pub = c.post(f"/api/jobs/{job_id}/publish").json()
    r = c.get(f"/web/{pub['slug']}")
    assert r.status_code == 200
    body = r.text
    # The beacon JS comment header should not appear.
    assert "atelier_anon" not in body
    assert "/api/events" not in body
    # Privacy notice is also suppressed when off.
    assert "anonymous viewing analytics enabled" not in body


def test_opt_in_emits_beacon_and_notice(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "ev-optin")
    c = TestClient(app)
    pub = c.post(f"/api/jobs/{job_id}/publish").json()
    r = c.get(f"/web/{pub['slug']}")
    body = r.text
    assert "atelier_anon" in body
    assert "/api/events" in body
    assert "anonymous viewing analytics enabled" in body


def test_renderer_default_is_no_beacon():
    """Calling render_deck_html without analytics_enabled must stay quiet."""
    deck = _make_deck()
    out = WebRenderer(Template()).render_deck_html(deck, slug="abc")
    assert "atelier_anon" not in out
    assert "/api/events" not in out


# ---------------------------------------------------------------------------
# 4. Dashboard renders aggregate stats.
# ---------------------------------------------------------------------------


def test_dashboard_renders_aggregate_stats(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "ev-dash")
    c = TestClient(app)
    # Seed a small population.
    for sess in ("session-aaaa1111", "session-bbbb2222", "session-cccc3333"):
        c.post("/api/events", json=_ev(job_id, "slug-d", 0, "slide_enter",
                                       anon_session=sess))
        c.post("/api/events", json=_ev(job_id, "slug-d", 1, "slide_enter",
                                       anon_session=sess))
        c.post("/api/events", json=_ev(job_id, "slug-d", 1, "slide_exit",
                                       duration_ms=3000, anon_session=sess))
    # Only one viewer makes it to slide 2.
    c.post("/api/events", json=_ev(job_id, "slug-d", 2, "slide_enter",
                                   anon_session="session-aaaa1111"))
    # One CTA click on slide 1.
    c.post("/api/events", json=_ev(job_id, "slug-d", 1, "cta_click",
                                   anon_session="session-aaaa1111",
                                   extras={"href": "https://example.com"}))

    r = c.get(f"/workflow/{job_id}/analytics")
    assert r.status_code == 200, r.text
    body = r.text
    # Headline appears with the unique session count.
    assert "Total unique viewers" in body
    # Funnel + dwell + CTA sections render.
    assert "Per-slide retention" in body
    assert "Dwell time per slide" in body
    assert "CTA click-through rate" in body
    # 3 unique viewers seeded.
    assert ">3<" in body or "3</div>" in body

    # Funnel helper directly: slide 0 = 3, slide 2 = 1.
    funnel = analytics.deck_funnel(job_id, output_dir=tmp_path)
    by_idx = {row["slide_idx"]: row for row in funnel}
    assert by_idx[0]["unique_sessions"] == 3
    assert by_idx[2]["unique_sessions"] == 1
    assert abs(by_idx[2]["retention"] - 1 / 3) < 1e-6


# ---------------------------------------------------------------------------
# 5. anon_session is preserved across requests.
# ---------------------------------------------------------------------------


def test_anon_session_preserved_across_requests(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "ev-sess")
    c = TestClient(app)
    sess = "persistent12345678"
    for idx in range(3):
        r = c.post(
            "/api/events",
            json=_ev(job_id, "slug-s", idx, "slide_enter", anon_session=sess),
        )
        assert r.status_code == 200
    rows = analytics.all_events_for(job_id, output_dir=tmp_path)
    assert len(rows) == 3
    assert {r["anon_session"] for r in rows} == {sess}


# ---------------------------------------------------------------------------
# 6. PII scrubbing.
# ---------------------------------------------------------------------------


def test_extras_scrubs_emails_and_phones(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "ev-pii")
    c = TestClient(app)
    p = _ev(job_id, "slug-p", 0, "slide_enter")
    p["extras"] = {
        "referrer": "https://example.com/path?q=1",
        "label": "ping me at user@example.com or 555-867-5309",
        "ok": "harmless",
    }
    r = c.post("/api/events", json=p)
    assert r.status_code == 200, r.text
    rows = analytics.all_events_for(job_id, output_dir=tmp_path)
    extras = json.loads(rows[0]["extras_json"])
    # Email/phone-laden field is dropped; harmless field survives.
    assert "label" not in extras
    assert extras.get("ok") == "harmless"
    # Referrer is reduced to its origin.
    assert extras.get("referrer") == "https://example.com"


def test_pii_keys_are_dropped(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "ev-pii-keys")
    c = TestClient(app)
    p = _ev(job_id, "slug-pk", 0, "slide_enter")
    p["extras"] = {"email": "x@y.com", "ip": "1.2.3.4", "ok": "z"}
    r = c.post("/api/events", json=p)
    assert r.status_code == 200
    rows = analytics.all_events_for(job_id, output_dir=tmp_path)
    extras = json.loads(rows[0]["extras_json"])
    assert "email" not in extras
    assert "ip" not in extras
    assert extras.get("ok") == "z"


# ---------------------------------------------------------------------------
# 7. Toggle endpoint flips settings, and a follow-up beacon POST is dropped.
# ---------------------------------------------------------------------------


def test_toggle_off_then_post_returns_204_no_row(tmp_path, monkeypatch):
    job_id = _seed_job(tmp_path, monkeypatch, "ev-toggle")
    c = TestClient(app)
    # Flip off.
    r = c.post(
        f"/workflow/{job_id}/analytics/toggle",
        json={"analytics_enabled": False},
    )
    assert r.status_code == 200
    assert r.json()["analytics_enabled"] is False
    # POST an event — should be silently dropped (204) and no row written.
    r2 = c.post("/api/events", json=_ev(job_id, "slug-t", 0, "slide_enter"))
    assert r2.status_code == 204
    rows = analytics.all_events_for(job_id, output_dir=tmp_path)
    assert rows == []


# ---------------------------------------------------------------------------
# 8. Aggregations directly: median + p90.
# ---------------------------------------------------------------------------


def test_dwell_median_and_p90_are_correct(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_id = "ev-dwell"
    # Insert 10 exits on slide 0 with durations 1000..10000 ms.
    for i, ms in enumerate(range(1000, 11000, 1000), start=1):
        analytics.record_event(
            {
                "deck_id": job_id,
                "slug": "slug-w",
                "slide_idx": 0,
                "event": "slide_exit",
                "ts": 1700000000.0 + i,
                "anon_session": f"session-{i:016d}",
                "duration_ms": ms,
                "extras": {},
            },
            output_dir=tmp_path,
        )
    dwell = analytics.slide_dwell(job_id, output_dir=tmp_path)
    assert len(dwell) == 1
    row = dwell[0]
    assert row["samples"] == 10
    # p50 of 1000..10000 with our nearest-rank impl ~ 5000 or 6000.
    assert row["median_ms"] in (5000, 6000)
    assert row["p90_ms"] in (9000, 10000)


# ---------------------------------------------------------------------------
# 9. Schema is created idempotently — does not clash with auth tables.
# ---------------------------------------------------------------------------


def test_schema_is_idempotent_and_keeps_other_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    db_path = analytics.db_path(tmp_path)
    # Pretend the auth module already created its own table.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    # Now insert through analytics — it should create slide_events without
    # touching `users`.
    analytics.record_event(
        {
            "deck_id": "ev-sx",
            "slug": "slug-sx",
            "slide_idx": 0,
            "event": "slide_enter",
            "ts": 1.0,
            "anon_session": "schema-test-1234",
            "duration_ms": None,
            "extras": {},
        },
        output_dir=tmp_path,
    )
    conn = sqlite3.connect(str(db_path))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    conn.close()
    assert "users" in tables
    assert "slide_events" in tables
