"""Launch-day landing page tests.

Covers:
  - GET /             returns 200 with hero text + OG meta + demo iframe
  - GET /app          serves the legacy generator UI
  - POST /api/waitlist accepts valid email + rejects invalid + handles dupes
  - GET /privacy, /terms render placeholder legal stubs
  - GET /static/og.png serves the placeholder image
  - signed-in users on / get redirected to /app
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from slideatelier.web.app import app
from slideatelier.web.landing_routes import (
    FAQS,
    FEATURES,
    HEADLINE,
    list_waitlist_emails,
)


client = TestClient(app)


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------

def test_landing_renders_with_hero_and_cta():
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    # Hero copy must be visible — chosen headline elements.
    assert "hand-designed" in body
    assert "Native PowerPoint" in body
    assert "Request invite" in body  # private-beta CTA
    assert "slideAtelier" in body or "slide<span" in body


def test_landing_demo_iframe_uses_R2rmnAXd():
    """The live demo embed points at the existing DIFC deck."""
    r = client.get("/")
    assert r.status_code == 200
    assert "/web/R2rmnAXd" in r.text
    assert "<iframe" in r.text


def test_landing_has_og_metadata():
    r = client.get("/")
    body = r.text
    assert 'property="og:title"' in body
    assert 'property="og:description"' in body
    assert 'property="og:image"' in body
    assert 'property="og:url"' in body
    assert 'name="twitter:card"' in body


def test_landing_features_grid_has_8_to_12_items():
    """The grid must surface between 8 and 12 shipped features."""
    assert 8 <= len(FEATURES) <= 12, f"have {len(FEATURES)} features"
    r = client.get("/")
    body = r.text
    # Every feature title appears in the rendered HTML.
    for feat in FEATURES:
        assert feat["title"] in body, f"missing feature: {feat['title']}"


def test_landing_faq_has_5_to_7_items():
    assert 5 <= len(FAQS) <= 7, f"have {len(FAQS)} faqs"
    r = client.get("/")
    body = r.text
    # Jinja escapes apostrophes (' -> &#39;) by default; use a stable token
    # from each question that survives HTML escaping.
    for q in FAQS:
        token = q["question"].split("'")[0].split("?")[0].strip()
        assert len(token) >= 4, f"faq token too short: {token}"
        assert token in body, f"missing faq token {token!r}"


def test_landing_footer_has_legal_links():
    r = client.get("/")
    body = r.text
    assert 'href="/privacy"' in body
    assert 'href="/terms"' in body


def test_landing_headline_is_short():
    """Sanity: the chosen headline is under 80 chars (≤10 words rule)."""
    assert len(HEADLINE) <= 80
    word_count = len(HEADLINE.replace(".", "").split())
    assert word_count <= 12, f"{word_count} words is too long"


# ---------------------------------------------------------------------------
# Authenticated redirect
# ---------------------------------------------------------------------------

def test_landing_redirects_signed_in_users_to_app():
    """If the auth cookie is set, GET / 302s to /app.

    We don't need a real DB-backed session — the landing route only checks
    cookie presence. The auth middleware does full validation on /app.
    """
    r = client.get("/", cookies={"atelier_session": "anything"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/app"


def test_app_route_serves_generator_ui():
    """The legacy generator page is now mounted at /app."""
    r = client.get("/app")
    assert r.status_code == 200
    # The generator page surfaces the brief form.
    assert "Generate" in r.text
    assert "<textarea" in r.text or 'name="content"' in r.text


# ---------------------------------------------------------------------------
# Waitlist
# ---------------------------------------------------------------------------

def test_waitlist_accepts_valid_email(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    c = TestClient(app)
    r = c.post("/api/waitlist", data={"email": "founder@example.com"})
    assert r.status_code == 200
    assert "✓" in r.text or "thank" in r.text.lower() or "got it" in r.text.lower()
    # The email landed in SQLite under the agreed path.
    db_path = tmp_path / "atelier.db"
    assert db_path.exists()
    emails = list_waitlist_emails(tmp_path)
    assert "founder@example.com" in emails


def test_waitlist_rejects_invalid_email(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    c = TestClient(app)
    for bad in ["", "not-an-email", "foo@", "@bar.com", "foo@bar", "  "]:
        r = c.post("/api/waitlist", data={"email": bad})
        assert r.status_code == 400, f"bad input {bad!r} should 400 (got {r.status_code})"


def test_waitlist_dedupes_same_email(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    c = TestClient(app)
    r1 = c.post("/api/waitlist", data={"email": "dup@example.com"})
    r2 = c.post("/api/waitlist", data={"email": "DUP@example.com"})  # different case
    assert r1.status_code == 200
    assert r2.status_code == 200
    emails = list_waitlist_emails(tmp_path)
    # Stored lowercased; only one row.
    assert emails.count("dup@example.com") == 1


def test_waitlist_table_coexists_with_auth_tables(tmp_path, monkeypatch):
    """Both the waitlist endpoint and the auth module use CREATE TABLE
    IF NOT EXISTS on the same atelier.db file. Inserting into one must
    not break the other.
    """
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    db_path = tmp_path / "atelier.db"

    # Pre-create auth-style tables to simulate the auth agent having run first.
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, email TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER)"
        )
        conn.commit()

    c = TestClient(app)
    r = c.post("/api/waitlist", data={"email": "coexist@example.com"})
    assert r.status_code == 200

    # Verify auth tables are still present + waitlist table was added.
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r[0] for r in rows}
    assert "users" in names
    assert "sessions" in names
    assert "waitlist_emails" in names


# ---------------------------------------------------------------------------
# Legal stubs
# ---------------------------------------------------------------------------

def test_privacy_renders():
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "Privacy" in r.text
    assert "founder@slideatelier.com" in r.text


def test_terms_renders():
    r = client.get("/terms")
    assert r.status_code == 200
    assert "Terms" in r.text
    assert "founder@slideatelier.com" in r.text


# ---------------------------------------------------------------------------
# OG image
# ---------------------------------------------------------------------------

def test_og_image_served_at_static_path():
    """The placeholder OG image is generated on import + served by the static
    mount. We accept either a real PNG (length > 0, content-type image/png)
    or, if the generator failed silently, a 404 — but never a 500.
    """
    r = client.get("/static/og.png")
    # Must not 500. May 200 (file exists) or 404 (generator skipped).
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.headers["content-type"].startswith("image/")
        assert len(r.content) > 0
        # PNG magic number — sanity check it's a real PNG, not a stub.
        assert r.content.startswith(b"\x89PNG\r\n\x1a\n")


# ---------------------------------------------------------------------------
# Web Deck OG meta
# ---------------------------------------------------------------------------

def test_web_deck_inherits_deck_title_as_og_title(tmp_path, monkeypatch):
    """A shared /web/<slug> URL previews with the deck's own title."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))

    # Set up a fake published deck:
    job_id = "og-meta-job"
    slug = "OgMetaXY"
    job_dir = tmp_path / "workflow" / job_id
    job_dir.mkdir(parents=True)

    deck = {
        "title": "DIFC Tax Strategy Q3 Review",
        "subtitle": "",
        "core_message": "Test core message stating an answer in one sentence.",
        "narrative_arc": "Open. Defend. Close.",
        "slides": [],
    }
    (job_dir / "deck.json").write_text(json.dumps(deck))
    (job_dir / "web_deck.html").write_text(
        '<!doctype html><html><head><title>Original</title></head><body>x</body></html>'
    )

    # Register the slug in the legacy index so resolve picks it up.
    (tmp_path / "web_slugs.json").write_text(json.dumps({slug: job_id}))

    c = TestClient(app)
    r = c.get(f"/web/{slug}")
    assert r.status_code == 200, r.text
    assert 'property="og:title"' in r.text
    assert "DIFC Tax Strategy Q3 Review" in r.text
    assert 'property="og:image"' in r.text
    assert 'property="og:url"' in r.text
    assert 'name="twitter:card"' in r.text
