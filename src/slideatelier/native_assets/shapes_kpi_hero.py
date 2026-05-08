"""KpiHero — oversized number callout with a small caption + accent anchor.

Designed to feel hero-like: one giant percentage in the primary color, one
small caption underneath, plus a thin accent rectangle/chevron as a visual
anchor. All native primitives.
"""
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Pt

from .base import AssetShape, ShapeRenderContext


class KpiHero(AssetShape):
    id = "kpi-hero"
    name = "KPI Hero"
    description = "Oversized hero metric (e.g. 42%) with caption and accent anchor."
    style_tags = ("metric", "graphics-heavy", "hero", "contemporary")
    aspect_ratio_hint = 1.6

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        # Vertical stack: accent anchor (~6%), big number (~62%), caption (~22%).
        anchor_h = int(ctx.height * 0.06)
        number_h = int(ctx.height * 0.62)
        caption_h = ctx.height - anchor_h - number_h - int(ctx.height * 0.02)

        # 1) Accent anchor (chevron when accent_treatment="fill", thin
        # rectangle for outline themes). Centered horizontally, ~22% width.
        anchor_w = int(ctx.width * 0.22)
        anchor_left = ctx.left + (ctx.width - anchor_w) // 2
        anchor_top = ctx.top

        outline_only = theme.accent_treatment == "outline" or theme.is_dark
        if outline_only:
            anchor_kind = MSO_SHAPE.RECTANGLE
        else:
            anchor_kind = MSO_SHAPE.CHEVRON if theme.corner_radius_pct == 0 else MSO_SHAPE.ROUNDED_RECTANGLE

        anchor = slide.shapes.add_shape(
            anchor_kind, anchor_left, anchor_top, anchor_w, anchor_h
        )
        anchor.fill.solid()
        if outline_only:
            anchor.fill.fore_color.rgb = palette.accent
            anchor.line.color.rgb = palette.accent
        else:
            anchor.fill.fore_color.rgb = palette.accent
            anchor.line.color.rgb = palette.accent
        anchor.line.width = theme.line_weight_emu

        # 2) Big number — placed in a wide textbox.
        number_top = ctx.top + anchor_h + int(ctx.height * 0.01)
        num_box = slide.shapes.add_textbox(
            ctx.left,
            number_top,
            ctx.width,
            number_h,
        )
        ntf = num_box.text_frame
        ntf.word_wrap = True
        ntf.margin_left = Pt(4)
        ntf.margin_right = Pt(4)
        ntf.margin_top = Pt(0)
        ntf.margin_bottom = Pt(0)
        np_ = ntf.paragraphs[0]
        np_.alignment = 2  # CENTER
        nrun = np_.add_run()
        nrun.text = "42%"
        # Hero font size: scale by number_h.
        # Approx 1pt = 12700 EMU. Take ~70% of number_h converted to pt.
        hero_pt = max(48, int((number_h / 12700.0) * 0.62))
        nrun.font.size = Pt(hero_pt)
        nrun.font.name = theme.typography.heading
        nrun.font.bold = True
        nrun.font.color.rgb = palette.primary

        # 3) Small caption.
        cap_top = number_top + number_h + int(ctx.height * 0.01)
        cap_box = slide.shapes.add_textbox(
            ctx.left,
            cap_top,
            ctx.width,
            caption_h,
        )
        ctf = cap_box.text_frame
        ctf.word_wrap = True
        ctf.margin_left = Pt(4)
        ctf.margin_right = Pt(4)
        cp = ctf.paragraphs[0]
        cp.alignment = 2  # CENTER
        crun = cp.add_run()
        crun.text = "Conversion lift YoY"
        crun.font.size = Pt(theme.typography.heading_size_pt)
        crun.font.name = theme.typography.body
        crun.font.color.rgb = palette.muted
