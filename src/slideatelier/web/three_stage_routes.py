"""Three-stage workflow routes (Storyboard -> Wireframe -> Hi-fi).

State persists on the filesystem under <config.output_dir>/workflow/<job_id>/ so
a single FastAPI worker process can survive restarts and the user can return to
an in-progress workflow via URL.
"""
from __future__ import annotations

import json
import re
import shutil
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from ..config import Config
from ..models import LayoutType, SlideDeck
from ..renderer import DeckRenderer
from ..storyboard import Storyboard, expand_storyboard_to_deck, plan_storyboard
from ..template import load_default_template, load_template

LAYOUT_TYPES: list[str] = [
    "title",
    "section_divider",
    "content",
    "two_column",
    "bullet_list",
    "key_takeaway",
    "closing",
]

STAGE_ORDER = ["storyboard", "wireframe", "hi_fi"]


# ---------------------------------------------------------------------------
# State helpers — filesystem layout under <output_dir>/workflow/<job_id>/
# ---------------------------------------------------------------------------

def _workflow_root(config: Config) -> Path:
    return config.output_dir / "workflow"


def _job_dir(config: Config, job_id: str) -> Path:
    return _workflow_root(config) / job_id


def _read_text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_status(job_dir: Path) -> dict:
    status_path = job_dir / "status.json"
    if not status_path.exists():
        return {
            "stage": "storyboard",
            "status": "pending",
            "message": "",
            "updated_at": _now_iso(),
        }
    try:
        return json.loads(status_path.read_text())
    except Exception:  # noqa: BLE001
        return {
            "stage": "storyboard",
            "status": "failed",
            "message": "status.json unreadable",
            "updated_at": _now_iso(),
        }


def _write_status(job_dir: Path, *, stage: str, status: str, message: str = "") -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage": stage,
        "status": status,
        "message": message,
        "updated_at": _now_iso(),
    }
    (job_dir / "status.json").write_text(json.dumps(payload, indent=2))


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", value or "deck").strip("-").lower()
    return s or "deck"


def _stage_done_set(status: dict) -> set[str]:
    """Given a status dict, return the set of stages that are confirmed done.

    Stages preceding the active one are implicitly done.
    """
    stage = status.get("stage", "storyboard")
    state = status.get("status", "pending")
    if stage not in STAGE_ORDER:
        return set()
    idx = STAGE_ORDER.index(stage)
    done = set(STAGE_ORDER[:idx])
    if state == "done":
        done.add(stage)
    return done


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

def _run_storyboard(job_id: str, config: Config) -> None:
    from ..claude_client import make_client

    job_dir = _job_dir(config, job_id)
    try:
        config.require_api_key()
        brief = _read_text(job_dir / "brief.txt")
        requirements = _read_text(job_dir / "requirements.txt")
        client = make_client(config)
        storyboard, _meta = plan_storyboard(client, config, brief, requirements)
        (job_dir / "storyboard.json").write_text(storyboard.model_dump_json(indent=2))
        # Snapshot the freshly-generated storyboard so the user can step back to
        # the original after editing.
        from .workflow_history import snapshot
        snapshot(job_dir, "storyboard")
        _write_status(
            job_dir,
            stage="storyboard",
            status="done",
            message=f"Storyboard ready ({len(storyboard.slides)} slides).",
        )
    except Exception as e:  # noqa: BLE001
        _write_status(
            job_dir,
            stage="storyboard",
            status="failed",
            message=f"{type(e).__name__}: {e}",
        )
        traceback.print_exc()


def _run_expand(job_id: str, config: Config) -> None:
    from ..claude_client import make_client

    job_dir = _job_dir(config, job_id)
    try:
        config.require_api_key()
        brief = _read_text(job_dir / "brief.txt")
        requirements = _read_text(job_dir / "requirements.txt")
        storyboard = Storyboard.model_validate_json((job_dir / "storyboard.json").read_text())
        client = make_client(config)
        deck, _meta = expand_storyboard_to_deck(client, config, storyboard, brief, requirements)
        (job_dir / "deck.json").write_text(deck.model_dump_json(indent=2))
        # Snapshot the freshly-expanded deck as the first deck-history entry.
        from .workflow_history import snapshot
        snapshot(job_dir, "deck")
        _write_status(
            job_dir,
            stage="wireframe",
            status="done",
            message=f"Wireframe ready ({len(deck.slides)} slides with body content).",
        )
    except Exception as e:  # noqa: BLE001
        _write_status(
            job_dir,
            stage="wireframe",
            status="failed",
            message=f"{type(e).__name__}: {e}",
        )
        traceback.print_exc()


def _run_render(job_id: str, config: Config) -> None:
    job_dir = _job_dir(config, job_id)
    try:
        deck = SlideDeck.model_validate_json((job_dir / "deck.json").read_text())
        template_name = _read_text(job_dir / "template_name.txt").strip()
        if not template_name or template_name == "default":
            tpl = load_default_template(config.templates_dir)
        else:
            tpl = load_template(config.templates_dir / f"{template_name}.json")
        deck_path = job_dir / "deck.pptx"
        DeckRenderer(tpl, config.templates_dir).render(deck, deck_path)
        _write_status(
            job_dir,
            stage="hi_fi",
            status="done",
            message=f"Deck rendered ({len(deck.slides)} slides).",
        )
    except Exception as e:  # noqa: BLE001
        _write_status(
            job_dir,
            stage="hi_fi",
            status="failed",
            message=f"{type(e).__name__}: {e}",
        )
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_three_stage_routes(app, templates, _config_callable):
    """Registers /workflow* routes on the FastAPI app.

    Args:
        app: FastAPI instance
        templates: Jinja2Templates instance from web/app.py
        _config_callable: function returning a Config instance
    """

    def _list_template_names(config: Config) -> list[dict]:
        out: list[dict] = []
        if not config.templates_dir.exists():
            return out
        for path in sorted(config.templates_dir.glob("*.json")):
            out.append({"name": path.stem, "filename": path.name})
        return out

    @app.get("/workflow", response_class=HTMLResponse)
    def page_workflow_index(request: Request, show_archived: bool = False):
        from .workflow_history import list_workflows
        config = _config_callable()
        workflows = list_workflows(
            config.output_dir / "workflow",
            include_archived=show_archived,
        )
        return templates.TemplateResponse(
            request,
            "workflow/workflow_index.html",
            {
                "templates_list": _list_template_names(config),
                "workflows": workflows,
                "show_archived": show_archived,
                "model": config.model,
                "api_key_set": bool(config.anthropic_api_key),
            },
        )

    @app.post("/workflow/storyboard")
    def page_workflow_start(
        request: Request,
        background_tasks: BackgroundTasks,
        brief: str = Form(""),
        requirements: str = Form(""),
        template: str = Form("default"),
    ):
        config = _config_callable()
        if not brief.strip():
            return HTMLResponse(
                "<div class='p-4 bg-red-50 border border-red-200 rounded text-red-800'>"
                "Brief is empty.</div>",
                status_code=400,
            )
        job_id = uuid.uuid4().hex[:12]
        job_dir = _job_dir(config, job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        _write_text(job_dir / "brief.txt", brief)
        _write_text(job_dir / "requirements.txt", requirements or "")
        _write_text(job_dir / "template_name.txt", (template or "default").strip())
        _write_status(
            job_dir,
            stage="storyboard",
            status="running",
            message="Sketching storyboard…",
        )
        background_tasks.add_task(_run_storyboard, job_id, config)

        target = f"/workflow/storyboard/{job_id}"
        if request.headers.get("HX-Request"):
            return Response(status_code=204, headers={"HX-Redirect": target})
        return RedirectResponse(url=target, status_code=303)

    @app.get("/workflow/storyboard/{job_id}", response_class=HTMLResponse)
    def page_workflow_storyboard(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        status = _read_status(job_dir)
        storyboard_path = job_dir / "storyboard.json"
        storyboard = None
        if storyboard_path.exists():
            try:
                storyboard = Storyboard.model_validate_json(storyboard_path.read_text())
            except Exception:  # noqa: BLE001
                storyboard = None
        ready = storyboard is not None and status.get("status") in ("done", "pending")
        return templates.TemplateResponse(
            request,
            "workflow/storyboard.html",
            {
                "job_id": job_id,
                "status": status,
                "storyboard": storyboard,
                "ready": ready,
                "layout_types": LAYOUT_TYPES,
                "current_stage": "storyboard",
                "done_stages": _stage_done_set(status),
                "model": config.model,
                "api_key_set": bool(config.anthropic_api_key),
            },
        )

    @app.post("/workflow/storyboard/{job_id}/save", response_class=HTMLResponse)
    async def page_workflow_storyboard_save(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        form = await request.form()
        title = (form.get("title") or "").strip()
        subtitle = (form.get("subtitle") or "").strip()
        core_message = (form.get("core_message") or "").strip()
        narrative_arc = (form.get("narrative_arc") or "").strip()

        # Reconstruct slides from indexed form fields slide_<i>_layout etc.
        slide_indices: set[int] = set()
        for key in form.keys():
            m = re.match(r"slide_(\d+)_", key)
            if m:
                slide_indices.add(int(m.group(1)))
        slides: list[dict] = []
        for i in sorted(slide_indices):
            layout = (form.get(f"slide_{i}_layout") or "content").strip()
            if layout not in LAYOUT_TYPES:
                layout = "content"
            slide_title = (form.get(f"slide_{i}_title") or "").strip()
            purpose = (form.get(f"slide_{i}_purpose") or "").strip()
            rationale = (form.get(f"slide_{i}_rationale") or "").strip()
            if not slide_title:
                continue
            slides.append({
                "layout": layout,
                "title": slide_title,
                "purpose": purpose,
                "rationale": rationale,
            })

        try:
            storyboard = Storyboard.model_validate({
                "title": title or "Untitled",
                "subtitle": subtitle,
                "core_message": core_message or "(missing core message)",
                "narrative_arc": narrative_arc or "(missing narrative arc)",
                "slides": slides,
            })
        except Exception as e:  # noqa: BLE001
            return HTMLResponse(
                f"<div class='p-3 bg-red-50 border border-red-200 rounded text-sm text-red-800'>"
                f"Save failed: {type(e).__name__}: {e}</div>",
                status_code=400,
            )

        (job_dir / "storyboard.json").write_text(storyboard.model_dump_json(indent=2))
        # Snapshot every successful storyboard save so undo/redo works.
        from .workflow_history import snapshot
        snapshot(job_dir, "storyboard")
        _write_status(
            job_dir,
            stage="storyboard",
            status="done",
            message="Edits saved.",
        )
        resp = HTMLResponse(
            "<div class='p-3 bg-emerald-50 border border-emerald-200 rounded text-sm text-emerald-800'>"
            "Saved.</div>"
        )
        # Trigger storyboard-saved event so save-status indicator can refresh.
        resp.headers["HX-Trigger"] = "storyboard-saved"
        return resp

    @app.post("/workflow/storyboard/{job_id}/expand")
    def page_workflow_expand(
        request: Request,
        job_id: str,
        background_tasks: BackgroundTasks,
    ):
        """Expand storyboard → full deck. Smart cache: if deck.json already exists
        AND its mtime is newer than storyboard.json's, skip the LLM call and jump
        straight to the wireframe (the cached deck is up-to-date with the current
        storyboard; only edits to the storyboard invalidate)."""
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        storyboard_path = job_dir / "storyboard.json"
        deck_path = job_dir / "deck.json"
        if not storyboard_path.exists():
            raise HTTPException(400, "storyboard not ready")

        target = f"/workflow/wireframe/{job_id}"

        # Cache hit: deck exists AND its mtime ≥ storyboard's mtime
        if (
            deck_path.exists()
            and deck_path.stat().st_mtime >= storyboard_path.stat().st_mtime
        ):
            # Mark stage as wireframe done so polling/page rendering reflects the cached state
            _write_status(
                job_dir,
                stage="wireframe",
                status="done",
                message="Using cached wireframe (no storyboard changes since last expand).",
            )
            if request.headers.get("HX-Request"):
                return Response(status_code=204, headers={"HX-Redirect": target})
            return RedirectResponse(url=target, status_code=303)

        # Otherwise (no deck yet, or storyboard edited since last expand): regenerate
        _write_status(
            job_dir,
            stage="wireframe",
            status="running",
            message="Expanding storyboard into full deck…",
        )
        background_tasks.add_task(_run_expand, job_id, config)
        if request.headers.get("HX-Request"):
            return Response(status_code=204, headers={"HX-Redirect": target})
        return RedirectResponse(url=target, status_code=303)

    @app.get("/workflow/wireframe/{job_id}", response_class=HTMLResponse)
    def page_workflow_wireframe(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        status = _read_status(job_dir)
        deck_path = job_dir / "deck.json"
        deck = None
        if deck_path.exists():
            try:
                deck = SlideDeck.model_validate_json(deck_path.read_text())
            except Exception:  # noqa: BLE001
                deck = None
        ready = deck is not None and status.get("stage") == "wireframe" and status.get("status") == "done"
        # Also accept past stages (hi_fi) — deck.json still represents wireframe content
        if deck is not None and status.get("stage") == "hi_fi":
            ready = True

        # Load the chosen template so the wireframe cards can preview with the right
        # colors/fonts. Defaults to the project default template.
        from ..template import load_default_template, load_template
        template_name = ""
        template_name_path = job_dir / "template_name.txt"
        if template_name_path.exists():
            template_name = template_name_path.read_text().strip()
        try:
            if template_name and template_name != "default":
                tpl_obj = load_template(config.templates_dir / f"{template_name}.json")
            else:
                tpl_obj = load_default_template(config.templates_dir)
        except Exception:  # noqa: BLE001
            tpl_obj = load_default_template(config.templates_dir)

        return templates.TemplateResponse(
            request,
            "workflow/wireframe.html",
            {
                "job_id": job_id,
                "status": status,
                "deck": deck,
                "ready": ready,
                "current_stage": "wireframe",
                "done_stages": _stage_done_set(status) | {"storyboard"},
                "model": config.model,
                "api_key_set": bool(config.anthropic_api_key),
                "tpl": tpl_obj,
                "template_name": template_name or "default",
            },
        )

    @app.post("/workflow/wireframe/{job_id}/render")
    def page_workflow_render(
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
        _write_status(
            job_dir,
            stage="hi_fi",
            status="running",
            message="Rendering .pptx…",
        )
        background_tasks.add_task(_run_render, job_id, config)
        target = f"/workflow/hi-fi/{job_id}"
        if request.headers.get("HX-Request"):
            return Response(status_code=204, headers={"HX-Redirect": target})
        return RedirectResponse(url=target, status_code=303)

    @app.get("/workflow/hi-fi/{job_id}", response_class=HTMLResponse)
    def page_workflow_hi_fi(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        status = _read_status(job_dir)
        deck_path = job_dir / "deck.json"
        deck = None
        if deck_path.exists():
            try:
                deck = SlideDeck.model_validate_json(deck_path.read_text())
            except Exception:  # noqa: BLE001
                deck = None
        deck_pptx = job_dir / "deck.pptx"
        ready = deck_pptx.exists() and status.get("stage") == "hi_fi" and status.get("status") == "done"
        done_stages = _stage_done_set(status) | {"storyboard", "wireframe"}
        if ready:
            done_stages.add("hi_fi")
        return templates.TemplateResponse(
            request,
            "workflow/hi_fi.html",
            {
                "job_id": job_id,
                "status": status,
                "deck": deck,
                "ready": ready,
                "current_stage": "hi_fi",
                "done_stages": done_stages,
                "model": config.model,
                "api_key_set": bool(config.anthropic_api_key),
            },
        )

    @app.get("/workflow/hi-fi/{job_id}/download")
    def page_workflow_download(job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        deck_pptx = job_dir / "deck.pptx"
        if not deck_pptx.exists():
            raise HTTPException(404, "deck not rendered")
        deck_path = job_dir / "deck.json"
        title = "deck"
        if deck_path.exists():
            try:
                deck = SlideDeck.model_validate_json(deck_path.read_text())
                title = deck.title
            except Exception:  # noqa: BLE001
                pass
        filename = f"{_slugify(title)}.pptx"
        return FileResponse(
            path=str(deck_pptx),
            filename=filename,
            media_type=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ),
        )

    @app.get("/workflow/{job_id}/status", response_class=HTMLResponse)
    def page_workflow_status(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        status = _read_status(job_dir)
        response = templates.TemplateResponse(
            request,
            "workflow/_status.html",
            {"job_id": job_id, "status": status},
        )
        # When a stage completes, force the browser to reload so the rest of
        # the page (form, slide cards, etc.) actually renders. The page-load
        # render path conditionals on `ready`, which was False during polling.
        if status.get("status") == "done":
            response.headers["HX-Refresh"] = "true"
        return response

    # ---- storyboard undo / redo ---------------------------------------------------

    @app.post("/workflow/storyboard/{job_id}/undo", response_class=HTMLResponse)
    def storyboard_undo(request: Request, job_id: str):
        from .workflow_history import undo
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        ok = undo(job_dir, "storyboard")
        if not ok:
            return HTMLResponse(_render_history_status(job_dir, "storyboard", "Nothing to undo."))
        return HTMLResponse("ok", headers={"HX-Refresh": "true"})

    @app.post("/workflow/storyboard/{job_id}/redo", response_class=HTMLResponse)
    def storyboard_redo(request: Request, job_id: str):
        from .workflow_history import redo
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        ok = redo(job_dir, "storyboard")
        if not ok:
            return HTMLResponse(_render_history_status(job_dir, "storyboard", "Nothing to redo."))
        return HTMLResponse("ok", headers={"HX-Refresh": "true"})

    def _render_history_status(job_dir: Path, kind: str, message: str = "All changes saved") -> str:
        from .workflow_history import can_undo, can_redo
        artifact_path = job_dir / f"{kind}.json"
        ts_label = ""
        if artifact_path.exists():
            mtime = artifact_path.stat().st_mtime
            secs = (datetime.now(timezone.utc).timestamp() - mtime)
            if secs < 5:
                ts_label = "just now"
            elif secs < 60:
                ts_label = f"{int(secs)}s ago"
            elif secs < 3600:
                ts_label = f"{int(secs / 60)}m ago"
            else:
                ts_label = f"{int(secs / 3600)}h ago"
        undo_ok = can_undo(job_dir, kind)  # type: ignore[arg-type]
        redo_ok = can_redo(job_dir, kind)  # type: ignore[arg-type]
        return (
            f'<div class="text-xs text-stone-500 flex items-center gap-2" '
            f'data-undo="{1 if undo_ok else 0}" data-redo="{1 if redo_ok else 0}">'
            f'<span class="inline-block w-2 h-2 rounded-full bg-emerald-500"></span>'
            f'<span>{message}</span>'
            f'{f"<span class=\"text-stone-400\">· {ts_label}</span>" if ts_label else ""}'
            f'</div>'
        )

    @app.get("/workflow/{job_id}/history-status", response_class=HTMLResponse)
    def workflow_history_status(request: Request, job_id: str, kind: str = "deck"):
        if kind not in ("deck", "storyboard"):
            raise HTTPException(400, f"unknown kind: {kind}")
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        return HTMLResponse(_render_history_status(job_dir, kind))

    # ---- cross-stage time travel: history list + restore -------------------------

    @app.get("/workflow/{job_id}/history", response_class=HTMLResponse)
    def workflow_history(request: Request, job_id: str, format: str = "html"):
        from .workflow_history import list_snapshots
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        snapshots = list_snapshots(job_dir)
        # Annotate with relative time labels for display
        now = datetime.now(timezone.utc).timestamp()
        for s in snapshots:
            secs = max(0, now - s["epoch"]) if s["epoch"] else 0
            if not s["epoch"]:
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

        if format == "json":
            return JSONResponse({"snapshots": snapshots})
        return templates.TemplateResponse(
            request,
            "workflow/_history_modal.html",
            {"job_id": job_id, "snapshots": snapshots},
        )

    @app.post("/workflow/{job_id}/restore", response_class=HTMLResponse)
    async def workflow_restore(request: Request, job_id: str):
        from .workflow_history import restore_snapshot
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        # Accept kind/timestamp from query params or form body
        kind = request.query_params.get("kind", "")
        timestamp = request.query_params.get("timestamp", "")
        if not kind or not timestamp:
            try:
                form = await request.form()
                kind = kind or (form.get("kind") or "").strip()
                timestamp = timestamp or (form.get("timestamp") or "").strip()
            except Exception:  # noqa: BLE001
                pass
        if kind not in ("deck", "storyboard"):
            raise HTTPException(400, f"unknown kind: {kind}")
        if not timestamp:
            raise HTTPException(400, "missing timestamp")
        ok = restore_snapshot(job_dir, kind, timestamp)  # type: ignore[arg-type]
        if not ok:
            return HTMLResponse(
                "<div class='p-2 text-xs text-red-700'>Snapshot not found.</div>",
                status_code=404,
            )
        return HTMLResponse("ok", headers={"HX-Refresh": "true"})

    # ---- workflow rename / archive / delete --------------------------------------

    def _current_workflow_title(job_dir: Path) -> str:
        """Best-effort current title — same logic as describe_workflow's title path."""
        from .workflow_history import describe_workflow
        info = describe_workflow(job_dir)
        return (info or {}).get("title", "")

    @app.post("/workflow/{job_id}/rename", response_class=HTMLResponse)
    async def workflow_rename(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        form = await request.form()
        new_title = (form.get("title") or "").strip()
        if not new_title:
            return HTMLResponse(
                "<div class='p-2 text-xs text-red-700'>Title cannot be empty.</div>",
                status_code=400,
            )
        (job_dir / "title_override.txt").write_text(new_title)
        return HTMLResponse("ok", headers={"HX-Refresh": "true"})

    @app.post("/workflow/{job_id}/archive", response_class=HTMLResponse)
    def workflow_archive(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        archive_root = _workflow_root(config) / ".archive"
        archive_root.mkdir(parents=True, exist_ok=True)
        target = archive_root / job_id
        if target.exists():
            # Already archived under same id — append a suffix
            target = archive_root / f"{job_id}-{uuid.uuid4().hex[:6]}"
        shutil.move(str(job_dir), str(target))
        return HTMLResponse("ok", headers={"HX-Refresh": "true"})

    @app.post("/workflow/{job_id}/delete", response_class=HTMLResponse)
    async def workflow_delete(request: Request, job_id: str):
        config = _config_callable()
        job_dir = _job_dir(config, job_id)
        if not job_dir.exists():
            raise HTTPException(404, "workflow not found")
        form = await request.form()
        confirm_title = (form.get("confirm_title") or "").strip()
        current_title = _current_workflow_title(job_dir)
        if confirm_title != current_title:
            return HTMLResponse(
                f"<div class='p-2 text-xs text-red-700'>"
                f"Confirmation title did not match. Expected: {current_title!r}.</div>",
                status_code=400,
            )
        shutil.rmtree(job_dir)
        return HTMLResponse("ok", headers={"HX-Refresh": "true"})
