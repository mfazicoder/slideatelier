"""LayeredCircles — 3 overlapping ovals forming a venn-style diagram.

Native MSO_SHAPE.OVAL primitives. To make overlap regions visible, all three
circles use semi-transparent-feeling tints (we approximate transparency by
using lightened palette colors) and an outline-only treatment falls back to
just outlined circles.
"""
import math

from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Pt

from .base import AssetShape, ShapeRenderContext
from .shapes_matrix import _lighten


_LABELS = ["People", "Process", "Product"]


class LayeredCircles(AssetShape):
    id = "layered-circles"
    name = "Layered Circles"
    description = "3 overlapping ovals (venn-style) for tri-factor frameworks."
    style_tags = ("comparison", "venn", "structured", "contemporary")
    aspect_ratio_hint = 1.2

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        outline_only = theme.accent_treatment == "outline" or theme.is_dark

        # Three circles in a triangular arrangement: 2 on top, 1 on bottom.
        # Diameter ~52% of bbox width so they overlap meaningfully.
        d = int(min(ctx.width * 0.55, ctx.height * 0.65))
        cx = ctx.left + ctx.width / 2.0
        cy = ctx.top + ctx.height / 2.0

        # Centers: equilateral triangle with side ~ 0.55 * d so adjacent
        # circles overlap by ~45%.
        side = d * 0.62
        # Circle 0 (top-left), 1 (top-right), 2 (bottom-center)
        centers = [
            (cx - side / 2.0, cy - side * math.sqrt(3.0) / 6.0),
            (cx + side / 2.0, cy - side * math.sqrt(3.0) / 6.0),
            (cx, cy + side * math.sqrt(3.0) / 3.0),
        ]

        # Per-circle base color: primary, accent, muted (or theme variants).
        base_colors = [palette.primary, palette.accent, palette.muted]

        for i, ((px, py), base, label) in enumerate(zip(centers, base_colors, _LABELS)):
            left = int(round(px - d / 2.0))
            top = int(round(py - d / 2.0))
            oval = slide.shapes.add_shape(
                MSO_SHAPE.OVAL, left, top, d, d
            )
            oval.fill.solid()

            if outline_only:
                oval.fill.fore_color.rgb = palette.background
                oval.line.color.rgb = base
                lbl_color = base
            else:
                # Use a heavily lightened tint so overlaps are visually
                # distinct (approximating transparency without freeform).
                oval.fill.fore_color.rgb = _lighten(base, 0.65)
                oval.line.color.rgb = base
                lbl_color = palette.text
            oval.line.width = theme.line_weight_emu

            tf = oval.text_frame
            tf.margin_left = Pt(8)
            tf.margin_right = Pt(8)
            tf.margin_top = Pt(8)
            tf.margin_bottom = Pt(8)
            p = tf.paragraphs[0]
            p.alignment = 2  # CENTER
            run = p.add_run()
            run.text = label
            run.font.size = Pt(theme.typography.body_size_pt)
            run.font.name = theme.typography.heading
            run.font.bold = True
            run.font.color.rgb = lbl_color
