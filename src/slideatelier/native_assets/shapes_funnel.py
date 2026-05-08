"""Funnel — a 4-stage narrowing funnel diagram.

Each stage is rendered as a native MSO_SHAPE.TRAPEZOID primitive (or
ROUNDED_RECTANGLE / RECTANGLE fallback when corner_radius_pct demands it).
The four bars are stacked vertically, progressively narrower, centered, with
captions to the right.
"""
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Pt

from .base import AssetShape, ShapeRenderContext


# Stage data: (label, caption)
_STAGES = [
    ("Stage 1", "Awareness"),
    ("Stage 2", "Consideration"),
    ("Stage 3", "Decision"),
    ("Stage 4", "Action"),
]
_WIDTH_PCTS = [1.00, 0.80, 0.60, 0.40]


class Funnel(AssetShape):
    id = "funnel"
    name = "Funnel"
    description = "A 4-stage narrowing funnel for marketing/conversion frameworks (Awareness → Action)."
    style_tags = ("process", "narrowing", "consulting", "marketing")
    aspect_ratio_hint = 1.2

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        n_stages = len(_STAGES)

        # Reserve ~32% of the bounding rect on the right side for captions.
        caption_w = int(ctx.width * 0.30)
        funnel_area_w = ctx.width - caption_w
        funnel_left = ctx.left
        funnel_top = ctx.top

        # Vertical layout — small gap between stages.
        gap_emu = int(ctx.height * 0.02)
        total_gap = gap_emu * (n_stages - 1)
        stage_h = (ctx.height - total_gap) // n_stages

        # Decide shape kind. TRAPEZOID gives the canonical funnel look but
        # python-pptx's TRAPEZOID renders narrower at top, wider at bottom — for
        # a funnel we want narrowing top→bottom, so we instead use stacked
        # rectangles whose widths shrink. (TRAPEZOID is non-mirrorable without
        # rotation and rotation breaks text orientation.) Rounded variant when
        # the theme asks for it.
        rect_kind = (
            MSO_SHAPE.ROUNDED_RECTANGLE
            if theme.corner_radius_pct > 0
            else MSO_SHAPE.RECTANGLE
        )

        # Choose fills/lines per accent_treatment + dark mode.
        # For "fill": gradient-ish step from primary (top) → accent (bottom),
        # implemented as discrete blended colors per stage.
        # For "outline": background fills, primary outline.
        if theme.accent_treatment == "outline" or theme.is_dark:
            stage_fills = [palette.background] * n_stages
            line_color = palette.primary
            text_on_stage = palette.text
            use_inverted_label = False
        else:
            stage_fills = [
                _blend(palette.primary, palette.accent, i / (n_stages - 1))
                for i in range(n_stages)
            ]
            line_color = palette.muted
            text_on_stage = palette.background  # white-ish on filled stages
            use_inverted_label = True

        for i, (label, caption) in enumerate(_STAGES):
            w = int(funnel_area_w * _WIDTH_PCTS[i])
            # center horizontally within the funnel area
            left = funnel_left + (funnel_area_w - w) // 2
            top = funnel_top + i * (stage_h + gap_emu)

            stage = slide.shapes.add_shape(rect_kind, left, top, w, stage_h)
            stage.fill.solid()
            stage.fill.fore_color.rgb = stage_fills[i]
            stage.line.color.rgb = line_color
            stage.line.width = theme.line_weight_emu

            tf = stage.text_frame
            tf.margin_left = Pt(8)
            tf.margin_right = Pt(8)
            tf.margin_top = Pt(4)
            tf.margin_bottom = Pt(4)
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = 2  # PP_ALIGN.CENTER  (2 == CENTER per python-pptx enum int)
            run = p.add_run()
            run.text = label
            run.font.size = Pt(theme.typography.body_size_pt)
            run.font.name = theme.typography.heading
            run.font.bold = True
            run.font.color.rgb = (
                text_on_stage if use_inverted_label else palette.text
            )

            # Caption to the right of the funnel area (one per stage).
            cap = slide.shapes.add_textbox(
                funnel_left + funnel_area_w + int(ctx.width * 0.01),
                top,
                caption_w - int(ctx.width * 0.01),
                stage_h,
            )
            ctf = cap.text_frame
            ctf.word_wrap = True
            ctf.margin_left = Pt(4)
            ctf.margin_right = Pt(4)
            ctf.margin_top = Pt(0)
            ctf.margin_bottom = Pt(0)
            cp = ctf.paragraphs[0]
            cp.alignment = 1  # PP_ALIGN.LEFT
            crun = cp.add_run()
            crun.text = caption
            crun.font.size = Pt(theme.typography.caption_size_pt)
            crun.font.name = theme.typography.body
            crun.font.color.rgb = palette.muted


def _blend(a: RGBColor, b: RGBColor, t: float) -> RGBColor:
    """Linear blend of two RGBColors. t=0 → a, t=1 → b."""
    t = max(0.0, min(1.0, t))
    r = int(a[0] + (b[0] - a[0]) * t)
    g = int(a[1] + (b[1] - a[1]) * t)
    bb = int(a[2] + (b[2] - a[2]) * t)
    return RGBColor(r, g, bb)
