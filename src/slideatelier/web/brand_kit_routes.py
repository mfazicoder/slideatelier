"""Brand Kit Wizard routes — onboarding and edit endpoints for the workspace
BrandKit (Sprint K).

Routes:
  - GET  /onboarding/brand-kit       — first-run 60-second wizard form
  - POST /onboarding/brand-kit       — save the kit + redirect to workflow
  - GET  /design-system/brand-kit    — edit existing kit (reuses the form)
  - POST /design-system/brand-kit    — save changes (re-renders form on success)

Logo uploads live at `<output>/uploads/brand_logo_<sha8>.<ext>` and are
validated with a magic-byte check that mirrors `font_routes.py`. We accept
PNG/JPEG/SVG/WEBP up to 2 MB. Anything that fails validation is rejected with
a 400 — we never persist a partial file.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from fastapi import Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from ..brand_kit import load_brand_kit, save_brand_kit
from ..models import BrandKit


_MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB

# Mirrors font_routes._MAGIC_TABLE: extension → (allowed magic-byte prefixes,
# content-type). SVG is XML-shaped so we accept the canonical opening tags.
_LOGO_MAGIC: dict[str, tuple[tuple[bytes, ...], str]] = {
    ".png": ((b"\x89PNG\r\n\x1a\n",), "image/png"),
    ".jpg": ((b"\xff\xd8\xff",), "image/jpeg"),
    ".jpeg": ((b"\xff\xd8\xff",), "image/jpeg"),
    ".webp": ((b"RIFF",), "image/webp"),  # bytes 0..3; "WEBP" is at 8..11
    ".svg": ((b"<?xml", b"<svg"), "image/svg+xml"),
}

_AUDIENCE_TONES = ("formal", "approachable", "bold", "minimal")
_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _user_id_for(request: Request) -> Optional[int | str]:
    """Pick the per-user kit path when a session exists, else None.

    The spec is explicit: `getattr(request.state, "user", None)` — if auth
    landed we route per-user, otherwise we use the global file. The kit
    storage helper handles the fallthrough so we just pass the id through.
    """
    user = getattr(request.state, "user", None)
    return user.id if user is not None else None


def _validate_logo_bytes(filename: str, head: bytes, total_size: int) -> str:
    """Validate logo magic + size; return the resolved extension or raise 400."""
    ext = Path(filename).suffix.lower()
    if ext not in _LOGO_MAGIC:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported logo extension {ext!r}. Use .png, .jpg, .svg, or .webp."
            ),
        )
    magics, _ct = _LOGO_MAGIC[ext]
    # Strip leading whitespace for SVG since text editors sometimes prepend it.
    head_check = head.lstrip() if ext == ".svg" else head
    if not any(head_check.startswith(m) for m in magics):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Uploaded file does not look like a real {ext} image "
                f"(magic-byte check failed)."
            ),
        )
    if total_size > _MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Logo is {total_size} bytes; the limit is {_MAX_LOGO_BYTES} bytes (2 MB)."
            ),
        )
    return ext


def _save_logo(output_dir: Path, file: UploadFile) -> str:
    """Persist a logo upload + return a stable web path (`/uploads/...`).

    Returns the URL path written into BrandKit.logo_path. The on-disk file
    lives at `<output>/uploads/brand_logo_<sha8>.<ext>` so re-uploading the
    same image produces the same filename (cheap dedup).
    """
    # Read up to limit+1 bytes so we can detect oversize without buffering
    # arbitrarily large files in memory.
    payload = file.file.read(_MAX_LOGO_BYTES + 1)
    head = payload[:32]
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")
    ext = _validate_logo_bytes(file.filename, head, len(payload))
    if len(payload) > _MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Logo exceeds the {_MAX_LOGO_BYTES}-byte limit.",
        )
    digest = hashlib.sha256(payload).hexdigest()[:8]
    uploads = output_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    dest = uploads / f"brand_logo_{digest}{ext}"
    dest.write_bytes(payload)
    # Web path served by the static mount registered in app.py.
    return f"/uploads/{dest.name}"


def _normalize_hex_or_400(label: str, value: str) -> str:
    if not _HEX_RE.match((value or "").strip()):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {label} colour {value!r}; expected '#RRGGBB'.",
        )
    s = value.strip()
    if not s.startswith("#"):
        s = "#" + s
    return s.upper()


def register_brand_kit_routes(app, templates, _config_callable):
    """Register the wizard routes on the FastAPI app.

    Args:
        app: FastAPI instance
        templates: Jinja2Templates from web/app.py
        _config_callable: function returning a Config (matches web/app.py)
    """

    def _render_form(request: Request, *, kit: BrandKit, message: str = "") -> HTMLResponse:
        from ..font_catalog import all_web_fonts

        return templates.TemplateResponse(
            request,
            "brand_kit/wizard.html",
            {
                "kit": kit,
                "tones": _AUDIENCE_TONES,
                "fonts": all_web_fonts(),
                "message": message,
            },
        )

    @app.get("/onboarding/brand-kit", response_class=HTMLResponse)
    def page_onboarding_brand_kit(request: Request):
        config = _config_callable()
        kit = load_brand_kit(config.output_dir, _user_id_for(request)) or BrandKit()
        return _render_form(request, kit=kit)

    @app.get("/design-system/brand-kit", response_class=HTMLResponse)
    def page_design_system_brand_kit(request: Request):
        config = _config_callable()
        kit = load_brand_kit(config.output_dir, _user_id_for(request)) or BrandKit()
        return _render_form(request, kit=kit)

    async def _save_handler(
        request: Request,
        *,
        color_primary: str,
        color_secondary: str,
        color_accent: str,
        color_bg: str,
        color_text: str,
        type_display: str,
        type_body: str,
        type_mono: str,
        audience_tone: str,
        logo: Optional[UploadFile],
        redirect_to: Optional[str],
    ) -> HTMLResponse:
        config = _config_callable()
        # Hex validation up front so we don't half-write the kit.
        primary = _normalize_hex_or_400("primary", color_primary)
        secondary = _normalize_hex_or_400("secondary", color_secondary)
        accent: Optional[str] = None
        if color_accent and color_accent.strip():
            accent = _normalize_hex_or_400("accent", color_accent)
        bg = _normalize_hex_or_400("background", color_bg)
        text = _normalize_hex_or_400("text", color_text)
        if audience_tone not in _AUDIENCE_TONES:
            raise HTTPException(
                status_code=400,
                detail=f"audience_tone must be one of {_AUDIENCE_TONES}.",
            )

        # Carry forward the previous logo unless a new file came in.
        existing = load_brand_kit(config.output_dir, _user_id_for(request))
        logo_path = existing.logo_path if existing is not None else None
        if logo is not None and logo.filename:
            logo_path = _save_logo(config.output_dir, logo)

        kit = BrandKit(
            logo_path=logo_path,
            color_primary=primary,
            color_secondary=secondary,
            color_accent=accent,
            color_bg=bg,
            color_text=text,
            type_display=type_display.strip() or "Inter",
            type_body=type_body.strip() or "Inter",
            type_mono=type_mono.strip() or "JetBrains Mono",
            audience_tone=audience_tone,  # type: ignore[arg-type]
        )
        save_brand_kit(config.output_dir, kit, _user_id_for(request))

        if redirect_to:
            return RedirectResponse(url=redirect_to, status_code=303)
        return _render_form(request, kit=kit, message="Brand kit saved.")

    @app.post("/onboarding/brand-kit", response_class=HTMLResponse)
    async def submit_onboarding_brand_kit(
        request: Request,
        color_primary: str = Form(...),
        color_secondary: str = Form(...),
        color_accent: str = Form(""),
        color_bg: str = Form(...),
        color_text: str = Form(...),
        type_display: str = Form("Inter"),
        type_body: str = Form("Inter"),
        type_mono: str = Form("JetBrains Mono"),
        audience_tone: str = Form("approachable"),
        logo: Optional[UploadFile] = None,
    ):
        return await _save_handler(
            request,
            color_primary=color_primary,
            color_secondary=color_secondary,
            color_accent=color_accent,
            color_bg=color_bg,
            color_text=color_text,
            type_display=type_display,
            type_body=type_body,
            type_mono=type_mono,
            audience_tone=audience_tone,
            logo=logo,
            redirect_to="/design-system/brand-kit",
        )

    @app.post("/design-system/brand-kit", response_class=HTMLResponse)
    async def submit_design_system_brand_kit(
        request: Request,
        color_primary: str = Form(...),
        color_secondary: str = Form(...),
        color_accent: str = Form(""),
        color_bg: str = Form(...),
        color_text: str = Form(...),
        type_display: str = Form("Inter"),
        type_body: str = Form("Inter"),
        type_mono: str = Form("JetBrains Mono"),
        audience_tone: str = Form("approachable"),
        logo: Optional[UploadFile] = None,
    ):
        return await _save_handler(
            request,
            color_primary=color_primary,
            color_secondary=color_secondary,
            color_accent=color_accent,
            color_bg=color_bg,
            color_text=color_text,
            type_display=type_display,
            type_body=type_body,
            type_mono=type_mono,
            audience_tone=audience_tone,
            logo=logo,
            redirect_to=None,
        )

    return {
        "max_logo_bytes": _MAX_LOGO_BYTES,
        "logo_magic": _LOGO_MAGIC,
    }
