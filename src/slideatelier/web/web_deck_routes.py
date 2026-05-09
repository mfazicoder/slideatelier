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

import json
import re
import secrets
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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


def _job_dir(config: Config, job_id: str) -> Path:
    # Defensively reject path traversal via job_id.
    if not re.match(r"^[A-Za-z0-9_-]+$", job_id or ""):
        raise HTTPException(400, "invalid job_id")
    return _workflow_root(config) / job_id


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


def _publish(job_id: str, config: Config) -> dict:
    """Idempotent-ish publish: reuses an existing slug if one is recorded for
    this job; otherwise mints a fresh one. Always re-renders the HTML so the
    public copy stays in sync with deck.json."""
    job_dir = _job_dir(config, job_id)
    if not job_dir.exists():
        raise HTTPException(404, "workflow not found")
    deck_path = job_dir / "deck.json"
    if not deck_path.exists():
        raise HTTPException(400, "deck not ready — finish wireframe first")

    try:
        deck = SlideDeck.model_validate_json(deck_path.read_text())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"deck.json invalid: {type(e).__name__}: {e}") from e

    # Slug: reuse if a previous publish recorded one and the index still maps it.
    slug_path = job_dir / "web_slug.txt"
    index = _read_slug_index(config)
    slug = ""
    if slug_path.exists():
        candidate = slug_path.read_text().strip()
        if candidate and index.get(candidate) == job_id:
            slug = candidate
    if not slug:
        slug = _generate_slug(index)
        slug_path.write_text(slug)
        index[slug] = job_id
        _write_slug_index(config, index)

    tpl = _load_template_for_job(job_dir, config)
    renderer = WebRenderer(tpl)
    html = renderer.render_deck_html(deck, slug=slug)
    (job_dir / "web_deck.html").write_text(html)

    return {
        "url": f"/web/{slug}",
        "slug": slug,
        "slide_count": len(deck.slides),
    }


def register_web_deck_routes(app, templates, _config_callable):
    """Register publish + public viewer routes on the FastAPI app."""

    @app.post("/api/jobs/{job_id}/publish")
    def api_publish(job_id: str):
        config = _config_callable()
        result = _publish(job_id, config)
        return JSONResponse(result)

    @app.get("/api/jobs/{job_id}/web-deck-url")
    def api_web_deck_url(job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
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

    @app.get("/web/{slug}", response_class=HTMLResponse)
    def public_view(slug: str):
        config = _config_callable()
        # CRITICAL: only resolve via the index. Never let user input touch the FS path.
        if not _SLUG_RE.match(slug or ""):
            raise HTTPException(404, "not found")
        index = _read_slug_index(config)
        job_id = index.get(slug)
        if not job_id:
            raise HTTPException(404, "not found")
        html_path = _job_dir(config, job_id) / "web_deck.html"
        if not html_path.exists():
            raise HTTPException(404, "not found")
        return HTMLResponse(html_path.read_text())

    @app.get("/web/{slug}/slide/{idx}")
    def public_view_slide(slug: str, idx: int):
        # Redirect into the main viewer with a fragment so the inline JS
        # scrolls to the right slide. Keeps everything in one HTML doc.
        if not _SLUG_RE.match(slug or "") or idx < 0:
            raise HTTPException(404, "not found")
        config = _config_callable()
        index = _read_slug_index(config)
        if slug not in index:
            raise HTTPException(404, "not found")
        return RedirectResponse(url=f"/web/{slug}#slide-{idx}", status_code=302)
