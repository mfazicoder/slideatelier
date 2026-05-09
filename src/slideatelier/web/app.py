"""FastAPI app — serves both JSON API (/api/*) and HTMX HTML pages (/, /jobs/...).
The JSON API is the contract for any future frontend (Next.js, mobile, plugin)."""
import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config, validate_production_env
from ..library import load_catalog
from ..library_thumbnails import asset_thumbnail_path
from ..metadata import PROMPT_VERSION
from ..observability import (
    RateLimitError,
    RateLimitMiddleware,
    RequestIdMiddleware,
    configure_logging,
    current_request_id,
    init_sentry,
)
from ..template import Template, load_template, save_template
from ..template_inspector import detect_layout_map
from .health_routes import register_health_routes
from .jobs import run_generate_job, store

# Configure logging + Sentry as early as possible. Both are idempotent.
configure_logging()
init_sentry()
logger = logging.getLogger(__name__)

# Validate required env vars early — refuses to start in production if any
# are missing. In dev mode this is a no-op warning so tests stay green.
validate_production_env()

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
LIBRARY_CATALOG = Path("./library/catalog.json")
LIBRARY_THUMBNAILS = Path("./library/thumbnails")

app = FastAPI(title="slideAtelier", version="0.4.0")

# ---- Auth: session middleware + startup migration ---------------------------
# Sessions resolved on every request. Anonymous traffic is allowed in dev so
# the legacy flat output layout keeps working; production-grade gating happens
# at the route level (login_redirect_if_anonymous on /workflow*).
from ..auth import SessionMiddleware  # noqa: E402
from ..auth.db import get_db, migrate_legacy_layout  # noqa: E402
from ..auth.routes import register_auth_routes  # noqa: E402

app.add_middleware(SessionMiddleware)
# Observability middleware. Order matters: RequestId must be OUTERMOST so the
# request_id is in scope when RateLimit / Session / route handlers log.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIdMiddleware)


# ---- Friendly error handlers (HTML for browsers, JSON for /api/*) ---------

def _wants_json(request: Request) -> bool:
    if request.url.path.startswith("/api/"):
        return True
    # HTMX swaps test event.detail.successful — must NOT receive an HTML
    # error page or the swap accidentally injects it. Force JSON.
    if request.headers.get("HX-Request"):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def _render_error(request: Request, status_code: int, name: str, **ctx) -> HTMLResponse:
    rid = getattr(request.state, "request_id", None) or current_request_id()
    payload = {"request_id": rid, **ctx}
    try:
        return templates.TemplateResponse(
            request, f"errors/{name}.html", payload, status_code=status_code
        )
    except Exception:  # noqa: BLE001
        logger.exception("error template render failed", extra={"template": name})
        return HTMLResponse(f"<h1>{status_code}</h1><p>{ctx.get('detail', '')}</p>",
                            status_code=status_code)


from starlette.exceptions import HTTPException as _StarletteHTTPException  # noqa: E402


@app.exception_handler(_StarletteHTTPException)
@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    code = exc.status_code
    rid = getattr(request.state, "request_id", None) or current_request_id()
    if _wants_json(request):
        return JSONResponse(
            {"detail": exc.detail, "request_id": rid, "status_code": code},
            status_code=code,
        )
    if code == 404:
        return _render_error(request, 404, "404", detail=str(exc.detail or "Not found"))
    if code == 429:
        return _render_error(request, 429, "429", detail=str(exc.detail or "Too many requests"))
    if code == 401:
        # Friendly path: redirect to /login. (Auth middleware already does this
        # for protected routes; this handler covers programmatically-raised 401s.)
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login", status_code=303)
    return _render_error(request, code, "500", detail=str(exc.detail or "Error"))


@app.exception_handler(RateLimitError)
async def _rate_limit_handler(request: Request, exc: RateLimitError):
    rid = getattr(request.state, "request_id", None) or current_request_id()
    if _wants_json(request):
        return JSONResponse(
            {"detail": "rate limit exceeded", "retry_after": exc.retry_after, "request_id": rid},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after)},
        )
    return _render_error(request, 429, "429", retry_after=exc.retry_after)


@app.exception_handler(Exception)
async def _catch_all_handler(request: Request, exc: Exception):
    # IMPORTANT: re-raise HTTPException so the dedicated handler runs.
    # Starlette/FastAPI dispatches Exception handlers by exact-class, so
    # registering Exception here would otherwise swallow HTTPException too.
    if isinstance(exc, _StarletteHTTPException):
        return await _http_exception_handler(request, exc)
    if isinstance(exc, RateLimitError):
        return await _rate_limit_handler(request, exc)
    rid = getattr(request.state, "request_id", None) or current_request_id()
    logger.exception("unhandled_exception", extra={"path": request.url.path, "request_id": rid})
    if _wants_json(request):
        return JSONResponse(
            {"detail": "internal server error", "request_id": rid},
            status_code=500,
        )
    return _render_error(request, 500, "500", detail="Something went wrong")


@app.on_event("startup")
def _auth_startup() -> None:
    """Initialise the SQLite store and seed legacy decks under SYSTEM_USER_ID."""
    config = _config()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    get_db(config.output_dir)  # CREATE TABLE IF NOT EXISTS (idempotent)
    migrate_legacy_layout(config.output_dir)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Serve thumbnails directly. Path is relative to CWD; works for both local dev
# (cwd = project root) and Docker (cwd = /app).
if LIBRARY_THUMBNAILS.exists():
    app.mount("/thumbnails", StaticFiles(directory=str(LIBRARY_THUMBNAILS)), name="thumbnails")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _load_catalog():
    """Lazy-load library catalog. Returns None if no scan has been run."""
    if not LIBRARY_CATALOG.exists():
        return None
    try:
        return load_catalog(LIBRARY_CATALOG)
    except Exception:  # noqa: BLE001
        return None


def _config() -> Config:
    return Config.from_env()


def _list_templates_info(config: Config) -> list[dict]:
    """Return a list of template metadata for the UI / API."""
    out = []
    if not config.templates_dir.exists():
        return out
    for path in sorted(config.templates_dir.glob("*.json")):
        try:
            tpl = load_template(path)
            out.append({
                "name": path.stem,
                "filename": path.name,
                "display_name": tpl.name,
                "description": tpl.description,
                "mode": "master" if tpl.master_pptx else "json",
                "is_default": path.name == "default.json",
            })
        except Exception as e:  # noqa: BLE001
            out.append({"name": path.stem, "filename": path.name, "display_name": "(invalid)",
                        "description": str(e), "mode": "error", "is_default": False})
    return out


# ---------------------------------------------------------------------------
# JSON API — the stable contract. Any future frontend hits these.
# ---------------------------------------------------------------------------

# /api/health and /api/ready are registered via register_health_routes (see
# bottom of file) — they verify output writability and library catalog
# parsability so platforms can pull a degraded instance out of rotation.


@app.get("/api/templates")
def api_list_templates():
    return {"templates": _list_templates_info(_config())}


# ---------- Library API ----------

@app.get("/api/library/categories")
def api_library_categories():
    catalog = _load_catalog()
    if catalog is None:
        return {"scanned": False, "categories": []}
    by_cat = catalog.by_category()
    cats = [
        {"name": cat, "asset_count": len(assets)}
        for cat, assets in sorted(by_cat.items())
    ]
    return {"scanned": True, "categories": cats, "asset_count": catalog.asset_count}


@app.get("/api/library/assets")
def api_library_assets(category: str | None = None, limit: int = 200):
    catalog = _load_catalog()
    if catalog is None:
        raise HTTPException(404, "Library not scanned yet. Run `atelier library scan`.")
    assets = catalog.assets
    if category:
        assets = [a for a in assets if category.lower() in a.category.lower()]
    return {"assets": [a.model_dump() for a in assets[:limit]], "total": len(assets)}


@app.post("/api/templates/register")
async def api_register_template(
    name: str = Form(...),
    description: str = Form(""),
    master: UploadFile = File(...),
):
    if not master.filename or not master.filename.lower().endswith(".pptx"):
        raise HTTPException(400, "File must be a .pptx")

    config = _config()
    config.templates_dir.mkdir(parents=True, exist_ok=True)
    slug = name.lower().replace(" ", "-")

    dest_master = config.templates_dir / f"{slug}-master.pptx"
    with dest_master.open("wb") as f:
        shutil.copyfileobj(master.file, f)

    layout_map = detect_layout_map(dest_master)
    tpl = Template(
        name=name,
        description=description,
        master_pptx=dest_master.name,
        layout_map=layout_map,
    )
    out_json = config.templates_dir / f"{slug}.json"
    save_template(tpl, out_json)

    return {
        "name": slug,
        "filename": out_json.name,
        "master": dest_master.name,
        "layout_map": layout_map,
    }


@app.post("/api/generate")
async def api_generate(
    background_tasks: BackgroundTasks,
    content: str = Form(""),
    template: str | None = Form(None),
    requirements: str = Form(""),
    regenerate: bool = Form(False),
):
    if not content.strip():
        raise HTTPException(400, "content cannot be empty")
    config = _config()
    config.require_api_key()

    job = store.create()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    background_tasks.add_task(
        run_generate_job,
        job.id,
        content,
        requirements,
        template or None,
        config.output_dir,
        regenerate,
    )
    return {"job_id": job.id, "status": job.status}


@app.get("/api/jobs/{job_id}")
def api_job_status(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job.to_dict()


@app.get("/api/decks/{job_id}/download")
def api_download_deck(job_id: str):
    job = store.get(job_id)
    if job is None or job.status != "done" or job.deck_path is None:
        raise HTTPException(404, "deck not ready")
    if not job.deck_path.exists():
        raise HTTPException(404, "deck file missing")
    filename = f"{(job.title or 'deck').replace(' ', '_')}.pptx"
    return FileResponse(
        path=str(job.deck_path),
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


# ---------------------------------------------------------------------------
# HTMX HTML endpoints — render Jinja templates. UI-specific; will be replaced
# wholesale by Next.js (or any other frontend) when we get there. Backend
# logic stays in /api routes.
# ---------------------------------------------------------------------------

def page_index(request: Request) -> HTMLResponse:
    """Generator UI — formerly mounted at GET /, now at GET /app via
    landing_routes.register_landing_routes(). Kept as a plain function so the
    landing module re-mounts it without duplicating the template context.
    """
    config = _config()
    catalog = _load_catalog()
    library_info = {
        "scanned": catalog is not None,
        "asset_count": catalog.asset_count if catalog else 0,
        "category_count": len(catalog.categories) if catalog else 0,
    }
    return templates.TemplateResponse(request, "index.html", {
        "templates_list": _list_templates_info(config),
        "model": config.model,
        "prompt_version": PROMPT_VERSION,
        "api_key_set": bool(config.anthropic_api_key),
        "library_info": library_info,
    })


@app.get("/library", response_class=HTMLResponse)
def page_library(request: Request, category: str | None = None):
    config = _config()
    catalog = _load_catalog()
    selected_category = category
    categories = []
    assets = []
    if catalog:
        by_cat = catalog.by_category()
        categories = sorted(by_cat.keys())
        if selected_category and selected_category in by_cat:
            assets = by_cat[selected_category]
        elif categories:
            selected_category = selected_category or categories[0]
            assets = by_cat.get(selected_category, [])
    return templates.TemplateResponse(request, "library.html", {
        "model": config.model,
        "prompt_version": PROMPT_VERSION,
        "api_key_set": bool(config.anthropic_api_key),
        "scanned": catalog is not None,
        "categories": categories,
        "selected_category": selected_category,
        "assets": assets,
        "asset_count": catalog.asset_count if catalog else 0,
    })


@app.post("/generate", response_class=HTMLResponse)
async def page_generate(
    request: Request,
    background_tasks: BackgroundTasks,
    content: str = Form(""),
    template: str | None = Form(None),
    requirements: str = Form(""),
    regenerate: bool = Form(False),
):
    config = _config()
    if not config.anthropic_api_key:
        return HTMLResponse(
            "<div class='p-4 bg-red-50 border border-red-200 rounded text-red-800'>"
            "ANTHROPIC_API_KEY is not set on the server. Add it to .env and restart."
            "</div>"
        )
    if not content.strip():
        return HTMLResponse(
            "<div class='p-4 bg-red-50 border border-red-200 rounded text-red-800'>"
            "Brief is empty — paste content or upload a file."
            "</div>"
        )
    job = store.create()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    background_tasks.add_task(
        run_generate_job, job.id, content, requirements, template or None, config.output_dir, regenerate,
    )
    return templates.TemplateResponse(request, "partials/job_status.html", {"job": job})


@app.get("/jobs/{job_id}/fragment", response_class=HTMLResponse)
def page_job_fragment(request: Request, job_id: str):
    job = store.get(job_id)
    if job is None:
        return HTMLResponse("<div class='text-red-700'>Job not found.</div>", status_code=404)
    return templates.TemplateResponse(request, "partials/job_status.html", {"job": job})


@app.post("/templates/register", response_class=HTMLResponse)
async def page_register_template(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    master: UploadFile = File(...),
):
    if not master.filename or not master.filename.lower().endswith(".pptx"):
        return HTMLResponse(
            "<div class='p-4 bg-red-50 border border-red-200 rounded text-red-800'>"
            "File must be a .pptx</div>"
        )
    config = _config()
    config.templates_dir.mkdir(parents=True, exist_ok=True)
    slug = name.lower().replace(" ", "-")
    dest_master = config.templates_dir / f"{slug}-master.pptx"
    with dest_master.open("wb") as f:
        shutil.copyfileobj(master.file, f)
    layout_map = detect_layout_map(dest_master)
    tpl = Template(name=name, description=description, master_pptx=dest_master.name, layout_map=layout_map)
    out_json = config.templates_dir / f"{slug}.json"
    save_template(tpl, out_json)
    # Return updated template list fragment so the picker refreshes
    return templates.TemplateResponse(request, "partials/template_options.html", {
        "templates_list": _list_templates_info(config),
        "selected": slug,
        "just_registered": slug,
    })


# ---------------------------------------------------------------------------
# Route modules registered from sub-files. Keep app.py focused; each feature
# lives in its own routes module that exposes a register_*_routes() function.
# ---------------------------------------------------------------------------

from .audit_routes import register_audit_routes  # noqa: E402
from .brand_kit_routes import register_brand_kit_routes  # noqa: E402
from .brief_inbox_routes import register_brief_inbox_routes  # noqa: E402
from .copilot_routes import register_copilot_routes  # noqa: E402
from .critique_routes import register_critique_routes  # noqa: E402
from .design_system_routes import register_design_system_routes  # noqa: E402
from .font_routes import register_font_routes  # noqa: E402
from .landing_routes import _ensure_og_image, register_landing_routes  # noqa: E402
from .library_routes import register_library_routes  # noqa: E402
from .sample_routes import register_sample_routes  # noqa: E402
from .three_stage_routes import register_three_stage_routes  # noqa: E402
from .web_deck_routes import register_web_deck_routes  # noqa: E402
from .wireframe_edit_routes import register_wireframe_edit_routes  # noqa: E402

register_health_routes(app, _config)
register_auth_routes(app, templates)
register_design_system_routes(app, templates, _config)
register_three_stage_routes(app, templates, _config)
register_brief_inbox_routes(app, templates, _config)
register_critique_routes(app, templates, _config)
register_wireframe_edit_routes(app, templates, _config)
register_copilot_routes(app, templates, _config)
register_audit_routes(app, templates, _config)
register_sample_routes(app, _config)
register_library_routes(app, templates, _config)
register_font_routes(app, templates, _config)
register_brand_kit_routes(app, templates, _config)

from .analytics_routes import register_analytics_routes  # noqa: E402
register_analytics_routes(app, templates, _config)
register_web_deck_routes(app, templates, _config)
# Landing page must be registered AFTER the generator's `/` route was removed
# from this file (we kept page_index() as a plain function for /app to reuse).
register_landing_routes(app, templates, _config, page_app_handler=page_index)
# Generate the placeholder OG image at startup if the file is missing.
_ensure_og_image(STATIC_DIR)

# Static mount for user-uploaded fonts (Sprint C). Created on demand by
# /fonts/upload; mount only if the dir exists so a fresh install doesn't crash.
USER_FONTS_DIR = Path("./library/user_fonts")
if USER_FONTS_DIR.exists():
    app.mount("/user-fonts", StaticFiles(directory=str(USER_FONTS_DIR)), name="user_fonts")


# Friendly error handling lives at the top of this module via the
# observability middleware (RequestIdMiddleware → RateLimitMiddleware) and
# the @app.exception_handler(HTTPException / RateLimitError / Exception)
# stack. The QA agent's earlier StarletteHTTPException handler has been
# folded into the unified content-negotiating handler above so request_id
# is consistently emitted.

# Sprint K: serve brand-kit logo uploads. The /onboarding/brand-kit POST
# writes into ./output/uploads/brand_logo_<sha8>.<ext> and the wizard renders
# them via <img src="/uploads/brand_logo_<sha8>.<ext>">. Only mount if the
# directory exists so a fresh install doesn't 500 at startup.
UPLOADS_DIR = Path(_config().output_dir) / "uploads"
if UPLOADS_DIR.exists():
    app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
