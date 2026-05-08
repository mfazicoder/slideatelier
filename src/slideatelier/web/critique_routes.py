"""AI partner critique routes — wires critic.py into the wireframe workflow.

State lives at <config.output_dir>/workflow/<job_id>/critique.json alongside
the deck.json that the workflow already writes. The presence of critique.json
is itself the "done" signal for the polling loop.
"""
from __future__ import annotations

import traceback
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..config import Config
from ..critic import (
    CritiqueResult,
    SlideCritique,  # noqa: F401  — re-exported via critic module; kept for clarity
    DeckCritique,  # noqa: F401
    apply_critique_to_deck,
    critique_deck,
)
from ..models import SlideDeck


def _job_dir(config: Config, job_id: str) -> Path:
    return config.output_dir / "workflow" / job_id


def _load_deck(job_dir: Path) -> SlideDeck:
    return SlideDeck.model_validate_json((job_dir / "deck.json").read_text())


def _load_critique(job_dir: Path) -> CritiqueResult | None:
    path = job_dir / "critique.json"
    if not path.exists():
        return None
    try:
        return CritiqueResult.model_validate_json(path.read_text())
    except Exception:  # noqa: BLE001
        return None


def _running_fragment(job_id: str) -> str:
    return (
        f'<div id="critique-summary" '
        f'hx-get="/workflow/wireframe/{job_id}/critique-status" '
        f'hx-trigger="every 2s" '
        f'hx-swap="outerHTML" '
        f'class="p-4 bg-amber-50 border border-amber-200 rounded flex items-center gap-3">'
        f'<span class="inline-block w-3 h-3 rounded-full bg-amber-500 animate-pulse"></span>'
        f'<div class="text-sm">'
        f'<div class="font-medium text-amber-800">Senior partner reviewing your deck…</div>'
        f'<div class="text-amber-700">This usually takes 30–60 seconds.</div>'
        f'</div></div>'
    )


def _failed_fragment(message: str) -> str:
    return (
        f'<div id="critique-summary" '
        f'class="p-4 bg-red-50 border border-red-200 rounded text-sm text-red-800">'
        f'<div class="font-medium">Critique failed.</div>'
        f'<div class="font-mono text-xs mt-1">{message}</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _run_critique(job_id: str, config: Config) -> None:
    from ..claude_client import make_client

    job_dir = _job_dir(config, job_id)
    error_path = job_dir / "critique.error.txt"
    try:
        config.require_api_key()
        deck = _load_deck(job_dir)
        client = make_client(config)
        result = critique_deck(client, config, deck)
        (job_dir / "critique.json").write_text(result.model_dump_json(indent=2))
        if error_path.exists():
            error_path.unlink()
    except Exception as e:  # noqa: BLE001
        job_dir.mkdir(parents=True, exist_ok=True)
        error_path.write_text(f"{type(e).__name__}: {e}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_critique_routes(app, templates, _config_callable):
    """Registers /workflow/wireframe/<job_id>/critique* routes on the FastAPI app.

    Args:
        app: FastAPI instance
        templates: Jinja2Templates instance from web/app.py
        _config_callable: function returning a Config instance
    """

    def _build_slide_panels(deck: SlideDeck, critique: CritiqueResult) -> list[dict]:
        panels: list[dict] = []
        for c in critique.slide_critiques:
            if 0 <= c.slide_index < len(deck.slides):
                panels.append({
                    "slide_idx": c.slide_index,
                    "slide": deck.slides[c.slide_index],
                    "slide_critique": c,
                })
        return panels

    @app.post("/workflow/wireframe/{job_id}/critique", response_class=HTMLResponse)
    def page_workflow_critique_start(
        request: Request,
        job_id: str,
        background_tasks: BackgroundTasks,
    ):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        if not (job_dir / "deck.json").exists():
            raise HTTPException(400, "deck not ready")

        # Reset any prior critique so the polling loop sees a fresh "running" state.
        critique_path = job_dir / "critique.json"
        if critique_path.exists():
            critique_path.unlink()
        error_path = job_dir / "critique.error.txt"
        if error_path.exists():
            error_path.unlink()

        background_tasks.add_task(_run_critique, job_id, config)
        return HTMLResponse(_running_fragment(job_id))

    @app.get("/workflow/wireframe/{job_id}/critique-status", response_class=HTMLResponse)
    def page_workflow_critique_status(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")

        error_path = job_dir / "critique.error.txt"
        if error_path.exists() and not (job_dir / "critique.json").exists():
            return HTMLResponse(_failed_fragment(error_path.read_text()))

        critique = _load_critique(job_dir)
        if critique is None:
            return HTMLResponse(_running_fragment(job_id))

        try:
            deck = _load_deck(job_dir)
        except Exception:  # noqa: BLE001
            return HTMLResponse(_failed_fragment("deck.json missing or invalid"))

        return templates.TemplateResponse(
            request,
            "workflow/_critique_summary.html",
            {
                "job_id": job_id,
                "critique": critique,
                "deck": deck,
                "slide_panels": _build_slide_panels(deck, critique),
            },
        )

    @app.get("/workflow/wireframe/{job_id}/critique-panel/{idx}", response_class=HTMLResponse)
    def page_workflow_critique_panel(request: Request, job_id: str, idx: int):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        critique = _load_critique(job_dir)
        if critique is None:
            raise HTTPException(404, "critique not ready")
        try:
            deck = _load_deck(job_dir)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"deck unreadable: {e}")
        if idx < 0 or idx >= len(deck.slides):
            raise HTTPException(404, "slide index out of range")

        slide_critique = next(
            (c for c in critique.slide_critiques if c.slide_index == idx),
            None,
        )
        if slide_critique is None:
            return HTMLResponse(
                f'<div id="critique-panel-{idx}" '
                f'class="text-xs text-stone-400 italic p-3">'
                f'No critique generated for slide #{idx + 1}.'
                f'</div>'
            )
        return templates.TemplateResponse(
            request,
            "workflow/_critique_panel.html",
            {
                "job_id": job_id,
                "slide_idx": idx,
                "slide": deck.slides[idx],
                "critique": slide_critique,
            },
        )

    @app.post("/workflow/wireframe/{job_id}/apply-title/{idx}", response_class=HTMLResponse)
    def page_workflow_apply_title(request: Request, job_id: str, idx: int):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        critique = _load_critique(job_dir)
        if critique is None:
            raise HTTPException(400, "critique not available")
        try:
            deck = _load_deck(job_dir)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"deck unreadable: {e}")
        if idx < 0 or idx >= len(deck.slides):
            raise HTTPException(404, "slide index out of range")

        slide_critique = next(
            (c for c in critique.slide_critiques if c.slide_index == idx),
            None,
        )
        if slide_critique is None or not slide_critique.proposed_title:
            return HTMLResponse(
                f'<div id="critique-panel-{idx}" '
                f'class="p-3 bg-stone-50 border border-stone-200 rounded text-xs text-stone-600">'
                f'No proposed title for this slide.'
                f'</div>'
            )

        new_title = slide_critique.proposed_title
        new_slides = list(deck.slides)
        new_slides[idx] = new_slides[idx].model_copy(update={"title": new_title})
        revised = deck.model_copy(update={"slides": new_slides})
        (job_dir / "deck.json").write_text(revised.model_dump_json(indent=2))

        return HTMLResponse(
            f'<div id="critique-panel-{idx}" '
            f'class="p-3 bg-emerald-50 border border-emerald-200 rounded text-sm text-emerald-800">'
            f'✓ Applied: <span class="font-semibold">{new_title}</span>'
            f'</div>'
        )

    @app.post("/workflow/wireframe/{job_id}/apply-all-major", response_class=HTMLResponse)
    def page_workflow_apply_all_major(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        critique = _load_critique(job_dir)
        if critique is None:
            raise HTTPException(400, "critique not available")
        try:
            deck = _load_deck(job_dir)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"deck unreadable: {e}")

        revised, changed = apply_critique_to_deck(
            deck, critique, accept_severities=("fatal", "major"),
        )
        (job_dir / "deck.json").write_text(revised.model_dump_json(indent=2))

        if not changed:
            return HTMLResponse(
                '<div class="mt-3 p-3 bg-stone-50 border border-stone-200 rounded text-sm text-stone-700">'
                'No fatal/major slides had a proposed title to apply.'
                '</div>'
            )
        count = len(changed)
        plural = "title" if count == 1 else "titles"
        return HTMLResponse(
            f'<div class="mt-3 p-3 bg-emerald-50 border border-emerald-200 rounded text-sm text-emerald-800">'
            f'✓ Applied {count} {plural} (slides {", ".join("#" + str(i + 1) for i in changed)}).'
            f'</div>'
        )

    @app.post("/workflow/wireframe/{job_id}/dismiss-critique/{idx}", response_class=HTMLResponse)
    def page_workflow_dismiss_critique(request: Request, job_id: str, idx: int):
        # Pure UI dismissal — return empty so HTMX swap removes the panel.
        return HTMLResponse("")
