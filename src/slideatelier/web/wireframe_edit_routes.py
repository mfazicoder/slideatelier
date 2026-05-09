"""Editing endpoints for the wireframe stage of the three-stage workflow.

Pure CRUD on <output_dir>/workflow/<job_id>/deck.json — no LLM calls.
The integration session wires register_wireframe_edit_routes(app, templates, _config)
into app.py and includes _slide_edit_card.html from wireframe.html.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from ..config import Config

LAYOUT_TYPES: list[str] = [
    "title",
    "section_divider",
    "content",
    "two_column",
    "bullet_list",
    "key_takeaway",
    "closing",
]


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _job_dir(config: Config, job_id: str, request=None) -> Path:
    """Auth-aware: returns the per-user namespace if SQLite says the job is
    owned by an authenticated user, else falls back to the legacy flat layout."""
    from ..auth import resolve_job_dir
    return resolve_job_dir(config, request, job_id)


def _deck_path(config: Config, job_id: str) -> Path:
    return _job_dir(config, job_id) / "deck.json"


def _load_deck(config: Config, job_id: str) -> dict:
    path = _deck_path(config, job_id)
    if not path.exists():
        raise HTTPException(404, "deck.json not found for this workflow")
    try:
        return json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"deck.json unreadable: {e}") from e


def _save_deck(config: Config, job_id: str, deck: dict) -> None:
    """Save the deck and snapshot the just-written state into history/ so the
    user can step through their edits via undo/redo. The head of history
    always equals the current deck.json content."""
    from .workflow_history import snapshot
    path = _deck_path(config, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(deck, indent=2))
    snapshot(path.parent, "deck")


def _empty_slide() -> dict:
    return {
        "layout": "content",
        "title": "",
        "strap": "",
        "body": [],
        "body_left": [],
        "body_right": [],
        "speaker_notes": "",
        "rationale": "",
        "asset_ref": None,
        "extras": [],
        "block_bbox": {},
        "block_style": {},
    }


def _split_bullets(raw: str) -> list[str]:
    """Split a textarea value on newlines, strip whitespace, drop empty lines."""
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _redistribute_for_layout_change(
    old_layout: str,
    new_layout: str,
    body: list[str],
    body_left: list[str],
    body_right: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Re-home body content into the new layout's visible slots.

    Layout shapes (which fields each layout RENDERS as visible textareas):
      - title / section_divider:  title + strap        (body hidden)
      - key_takeaway:             title + strap + body
      - content / bullet_list /
        closing:                  title + strap + body
      - two_column:               title + strap + body_left + body_right

    The wireframe template already preserves cross-layout fields via hidden
    inputs, so data is never lost on layout change. But the user perceives
    loss when their content is in a slot the new layout doesn't show. This
    helper moves content into a visible slot WHEN the destination is empty,
    so existing data is never overwritten.

    Rules:
      - Going TO `two_column` from a body-based layout: split `body` ~50/50
        into `body_left` and `body_right` if both columns are empty.
      - Going FROM `two_column` to a body-based layout: merge
        `body_left` + `body_right` into `body` if `body` is empty.
      - All other transitions: leave content where it is (visible if the new
        layout shows that field, or preserved-but-hidden — the template
        surfaces a "hidden content" badge so the user knows it's still there).
    """
    if old_layout == new_layout:
        return body, body_left, body_right

    BODY_LAYOUTS = {"content", "bullet_list", "closing", "key_takeaway"}

    # Split single-column body into two columns when going to two_column.
    if new_layout == "two_column" and old_layout in BODY_LAYOUTS:
        if body and not body_left and not body_right:
            mid = (len(body) + 1) // 2  # left gets the larger half if odd
            return [], list(body[:mid]), list(body[mid:])

    # Merge two columns back into body when leaving two_column.
    if old_layout == "two_column" and new_layout in BODY_LAYOUTS:
        if not body and (body_left or body_right):
            return list(body_left) + list(body_right), [], []

    return body, body_left, body_right


def _normalize_slide(slide: dict) -> dict:
    """Ensure all expected fields are present so the partial can render."""
    base = _empty_slide()
    base.update(slide or {})
    # Coerce list-y fields in case JSON has nulls
    for key in ("body", "body_left", "body_right"):
        if not isinstance(base.get(key), list):
            base[key] = []
    if base.get("layout") not in LAYOUT_TYPES:
        base["layout"] = "content"
    return base


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_wireframe_edit_routes(app, templates, _config_callable):
    """Adds editing endpoints for the wireframe stage of the workflow."""

    def _render_card(request: Request, slide_idx: int, slide: dict, *, saved: bool = False) -> HTMLResponse:
        config = _config_callable()
        # Resolve current template for the workflow so re-rendered cards keep the
        # WYSIWYG styling (fonts/colors) consistent with the page that opened.
        tpl = None
        try:
            from ..template import load_default_template, load_template
            job_id = request.path_params.get("job_id") or ""
            template_name_path = config.output_dir / "workflow" / job_id / "template_name.txt"
            tname = template_name_path.read_text().strip() if template_name_path.exists() else ""
            if tname and tname != "default":
                tpl = load_template(config.templates_dir / f"{tname}.json")
            else:
                tpl = load_default_template(config.templates_dir)
        except Exception:  # noqa: BLE001
            tpl = None
        resp = templates.TemplateResponse(
            request,
            "workflow/_slide_edit_card.html",
            {
                "job_id": request.path_params.get("job_id"),
                "slide_idx": slide_idx,
                "slide": _normalize_slide(slide),
                "layout_types": LAYOUT_TYPES,
                "saved": saved,
                "tpl": tpl,
            },
        )
        # Notify the page-level save-status indicator (and any other listeners)
        # that the deck just changed. HTMX dispatches a `deck-saved` event.
        resp.headers["HX-Trigger"] = "deck-saved"
        return resp

    def _require_slide(deck: dict, idx: int) -> dict:
        slides = deck.get("slides", [])
        if idx < 0 or idx >= len(slides):
            # idx is 0-based; surface a 1-based "slide #N" plus the deck size
            # so the user can see whether the deck shrank under them.
            raise HTTPException(
                404,
                f"slide #{idx + 1} doesn't exist (deck has {len(slides)} slide{'s' if len(slides) != 1 else ''}). "
                "Reload the page.",
            )
        return slides[idx]

    # ---- undo / redo ----

    @app.post("/workflow/wireframe/{job_id}/undo", response_class=HTMLResponse)
    def undo_deck(request: Request, job_id: str):
        from .workflow_history import undo, can_undo, can_redo
        config = _config_callable()
        job_dir = config.output_dir / "workflow" / job_id
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        ok = undo(job_dir, "deck")
        if not ok:
            return HTMLResponse(
                _render_history_status(job_dir, "Nothing to undo."),
                headers={"HX-Reswap": "outerHTML"},
            )
        # Reload the page so all slide cards re-render from the restored deck
        return HTMLResponse("ok", headers={"HX-Refresh": "true"})

    @app.post("/workflow/wireframe/{job_id}/redo", response_class=HTMLResponse)
    def redo_deck(request: Request, job_id: str):
        from .workflow_history import redo
        config = _config_callable()
        job_dir = config.output_dir / "workflow" / job_id
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        ok = redo(job_dir, "deck")
        if not ok:
            return HTMLResponse(
                _render_history_status(job_dir, "Nothing to redo."),
            )
        return HTMLResponse("ok", headers={"HX-Refresh": "true"})

    @app.get("/workflow/wireframe/{job_id}/history-status", response_class=HTMLResponse)
    def history_status(request: Request, job_id: str):
        config = _config_callable()
        job_dir = config.output_dir / "workflow" / job_id
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        return HTMLResponse(_render_history_status(job_dir))

    # ---- named version cards (Sprint N): rail, restore-and-fork, compare, rename ----

    def _annotate_relative(snaps: list[dict]) -> list[dict]:
        """Add a relative-time string for display in the rail."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).timestamp()
        for s in snaps:
            secs = max(0, now - s["epoch"]) if s.get("epoch") else 0
            if not s.get("epoch"):
                s["relative"] = ""
            elif secs < 5:
                s["relative"] = "just now"
            elif secs < 60:
                s["relative"] = f"{int(secs)}s ago"
            elif secs < 3600:
                s["relative"] = f"{int(secs / 60)}m ago"
            elif secs < 86400:
                s["relative"] = f"{int(secs / 3600)}h ago"
            else:
                s["relative"] = f"{int(secs / 86400)}d ago"
        return snaps

    @app.get("/workflow/wireframe/{job_id}/version-cards", response_class=HTMLResponse)
    def version_cards_rail(request: Request, job_id: str):
        """HTMX-loaded rail listing every snapshot as a Named Version Card.

        Cards sharing parent_timestamp group with a subtle vertical-line indent
        (rendered by the template via the `parent_timestamp` field).
        """
        from .workflow_history import list_snapshots
        config = _config_callable()
        job_dir = config.output_dir / "workflow" / job_id
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        snaps = _annotate_relative(list_snapshots(job_dir))
        return templates.TemplateResponse(
            request,
            "workflow/_version_cards_rail.html",
            {"job_id": job_id, "snapshots": snaps},
        )

    @app.post("/workflow/wireframe/{job_id}/version-cards/label", response_class=HTMLResponse)
    async def version_cards_rename(request: Request, job_id: str):
        """Rename a card. Form fields: kind, timestamp, label."""
        from .workflow_history import rename_snapshot
        config = _config_callable()
        job_dir = config.output_dir / "workflow" / job_id
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        form = await request.form()
        kind = (form.get("kind") or "").strip()
        timestamp = (form.get("timestamp") or "").strip()
        label = (form.get("label") or "").strip()
        if kind not in ("deck", "storyboard"):
            raise HTTPException(400, f"unknown kind: {kind}")
        if not timestamp:
            raise HTTPException(400, "missing timestamp")
        ok = rename_snapshot(job_dir, kind, timestamp, label)  # type: ignore[arg-type]
        if not ok:
            return HTMLResponse("snapshot not found", status_code=404)
        # Re-render the rail so the user sees the new label immediately
        from .workflow_history import list_snapshots
        snaps = _annotate_relative(list_snapshots(job_dir))
        return templates.TemplateResponse(
            request,
            "workflow/_version_cards_rail.html",
            {"job_id": job_id, "snapshots": snaps},
        )

    @app.post(
        "/workflow/wireframe/{job_id}/version-cards/restore/{timestamp}",
        response_class=HTMLResponse,
    )
    async def version_cards_restore(request: Request, job_id: str, timestamp: str):
        """Restore-and-fork: write the named snapshot's content back to the
        live artifact, then take a fresh snapshot with parent_timestamp pointing
        to the restored card. Form field `kind` selects deck (default) or
        storyboard."""
        from .workflow_history import restore_snapshot
        config = _config_callable()
        job_dir = config.output_dir / "workflow" / job_id
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        # Accept kind from form OR query param; default to deck for the wireframe rail.
        kind = request.query_params.get("kind", "")
        if not kind:
            try:
                form = await request.form()
                kind = (form.get("kind") or "").strip()
            except Exception:  # noqa: BLE001
                pass
        if not kind:
            kind = "deck"
        if kind not in ("deck", "storyboard"):
            raise HTTPException(400, f"unknown kind: {kind}")
        ok = restore_snapshot(job_dir, kind, timestamp)  # type: ignore[arg-type]
        if not ok:
            return HTMLResponse("snapshot not found", status_code=404)
        return HTMLResponse("ok", headers={"HX-Refresh": "true"})

    @app.get("/workflow/wireframe/{job_id}/compare", response_class=HTMLResponse)
    def version_cards_compare(request: Request, job_id: str, a: str = "", b: str = "", kind: str = "deck"):
        """Side-by-side compare of two snapshots, rendered as two iframes."""
        from .workflow_history import list_snapshots, read_snapshot_content
        config = _config_callable()
        job_dir = config.output_dir / "workflow" / job_id
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        if kind not in ("deck", "storyboard"):
            raise HTTPException(400, f"unknown kind: {kind}")
        if not a or not b:
            raise HTTPException(400, "missing a or b timestamp")
        content_a = read_snapshot_content(job_dir, kind, a)  # type: ignore[arg-type]
        content_b = read_snapshot_content(job_dir, kind, b)  # type: ignore[arg-type]
        if content_a is None or content_b is None:
            raise HTTPException(404, "one or both snapshots not found")
        # Build label lookup so each pane shows its card name.
        snaps = list_snapshots(job_dir)
        labels = {s["timestamp"]: s["label"] for s in snaps if s["kind"] == kind}
        return templates.TemplateResponse(
            request,
            "workflow/_version_cards_compare.html",
            {
                "job_id": job_id,
                "kind": kind,
                "a_ts": a,
                "b_ts": b,
                "a_label": labels.get(a, a),
                "b_label": labels.get(b, b),
                "content_a": content_a,
                "content_b": content_b,
            },
        )

    @app.get(
        "/workflow/wireframe/{job_id}/version-cards/snapshot/{timestamp}",
        response_class=HTMLResponse,
    )
    def version_cards_snapshot_view(request: Request, job_id: str, timestamp: str, kind: str = "deck"):
        """Render a single snapshot's content as a read-only HTML pane.
        Used by the compare page's iframes."""
        from .workflow_history import read_snapshot_content
        config = _config_callable()
        job_dir = config.output_dir / "workflow" / job_id
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        if kind not in ("deck", "storyboard"):
            raise HTTPException(400, f"unknown kind: {kind}")
        content = read_snapshot_content(job_dir, kind, timestamp)  # type: ignore[arg-type]
        if content is None:
            raise HTTPException(404, "snapshot not found")
        return templates.TemplateResponse(
            request,
            "workflow/_version_cards_snapshot_view.html",
            {
                "job_id": job_id,
                "kind": kind,
                "timestamp": timestamp,
                "content": content,
            },
        )

    def _render_history_status(job_dir, message: str = "All changes saved") -> str:
        from .workflow_history import can_undo, can_redo
        from datetime import datetime, timezone
        deck_path = job_dir / "deck.json"
        ts_label = ""
        if deck_path.exists():
            mtime = deck_path.stat().st_mtime
            secs = (datetime.now(timezone.utc).timestamp() - mtime)
            if secs < 5:
                ts_label = "just now"
            elif secs < 60:
                ts_label = f"{int(secs)}s ago"
            elif secs < 3600:
                ts_label = f"{int(secs / 60)}m ago"
            else:
                ts_label = f"{int(secs / 3600)}h ago"
        undo_ok = can_undo(job_dir, "deck")
        redo_ok = can_redo(job_dir, "deck")
        return (
            f'<div class="text-xs text-stone-500 flex items-center gap-2" '
            f'data-undo="{1 if undo_ok else 0}" data-redo="{1 if redo_ok else 0}">'
            f'<span class="inline-block w-2 h-2 rounded-full bg-emerald-500"></span>'
            f'<span>{message}</span>'
            f'{f"<span class=\"text-stone-400\">· {ts_label}</span>" if ts_label else ""}'
            f'</div>'
        )

    # ---- attach / remove growable extras (library assets, charts, etc.) ----

    @app.get("/workflow/wireframe/{job_id}/library-picker/{idx}", response_class=HTMLResponse)
    def library_picker(request: Request, job_id: str, idx: int, category: str | None = None):
        """HTMX modal/drawer fragment showing the asset library, grouped by category.
        When a thumbnail is clicked, the picker submits attach-extra for slide `idx`."""
        from ..library import load_catalog
        from pathlib import Path as _P

        catalog_path = _P("library/catalog.json")
        if not catalog_path.exists():
            return HTMLResponse(
                '<div class="p-6 text-center text-stone-500">'
                'Library not scanned. Run <code>uv run atelier library scan</code> first.'
                '</div>'
            )
        try:
            catalog = load_catalog(catalog_path)
        except Exception as e:  # noqa: BLE001
            return HTMLResponse(f"<div class='p-6 text-red-700'>Library load failed: {e}</div>")

        by_cat = catalog.by_category()
        categories = sorted(by_cat.keys())
        selected = category if category in by_cat else (categories[0] if categories else None)
        assets = by_cat.get(selected, [])[:60]  # cap for performance

        return templates.TemplateResponse(
            request,
            "workflow/_library_picker.html",
            {
                "job_id": job_id,
                "slide_idx": idx,
                "categories": categories,
                "selected": selected,
                "assets": assets,
            },
        )

    @app.post("/workflow/wireframe/{job_id}/attach-extra/{idx}", response_class=HTMLResponse)
    async def attach_extra(request: Request, job_id: str, idx: int):
        """Attach an extra to slide[idx]. Form fields:
            type:        the extra type (currently only 'library_asset')
            asset_ref:   the library asset id (for library_asset type)
            position:    'right' | 'below' | 'left' | 'inline'

        The response includes an HTML out-of-band swap that removes the
        library-picker modal from the DOM so the user is returned to the
        wireframe page after attaching.
        """
        config = _config_callable()
        deck = _load_deck(config, job_id)
        slide = _require_slide(deck, idx)
        form = await request.form()
        extra_type = (form.get("type") or "library_asset").strip()
        position = (form.get("position") or "right").strip()
        cfg: dict = {}
        if extra_type == "library_asset":
            asset_ref = (form.get("asset_ref") or "").strip()
            if not asset_ref:
                return HTMLResponse("missing asset_ref", status_code=400)
            cfg = {"asset_ref": asset_ref}
        slide.setdefault("extras", []).append({
            "type": extra_type,
            "position": position,
            "config": cfg,
        })
        deck["slides"][idx] = slide
        _save_deck(config, job_id, deck)
        card_response = _render_card(request, idx, slide, saved=True)
        # Append OOB swap: empty div replaces the picker modal, closing it.
        body = card_response.body.decode("utf-8")
        body += '\n<div id="library-picker-modal" hx-swap-oob="outerHTML"></div>'
        return HTMLResponse(body)

    @app.post("/workflow/wireframe/{job_id}/update-extra/{idx}/{extra_idx}", response_class=HTMLResponse)
    async def update_extra(request: Request, job_id: str, idx: int, extra_idx: int):
        """Update one of slide[idx].extras[extra_idx]'s drag-drop edit fields.

        Form fields (all optional; only the keys present in the form are touched):
            position:        'right' | 'below' | 'left' | 'inline'
            bbox:            JSON: {"left": 0..1, "top": 0..1, "width": 0..1, "height": 0..1}
                             — pass an empty string or "null" to clear bbox (revert to position-based).
            color_override:  '#RRGGBB' or empty string to clear.
        """
        config = _config_callable()
        deck = _load_deck(config, job_id)
        slide = _require_slide(deck, idx)
        extras = slide.get("extras") or []
        if extra_idx < 0 or extra_idx >= len(extras):
            raise HTTPException(404, f"extra index {extra_idx} out of range")
        extra = extras[extra_idx]
        cfg = dict(extra.get("config") or {})
        form = await request.form()

        if "position" in form:
            pos = (form.get("position") or "").strip()
            if pos in ("right", "below", "left", "inline"):
                extra["position"] = pos
            elif pos == "freeform":
                # 'freeform' is a UI-only sentinel meaning "use bbox, ignore position";
                # we keep the prior position for fallback when bbox is later cleared.
                pass

        if "bbox" in form:
            raw = (form.get("bbox") or "").strip()
            if not raw or raw.lower() == "null":
                cfg.pop("bbox", None)
            else:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    return HTMLResponse(f"invalid bbox json: {raw}", status_code=400)
                if not isinstance(parsed, dict):
                    return HTMLResponse("bbox must be an object", status_code=400)
                bbox = {}
                for k in ("left", "top", "width", "height"):
                    if k not in parsed:
                        return HTMLResponse(f"bbox missing key: {k}", status_code=400)
                    try:
                        v = float(parsed[k])
                    except (TypeError, ValueError):
                        return HTMLResponse(f"bbox.{k} must be a number", status_code=400)
                    bbox[k] = max(0.0, min(1.0, v))
                cfg["bbox"] = bbox

        if "color_override" in form:
            raw = (form.get("color_override") or "").strip()
            if not raw:
                cfg.pop("color_override", None)
            else:
                if not raw.startswith("#"):
                    raw = "#" + raw
                hexpart = raw.lstrip("#")
                if len(hexpart) != 6 or any(c not in "0123456789abcdefABCDEF" for c in hexpart):
                    return HTMLResponse(f"color_override must be #RRGGBB; got {raw}", status_code=400)
                cfg["color_override"] = "#" + hexpart.lower()

        extra["config"] = cfg
        extras[extra_idx] = extra
        slide["extras"] = extras
        deck["slides"][idx] = slide
        _save_deck(config, job_id, deck)
        return _render_card(request, idx, slide, saved=True)

    @app.post("/workflow/wireframe/{job_id}/update-block/{idx}/{block_name}", response_class=HTMLResponse)
    async def update_block(request: Request, job_id: str, idx: int, block_name: str):
        """Set or clear the freeform bbox for one of a slide's text blocks.

        Block names: 'title', 'strap', 'body', 'body_left', 'body_right'.

        Form fields:
            bbox:  JSON {"left":0..1,"top":0..1,"width":0..1,"height":0..1}
                   — empty string / "null" clears the override (revert to layout default).

        The override is persisted at slide.block_bbox[block_name] and consumed
        by the wireframe slide-card template; .pptx renderer integration follows
        in the next sprint.
        """
        VALID_BLOCKS = {"title", "strap", "body", "body_left", "body_right"}
        if block_name not in VALID_BLOCKS:
            raise HTTPException(400, f"unknown block: {block_name}; must be one of {sorted(VALID_BLOCKS)}")
        config = _config_callable()
        deck = _load_deck(config, job_id)
        slide = _require_slide(deck, idx)
        block_bbox = dict(slide.get("block_bbox") or {})
        form = await request.form()

        raw = (form.get("bbox") or "").strip()
        if not raw or raw.lower() == "null":
            block_bbox.pop(block_name, None)
        else:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return HTMLResponse(f"invalid bbox json: {raw}", status_code=400)
            if not isinstance(parsed, dict):
                return HTMLResponse("bbox must be an object", status_code=400)
            normalized: dict[str, float] = {}
            for k in ("left", "top", "width", "height"):
                if k not in parsed:
                    return HTMLResponse(f"bbox missing key: {k}", status_code=400)
                try:
                    v = float(parsed[k])
                except (TypeError, ValueError):
                    return HTMLResponse(f"bbox.{k} must be a number", status_code=400)
                normalized[k] = max(0.0, min(1.0, v))
            block_bbox[block_name] = normalized

        slide["block_bbox"] = block_bbox
        deck["slides"][idx] = slide
        _save_deck(config, job_id, deck)
        return _render_card(request, idx, slide, saved=True)

    @app.post("/workflow/wireframe/{job_id}/update-block-style/{idx}/{block_name}", response_class=HTMLResponse)
    async def update_block_style(request: Request, job_id: str, idx: int, block_name: str):
        """Update typography overrides for one of a slide's text blocks.

        Block names: 'title', 'strap', 'body', 'body_left', 'body_right'.

        Form fields (all optional; only keys present in the form are touched —
        empty string clears that key, falling back to the layout default):
            font_family:  str (any non-empty string)
            font_size:    int 8..96
            color:        '#RRGGBB' or empty
            bold:         '1'/'true'/'on' = true; '0'/'false'/'off'/'' = clear
            italic:       same encoding as bold
            align:        'left' | 'center' | 'right' | 'justify' or empty
        """
        VALID_BLOCKS = {"title", "strap", "body", "body_left", "body_right"}
        if block_name not in VALID_BLOCKS:
            raise HTTPException(400, f"unknown block: {block_name}; must be one of {sorted(VALID_BLOCKS)}")
        config = _config_callable()
        deck = _load_deck(config, job_id)
        slide = _require_slide(deck, idx)
        block_style_all = dict(slide.get("block_style") or {})
        current = dict(block_style_all.get(block_name) or {})
        form = await request.form()

        def _truthy(raw: str) -> bool:
            return (raw or "").strip().lower() in {"1", "true", "on", "yes"}

        if "font_family" in form:
            v = (form.get("font_family") or "").strip()
            if v:
                current["font_family"] = v
            else:
                current.pop("font_family", None)

        if "font_size" in form:
            raw = (form.get("font_size") or "").strip()
            if not raw:
                current.pop("font_size", None)
            else:
                try:
                    n = int(float(raw))
                except (TypeError, ValueError):
                    return HTMLResponse(f"font_size must be an integer; got {raw}", status_code=400)
                current["font_size"] = max(8, min(96, n))

        if "color" in form:
            raw = (form.get("color") or "").strip()
            if not raw:
                current.pop("color", None)
            else:
                if not raw.startswith("#"):
                    raw = "#" + raw
                hexpart = raw.lstrip("#")
                if len(hexpart) != 6 or any(c not in "0123456789abcdefABCDEF" for c in hexpart):
                    return HTMLResponse(f"color must be #RRGGBB; got {raw}", status_code=400)
                current["color"] = "#" + hexpart.lower()

        if "bold" in form:
            raw = (form.get("bold") or "").strip()
            if raw == "":
                current.pop("bold", None)
            else:
                current["bold"] = _truthy(raw)

        if "italic" in form:
            raw = (form.get("italic") or "").strip()
            if raw == "":
                current.pop("italic", None)
            else:
                current["italic"] = _truthy(raw)

        if "align" in form:
            raw = (form.get("align") or "").strip().lower()
            if not raw:
                current.pop("align", None)
            elif raw in {"left", "center", "right", "justify"}:
                current["align"] = raw
            else:
                return HTMLResponse(f"align must be left|center|right|justify; got {raw}", status_code=400)

        # Empty style dict means "no overrides" — drop the key so block_style
        # stays minimal.
        if current:
            block_style_all[block_name] = current
        else:
            block_style_all.pop(block_name, None)
        slide["block_style"] = block_style_all
        deck["slides"][idx] = slide
        _save_deck(config, job_id, deck)
        return _render_card(request, idx, slide, saved=True)

    @app.post("/workflow/wireframe/{job_id}/remove-extra/{idx}/{extra_idx}", response_class=HTMLResponse)
    async def remove_extra(request: Request, job_id: str, idx: int, extra_idx: int):
        config = _config_callable()
        deck = _load_deck(config, job_id)
        slide = _require_slide(deck, idx)
        extras = slide.get("extras") or []
        if 0 <= extra_idx < len(extras):
            extras.pop(extra_idx)
            slide["extras"] = extras
            deck["slides"][idx] = slide
            _save_deck(config, job_id, deck)
        return _render_card(request, idx, slide, saved=True)

    # ---- inline-edit deck-meta fields (click-to-edit pattern) ----
    META_FIELDS = {
        "title":         {"kind": "input",    "placeholder": "Click to add a deck title", "rows": 1},
        "subtitle":      {"kind": "input",    "placeholder": "Click to add subtitle", "rows": 1},
        "core_message":  {"kind": "textarea", "placeholder": "Click to add the core message — the single sentence the deck delivers", "rows": 4},
        "narrative_arc": {"kind": "textarea", "placeholder": "Click to add the narrative arc — how slides connect to defend the core message", "rows": 5},
    }

    def _meta_response(request: Request, job_id: str, field: str, value: str, mode: str):
        cfg = META_FIELDS[field]
        return templates.TemplateResponse(
            request,
            "workflow/_meta_field.html",
            {
                "job_id": job_id,
                "field": field,
                "value": value,
                "mode": mode,
                "kind": cfg["kind"],
                "placeholder": cfg["placeholder"],
                "rows": cfg["rows"],
            },
        )

    @app.get("/workflow/wireframe/{job_id}/meta-view/{field}", response_class=HTMLResponse)
    def meta_view(request: Request, job_id: str, field: str):
        if field not in META_FIELDS:
            raise HTTPException(404, f"unknown field: {field}")
        config = _config_callable()
        deck = _load_deck(config, job_id)
        value = deck.get(field, "") or ""
        return _meta_response(request, job_id, field, value, "view")

    @app.get("/workflow/wireframe/{job_id}/meta-edit/{field}", response_class=HTMLResponse)
    def meta_edit(request: Request, job_id: str, field: str):
        if field not in META_FIELDS:
            raise HTTPException(404, f"unknown field: {field}")
        config = _config_callable()
        deck = _load_deck(config, job_id)
        value = deck.get(field, "") or ""
        return _meta_response(request, job_id, field, value, "edit")

    @app.post("/workflow/wireframe/{job_id}/meta-save/{field}", response_class=HTMLResponse)
    async def meta_save(request: Request, job_id: str, field: str):
        if field not in META_FIELDS:
            raise HTTPException(404, f"unknown field: {field}")
        config = _config_callable()
        deck = _load_deck(config, job_id)
        form = await request.form()
        new_value = (form.get("value") or "").strip()
        deck[field] = new_value
        _save_deck(config, job_id, deck)
        return _meta_response(request, job_id, field, new_value, "view")

    # ---- reorder slides via drag-and-drop ----
    @app.post("/workflow/wireframe/{job_id}/reorder", response_class=HTMLResponse)
    async def reorder_slides(request: Request, job_id: str):
        """Accepts form field `order` = comma-separated original indices in their new position.
        Example: order="2,0,3,1" means slide originally at index 2 is now first."""
        config = _config_callable()
        deck = _load_deck(config, job_id)
        form = await request.form()
        raw_order = (form.get("order") or "").strip()
        if not raw_order:
            return HTMLResponse("missing order", status_code=400)
        try:
            new_order = [int(x) for x in raw_order.split(",") if x.strip()]
        except ValueError:
            return HTMLResponse("invalid order", status_code=400)
        slides = deck.get("slides", [])
        if sorted(new_order) != list(range(len(slides))):
            return HTMLResponse(
                f"order must be a permutation of 0..{len(slides) - 1}; got {new_order}",
                status_code=400,
            )
        deck["slides"] = [slides[i] for i in new_order]
        _save_deck(config, job_id, deck)
        return HTMLResponse(
            f'<span class="text-xs text-emerald-700">✓ Reordered ({len(slides)} slides)</span>'
        )

    # ---- add a new slide at top or bottom ----
    @app.post("/workflow/wireframe/{job_id}/add-slide", response_class=HTMLResponse)
    async def add_slide(request: Request, job_id: str):
        """Form fields: position=top|bottom, layout=<LayoutType>."""
        config = _config_callable()
        deck = _load_deck(config, job_id)
        form = await request.form()
        position = (form.get("position") or "bottom").strip()
        layout = (form.get("layout") or "content").strip()
        if layout not in LAYOUT_TYPES:
            layout = "content"
        new_slide = _empty_slide()
        new_slide["layout"] = layout
        slides = deck.setdefault("slides", [])
        if position == "top":
            slides.insert(0, new_slide)
        else:
            slides.append(new_slide)
        _save_deck(config, job_id, deck)
        # Force the page to reload so all slide indices/cards re-render correctly
        response = HTMLResponse("ok")
        response.headers["HX-Refresh"] = "true"
        return response

    # ---- save full slide ----
    @app.post("/workflow/wireframe/{job_id}/save-slide/{idx}", response_class=HTMLResponse)
    async def save_slide(request: Request, job_id: str, idx: int):
        config = _config_callable()
        deck = _load_deck(config, job_id)
        slide = _require_slide(deck, idx)

        form = await request.form()
        new_layout = (form.get("layout") or slide.get("layout") or "content").strip()
        if new_layout not in LAYOUT_TYPES:
            new_layout = "content"
        old_layout = slide.get("layout") or new_layout
        title = (form.get("title") or "").strip()
        strap = (form.get("strap") or "").strip()
        body = _split_bullets(form.get("body") or "")
        body_left = _split_bullets(form.get("body_left") or "")
        body_right = _split_bullets(form.get("body_right") or "")
        speaker_notes = (form.get("speaker_notes") or "").rstrip()

        # When the layout changes, re-home content into the new layout's
        # visible slots so the user doesn't see "their bullets disappear" just
        # because the new layout shows different fields. Conservative rule:
        # only move content when the destination is empty (never overwrite).
        body, body_left, body_right = _redistribute_for_layout_change(
            old_layout, new_layout, body, body_left, body_right
        )

        # Auto-promote stranded body content to a freeform block when going to
        # a layout with no visible body slot (title / section_divider). The
        # block_bbox makes the body re-appear as a freely-positioned overlay
        # below the strap; user can drag/resize from there.
        block_bbox = dict(slide.get("block_bbox") or {})
        NO_BODY_LAYOUTS = {"title", "section_divider"}
        if (
            old_layout != new_layout
            and new_layout in NO_BODY_LAYOUTS
            and body
            and "body" not in block_bbox
        ):
            block_bbox["body"] = {
                "left": 0.07, "top": 0.72, "width": 0.86, "height": 0.22,
            }
        # Conversely, when leaving a no-body layout BACK into a body-rendering
        # layout, drop the auto-created block_bbox['body'] so the body returns
        # to the layout's normal flow position. (User-explicit block_bboxes set
        # via /update-block are preserved unless they explicitly reset.)
        if (
            old_layout != new_layout
            and old_layout in NO_BODY_LAYOUTS
            and new_layout not in NO_BODY_LAYOUTS
            and block_bbox.get("body") == {
                "left": 0.07, "top": 0.72, "width": 0.86, "height": 0.22,
            }
        ):
            block_bbox.pop("body", None)

        slide["layout"] = new_layout
        slide["title"] = title
        slide["strap"] = strap
        slide["body"] = body
        slide["body_left"] = body_left
        slide["body_right"] = body_right
        slide["speaker_notes"] = speaker_notes
        slide["block_bbox"] = block_bbox

        deck["slides"][idx] = slide
        _save_deck(config, job_id, deck)
        return _render_card(request, idx, slide, saved=True)

    # ---- add empty bullet ----
    @app.post("/workflow/wireframe/{job_id}/add-bullet/{idx}", response_class=HTMLResponse)
    def add_bullet(request: Request, job_id: str, idx: int):
        config = _config_callable()
        deck = _load_deck(config, job_id)
        slide = _normalize_slide(_require_slide(deck, idx))
        slide.setdefault("body", []).append("")
        deck["slides"][idx] = slide
        _save_deck(config, job_id, deck)
        return _render_card(request, idx, slide)

    # ---- delete one bullet ----
    @app.post(
        "/workflow/wireframe/{job_id}/delete-bullet/{idx}/{bullet_idx}",
        response_class=HTMLResponse,
    )
    def delete_bullet(request: Request, job_id: str, idx: int, bullet_idx: int):
        config = _config_callable()
        deck = _load_deck(config, job_id)
        slide = _normalize_slide(_require_slide(deck, idx))
        body = slide.get("body", [])
        if 0 <= bullet_idx < len(body):
            body.pop(bullet_idx)
        slide["body"] = body
        deck["slides"][idx] = slide
        _save_deck(config, job_id, deck)
        return _render_card(request, idx, slide)

    # ---- delete slide ----
    @app.post("/workflow/wireframe/{job_id}/delete-slide/{idx}", response_class=HTMLResponse)
    def delete_slide(request: Request, job_id: str, idx: int):
        config = _config_callable()
        deck = _load_deck(config, job_id)
        slides = deck.get("slides", [])
        if idx < 0 or idx >= len(slides):
            raise HTTPException(
                404,
                f"slide #{idx + 1} doesn't exist (deck has {len(slides)} slide{'s' if len(slides) != 1 else ''}).",
            )
        slides.pop(idx)
        deck["slides"] = slides
        _save_deck(config, job_id, deck)
        # Empty fragment + trigger so the page can know it should refresh slide
        # numbering. The card root element is replaced with this empty div.
        return HTMLResponse(
            "<div></div>",
            headers={"HX-Trigger": "wireframe-needs-reload"},
        )

    # ---- insert empty slide after idx ----
    @app.post("/workflow/wireframe/{job_id}/insert-slide/{idx}", response_class=HTMLResponse)
    def insert_slide(request: Request, job_id: str, idx: int):
        config = _config_callable()
        deck = _load_deck(config, job_id)
        slides = deck.get("slides", [])
        if idx < -1 or idx > len(slides):
            raise HTTPException(
                404,
                f"can't insert after slide #{idx + 1} (deck has {len(slides)} slide{'s' if len(slides) != 1 else ''}).",
            )
        new_slide = _empty_slide()
        insert_at = idx + 1
        slides.insert(insert_at, new_slide)
        deck["slides"] = slides
        _save_deck(config, job_id, deck)
        return _render_card(request, insert_at, new_slide)

    # ---- save deck-level fields ----
    @app.post("/workflow/wireframe/{job_id}/save-deck-meta", response_class=HTMLResponse)
    async def save_deck_meta(request: Request, job_id: str):
        config = _config_callable()
        deck = _load_deck(config, job_id)
        form = await request.form()
        title = (form.get("title") or "").strip()
        subtitle = (form.get("subtitle") or "").strip()
        core_message = (form.get("core_message") or "").strip()
        narrative_arc = (form.get("narrative_arc") or "").strip()
        if title:
            deck["title"] = title
        deck["subtitle"] = subtitle
        if core_message:
            deck["core_message"] = core_message
        if narrative_arc:
            deck["narrative_arc"] = narrative_arc
        _save_deck(config, job_id, deck)
        return HTMLResponse(
            "<div class='p-2 text-xs text-emerald-700 bg-emerald-50 "
            "border border-emerald-200 rounded'>✓ Deck info saved.</div>"
        )
