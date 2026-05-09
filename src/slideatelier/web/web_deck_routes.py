"""Web Deck publishing routes (Sprint J.D).

Endpoints:
  POST /api/jobs/{job_id}/publish      - render & publish, returns {url, slug}
  GET  /web/{slug}                     - public viewer
  GET  /web/{slug}/slide/{idx}         - viewer scrolled to slide #idx
  GET  /api/jobs/{job_id}/web-deck-url - lookup published URL for a job

Storage:
  output/workflow/<job_id>/web_slug.txt  (slug for this job)
  output/workflow/<job_id>/web_deck.html (rendered viewer)
  output/web_slugs.json                  ({slug: job_id} index)

Single-tenant assumption: a single shared web_slugs.json under output_dir is
fine for the current installer footprint. When we go multi-tenant the index
moves into per-tenant storage.
"""
from __future__ import annotations

import html as _html
import json
import re
import secrets
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..auth import get_current_user, resolve_job_dir, user_owns_job
from ..auth.db import SYSTEM_USER_ID, get_db
from ..config import Config
from ..models import SlideDeck
from ..template import load_default_template, load_template
from ..web_renderer import WebRenderer

# URL-safe alphabet for slug generation. Avoids look-alike chars (0/O/1/l).
_SLUG_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
_SLUG_LEN = 8
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{4,32}$")


def _workflow_root(config: Config) -> Path:
    return config.output_dir / "workflow"


def _build_og_meta_block(request: "Request", slug: str, job_dir: Path) -> str:
    """Read the deck title from deck.json and emit an OG/Twitter meta block.

    Inherited as og:title so a shared URL like https://slideatelier.com/web/<slug>
    previews with the deck's own title in Twitter/Facebook/LinkedIn cards.
    Falls back silently to a generic title if deck.json is unreadable.
    """
    title = "slideAtelier deck"
    try:
        deck_json = job_dir / "deck.json"
        if deck_json.exists():
            data = json.loads(deck_json.read_text())
            t = (data or {}).get("title")
            if isinstance(t, str) and t.strip():
                title = t.strip()[:120]
    except Exception:
        pass
    title_e = _html.escape(title)
    desc_e = _html.escape(f"{title} — published with slideAtelier")
    try:
        canonical = f"{request.url.scheme}://{request.url.netloc}/web/{slug}"
    except Exception:
        canonical = f"/web/{slug}"
    canonical_e = _html.escape(canonical)
    return (
        f'  <meta property="og:type" content="article">\n'
        f'  <meta property="og:title" content="{title_e}">\n'
        f'  <meta property="og:description" content="{desc_e}">\n'
        f'  <meta property="og:url" content="{canonical_e}">\n'
        f'  <meta property="og:image" content="/static/og.png">\n'
        f'  <meta property="og:site_name" content="slideAtelier">\n'
        f'  <meta name="twitter:card" content="summary_large_image">\n'
        f'  <meta name="twitter:title" content="{title_e}">\n'
        f'  <meta name="twitter:description" content="{desc_e}">\n'
        f'  <meta name="twitter:image" content="/static/og.png">'
    )


def _job_dir(config: Config, job_id: str, request: Request | None = None) -> Path:
    # Defensively reject path traversal via job_id.
    if not re.match(r"^[A-Za-z0-9_-]+$", job_id or ""):
        raise HTTPException(400, "invalid job_id")
    return resolve_job_dir(config, request, job_id)


def _slug_index_path(config: Config) -> Path:
    return config.output_dir / "web_slugs.json"


def _read_slug_index(config: Config) -> dict[str, str]:
    p = _slug_index_path(config)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            return {}
        # Coerce values to str defensively.
        return {str(k): str(v) for k, v in data.items()}
    except Exception:  # noqa: BLE001
        return {}


def _write_slug_index(config: Config, idx: dict[str, str]) -> None:
    p = _slug_index_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(idx, indent=2, sort_keys=True))


def _generate_slug(existing: dict[str, str]) -> str:
    """Generate a URL-safe random slug not already present in the index."""
    for _ in range(20):
        s = "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LEN))
        if s not in existing:
            return s
    # Vanishingly unlikely; bump length to break the deadlock.
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LEN + 4))


def _load_template_for_job(job_dir: Path, config: Config):
    template_name = ""
    name_path = job_dir / "template_name.txt"
    if name_path.exists():
        template_name = name_path.read_text().strip()
    try:
        if template_name and template_name != "default":
            return load_template(config.templates_dir / f"{template_name}.json")
    except Exception:  # noqa: BLE001
        pass
    return load_default_template(config.templates_dir)


def _publish(job_id: str, config: Config, request: Request | None = None) -> dict:
    """Idempotent-ish publish: reuses an existing slug if one is recorded for
    this job; otherwise mints a fresh one. Always re-renders the HTML so the
    public copy stays in sync with deck.json."""
    job_dir = _job_dir(config, job_id, request)
    if not job_dir.exists():
        raise HTTPException(404, "workflow not found")
    deck_path = job_dir / "deck.json"
    if not deck_path.exists():
        raise HTTPException(400, "deck not ready — finish wireframe first")

    try:
        deck = SlideDeck.model_validate_json(deck_path.read_text())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"deck.json invalid: {type(e).__name__}: {e}") from e

    # Slug: reuse if a previous publish recorded one and SQL still maps it.
    db = get_db(config.output_dir)
    db.record_deck(  # ensure a row exists; INSERT OR IGNORE
        job_id,
        get_current_user(request).id if get_current_user(request) is not None else SYSTEM_USER_ID,
    )
    deck_row = db.get_deck(job_id)
    slug_path = job_dir / "web_slug.txt"
    legacy_index = _read_slug_index(config)
    slug = ""
    if deck_row is not None and deck_row.slug:
        slug = deck_row.slug
    elif slug_path.exists():
        candidate = slug_path.read_text().strip()
        # Migrate legacy index → SQL on first publish post-deploy.
        if candidate and (
            legacy_index.get(candidate) == job_id
            or db.get_deck_by_slug(candidate) is None
        ):
            slug = candidate
    if not slug:
        # Build a "taken" set spanning both stores so the new slug is unique
        # under either lookup path.
        taken = dict(legacy_index)
        for d in db.list_decks_for_user(SYSTEM_USER_ID):
            if d.slug:
                taken[d.slug] = d.job_id
        slug = _generate_slug(taken)
    slug_path.write_text(slug)
    try:
        db.set_deck_slug(job_id, slug)
    except Exception:  # noqa: BLE001
        # Slug uniqueness conflict: pick a fresh one.
        slug = _generate_slug({**legacy_index, slug: job_id})
        slug_path.write_text(slug)
        db.set_deck_slug(job_id, slug)
    # Keep the legacy JSON in sync as a fallback for older code paths.
    legacy_index[slug] = job_id
    _write_slug_index(config, legacy_index)

    tpl = _load_template_for_job(job_dir, config)
    # Load the library catalog so the WebRenderer can resolve library_asset
    # extras to their source .pptx and emit live SVG (Sprint Z.v2). When the
    # catalog isn't available we silently fall back to thumbnail <img>.
    catalog = None
    try:
        from ..library import load_catalog
        from pathlib import Path as _Path
        cat_path = _Path("./library/catalog.json")
        if cat_path.exists():
            catalog = load_catalog(cat_path)
    except Exception:  # noqa: BLE001
        catalog = None
    # Sprint K: load the workspace BrandKit so the published Web Deck picks
    # up brand-token overrides at render time. Anonymous (no user) falls
    # through to the global kit; nothing here is fatal — a missing kit
    # silently leaves theme defaults in place.
    brand_kit = None
    try:
        from ..brand_kit import load_brand_kit
        user = get_current_user(request) if request is not None else None
        user_id = user.id if user is not None else None
        brand_kit = load_brand_kit(config.output_dir, user_id)
    except Exception:  # noqa: BLE001 — kit is optional; never block publish on it
        brand_kit = None
    renderer = WebRenderer(tpl, catalog=catalog, brand_kit=brand_kit)
    # Sprint Q: respect per-deck analytics opt-out.
    analytics_enabled = True
    try:
        import json as _json
        s_path = job_dir / "analytics_settings.json"
        if s_path.exists():
            settings = _json.loads(s_path.read_text())
            analytics_enabled = bool(settings.get("analytics_enabled", True))
    except Exception:  # noqa: BLE001
        analytics_enabled = True
    html = renderer.render_deck_html(
        deck, slug=slug, deck_id=job_id, analytics_enabled=analytics_enabled,
    )
    (job_dir / "web_deck.html").write_text(html)

    return {
        "url": f"/web/{slug}",
        "slug": slug,
        "slide_count": len(deck.slides),
    }


def register_web_deck_routes(app, templates, _config_callable):
    """Register publish + public viewer routes on the FastAPI app."""

    @app.post("/api/jobs/{job_id}/publish")
    def api_publish(request: Request, job_id: str):
        config = _config_callable()
        # Ownership check: the publish endpoint is the only edit-style action on
        # a deck that needs to ALSO work for legacy/anonymous users in dev.
        # `user_owns_job` returns True for anonymous (legacy flat layout).
        if not user_owns_job(config.output_dir, get_current_user(request), job_id):
            raise HTTPException(403, "you do not own this deck")

        # Sprint V — optional publish gate. Off by default so the launch isn't
        # blocked by linter false-positives. Set SLIDEATELIER_AUDIT_GATE=1 to
        # enforce: any error-severity audit issue blocks publish with HTTP 400.
        import os as _os
        if _os.environ.get("SLIDEATELIER_AUDIT_GATE", "").strip() in ("1", "true", "yes"):
            from ..audit import audit_deck
            job_dir = _job_dir(config, job_id, request)
            deck_path = job_dir / "deck.json"
            if deck_path.exists():
                try:
                    deck_for_audit = SlideDeck.model_validate_json(deck_path.read_text())
                    tpl_for_audit = _load_template_for_job(job_dir, config)
                    blocking = [
                        i for i in audit_deck(deck_for_audit, tpl_for_audit)
                        if i.severity == "error"
                    ]
                    if blocking:
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "error": "audit_gate_blocked",
                                "message": (
                                    f"{len(blocking)} error-severity audit issue(s); "
                                    "fix them or unset SLIDEATELIER_AUDIT_GATE."
                                ),
                                "issues": [i.model_dump() for i in blocking],
                            },
                        )
                except HTTPException:
                    raise
                except Exception:  # noqa: BLE001 — never block on auditor crash
                    pass

        result = _publish(job_id, config, request)
        return JSONResponse(result)

    @app.get("/api/jobs/{job_id}/web-deck-url")
    def api_web_deck_url(request: Request, job_id: str):
        config = _config_callable()
        # Prefer SQL-recorded slug; fall back to file/legacy.
        db = get_db(config.output_dir)
        deck = db.get_deck(job_id)
        if deck is not None and deck.slug:
            return {"url": f"/web/{deck.slug}", "slug": deck.slug}
        job_dir = _job_dir(config, job_id, request)
        slug_path = job_dir / "web_slug.txt"
        if not slug_path.exists():
            raise HTTPException(404, "not published")
        slug = slug_path.read_text().strip()
        if not slug:
            raise HTTPException(404, "not published")
        index = _read_slug_index(config)
        if index.get(slug) != job_id:
            raise HTTPException(404, "not published")
        return {"url": f"/web/{slug}", "slug": slug}

    def _resolve_slug_to_job_dir(config: Config, slug: str) -> Path | None:
        """Resolve a public slug → on-disk job dir, prioritising SQLite over
        the legacy shared JSON. Returns None if no mapping exists."""
        db = get_db(config.output_dir)
        deck = db.get_deck_by_slug(slug)
        if deck is not None:
            owner = db.get_user(deck.owner_user_id)
            if owner is not None and owner.id != SYSTEM_USER_ID:
                from ..auth import user_workflow_root
                return user_workflow_root(config.output_dir, owner) / deck.job_id
            return config.output_dir / "workflow" / deck.job_id
        # Fallback to legacy index for decks created pre-migration.
        index = _read_slug_index(config)
        job_id = index.get(slug)
        if not job_id:
            return None
        return config.output_dir / "workflow" / job_id

    @app.get("/web/{slug}", response_class=HTMLResponse)
    def public_view(request: Request, slug: str):
        config = _config_callable()
        # CRITICAL: only resolve via the index. Never let user input touch the FS path.
        if not _SLUG_RE.match(slug or ""):
            raise HTTPException(404, "not found")
        job_dir = _resolve_slug_to_job_dir(config, slug)
        if job_dir is None:
            raise HTTPException(404, "not found")
        html_path = job_dir / "web_deck.html"
        if not html_path.exists():
            raise HTTPException(404, "not found")
        body = html_path.read_text()

        # Inject OG / Twitter meta so shared URLs preview correctly. We read
        # the deck title from deck.json (cheap; few KB) and slot it into the
        # already-rendered <head>. This avoids touching web_renderer.py.
        og_meta = _build_og_meta_block(request, slug, job_dir)
        if og_meta and "</head>" in body:
            body = body.replace("</head>", og_meta + "\n</head>", 1)
        return HTMLResponse(body)

    @app.get("/web/{slug}/slide/{idx}")
    def public_view_slide(slug: str, idx: int):
        # Redirect into the main viewer with a fragment so the inline JS
        # scrolls to the right slide. Keeps everything in one HTML doc.
        if not _SLUG_RE.match(slug or "") or idx < 0:
            raise HTTPException(404, "not found")
        config = _config_callable()
        if _resolve_slug_to_job_dir(config, slug) is None:
            raise HTTPException(404, "not found")
        return RedirectResponse(url=f"/web/{slug}#slide-{idx}", status_code=302)
