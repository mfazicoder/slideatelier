"""Landing page + waitlist routes (launch readiness).

Surfaces:
  GET  /                — public marketing landing (or 302 to /app for signed-in users)
  GET  /app             — the existing generator UI (formerly mounted at /)
  POST /api/waitlist    — email-capture from the landing-page footer
  GET  /privacy         — legal stub
  GET  /terms           — legal stub
  GET  /static/og.png   — generated lazily on import if missing

The waitlist table lives in the SAME SQLite file the auth module owns
(`${SLIDEATELIER_OUTPUT_DIR}/atelier.db`). We coordinate by using
`CREATE TABLE IF NOT EXISTS` and never touching the auth tables.
"""
from __future__ import annotations

import datetime as _dt
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse


# RFC-5322-ish; intentionally permissive — we accept anything with a sane local
# part, an @, a dot in the domain, and a 2+ char tld. Stricter validation
# (DNS, MX) is out of scope for a waitlist signup form.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_AUTH_COOKIE_NAME = "atelier_session"  # mirrors slideatelier.auth.db.SESSION_COOKIE_NAME


# ---------------------------------------------------------------------------
# Headline copy + feature grid + FAQ — kept in code (not the template) so the
# template is presentation-only and so tests can introspect this content.
# ---------------------------------------------------------------------------

# Chosen headline. Three candidates were drafted; the chosen one leads the
# template hero verbatim.
HEADLINE = "Decks that look hand-designed. Native PowerPoint. Public web URL."


FEATURES: list[dict[str, str]] = [
    {
        "tag": "Generation",
        "title": "AI-driven deck generation",
        "description": "Drop in a brief — Claude proposes narrative arc, core message, and slide outline you can edit at any stage.",
    },
    {
        "tag": "Output",
        "title": "Native PowerPoint export",
        "description": ".pptx output where squares stay squares and charts stay native PowerPoint charts. Edits round-trip cleanly.",
    },
    {
        "tag": "Hosted",
        "title": "Public web deck per deck",
        "description": "Every deck gets a public URL with native HTML+SVG, presenter mode, fullscreen, and keyboard navigation.",
    },
    {
        "tag": "Visuals",
        "title": "14 native shapes × 4 themes",
        "description": "2x2 matrices, funnels, value chains, donut charts, swim-lanes — all native primitives, none flat-PNG approximations.",
    },
    {
        "tag": "Workflow",
        "title": "Three-stage workflow",
        "description": "Storyboard → Wireframe → Hi-fi. Modeled on how designers actually work; cross-stage history keeps your edits.",
    },
    {
        "tag": "Canvas",
        "title": "Freeform text blocks",
        "description": "Lift any title, body, or caption out of layout flow. Drag, resize, and style every block independently — Framer-style.",
    },
    {
        "tag": "Typography",
        "title": "Per-block typography",
        "description": "Override font family, size, color, weight, and alignment on any text block. Renders identically in .pptx and on the web.",
    },
    {
        "tag": "Library",
        "title": "Curated asset library",
        "description": "Browse, preview, and drop in vetted shapes and diagrams. Selective text stripping so labels stay editable in your deck.",
    },
    {
        "tag": "Fonts",
        "title": "Comprehensive font system",
        "description": "Bring your own fonts; system maps them onto the right shapes and inline SVG with no manual fiddling.",
    },
    {
        "tag": "Templates",
        "title": "Custom .pptx templates",
        "description": "Register any company-master deck — slideAtelier extracts its layouts and respects them at render time.",
    },
    {
        "tag": "Critique",
        "title": "AI design critique",
        "description": "One click for a structured critique of any slide — find dense slides, weak hierarchies, mismatched themes, before they ship.",
    },
    {
        "tag": "Browser",
        "title": "Browser-native, no install",
        "description": "Generate, edit, publish — all in the browser. No desktop install, no plugin, no Office subscription required to author.",
    },
]


FAQS: list[dict[str, str]] = [
    {
        "question": "What's the pricing?",
        "answer": (
            "Free tier during launch: 5 generations a month and unlimited public web decks. "
            "Paid plans land soon for unlimited generation, custom branding, and team seats. "
            "Existing free-tier users keep their tier when paid lands."
        ),
    },
    {
        "question": "Who owns the decks I generate?",
        "answer": (
            "You do — full IP and ownership. We do not train models on your content. "
            "Your prompts pass through Anthropic's Claude API under their standard "
            "data-handling terms; no third-party sharing beyond that."
        ),
    },
    {
        "question": "Why .pptx instead of Google Slides?",
        "answer": (
            "Native .pptx is the universal interchange format — it opens in PowerPoint, Keynote, "
            "and Google Slides. Crucially, slideAtelier emits <em>native primitives</em>: every "
            "shape, chart, and text block remains independently editable in the destination tool. "
            "Google Slides export is on the roadmap, but .pptx-first means your output works "
            "everywhere on day one."
        ),
    },
    {
        "question": "Can I use my company's brand and template?",
        "answer": (
            "Yes. Upload any .pptx as a master template and slideAtelier inspects its layouts, "
            "fonts, and theme colours, then generates new decks against it. A dedicated Brand Kit "
            "(per-workspace logos, colours, type pair) is shipping next."
        ),
    },
    {
        "question": "How much does the AI cost on top?",
        "answer": (
            "Nothing on the free tier — generation cost is included. On paid tiers you'll see a "
            "transparent per-generation meter; tokens are billed at cost with no markup. "
            "Most decks generate end-to-end well under $1 in API spend."
        ),
    },
    {
        "question": "Can I edit a generated deck after the fact?",
        "answer": (
            "Yes — extensively. The whole product is built around editability. Re-generate any "
            "slide, drag any text block freeform, tweak typography per block, swap shapes, edit "
            "speaker notes, then re-export. Roundtrip from PowerPoint is also on the roadmap."
        ),
    },
    {
        "question": "Is my data private?",
        "answer": (
            "Yes. Decks are stored per-user; no public access unless you explicitly publish a "
            "deck (which generates a random unguessable slug). Account email + bcrypt-hashed "
            "password is all the personal data we hold. "
            "Email <a href='mailto:founder@slideatelier.com' class='text-amber-700 hover:underline'>founder@slideatelier.com</a> "
            "any time to delete your account and data."
        ),
    },
]


# ---------------------------------------------------------------------------
# Waitlist storage — coexists with auth tables in the same SQLite file.
# ---------------------------------------------------------------------------

def _waitlist_db_path(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "atelier.db"


def _ensure_waitlist_table(db_path: Path) -> None:
    """Idempotent — coordinates with the auth agent's CREATE TABLE IF NOT EXISTS."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS waitlist_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL DEFAULT 'landing',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _insert_waitlist_email(db_path: Path, email: str, source: str = "landing") -> bool:
    """Returns True on insert, False if email is already on the list."""
    _ensure_waitlist_table(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        try:
            conn.execute(
                "INSERT INTO waitlist_emails (email, source, created_at) VALUES (?, ?, ?)",
                (email, source, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


# Public test helper.
def list_waitlist_emails(output_dir: Path) -> list[str]:
    db_path = _waitlist_db_path(output_dir)
    _ensure_waitlist_table(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT email FROM waitlist_emails ORDER BY id ASC"
        ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# OG image — lazy-generated at import time.
# ---------------------------------------------------------------------------

def _ensure_og_image(static_dir: Path) -> None:
    target = static_dir / "og.png"
    if target.exists() and target.stat().st_size > 0:
        return
    try:
        from . import static as _static_pkg  # type: ignore  # noqa: F401
    except Exception:
        pass
    # Re-import the standalone generator module by path (it has no package).
    gen_path = static_dir / "og_generator.py"
    if not gen_path.exists():
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("_atelier_og_gen", gen_path)
    if spec is None or spec.loader is None:
        return
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        mod.write_png(mod.build_pixels(), target)
    except Exception:
        # Don't crash the app over a placeholder image.
        pass


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_landing_routes(app, templates, _config_callable, page_app_handler) -> None:
    """Register landing/waitlist/legal routes.

    `page_app_handler` is the existing /-page renderer that we re-mount at
    /app — passed in to keep this module decoupled from app.py internals.
    """

    @app.get("/", response_class=HTMLResponse)
    def page_landing(request: Request):
        # Redirect signed-in users to the app. We trust the cookie's mere
        # presence here; the auth middleware will fully validate it on /app.
        if request.cookies.get(_AUTH_COOKIE_NAME):
            return RedirectResponse(url="/app", status_code=302)

        canonical = str(request.url_for("page_landing")) if False else "/"
        # request.url gives us the actual incoming URL — strip query/path for
        # the OG canonical. Falls back to "/" in tests where url is empty.
        try:
            canonical = f"{request.url.scheme}://{request.url.netloc}/"
        except Exception:
            canonical = "/"

        return templates.TemplateResponse(
            request,
            "landing.html",
            {
                "headline": HEADLINE,
                "features": FEATURES,
                "faqs": FAQS,
                "year": _dt.datetime.now().year,
                "canonical_url": canonical,
            },
        )

    @app.get("/app", response_class=HTMLResponse)
    def page_app(request: Request):
        # Delegates to the existing generator-page handler. Keeps the legacy
        # behaviour for signed-in users without duplicating its template
        # context-building logic.
        return page_app_handler(request)

    @app.post("/api/waitlist", response_class=HTMLResponse)
    def post_waitlist(request: Request, email: str = Form("")):
        clean = (email or "").strip().lower()
        if not clean or not _EMAIL_RE.match(clean) or len(clean) > 254:
            return HTMLResponse(
                """
                <div id="waitlist-target" class="max-w-md mx-auto bg-red-50 border border-red-200 rounded-md p-4 text-red-800">
                  That doesn't look like a valid email. <button onclick="location.reload()" class="ml-2 underline">try again</button>
                </div>
                """.strip(),
                status_code=400,
            )

        config = _config_callable()
        try:
            db_path = _waitlist_db_path(config.output_dir)
            inserted = _insert_waitlist_email(db_path, clean, source="landing")
        except Exception:
            return HTMLResponse(
                """
                <div id="waitlist-target" class="max-w-md mx-auto bg-amber-50 border border-amber-200 rounded-md p-4 text-amber-800">
                  Something went wrong saving your email — please email founder@slideatelier.com directly.
                </div>
                """.strip(),
                status_code=500,
            )

        if inserted:
            msg = "Got it — we'll be in touch when major capabilities ship."
        else:
            msg = "You're already on the waitlist — thanks for the patience!"

        return HTMLResponse(
            f"""
            <div id="waitlist-target" class="max-w-md mx-auto bg-emerald-50 border border-emerald-200 rounded-md p-6 text-center">
              <div class="text-2xl mb-2">✓</div>
              <p class="font-semibold text-emerald-900">{msg}</p>
              <p class="text-sm text-emerald-700 mt-2">Or <a href="/app" class="underline font-semibold">sign up free now →</a></p>
            </div>
            """.strip(),
            status_code=200,
        )

    @app.get("/privacy", response_class=HTMLResponse)
    def page_privacy(request: Request):
        return templates.TemplateResponse(request, "legal/privacy.html", {})

    @app.get("/terms", response_class=HTMLResponse)
    def page_terms(request: Request):
        return templates.TemplateResponse(request, "legal/terms.html", {})
