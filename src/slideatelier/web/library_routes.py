"""Library UX upgrade routes (Sprint B):

- GET /library/expand/<asset_id>   — modal fragment with larger preview, metadata,
                                     style_tags, and select-and-attach form
- GET /api/library/tags            — JSON list of all unique style_tags + counts
- GET /library/filter              — HTMX fragment re-rendering the asset grid
                                     filtered by ?category=X&tags=foo,bar (AND)

Registered from app.py via `register_library_routes(app, templates, _config_callable)`.
The library catalog itself is loaded lazily on each call so freshly-scanned assets
(or hot-reloaded style_tags from a sibling agent's work) are always reflected.
"""
from __future__ import annotations

import json
import urllib.parse
from collections import Counter
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..library import LibraryAsset, load_catalog

LIBRARY_CATALOG = Path("./library/catalog.json")


# ---------------------------------------------------------------------------
# Tag color-coding — mirrored in the partial. Kept here so /library/expand can
# pass a resolved color into the template too.
# ---------------------------------------------------------------------------

TAG_COLORS: dict[str, str] = {
    "consulting": "stone",
    "corporate": "stone",
    "structured": "stone",
    "scaffold": "slate",
    "dark-mode": "slate",
    "technical": "slate",
    "minimal": "amber",
    "artistic": "amber",
    "editorial": "amber",
    "creative": "violet",
    "contemporary": "violet",
    "vibrant": "violet",
    "modern": "violet",
    "native": "amber",
    "framework": "stone",
    "process": "blue",
    "comparison": "blue",
    "marketing": "rose",
    "graphics-heavy": "rose",
}


def tag_color(tag: str) -> str:
    """Resolve a style_tag string to a Tailwind color name. Falls back to stone.

    Match strategy: exact match first, then any sub-token from a hyphen split
    (so e.g. ``native-matrix-2x2`` resolves on ``native``)."""
    if not tag:
        return "stone"
    t = tag.lower()
    if t in TAG_COLORS:
        return TAG_COLORS[t]
    for part in t.split("-"):
        if part in TAG_COLORS:
            return TAG_COLORS[part]
    return "stone"


# ---------------------------------------------------------------------------
# Catalog access helpers
# ---------------------------------------------------------------------------

def _load_catalog_safe():
    if not LIBRARY_CATALOG.exists():
        return None
    try:
        return load_catalog(LIBRARY_CATALOG)
    except Exception:  # noqa: BLE001
        return None


def _asset_tags(a: LibraryAsset) -> list[str]:
    """Read style_tags safely — a sibling agent is concurrently adding the
    field to LibraryAsset, so we tolerate its absence."""
    return list(getattr(a, "style_tags", []) or [])


def _filter_assets(
    assets: list[LibraryAsset],
    *,
    category: str | None,
    tags: list[str],
) -> list[LibraryAsset]:
    out = list(assets)
    if category:
        cat_l = category.lower()
        out = [a for a in out if cat_l in a.category.lower()]
    if tags:
        wanted = {t.lower() for t in tags if t.strip()}
        if wanted:
            def _matches(a: LibraryAsset) -> bool:
                have = {t.lower() for t in _asset_tags(a)}
                return wanted.issubset(have)
            out = [a for a in out if _matches(a)]
    return out


def _parse_tags_param(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Workflow picker — scan output/workflow/* for decks
# ---------------------------------------------------------------------------

def _list_workflows_with_decks(output_dir: Path) -> list[dict]:
    """Return [{job_id, title, slide_count}, ...] for workflows that have a deck.json.
    Sorted by deck.json mtime desc."""
    base = output_dir / "workflow"
    if not base.exists():
        return []
    rows: list[tuple[float, dict]] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        deck_path = child / "deck.json"
        if not deck_path.exists():
            continue
        try:
            data = json.loads(deck_path.read_text())
        except Exception:  # noqa: BLE001
            continue
        title = (data.get("title") or "").strip() or child.name
        slides = data.get("slides") or []
        slide_count = len(slides) if isinstance(slides, list) else 0
        rows.append((deck_path.stat().st_mtime, {
            "job_id": child.name,
            "title": title,
            "slide_count": slide_count,
        }))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in rows]


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_library_routes(app, templates, _config_callable):
    """Wire library UX upgrade routes onto the FastAPI app."""

    # Expose tag_color() into Jinja so partials can resolve colors per chip.
    templates.env.filters.setdefault("tag_color", tag_color)

    # ----- /api/library/tags -----
    @app.get("/api/library/tags")
    def api_library_tags():
        catalog = _load_catalog_safe()
        if catalog is None:
            return JSONResponse({"scanned": False, "tags": []})
        counter: Counter[str] = Counter()
        for a in catalog.assets:
            for t in _asset_tags(a):
                if t:
                    counter[t] += 1
        tags_sorted = [
            {"tag": t, "count": c, "color": tag_color(t)}
            for t, c in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        return JSONResponse({"scanned": True, "tags": tags_sorted})

    # ----- /library/filter -----
    @app.get("/library/filter", response_class=HTMLResponse)
    def library_filter(
        request: Request,
        category: str | None = None,
        tags: str | None = None,
    ):
        catalog = _load_catalog_safe()
        if catalog is None:
            return HTMLResponse(
                '<div class="p-6 text-center text-stone-500 text-sm">Library not scanned.</div>'
            )
        tag_list = _parse_tags_param(tags)
        assets = _filter_assets(catalog.assets, category=category, tags=tag_list)
        return templates.TemplateResponse(
            request,
            "_library_grid.html",
            {
                "assets": assets,
                "active_tags": tag_list,
                "selected_category": category,
            },
        )

    # ----- /library/expand/<asset_id_encoded> -----
    @app.get("/library/expand/{asset_id_encoded:path}", response_class=HTMLResponse)
    def library_expand(request: Request, asset_id_encoded: str):
        # Path converter does not URL-decode percent-escaped slashes that have
        # already been decoded by Starlette, but query-encoded IDs may still
        # arrive percent-encoded. Decode defensively.
        asset_id = urllib.parse.unquote(asset_id_encoded)

        catalog = _load_catalog_safe()
        if catalog is None:
            raise HTTPException(404, "Library not scanned")
        asset = catalog.find(asset_id)
        if asset is None:
            raise HTTPException(404, f"Asset not found: {asset_id}")

        config = _config_callable()
        workflows = _list_workflows_with_decks(config.output_dir)

        # For each workflow, pre-pull its slide titles so the slide-picker
        # dropdown can show "1. <title>" entries without a second request.
        workflow_slides: dict[str, list[dict]] = {}
        for wf in workflows:
            deck_path = config.output_dir / "workflow" / wf["job_id"] / "deck.json"
            try:
                data = json.loads(deck_path.read_text())
            except Exception:  # noqa: BLE001
                workflow_slides[wf["job_id"]] = []
                continue
            slides = data.get("slides") or []
            workflow_slides[wf["job_id"]] = [
                {
                    "idx": i,
                    "title": (s.get("title") or "").strip() or f"Slide {i + 1}",
                    "layout": s.get("layout") or "content",
                }
                for i, s in enumerate(slides)
            ]

        return templates.TemplateResponse(
            request,
            "_library_expand_modal.html",
            {
                "asset": asset,
                "asset_tags": _asset_tags(asset),
                "workflows": workflows,
                "workflow_slides": workflow_slides,
                "asset_id_url": urllib.parse.quote(asset.id, safe=""),
            },
        )
