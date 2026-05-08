"""Font picker routes — web font catalog, user uploads, and font file serving.

Three sources of fonts are exposed by these endpoints:

  - GET  /api/fonts/web      — JSON list of curated Google-hosted web fonts
  - GET  /api/fonts/user     — JSON list of user-uploaded fonts on disk
  - POST /fonts/upload       — accept .ttf/.otf/.woff2 with magic-byte check
  - GET  /user-fonts/<name>  — serve a single user-uploaded font file

System fonts are *not* served here — the browser enumerates them via
`window.queryLocalFonts()` directly inside the picker UI.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ..font_catalog import FontEntry, all_web_fonts, list_user_fonts

# Map extensions to (allowed magic-byte prefixes, content-type).
# References:
#   - TTF: starts with `\x00\x01\x00\x00` (sfnt) or `true` (legacy macOS).
#   - OTF: starts with `OTTO` (CFF-flavoured OpenType).
#   - WOFF2: starts with `wOF2`. (`wOFF` is the older WOFF1 we don't accept.)
_MAGIC_TABLE: dict[str, tuple[tuple[bytes, ...], str]] = {
    ".ttf": ((b"\x00\x01\x00\x00", b"true"), "font/ttf"),
    ".otf": ((b"OTTO",), "font/otf"),
    ".woff2": ((b"wOF2",), "font/woff2"),
}

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    """Strip path components and unsafe chars from an uploaded filename."""
    base = Path(name).name  # drop any directory parts the client put in
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("_.")
    return cleaned or "font"


def _user_fonts_dir(config) -> Path:
    """Resolve the on-disk location for user-uploaded fonts.

    Lives at `<output_dir>/../library/user_fonts/`. We don't reach into
    config for a dedicated knob — the orchestration layer has agreed on
    `library/user_fonts/` and that's stable.
    """
    base = config.output_dir.parent if config.output_dir.parent != Path("") else Path(".")
    return base / "library" / "user_fonts"


def _validate_font_bytes(filename: str, head: bytes) -> tuple[str, str]:
    """Check extension + magic bytes. Return (ext, content_type) or raise 400."""
    ext = Path(filename).suffix.lower()
    if ext not in _MAGIC_TABLE:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported extension {ext!r}. Use .ttf, .otf, or .woff2.",
        )
    magics, content_type = _MAGIC_TABLE[ext]
    if not any(head.startswith(m) for m in magics):
        raise HTTPException(
            status_code=400,
            detail=f"File does not look like a real {ext} font (magic-byte check failed).",
        )
    return ext, content_type


def register_font_routes(app, templates, _config_callable):
    """Register the font catalog + upload routes on the FastAPI app.

    Args:
        app: FastAPI instance
        templates: Jinja2Templates (unused today; passed for parity with
            other route modules and reserved for future HTML fragments).
        _config_callable: function returning a Config (matches web/app.py).
    """
    del templates  # intentionally unused; reserved for future partial returns

    @app.get("/api/fonts/web")
    def api_fonts_web():
        return {"fonts": [f.model_dump() for f in all_web_fonts()]}

    @app.get("/api/fonts/user")
    def api_fonts_user():
        config = _config_callable()
        entries = list_user_fonts(_user_fonts_dir(config))
        return {"fonts": [f.model_dump() for f in entries]}

    @app.post("/fonts/upload")
    async def page_font_upload(file: UploadFile):
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided.")

        # Read enough bytes to confirm magic, then read the rest.
        head = await file.read(16)
        ext, _content_type = _validate_font_bytes(file.filename, head)
        rest = await file.read()
        payload = head + rest

        config = _config_callable()
        target_dir = _user_fonts_dir(config)
        target_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _safe_filename(file.filename)
        # Ensure extension is preserved (and matches what we validated).
        if not safe_name.lower().endswith(ext):
            safe_name = f"{safe_name}{ext}"

        dest = target_dir / safe_name
        # If a name collision exists, append a numeric suffix until we have a fresh slot.
        if dest.exists():
            stem = dest.stem
            i = 1
            while True:
                candidate = target_dir / f"{stem}-{i}{ext}"
                if not candidate.exists():
                    dest = candidate
                    break
                i += 1

        with dest.open("wb") as f:
            f.write(payload)

        entry = FontEntry(
            name=dest.name,
            source="user",
            family=dest.stem,
            weights=[400],
            style_tags=["user-uploaded"],
            web_url=f"/user-fonts/{dest.name}",
            is_royalty_free=True,
        )
        return JSONResponse(entry.model_dump(), status_code=201)

    @app.get("/user-fonts/{filename}")
    def page_user_font(filename: str):
        # Reject anything sketchy — only a safe filename, no path traversal.
        safe = _safe_filename(filename)
        if safe != filename:
            raise HTTPException(status_code=400, detail="Invalid filename.")
        config = _config_callable()
        target_dir = _user_fonts_dir(config)
        path = target_dir / safe
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Font not found.")
        ext = path.suffix.lower()
        content_type = _MAGIC_TABLE.get(ext, ((), "application/octet-stream"))[1]
        return FileResponse(path=str(path), media_type=content_type, filename=path.name)

    return {
        "user_fonts_dir": _user_fonts_dir,
        "magic_table": _MAGIC_TABLE,
    }
