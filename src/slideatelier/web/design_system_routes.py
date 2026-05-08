"""Interactive Design System Builder routes.

Lets the user define brand colors + fonts in the browser, see a live HTML
preview, and save the result as a slideAtelier JSON template.
"""
from __future__ import annotations

from fastapi import Form, Request
from fastapi.responses import HTMLResponse

from ..template import (
    Template,
    TemplateColors,
    TemplateFonts,
    is_valid_hex,
    parse_hex,
    save_template,
)


_COLOR_FIELDS = (
    "primary",
    "accent",
    "text",
    "muted",
    "background",
    "success",
    "warning",
    "danger",
)


def _slugify(name: str) -> str:
    """Mirror the slug rule used elsewhere in web/app.py (lowercase, spaces→dashes)."""
    return name.strip().lower().replace(" ", "-")


def _list_template_filenames(templates_dir) -> list[dict]:
    """Cheap listing for the saved-templates panel — name + filename only."""
    out: list[dict] = []
    if not templates_dir.exists():
        return out
    for path in sorted(templates_dir.glob("*.json")):
        out.append({"name": path.stem, "filename": path.name})
    return out


def _error_fragment(message: str) -> HTMLResponse:
    return HTMLResponse(
        f"<div class='bg-red-50 border border-red-200 rounded p-3 text-sm text-red-800'>"
        f"{message}</div>",
        status_code=400,
    )


def register_design_system_routes(app, templates, _config_callable):
    """Registers /design-system routes on the FastAPI app.

    Args:
        app: FastAPI instance
        templates: Jinja2Templates instance from web/app.py
        _config_callable: function returning a Config (matches web/app.py's _config()).
    """

    @app.get("/design-system", response_class=HTMLResponse)
    def page_design_system(request: Request):
        config = _config_callable()
        return templates.TemplateResponse(
            request,
            "design_system.html",
            {
                "templates_list": _list_template_filenames(config.templates_dir),
                "model": config.model,
                "api_key_set": bool(config.anthropic_api_key),
            },
        )

    @app.post("/design-system/preview", response_class=HTMLResponse)
    def page_design_system_preview(
        request: Request,
        primary: str = Form("#1F3A5F"),
        accent: str = Form("#C86E3C"),
        text: str = Form("#222222"),
        muted: str = Form("#6B6B6B"),
        background: str = Form("#FFFFFF"),
        success: str = Form("#2E7D5B"),
        warning: str = Form("#C9941F"),
        danger: str = Form("#B23A3A"),
        heading_font: str = Form("Calibri"),
        body_font: str = Form("Calibri"),
    ):
        raw = {
            "primary": primary,
            "accent": accent,
            "text": text,
            "muted": muted,
            "background": background,
            "success": success,
            "warning": warning,
            "danger": danger,
        }
        for field, value in raw.items():
            if not is_valid_hex(value):
                return _error_fragment(
                    f"Invalid hex for <code>{field}</code>: <code>{value}</code>"
                )
        ctx = {field: parse_hex(value) for field, value in raw.items()}
        ctx["heading_font"] = heading_font
        ctx["body_font"] = body_font
        return templates.TemplateResponse(
            request, "partials/design_system_preview.html", ctx
        )

    @app.post("/design-system/save", response_class=HTMLResponse)
    def page_design_system_save(
        name: str = Form(...),
        description: str = Form(""),
        primary: str = Form("#1F3A5F"),
        accent: str = Form("#C86E3C"),
        text: str = Form("#222222"),
        muted: str = Form("#6B6B6B"),
        background: str = Form("#FFFFFF"),
        success: str = Form("#2E7D5B"),
        warning: str = Form("#C9941F"),
        danger: str = Form("#B23A3A"),
        heading_font: str = Form("Calibri"),
        body_font: str = Form("Calibri"),
    ):
        if not name.strip():
            return _error_fragment("Name is required.")

        raw_colors = {
            "primary": primary,
            "accent": accent,
            "text": text,
            "muted": muted,
            "background": background,
            "success": success,
            "warning": warning,
            "danger": danger,
        }
        for field, value in raw_colors.items():
            if not is_valid_hex(value):
                return _error_fragment(
                    f"Invalid hex for <code>{field}</code>: <code>{value}</code>"
                )

        config = _config_callable()
        config.templates_dir.mkdir(parents=True, exist_ok=True)
        slug = _slugify(name)
        if not slug:
            return _error_fragment("Name must contain at least one non-space character.")

        tpl = Template(
            name=name,
            description=description,
            colors=TemplateColors(**{k: parse_hex(v) for k, v in raw_colors.items()}),
            fonts=TemplateFonts(heading=heading_font, body=body_font),
        )
        out_path = config.templates_dir / f"{slug}.json"
        save_template(tpl, out_path)

        return HTMLResponse(
            "<div class='bg-emerald-50 border border-emerald-200 rounded p-3 "
            "text-sm text-emerald-900'>"
            f"✓ Saved as <code>templates/{slug}.json</code>"
            "</div>"
        )

    # Expose for tests / introspection.
    return {
        "color_fields": _COLOR_FIELDS,
    }
