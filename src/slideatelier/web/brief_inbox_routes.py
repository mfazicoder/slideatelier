"""Brief Inbox routes — Sprint W's fourth entry route.

GET  /brief-inbox                 → intake page (single textarea + upload chip)
POST /api/brief-inbox/ingest      → run ingestion + analysis, returns job_id
GET  /brief-inbox/{job_id}/review → review page (storyboard + BriefAnalysis)
POST /brief-inbox/{job_id}/reprompt → re-run analysis with extra notes

State is written to the same `<output_dir>/workflow/<job_id>/` directory used
by the three-stage workflow, so once the user clicks "Continue to wireframe"
the existing storyboard/wireframe routes pick up the same job_id seamlessly.
We add three artifacts on top of the standard set:
- `brief.txt`            — the assembled brief text (paste + URLs + attachments)
- `brief_analysis.json`  — BriefAnalysis as JSON
- `brief_sources.json`   — list of source attributions

The route module never touches `auth/` internals; it reads the optional user
via `getattr(request.state, "user", None)` so anonymous traffic still works.
"""
from __future__ import annotations

import json
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.datastructures import UploadFile

from ..brief_inbox import (
    ALLOWED_ATTACHMENT_SUFFIXES,
    MAX_ATTACHMENT_BYTES,
    analyze_brief,
    assemble_brief,
    write_sources,
)
from ..config import Config
from ..models import BriefAnalysis
from ..storyboard import Storyboard


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_workflow_root(config: Config, request: Request) -> Path:
    """Resolve the workflow root for this request — auth-aware path or the
    legacy `<output_dir>/workflow/` for anonymous traffic. We avoid importing
    auth helpers at module import-time so this module stays decoupled from
    the auth subsystem (per Sprint W hard rules).
    """
    user = getattr(request.state, "user", None)
    if user is None:
        return config.output_dir / "workflow"
    # Try to delegate to auth.middleware if it's available; fall back to a
    # plausible per-user path. The fallback never executes when auth is
    # registered (the normal case in app.py).
    try:
        from ..auth.middleware import user_workflow_root  # type: ignore[import-not-found]

        return user_workflow_root(config.output_dir, user)
    except Exception:  # noqa: BLE001
        uid = getattr(user, "id", None)
        if uid is None:
            return config.output_dir / "workflow"
        return config.output_dir / "users" / str(uid) / "workflow"


def _write_status(job_dir: Path, *, status: str, message: str = "") -> None:
    """Write status.json mirroring the three-stage workflow's format so the
    user can resume into the storyboard route (which reads the same file)."""
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "stage": "storyboard",
        "status": status,
        "message": message,
        "updated_at": _now_iso(),
    }
    (job_dir / "status.json").write_text(json.dumps(payload, indent=2))


def _run_inbox_job(job_id: str, config: Config, brief_text: str, requirements: str, job_dir: Path) -> None:
    """Background worker: brief_text → (Storyboard, BriefAnalysis) on disk."""
    try:
        storyboard, analysis, _meta = analyze_brief(config, brief_text, requirements)
        (job_dir / "storyboard.json").write_text(storyboard.model_dump_json(indent=2))
        (job_dir / "brief_analysis.json").write_text(analysis.model_dump_json(indent=2))
        # Snapshot so undo/redo + cross-stage history works the same way as
        # the storyboard-stage flow expects.
        try:
            from .workflow_history import snapshot

            snapshot(job_dir, "storyboard")
        except Exception:  # noqa: BLE001 — snapshot failure shouldn't fail the job
            pass
        _write_status(
            job_dir,
            status="done",
            message=f"Brief drafted ({len(storyboard.slides)} slides).",
        )
    except Exception as e:  # noqa: BLE001
        _write_status(
            job_dir,
            status="failed",
            message=f"{type(e).__name__}: {e}",
        )
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_brief_inbox_routes(app, templates, _config_callable):
    """Registers /brief-inbox* routes on the FastAPI app.

    Args:
        app: FastAPI instance
        templates: Jinja2Templates instance from web/app.py
        _config_callable: function returning a Config
    """

    @app.get("/brief-inbox", response_class=HTMLResponse)
    def page_brief_inbox(request: Request):
        config = _config_callable()
        return templates.TemplateResponse(
            request,
            "brief_inbox/intake.html",
            {
                "model": config.model,
                "api_key_set": bool(config.anthropic_api_key),
                "max_attachment_mb": MAX_ATTACHMENT_BYTES // (1024 * 1024),
                "allowed_suffixes": sorted(ALLOWED_ATTACHMENT_SUFFIXES),
            },
        )

    @app.post("/api/brief-inbox/ingest")
    async def api_brief_inbox_ingest(
        request: Request,
        background_tasks: BackgroundTasks,
    ):
        """Accept a brief paste + optional attachments. Synchronously assembles
        the brief text (URL fetch + attachment extraction), then kicks off
        the LLM call as a background task and returns the job_id. The review
        page polls status.json — same pattern as the storyboard stage.

        We parse `request.form()` manually instead of declaring `Form()` and
        `File()` parameters in the signature so the endpoint accepts BOTH
        `application/x-www-form-urlencoded` (text-only) and
        `multipart/form-data` (with attachments) without forcing one. This
        keeps the path uniform for the JSON `data=` test client AND for
        browser uploads.
        """
        config = _config_callable()

        try:
            form = await request.form()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"could not parse request body: {e}")

        content = (form.get("content") or "")
        requirements = (form.get("requirements") or "")
        if isinstance(content, UploadFile):
            content = ""
        if isinstance(requirements, UploadFile):
            requirements = ""
        content = str(content)
        requirements = str(requirements)

        # Pull attachments out of the form by key — the intake form names them
        # all "attachments" so we accept the multi-valued list.
        raw_attachments = [
            v
            for k, v in form.multi_items()
            if k == "attachments" and isinstance(v, UploadFile)
        ]

        if not content.strip() and not raw_attachments:
            raise HTTPException(400, "content cannot be empty")

        # Read attachment bytes up-front; enforce the per-file size cap.
        attachment_payloads: list[tuple[str, bytes]] = []
        for upload in raw_attachments:
            filename = upload.filename or ""
            if not filename:
                continue
            data = await upload.read()
            if len(data) > MAX_ATTACHMENT_BYTES:
                raise HTTPException(
                    400,
                    f"attachment {filename!r} exceeds the "
                    f"{MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB per-file cap",
                )
            attachment_payloads.append((filename, data))

        # Assemble synchronously — URL fetches and PDF parsing are bounded
        # (5 s per URL, 5 MB per file) so this never blocks long enough to
        # warrant moving into the background task.
        brief_text, sources = assemble_brief(
            content, attachment_payloads, fetch_urls=True
        )
        if not brief_text.strip():
            raise HTTPException(
                400,
                "brief is empty after extraction (all URLs failed and "
                "attachments yielded no text)",
            )

        # Allocate a job under the user's workflow root (or anonymous root).
        job_id = uuid.uuid4().hex[:12]
        workflow_root = _user_workflow_root(config, request)
        workflow_root.mkdir(parents=True, exist_ok=True)
        job_dir = workflow_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # Persist artifacts the storyboard route expects.
        (job_dir / "brief.txt").write_text(brief_text)
        (job_dir / "requirements.txt").write_text(requirements or "")
        (job_dir / "template_name.txt").write_text("default")
        write_sources(job_dir, sources)
        _write_status(
            job_dir,
            status="running",
            message="Reading the brief and sketching the storyboard…",
        )

        # Best-effort registration with the auth/decks index when present —
        # mirrors what the three-stage workflow does. Failure is non-fatal so
        # tests without the SQLite tables wired up still pass.
        try:
            from ..auth.db import get_db  # type: ignore[import-not-found]

            user = getattr(request.state, "user", None)
            if user is not None:
                db = get_db(config.output_dir)
                db.record_deck(job_id=job_id, owner_user_id=user.id)
        except Exception:  # noqa: BLE001
            pass

        background_tasks.add_task(
            _run_inbox_job, job_id, config, brief_text, requirements, job_dir
        )

        target = f"/brief-inbox/{job_id}/review"
        if request.headers.get("HX-Request"):
            return Response(status_code=204, headers={"HX-Redirect": target})
        # Browsers posting a normal form expect a redirect; JSON clients get
        # the JSON body. We pick by Accept header.
        accept = (request.headers.get("accept") or "").lower()
        if "text/html" in accept and "application/json" not in accept:
            return RedirectResponse(url=target, status_code=303)
        return JSONResponse(
            {
                "job_id": job_id,
                "review_url": target,
                "sources": [s.to_dict() for s in sources],
            }
        )

    @app.get("/brief-inbox/{job_id}/review", response_class=HTMLResponse)
    def page_brief_inbox_review(request: Request, job_id: str):
        config = _config_callable()
        # Try the user's workflow root first, then fall back to the anonymous
        # root — this lets tests (which don't go through SessionMiddleware
        # consistently) and dev mode both work.
        workflow_root = _user_workflow_root(config, request)
        job_dir = workflow_root / job_id
        if not job_dir.exists():
            anon = config.output_dir / "workflow" / job_id
            if anon.exists():
                job_dir = anon
        if not job_dir.exists():
            raise HTTPException(404, "brief inbox job not found")

        status: dict = {"status": "pending", "message": ""}
        status_path = job_dir / "status.json"
        if status_path.exists():
            try:
                status = json.loads(status_path.read_text())
            except Exception:  # noqa: BLE001
                pass

        storyboard = None
        if (job_dir / "storyboard.json").exists():
            try:
                storyboard = Storyboard.model_validate_json(
                    (job_dir / "storyboard.json").read_text()
                )
            except Exception:  # noqa: BLE001
                storyboard = None

        analysis = None
        if (job_dir / "brief_analysis.json").exists():
            try:
                analysis = BriefAnalysis.model_validate_json(
                    (job_dir / "brief_analysis.json").read_text()
                )
            except Exception:  # noqa: BLE001
                analysis = None

        sources: list[dict] = []
        if (job_dir / "brief_sources.json").exists():
            try:
                sources = json.loads((job_dir / "brief_sources.json").read_text())
            except Exception:  # noqa: BLE001
                sources = []

        ready = storyboard is not None and analysis is not None and status.get(
            "status"
        ) == "done"

        return templates.TemplateResponse(
            request,
            "brief_inbox/review.html",
            {
                "job_id": job_id,
                "status": status,
                "storyboard": storyboard,
                "analysis": analysis,
                "sources": sources,
                "ready": ready,
                "model": config.model,
                "api_key_set": bool(config.anthropic_api_key),
            },
        )

    @app.post("/brief-inbox/{job_id}/reprompt")
    async def page_brief_inbox_reprompt(
        request: Request,
        background_tasks: BackgroundTasks,
        job_id: str,
    ):
        """Re-run analysis with appended notes. The notes are concatenated
        onto requirements so both the storyboard and the analysis pick up
        the user's correction in a single re-run."""
        try:
            form = await request.form()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"could not parse request body: {e}")
        notes = str(form.get("notes") or "")
        config = _config_callable()
        workflow_root = _user_workflow_root(config, request)
        job_dir = workflow_root / job_id
        if not job_dir.exists():
            anon = config.output_dir / "workflow" / job_id
            if anon.exists():
                job_dir = anon
        if not job_dir.exists():
            raise HTTPException(404, "brief inbox job not found")
        if not (job_dir / "brief.txt").exists():
            raise HTTPException(400, "brief.txt missing — cannot re-prompt")

        brief_text = (job_dir / "brief.txt").read_text()
        prior_req = ""
        if (job_dir / "requirements.txt").exists():
            prior_req = (job_dir / "requirements.txt").read_text().strip()
        # Append the user's correction notes to the existing requirements so
        # the next pass takes both into account.
        new_req = prior_req
        notes = (notes or "").strip()
        if notes:
            new_req = (
                f"{prior_req}\n\n# Re-prompt notes ({_now_iso()})\n{notes}".strip()
            )
        (job_dir / "requirements.txt").write_text(new_req)

        _write_status(
            job_dir,
            status="running",
            message="Re-running with your notes…",
        )
        background_tasks.add_task(
            _run_inbox_job, job_id, config, brief_text, new_req, job_dir
        )

        target = f"/brief-inbox/{job_id}/review"
        if request.headers.get("HX-Request"):
            return Response(status_code=204, headers={"HX-Redirect": target})
        return RedirectResponse(url=target, status_code=303)
