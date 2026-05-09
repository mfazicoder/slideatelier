"""Atelier Copilot routes — sticky right-rail chat for scoped diff edits.

Wires `copilot.py` into the wireframe page. Three endpoints:

  POST /workflow/wireframe/<job_id>/copilot/ask
        Body: prompt, selection_kind, selection_id, optionally selection_json.
        Loads deck.json, builds a focused slice, asks Claude for a patch,
        snapshots, applies, and returns an HTMX-swap-OOB partial that updates
        only the affected slide-card.

  POST /workflow/wireframe/<job_id>/copilot/rethink/<idx>
        "Rethink this slide" — returns shape suggestions with rationale.

  GET  /workflow/wireframe/<job_id>/copilot/log
        Returns the turn log as JSON (for the chat history rail).

All Anthropic calls are mockable: `make_client` is imported lazily inside the
handler so tests can monkeypatch the symbol.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import Config
from ..copilot import (
    apply_patch,
    append_turn_log,
    ask_copilot,
    build_focused_slice,
    parse_prompt,
    rethink_slide,
)


def _job_dir(config: Config, job_id: str) -> Path:
    return config.output_dir / "workflow" / job_id


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


def _load_shape_registry() -> list[str]:
    """Return the list of available shape IDs. Best-effort; on missing catalog
    returns a small built-in set of primitives so 'rethink' still works."""
    try:
        from ..library import load_catalog
        catalog_path = Path("library/catalog.json")
        if catalog_path.exists():
            cat = load_catalog(catalog_path)
            return [a.id for a in cat.assets][:200]
    except Exception:  # noqa: BLE001
        pass
    return [
        "matrix_2x2",
        "process_funnel",
        "process_horizontal",
        "venn_2",
        "venn_3",
        "stacked_bar",
        "donut",
        "kpi_callout",
    ]


def register_copilot_routes(app, templates, _config_callable):
    """Register the copilot endpoints on the FastAPI app."""

    def _render_card(request: Request, slide_idx: int, slide: dict) -> str:
        """Re-render a single slide card as HTML. Mirrors the helper in
        wireframe_edit_routes — kept inline so we don't cross-import."""
        # Reuse the wireframe_edit_routes' template rendering by going through
        # templates directly. We only need the partial, not the whole page.
        from ..template import load_default_template, load_template
        config = _config_callable()
        job_id = request.path_params.get("job_id") or ""
        tpl = None
        try:
            template_name_path = config.output_dir / "workflow" / job_id / "template_name.txt"
            tname = template_name_path.read_text().strip() if template_name_path.exists() else ""
            if tname and tname != "default":
                tpl = load_template(config.templates_dir / f"{tname}.json")
            else:
                tpl = load_default_template(config.templates_dir)
        except Exception:  # noqa: BLE001
            tpl = None

        # Normalize slide so the partial can render even if the patch left
        # some keys missing.
        from .wireframe_edit_routes import LAYOUT_TYPES, _normalize_slide
        rendered = templates.TemplateResponse(
            request,
            "workflow/_slide_edit_card.html",
            {
                "job_id": job_id,
                "slide_idx": slide_idx,
                "slide": _normalize_slide(slide),
                "layout_types": LAYOUT_TYPES,
                "saved": True,
                "tpl": tpl,
            },
        )
        return rendered.body.decode("utf-8")

    @app.post("/workflow/wireframe/{job_id}/copilot/ask", response_class=HTMLResponse)
    async def copilot_ask(request: Request, job_id: str):
        """Run one copilot turn against a scoped slice of the deck.

        Form fields:
          prompt:         user's text (may include leading slash shortcut)
          selection_kind: "slide" | "shape" | "theme" | "deck"
          selection_id:   for slide → int index; for shape → shape id; else ""

        Returns HTML: a chat-bubble fragment for the rail, plus an OOB swap
        for the affected slide card when the patch changes a slide.
        """
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")

        form = await request.form()
        raw_prompt = (form.get("prompt") or "").strip()
        if not raw_prompt:
            return HTMLResponse(
                '<div class="text-xs text-red-700 px-2 py-1">Prompt is empty.</div>',
                status_code=400,
            )
        sel_kind = (form.get("selection_kind") or "deck").strip() or "deck"
        sel_id_raw = (form.get("selection_id") or "").strip()
        selection: dict = {"kind": sel_kind, "id": None}
        if sel_kind == "slide":
            try:
                selection["id"] = int(sel_id_raw)
            except ValueError:
                selection["id"] = 0
        elif sel_kind == "shape":
            selection["id"] = sel_id_raw or None

        # Slash-shortcut overrides selection.
        parsed = parse_prompt(raw_prompt)
        if parsed.selection_override:
            selection = parsed.selection_override
        body = parsed.body or raw_prompt

        deck = _load_deck(config, job_id)
        focused = build_focused_slice(deck, selection)

        # Snapshot BEFORE mutation so undo restores the prior state.
        from .workflow_history import snapshot
        snapshot(job_dir, "deck")

        # Lazy import so the test suite can monkeypatch `make_client` on the
        # module without forcing an Anthropic client at import time.
        try:
            config.require_api_key()
        except Exception as e:  # noqa: BLE001
            return HTMLResponse(
                f'<div class="text-xs text-red-700 px-2 py-1">{e}</div>',
                status_code=400,
            )
        from ..claude_client import make_client
        client = make_client(config)

        try:
            patch = ask_copilot(
                client,
                model=config.model,
                prompt=body,
                selection=selection,
                focused_slice=focused,
            )
        except Exception as e:  # noqa: BLE001
            append_turn_log(
                job_dir,
                prompt=raw_prompt,
                selection=selection,
                patch={},
                success=False,
                error=str(e),
            )
            return HTMLResponse(
                f'<div class="text-xs text-red-700 px-2 py-1">Copilot error: {e}</div>',
                status_code=502,
            )

        # Apply the patch and persist.
        apply_patch(deck, patch)
        _deck_path(config, job_id).write_text(json.dumps(deck, indent=2))
        append_turn_log(
            job_dir,
            prompt=raw_prompt,
            selection=selection,
            patch=patch,
            success=True,
        )

        # Build the chat reply bubble.
        rationale = patch.rationale or "(no rationale)"
        scope_label = patch.scope
        target_label = "" if patch.target is None else f" #{patch.target}"
        bubble = (
            '<div class="copilot-turn space-y-1.5 text-sm">'
            f'<div class="text-stone-700 bg-stone-100 rounded px-2 py-1.5"><b>You:</b> {raw_prompt}</div>'
            '<div class="text-stone-800 bg-amber-50 border border-amber-200 rounded px-2 py-1.5">'
            f'<b>Atelier:</b> patched <code class="text-[11px]">{scope_label}{target_label}</code> · '
            f'<span class="italic text-stone-600">{rationale}</span>'
            '</div></div>'
        )

        # OOB-swap the affected slide card so the page updates without reload.
        oob_html = ""
        if patch.scope == "slide":
            try:
                idx = int(patch.target)
            except (TypeError, ValueError):
                idx = -1
            slides = deck.get("slides") or []
            if 0 <= idx < len(slides):
                card_html = _render_card(request, idx, slides[idx])
                # Tag the rendered card with hx-swap-oob so HTMX replaces the
                # card on the page in place.
                tag = '<article '
                if tag in card_html:
                    card_html = card_html.replace(
                        tag, '<article hx-swap-oob="outerHTML" ', 1
                    )
                oob_html = card_html

        resp = HTMLResponse(bubble + oob_html)
        resp.headers["HX-Trigger"] = "deck-saved"
        return resp

    @app.post("/workflow/wireframe/{job_id}/copilot/rethink/{idx}", response_class=HTMLResponse)
    async def copilot_rethink(request: Request, job_id: str, idx: int):
        """Suggest better visual shapes for a slide's content."""
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")

        deck = _load_deck(config, job_id)
        slides = deck.get("slides") or []
        if idx < 0 or idx >= len(slides):
            raise HTTPException(404, "slide index out of range")
        slide = slides[idx]

        try:
            config.require_api_key()
        except Exception as e:  # noqa: BLE001
            return HTMLResponse(
                f'<div class="text-xs text-red-700 px-2 py-1">{e}</div>',
                status_code=400,
            )
        from ..claude_client import make_client
        client = make_client(config)

        registry = _load_shape_registry()
        try:
            suggestions = rethink_slide(
                client,
                model=config.model,
                slide=slide,
                available_shape_ids=registry,
            )
        except Exception as e:  # noqa: BLE001
            return HTMLResponse(
                f'<div class="text-xs text-red-700 px-2 py-1">Rethink error: {e}</div>',
                status_code=502,
            )

        if not suggestions:
            return HTMLResponse(
                f'<div id="rethink-panel-{idx}" '
                'class="p-3 bg-stone-50 border border-stone-200 rounded text-sm text-stone-700">'
                'No shape suggestions for this slide.'
                '</div>'
            )

        items = []
        for s in suggestions:
            sid = s.get("shape_id", "")
            rat = s.get("rationale", "")
            conf = s.get("confidence", "medium")
            items.append(
                '<li class="flex items-start gap-2 py-1.5">'
                f'<span class="font-mono text-[11px] bg-amber-100 text-amber-900 px-1.5 py-0.5 rounded">{sid}</span>'
                f'<span class="text-xs text-stone-700 flex-1">{rat}</span>'
                f'<span class="text-[10px] uppercase text-stone-500">{conf}</span>'
                '<form '
                f'hx-post="/workflow/wireframe/{job_id}/attach-extra/{idx}" '
                'hx-target="closest [data-slide-card]" hx-swap="outerHTML" '
                'class="inline-block">'
                '<input type="hidden" name="type" value="library_asset">'
                f'<input type="hidden" name="asset_ref" value="{sid}">'
                '<input type="hidden" name="position" value="right">'
                '<button type="submit" class="text-[11px] px-2 py-0.5 bg-stone-900 text-white rounded hover:bg-stone-700">apply</button>'
                '</form>'
                '</li>'
            )

        return HTMLResponse(
            f'<div id="rethink-panel-{idx}" class="mt-2 p-3 bg-amber-50/60 border border-amber-200 rounded">'
            '<div class="text-xs font-semibold text-amber-900 mb-1">Atelier suggests</div>'
            f'<ul class="divide-y divide-amber-200/60">{"".join(items)}</ul>'
            '</div>'
        )

    @app.get("/workflow/wireframe/{job_id}/copilot/log")
    def copilot_log(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        path = job_dir / "copilot" / "turns.jsonl"
        if not path.exists():
            return JSONResponse({"turns": []})
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return JSONResponse({"turns": out})
