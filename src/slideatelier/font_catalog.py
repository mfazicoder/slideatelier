"""Font catalog for slideAtelier.

Three sources of fonts power the picker:
  1. **System** — fonts the user's browser reports installed locally
     (detected client-side via `window.queryLocalFonts()` in Chromium).
  2. **Web** — a curated, hard-coded catalog of contemporary, royalty-free
     fonts available from Google Fonts. Loaded on demand by the browser.
  3. **User** — `.ttf` / `.otf` / `.woff2` files uploaded by the user;
     stored under `library/user_fonts/` and served at `/user-fonts/<name>`.

This module is the single source of truth for the curated web list and for
turning files in `library/user_fonts/` into `FontEntry` objects. The web routes
in `web/font_routes.py` consume these. System fonts never live on the server
side — they're enumerated by the browser and rendered into the picker UI
client-side.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

FontSource = Literal["system", "web", "user"]


class FontEntry(BaseModel):
    """One font available to the picker, regardless of origin."""

    name: str
    source: FontSource
    family: str
    weights: list[int] = []
    style_tags: list[str] = []
    web_url: str | None = None
    is_royalty_free: bool = True


_DEFAULT_WEIGHTS: list[int] = [400, 500, 600, 700]


def _google_url(name: str) -> str:
    """Build a Google Fonts CSS2 URL for the standard weight ladder."""
    fam = name.replace(" ", "+")
    return (
        f"https://fonts.googleapis.com/css2?family={fam}"
        f":wght@400;500;600;700&display=swap"
    )


def _web(name: str, category: str) -> FontEntry:
    """Helper: build a FontEntry for a Google Fonts curated entry."""
    return FontEntry(
        name=name,
        source="web",
        family=name,
        weights=list(_DEFAULT_WEIGHTS),
        style_tags=[category],
        web_url=_google_url(name),
        is_royalty_free=True,
    )


# Categories used:
#   - "sans-serif"  modern grotesques, geometric sans, humanist sans
#   - "serif"       text serifs, transitional, slab, display serif
#   - "mono"        monospace
#   - "display"     condensed / display / decorative
_CATALOG_RAW: list[tuple[str, str]] = [
    # --- Sans-serif workhorses (modern professional) ------------------------
    ("Inter", "sans-serif"),
    ("DM Sans", "sans-serif"),
    ("IBM Plex Sans", "sans-serif"),
    ("Roboto", "sans-serif"),
    ("Open Sans", "sans-serif"),
    ("Lato", "sans-serif"),
    ("Source Sans 3", "sans-serif"),
    ("Nunito", "sans-serif"),
    ("Nunito Sans", "sans-serif"),
    ("Manrope", "sans-serif"),
    ("Plus Jakarta Sans", "sans-serif"),
    ("Outfit", "sans-serif"),
    ("Sora", "sans-serif"),
    ("Space Grotesk", "sans-serif"),
    ("Karla", "sans-serif"),
    ("Public Sans", "sans-serif"),
    ("Work Sans", "sans-serif"),
    ("Mulish", "sans-serif"),
    ("Geist", "sans-serif"),
    ("Bricolage Grotesque", "sans-serif"),
    ("Onest", "sans-serif"),
    ("Albert Sans", "sans-serif"),
    ("Figtree", "sans-serif"),
    ("Hanken Grotesk", "sans-serif"),
    ("Anek Latin", "sans-serif"),
    ("Archivo", "sans-serif"),
    ("Be Vietnam Pro", "sans-serif"),
    ("Cabin", "sans-serif"),
    ("Catamaran", "sans-serif"),
    ("Encode Sans", "sans-serif"),
    ("Exo 2", "sans-serif"),
    ("Josefin Sans", "sans-serif"),
    ("Kanit", "sans-serif"),
    ("Kumbh Sans", "sans-serif"),
    ("Lexend", "sans-serif"),
    ("Libre Franklin", "sans-serif"),
    ("Maven Pro", "sans-serif"),
    ("Merriweather Sans", "sans-serif"),
    ("Montserrat", "sans-serif"),
    ("Mukta", "sans-serif"),
    ("Noto Sans", "sans-serif"),
    ("Overpass", "sans-serif"),
    ("Oxygen", "sans-serif"),
    ("PT Sans", "sans-serif"),
    ("Poppins", "sans-serif"),
    ("Prompt", "sans-serif"),
    ("Quicksand", "sans-serif"),
    ("Raleway", "sans-serif"),
    ("Red Hat Display", "sans-serif"),
    ("Red Hat Text", "sans-serif"),
    ("Rubik", "sans-serif"),
    ("Saira", "sans-serif"),
    ("Schibsted Grotesk", "sans-serif"),
    ("Sen", "sans-serif"),
    ("Tenor Sans", "sans-serif"),
    ("Titillium Web", "sans-serif"),
    ("Ubuntu", "sans-serif"),
    ("Urbanist", "sans-serif"),
    ("Yantramanav", "sans-serif"),
    # --- Serifs (editorial / consulting / academic) -------------------------
    ("IBM Plex Serif", "serif"),
    ("Roboto Slab", "serif"),
    ("Source Serif 4", "serif"),
    ("Fraunces", "serif"),
    ("Playfair Display", "serif"),
    ("Crimson Text", "serif"),
    ("Crimson Pro", "serif"),
    ("Lora", "serif"),
    ("Merriweather", "serif"),
    ("EB Garamond", "serif"),
    ("Cormorant Garamond", "serif"),
    ("Cardo", "serif"),
    ("Libre Baskerville", "serif"),
    ("Libre Caslon Text", "serif"),
    ("DM Serif Display", "serif"),
    ("DM Serif Text", "serif"),
    ("Noto Serif", "serif"),
    ("PT Serif", "serif"),
    ("Playfair", "serif"),
    ("Spectral", "serif"),
    ("Tinos", "serif"),
    ("Zilla Slab", "serif"),
    # --- Monospace ----------------------------------------------------------
    ("IBM Plex Mono", "mono"),
    ("JetBrains Mono", "mono"),
    ("Source Code Pro", "mono"),
    ("Geist Mono", "mono"),
    ("DM Mono", "mono"),
    ("Inconsolata", "mono"),
    ("Red Hat Mono", "mono"),
    ("Roboto Mono", "mono"),
    ("Space Mono", "mono"),
    ("Ubuntu Mono", "mono"),
    # --- Display / condensed -----------------------------------------------
    ("Oswald", "display"),
    ("Roboto Condensed", "display"),
    ("Saira Condensed", "display"),
    ("Syne", "display"),
    ("Bebas Neue", "display"),
    ("Abril Fatface", "display"),
    ("Anton", "display"),
    ("Barlow", "sans-serif"),
    ("Barlow Condensed", "display"),
]


WEB_FONTS: list[FontEntry] = [_web(name, cat) for name, cat in _CATALOG_RAW]


def all_web_fonts() -> list[FontEntry]:
    """Return the curated catalog of web fonts (Google Fonts)."""
    return list(WEB_FONTS)


# ---------------------------------------------------------------------------
# User-uploaded font enumeration
# ---------------------------------------------------------------------------

_USER_FONT_EXTS = {".ttf", ".otf", ".woff2"}


def list_user_fonts(user_fonts_dir: Path) -> list[FontEntry]:
    """Scan `user_fonts_dir` for `.ttf`/`.otf`/`.woff2` and return entries.

    Returns an empty list if the directory does not exist (we don't create it
    here — that's the upload route's job).
    """
    if not user_fonts_dir.exists() or not user_fonts_dir.is_dir():
        return []

    out: list[FontEntry] = []
    for path in sorted(user_fonts_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _USER_FONT_EXTS:
            continue
        family = path.stem  # "MyBrand-Bold" → "MyBrand-Bold"; users name as they wish
        out.append(
            FontEntry(
                name=path.name,
                source="user",
                family=family,
                weights=[400],
                style_tags=["user-uploaded"],
                web_url=f"/user-fonts/{path.name}",
                is_royalty_free=True,  # user owns it; we treat as free for them
            )
        )
    return out
