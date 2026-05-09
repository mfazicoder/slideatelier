"""Web Deck renderer — turns a SlideDeck into a complete inline-HTML+SVG
document that:
  - renders text natively (so it reflows + stays selectable)
  - renders extras (charts, matrices, library assets) as inline <svg> via each
    shape's render_svg() method
  - respects per-block bbox + style overrides (Sprint Y) by porting the
    JSONRenderer's _block_rect / _apply_block_style_textbox logic to CSS
  - exposes theme tokens as CSS custom properties on each <section> so
    Sprint K's brand tokens slot in without rebuilding the page

Output is a single self-contained <html> document. No external assets except
Google Fonts. The "deck" is a vertically-scrolling stack of 16:9 sections;
keyboard nav (arrows / space / esc / f / p) drives a presenter mode overlay.

Hard rule: native primitives only — when a shape exposes render_svg() we use
its output verbatim; otherwise we fall back to a labelled placeholder rect so
the rest of the deck still renders cleanly while parallel agents add the
method to the remaining shape classes.
"""
from __future__ import annotations

import html
from typing import Any

from .models import Slide, SlideDeck, SlideExtra
from .template import Template


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class WebRenderer:
    """Render a SlideDeck as a self-contained HTML+SVG document.

    Mirrors the JSONRenderer's per-block bbox/style behaviour but emits CSS
    instead of EMU. Theme colours from the loaded Template become CSS custom
    properties on each <section> so a future brand-tokens layer (Sprint K)
    can hot-swap them.
    """

    def __init__(self, template: Template, catalog=None):
        self.tpl = template
        self.catalog = catalog

    # -- entry point ------------------------------------------------------

    def render_deck_html(self, deck: SlideDeck, *, slug: str) -> str:
        sections = "\n".join(
            self._render_slide_section(slide, idx, deck)
            for idx, slide in enumerate(deck.slides)
        )
        css = _GLOBAL_CSS
        js = _PRESENTER_JS
        title_safe = html.escape(deck.title or "Deck")
        return (
            "<!doctype html>\n"
            "<html lang=\"en\">\n"
            "<head>\n"
            "  <meta charset=\"utf-8\">\n"
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"  <title>{title_safe}</title>\n"
            "  <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">\n"
            "  <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>\n"
            "  <link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap\" rel=\"stylesheet\">\n"
            f"  <style>{css}</style>\n"
            "</head>\n"
            f"<body data-slug=\"{html.escape(slug)}\">\n"
            "  <div class=\"deck-toolbar\" aria-hidden=\"true\">\n"
            "    <span class=\"hint\">← / → · space · f · p · esc</span>\n"
            "    <span class=\"counter\" id=\"slide-counter\">1 / "
            f"{len(deck.slides)}</span>\n"
            "  </div>\n"
            "  <main class=\"deck\" tabindex=\"0\">\n"
            f"{sections}\n"
            "  </main>\n"
            "  <aside class=\"presenter-overlay\" id=\"presenter-overlay\" hidden>\n"
            "    <h2>Speaker notes</h2>\n"
            "    <div class=\"presenter-notes\" id=\"presenter-notes\"></div>\n"
            "    <p class=\"presenter-hint\">Press <kbd>p</kbd> to close.</p>\n"
            "  </aside>\n"
            f"  <script>{js}</script>\n"
            "</body>\n"
            "</html>\n"
        )

    # -- per-slide --------------------------------------------------------

    def _render_slide_section(self, slide: Slide, idx: int, deck: SlideDeck) -> str:
        css_vars = self._theme_css_vars()
        notes = html.escape(slide.speaker_notes or "", quote=True)
        layout = slide.layout
        body_html = self._render_blocks(slide, deck, layout)
        extras_html = self._render_extras(slide)
        return (
            f"    <section class=\"slide layout-{html.escape(layout)}\" "
            f"id=\"slide-{idx}\" "
            f"data-slide-index=\"{idx}\" "
            f"data-speaker-notes=\"{notes}\" "
            f"style=\"{css_vars}\">\n"
            f"      <div class=\"slide-frame\">\n"
            f"{body_html}\n"
            f"{extras_html}\n"
            f"      </div>\n"
            "    </section>"
        )

    def _render_blocks(self, slide: Slide, deck: SlideDeck, layout: str) -> str:
        """Per-layout HTML: title / strap / body. Honours block_bbox + block_style."""
        out: list[str] = []
        # On the title slide the deck's title/subtitle displace the slide's own.
        if layout == "title":
            title_text = deck.title or slide.title
            strap_text = deck.subtitle or slide.strap
        else:
            title_text = slide.title
            strap_text = slide.strap

        # Title
        title_default = _layout_default_rect(layout, "title")
        out.append(self._render_block(
            slide, "title", title_text, title_default,
            extra_class=f"block-title size-{layout}-title",
        ))

        # Strap
        if strap_text:
            strap_default = _layout_default_rect(layout, "strap")
            out.append(self._render_block(
                slide, "strap", strap_text, strap_default,
                extra_class=f"block-strap size-{layout}-strap",
            ))

        # Body / body_left / body_right
        if layout == "two_column":
            if slide.body_left:
                out.append(self._render_bullets(
                    slide, "body_left", slide.body_left,
                    _layout_default_rect(layout, "body_left"),
                ))
            if slide.body_right:
                out.append(self._render_bullets(
                    slide, "body_right", slide.body_right,
                    _layout_default_rect(layout, "body_right"),
                ))
            if slide.body:
                # rare but supported via explicit block_bbox['body']
                out.append(self._render_bullets(
                    slide, "body", slide.body,
                    _layout_default_rect(layout, "body"),
                ))
        elif layout == "key_takeaway":
            if slide.body:
                # key-takeaway joins body items with em-dashes (mirrors JSONRenderer)
                joined = " — ".join(slide.body)
                out.append(self._render_block(
                    slide, "body", joined,
                    _layout_default_rect(layout, "body"),
                    extra_class="block-body block-body-takeaway",
                ))
        else:
            if slide.body:
                marker = "→" if layout == "closing" else "•"
                out.append(self._render_bullets(
                    slide, "body", slide.body,
                    _layout_default_rect(layout, "body"),
                    marker=marker,
                ))

        return "\n".join(out)

    def _render_block(
        self,
        slide: Slide,
        name: str,
        text: str,
        default_rect: tuple[float, float, float, float] | None,
        *,
        extra_class: str = "",
    ) -> str:
        rect = self._block_rect(slide, name) or default_rect
        style_decl = self._block_style_css(slide, name)
        positioning = _positioning_css(rect)
        klass = f"block {extra_class}".strip()
        text_safe = html.escape(text or "")
        return (
            f'        <div class="{klass}" data-block="{name}" '
            f'style="{positioning}{style_decl}">'
            f'{text_safe}</div>'
        )

    def _render_bullets(
        self,
        slide: Slide,
        name: str,
        items: list[str],
        default_rect: tuple[float, float, float, float] | None,
        *,
        marker: str = "•",
    ) -> str:
        rect = self._block_rect(slide, name) or default_rect
        style_decl = self._block_style_css(slide, name)
        positioning = _positioning_css(rect)
        lis = "\n".join(
            f'          <li><span class="bullet-marker">{html.escape(marker)}</span>'
            f'<span class="bullet-text">{html.escape(item)}</span></li>'
            for item in items
        )
        return (
            f'        <ul class="block block-bullets" data-block="{name}" '
            f'style="{positioning}{style_decl}">\n{lis}\n        </ul>'
        )

    # -- per-block plumbing ----------------------------------------------

    def _block_rect(
        self, slide: Slide, name: str
    ) -> tuple[float, float, float, float] | None:
        """Read normalised 0..1 bbox from slide.block_bbox; return None if absent."""
        bb_map = getattr(slide, "block_bbox", None) or {}
        bb = bb_map.get(name)
        if not bb:
            return None
        try:
            left = float(bb["left"])
            top = float(bb["top"])
            width = float(bb["width"])
            height = float(bb["height"])
        except (KeyError, TypeError, ValueError):
            return None
        # Clamp so a misbehaving client can't push the block off-canvas.
        left = max(0.0, min(1.0, left))
        top = max(0.0, min(1.0, top))
        width = max(0.01, min(1.0 - left, width))
        height = max(0.01, min(1.0 - top, height))
        return (left, top, width, height)

    def _block_style_css(self, slide: Slide, name: str) -> str:
        bs_map = getattr(slide, "block_style", None) or {}
        s = bs_map.get(name) or {}
        if not isinstance(s, dict):
            return ""
        parts: list[str] = []
        if s.get("font_family"):
            parts.append(f"font-family:'{_css_escape(str(s['font_family']))}',Inter,sans-serif")
        if s.get("font_size"):
            try:
                parts.append(f"font-size:{int(s['font_size'])}pt")
            except (TypeError, ValueError):
                pass
        if s.get("color"):
            parts.append(f"color:{_css_escape(str(s['color']))}")
        if s.get("bold"):
            parts.append("font-weight:700")
        if s.get("italic"):
            parts.append("font-style:italic")
        align = s.get("align")
        if align in ("left", "center", "right", "justify"):
            parts.append(f"text-align:{align}")
        return ";".join(parts) + (";" if parts else "")

    def _theme_css_vars(self) -> str:
        c = self.tpl.colors
        f = self.tpl.fonts
        return (
            f"--brand-primary:{c.primary};"
            f"--brand-accent:{c.accent};"
            f"--brand-text:{c.text};"
            f"--brand-muted:{c.muted};"
            f"--brand-bg:{c.background};"
            f"--brand-success:{c.success};"
            f"--brand-warning:{c.warning};"
            f"--brand-danger:{c.danger};"
            f"--type-display:'{_css_escape(f.heading)}',Inter,sans-serif;"
            f"--type-body:'{_css_escape(f.body)}',Inter,sans-serif;"
        )

    # -- extras -----------------------------------------------------------

    def _render_extras(self, slide: Slide) -> str:
        if not slide.extras:
            return ""
        out: list[str] = []
        for ex in slide.extras:
            out.append(self._render_extra(ex))
        return "\n".join(p for p in out if p)

    def _render_extra(self, extra: SlideExtra) -> str:
        # Resolve target rect (% in 0..1 of slide canvas)
        rect = _extra_rect(extra)
        positioning = _positioning_css(rect)
        # Determine pixel dims for SVG canvas — viewBox absorbs scaling so just
        # pick stable nominal dimensions roughly matching the natural extra size.
        # 16:9 slide @ 1280x720 → target rect in px:
        w_px = max(80, int(1280 * rect[2]))
        h_px = max(60, int(720 * rect[3]))
        svg_inner = self._extra_svg(extra, w_px, h_px)
        return (
            f'        <div class="extra extra-{html.escape(extra.type)}" '
            f'data-extra-type="{html.escape(extra.type)}" '
            f'style="{positioning}">{svg_inner}</div>'
        )

    def _extra_svg(self, extra: SlideExtra, w_px: int, h_px: int) -> str:
        """Resolve a render_svg() impl from registry / catalog if available;
        otherwise fall back to a labelled placeholder. NEVER raise — extras
        are decorative and a broken one shouldn't kill the whole page.

        Special case: `library_asset` extras reference an external .pptx slide
        from the user's library (e.g. 'pyramid-diagram-powerpoint/...'). Those
        aren't native AssetShapes — they have no render_svg() — but the
        wireframe and library page already serve a PNG thumbnail at
        /thumbnails/<asset_id>.png (rendered by LibreOffice during catalog
        build). We embed that thumbnail as an <img> wrapped in an SVG so the
        sizing math upstream still works.
        """
        cfg = extra.config or {}
        if extra.type == "library_asset":
            ref = cfg.get("asset_ref") or ""
            if ref:
                # Mirror the same URL pattern used by the wireframe slide-card:
                # /thumbnails/<asset_ref-with-/-replaced-by-__>.png
                thumb_path = "/thumbnails/" + ref.replace("/", "__") + ".png"
                # Use foreignObject so the <img> inherits SVG sizing semantics
                # without us having to re-implement object-fit. xmlns is
                # required on the inner <div> for SVG XHTML inclusion to be
                # well-formed.
                return (
                    f'<svg xmlns="http://www.w3.org/2000/svg" '
                    f'width="{w_px}" height="{h_px}" '
                    f'viewBox="0 0 {w_px} {h_px}">\n'
                    f'  <foreignObject x="0" y="0" width="{w_px}" height="{h_px}">\n'
                    f'    <div xmlns="http://www.w3.org/1999/xhtml" '
                    f'style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;">\n'
                    f'      <img src="{html.escape(thumb_path)}" '
                    f'alt="{html.escape(ref)}" '
                    f'style="max-width:100%;max-height:100%;object-fit:contain;" '
                    f'loading="lazy" />\n'
                    f'    </div>\n'
                    f'  </foreignObject>\n'
                    f'</svg>'
                )

        shape = self._resolve_shape_for_extra(extra)
        if shape is not None:
            render_svg = getattr(shape, "render_svg", None)
            if callable(render_svg):
                try:
                    ctx = self._build_svg_ctx(w_px, h_px)
                    return render_svg(ctx, w_px, h_px)
                except Exception as e:  # noqa: BLE001
                    return _placeholder_svg(
                        w_px, h_px, label=f"shape error: {extra.type}", detail=str(e)
                    )
        # Fallback placeholder.
        label = extra.type
        ref = cfg.get("asset_ref") or cfg.get("shape_id") or ""
        return _placeholder_svg(w_px, h_px, label=label, detail=str(ref))

    def _resolve_shape_for_extra(self, extra: SlideExtra) -> Any | None:
        """Best-effort resolution: try the global native registry first
        (chart_bar, chart_donut, matrix_2x2 etc.), then ask the catalog if it
        knows. Each lookup is wrapped because parallel agents may not have
        registered their shape yet — we degrade to a placeholder rather than
        crash."""
        # Try native registry by extra type or by extra.config.shape_id.
        try:
            from .native_assets.registry import get_registry  # local import
            registry = get_registry()
            cfg = extra.config or {}
            shape_id = cfg.get("shape_id") or extra.type
            shape = registry.shape(shape_id) if registry else None
            if shape is not None:
                return shape
        except Exception:  # noqa: BLE001
            pass
        return None

    def _build_svg_ctx(self, w_px: int, h_px: int) -> Any:
        """Build a ShapeRenderContext-shaped object the shapes can consume.

        Shapes were originally written for python-pptx EMU coordinates; for SVG
        they expect a context exposing .palette / .theme / width / height.
        We pass through whatever the registry gives us, falling back to a
        light dataclass if needed."""
        try:
            from .native_assets.base import Palette, ShapeRenderContext, Theme, Typography
            c = self.tpl.colors
            palette = Palette.from_hex(
                primary=c.primary, accent=c.accent, text=c.text, muted=c.muted,
                background=c.background, success=c.success, warning=c.warning,
                danger=c.danger,
            )
            theme = Theme(
                id="web", name="Web", description="WebRenderer-synthesised theme",
                palette=palette, typography=Typography(
                    heading=self.tpl.fonts.heading, body=self.tpl.fonts.body,
                ),
            )
            # SVG renderers expect width/height in pixels; we pass them as such
            # via `width`/`height`. If a shape really wants EMU it can scale.
            return ShapeRenderContext(left=0, top=0, width=w_px, height=h_px, theme=theme)
        except Exception:  # noqa: BLE001
            class _FallbackCtx:
                width = w_px
                height = h_px
            return _FallbackCtx()


# ---------------------------------------------------------------------------
# Layout defaults — port of JSONRenderer's hard-coded inch rectangles into
# normalised 0..1 fractions of a 13.333" x 7.5" canvas. Only blocks that have
# a specific layout-driven default appear here; everything else falls through
# to None (i.e. no positioning, normal flex flow inside .slide-frame).
# ---------------------------------------------------------------------------

_SLIDE_W = 13.333
_SLIDE_H = 7.5


def _frac(left_in: float, top_in: float, w_in: float, h_in: float):
    return (left_in / _SLIDE_W, top_in / _SLIDE_H, w_in / _SLIDE_W, h_in / _SLIDE_H)


_LAYOUT_DEFAULTS = {
    "title": {
        "title": _frac(1.0, 2.8, 11.3, 1.5),
        "strap": _frac(1.0, 4.4, 11.3, 0.8),
    },
    "section_divider": {
        "title": _frac(1.0, 3.2, 11.3, 1.2),
        "strap": _frac(1.0, 4.6, 11.3, 0.7),
    },
    "content": {
        "title": _frac(0.6, 0.5, 12.1, 0.8),
        "strap": _frac(0.6, 1.3, 12.1, 0.5),
        "body": _frac(0.6, 2.0, 12.1, 5.2),
    },
    "bullet_list": {
        "title": _frac(0.6, 0.5, 12.1, 0.8),
        "strap": _frac(0.6, 1.3, 12.1, 0.5),
        "body": _frac(0.6, 2.0, 12.1, 5.2),
    },
    "closing": {
        "title": _frac(0.6, 0.5, 12.1, 0.8),
        "strap": _frac(0.6, 1.3, 12.1, 0.5),
        "body": _frac(0.6, 2.0, 12.1, 5.2),
    },
    "two_column": {
        "title": _frac(0.6, 0.5, 12.1, 0.8),
        "strap": _frac(0.6, 1.3, 12.1, 0.5),
        "body_left": _frac(0.6, 2.0, 5.9, 5.2),
        "body_right": _frac(6.85, 2.0, 5.9, 5.2),
    },
    "key_takeaway": {
        "title": _frac(1.5, 2.5, 10.3, 2.5),
        "strap": _frac(1.5, 4.7, 10.3, 0.6),
        "body": _frac(1.5, 5.0, 10.3, 1.5),
    },
}


def _layout_default_rect(layout: str, name: str):
    return _LAYOUT_DEFAULTS.get(layout, {}).get(name)


def _extra_rect(extra: SlideExtra) -> tuple[float, float, float, float]:
    cfg = extra.config or {}
    bbox = cfg.get("bbox") if isinstance(cfg, dict) else None
    if isinstance(bbox, dict):
        try:
            left = max(0.0, min(1.0, float(bbox.get("left", 0.0))))
            top = max(0.0, min(1.0, float(bbox.get("top", 0.0))))
            width = max(0.01, min(1.0 - left, float(bbox.get("width", 0.0))))
            height = max(0.01, min(1.0 - top, float(bbox.get("height", 0.0))))
            return (left, top, width, height)
        except (TypeError, ValueError):
            pass
    pos = getattr(extra, "position", "right")
    if pos == "right":
        return (0.50, 0.18, 0.48, 0.77)
    if pos == "left":
        return (0.02, 0.18, 0.45, 0.77)
    if pos == "below":
        return (0.04, 0.55, 0.92, 0.40)
    return (0.15, 0.27, 0.70, 0.60)


def _positioning_css(rect: tuple[float, float, float, float] | None) -> str:
    if rect is None:
        return ""
    left, top, width, height = rect
    return (
        f"position:absolute;"
        f"left:{left*100:.4f}%;top:{top*100:.4f}%;"
        f"width:{width*100:.4f}%;height:{height*100:.4f}%;"
    )


def _css_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _placeholder_svg(w: int, h: int, *, label: str, detail: str = "") -> str:
    label_safe = html.escape(label or "shape")
    detail_safe = html.escape(detail or "")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="100%" height="100%" preserveAspectRatio="xMidYMid meet" '
        f'class="extra-placeholder">'
        f'<rect width="100%" height="100%" fill="#eee" stroke="#bbb" '
        f'stroke-dasharray="6 4"/>'
        f'<text x="50%" y="48%" text-anchor="middle" font-family="Inter,sans-serif" '
        f'font-size="14" fill="#666">shape: {label_safe}</text>'
        + (
            f'<text x="50%" y="62%" text-anchor="middle" font-family="Inter,sans-serif" '
            f'font-size="11" fill="#999">{detail_safe}</text>'
            if detail_safe else ""
        )
        + '</svg>'
    )


# ---------------------------------------------------------------------------
# Static CSS + JS bundles — kept inline so the published deck is one file.
# ---------------------------------------------------------------------------

_GLOBAL_CSS = """
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:#0f172a;
  color:var(--brand-text,#222);
  font-family:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;
  -webkit-font-smoothing:antialiased;
}
body.presenter{cursor:none}
.deck{display:flex;flex-direction:column;align-items:center;gap:24px;padding:24px 0;outline:none}
.slide{
  position:relative;
  width:min(95vw,1280px);
  aspect-ratio:16/9;
  background:var(--brand-bg,#fff);
  border-radius:8px;
  box-shadow:0 6px 24px rgba(0,0,0,0.25);
  overflow:hidden;
  scroll-margin-top:24px;
  font-family:var(--type-body,Inter,sans-serif);
  color:var(--brand-text,#222);
}
.slide-frame{position:relative;width:100%;height:100%}
.block{
  position:absolute;
  font-family:var(--type-body,Inter,sans-serif);
  color:var(--brand-text,#222);
  line-height:1.3;
}
.block-title{
  font-family:var(--type-display,Inter,sans-serif);
  font-weight:700;
  color:var(--brand-primary,#1F3A5F);
  font-size:1.6em;
  letter-spacing:-0.01em;
}
.size-title-title{font-size:2.6em;line-height:1.1}
.size-section_divider-title{font-size:2.2em;line-height:1.15}
.size-key_takeaway-title{font-size:2em;text-align:center;line-height:1.2}
.block-strap{
  color:var(--brand-muted,#6B6B6B);
  font-size:0.9em;
}
.size-key_takeaway-strap{text-align:center}
.block-body,.block-bullets{font-size:0.95em;color:var(--brand-text,#222)}
.block-bullets{list-style:none;margin:0;padding:0}
.block-bullets li{display:flex;gap:0.5em;margin-bottom:0.4em;align-items:baseline}
.bullet-marker{color:var(--brand-accent,#C86E3C);font-weight:700;flex-shrink:0}
.bullet-text{flex:1}
.block-body-takeaway{
  text-align:center;font-size:1.1em;color:var(--brand-muted,#6B6B6B);
}
.layout-key_takeaway .slide-frame{display:flex;align-items:center;justify-content:center}
.extra{position:absolute;display:flex;align-items:center;justify-content:center}
.extra svg{width:100%;height:100%;max-width:100%;max-height:100%}

.deck-toolbar{
  position:fixed;top:12px;right:16px;z-index:30;
  display:flex;gap:14px;align-items:center;
  font-size:11px;color:#cbd5e1;
  background:rgba(15,23,42,0.7);
  padding:6px 10px;border-radius:6px;
  font-family:'Inter',sans-serif;
  pointer-events:none;
}
.deck-toolbar .counter{font-variant-numeric:tabular-nums}

.presenter-overlay{
  position:fixed;left:0;right:0;bottom:0;
  background:rgba(15,23,42,0.95);color:#f1f5f9;
  padding:18px 24px;border-top:1px solid rgba(255,255,255,0.08);
  font-family:'Inter',sans-serif;font-size:14px;line-height:1.5;
  max-height:35vh;overflow-y:auto;z-index:40;
}
.presenter-overlay h2{margin:0 0 6px 0;font-size:11px;letter-spacing:0.08em;
  text-transform:uppercase;color:#94a3b8;font-weight:600}
.presenter-overlay .presenter-notes{white-space:pre-wrap}
.presenter-overlay .presenter-hint{margin:8px 0 0 0;color:#64748b;font-size:11px}
.presenter-overlay kbd{
  background:#334155;color:#f1f5f9;padding:1px 6px;border-radius:3px;
  font-family:ui-monospace,monospace;font-size:11px;
}

body.presenter-mode .deck{padding:0}
body.presenter-mode .slide{
  width:100vw;height:100vh;border-radius:0;
  box-shadow:none;
}
body.presenter-mode .deck-toolbar{display:none}

@media print{
  body{background:#fff}
  .deck{padding:0;gap:0}
  .slide{box-shadow:none;page-break-after:always;border-radius:0;width:100%}
  .deck-toolbar,.presenter-overlay{display:none}
}
"""


_PRESENTER_JS = """
(function(){
  const slides=Array.from(document.querySelectorAll('.slide'));
  if(!slides.length)return;
  const counter=document.getElementById('slide-counter');
  const overlay=document.getElementById('presenter-overlay');
  const notesEl=document.getElementById('presenter-notes');
  let idx=0;
  let presenter=false;

  function clamp(i){return Math.max(0,Math.min(slides.length-1,i));}
  function go(i){
    idx=clamp(i);
    slides[idx].scrollIntoView({behavior:'smooth',block:'start'});
    if(counter)counter.textContent=(idx+1)+' / '+slides.length;
    if(presenter)refreshNotes();
  }
  function refreshNotes(){
    if(!notesEl)return;
    const n=slides[idx].getAttribute('data-speaker-notes')||'';
    notesEl.textContent=n||'(no speaker notes)';
  }
  function togglePresenter(){
    presenter=!presenter;
    document.body.classList.toggle('presenter-mode',presenter);
    if(overlay){
      overlay.hidden=!presenter;
      if(presenter)refreshNotes();
    }
  }
  function toggleFullscreen(){
    if(!document.fullscreenElement){
      (document.documentElement.requestFullscreen||(()=>{}))();
    }else{
      (document.exitFullscreen||(()=>{}))();
    }
  }
  // Hash deep-link (#slide-3)
  const hash=window.location.hash;
  if(hash&&hash.indexOf('slide-')!==-1){
    const m=hash.match(/slide-(\\d+)/);
    if(m){idx=clamp(parseInt(m[1],10));}
  }
  go(idx);

  document.addEventListener('keydown',function(e){
    if(e.target&&['INPUT','TEXTAREA'].indexOf(e.target.tagName)>=0)return;
    const k=e.key;
    if(k==='ArrowRight'||k==='PageDown'||k===' '){
      e.preventDefault();go(idx+1);
    }else if(k==='ArrowLeft'||k==='PageUp'||(k==='Backspace')){
      e.preventDefault();go(idx-1);
    }else if(e.shiftKey&&(k==='ArrowDown'||k==='ArrowUp')){
      e.preventDefault();go(idx+(k==='ArrowDown'?1:-1));
    }else if(k==='Escape'){
      if(presenter)togglePresenter();
    }else if(k==='f'||k==='F'){
      toggleFullscreen();
    }else if(k==='p'||k==='P'){
      togglePresenter();
    }else if(k==='Home'){
      e.preventDefault();go(0);
    }else if(k==='End'){
      e.preventDefault();go(slides.length-1);
    }
  });

  // Keep counter in sync as user scrolls manually.
  const io=new IntersectionObserver(function(entries){
    entries.forEach(function(en){
      if(en.isIntersecting&&en.intersectionRatio>0.5){
        const i=parseInt(en.target.getAttribute('data-slide-index'),10);
        if(!isNaN(i)){idx=i;
          if(counter)counter.textContent=(idx+1)+' / '+slides.length;
          if(presenter)refreshNotes();
        }
      }
    });
  },{threshold:[0.5]});
  slides.forEach(function(s){io.observe(s);});
})();
"""
