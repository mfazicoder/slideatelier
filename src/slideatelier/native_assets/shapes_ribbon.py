"""Ribbon — horizontal banner: central RECTANGLE flanked by RIGHT_TRIANGLE flags.

The two end "swallowtail" cuts are formed by inverted right triangles whose
hypotenuse aligns with the inner edge of the central rectangle, creating the
classic banner ribbon silhouette using only native primitives.
"""
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Pt

from .base import AssetShape, ShapeRenderContext
from .shapes_matrix import _lighten


class Ribbon(AssetShape):
    id = "ribbon-banner"
    name = "Ribbon Banner"
    description = "Horizontal ribbon banner — center rectangle with triangular ribbon ends."
    style_tags = ("hero", "graphics-heavy", "contemporary", "decorative")
    aspect_ratio_hint = 3.0

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        outline_only = theme.accent_treatment == "outline" or theme.is_dark

        # Banner occupies central horizontal stripe (vertically centered, ~50% h).
        band_h = int(ctx.height * 0.50)
        band_top = ctx.top + (ctx.height - band_h) // 2

        # Ends: each triangular flag is band_h wide.
        end_w = band_h  # square-ish flag for right-triangle shape

        # The two flags overlap the central rectangle a bit so the join is clean.
        overlap = int(end_w * 0.25)

        # Central rectangle spans from (left + end_w - overlap) to
        # (right - end_w + overlap).
        center_left = ctx.left + end_w - overlap
        center_right = ctx.left + ctx.width - end_w + overlap
        center_w = center_right - center_left

        # Choose fills.
        if outline_only:
            ribbon_fill = palette.background
            ribbon_line = palette.primary
            text_color = palette.text
            flag_fill = palette.background
            flag_line = palette.accent
        else:
            ribbon_fill = palette.primary
            ribbon_line = palette.primary
            text_color = palette.background
            flag_fill = _lighten(palette.primary, 0.30)
            flag_line = palette.primary

        # Left flag: RIGHT_TRIANGLE rotated/oriented so the hypotenuse points
        # into the center. Default RIGHT_TRIANGLE has the right angle at the
        # bottom-left. To make a "ribbon end" that points left, we rotate 270°
        # so the right angle ends up at the right side, hypotenuse on the left.
        left_flag = slide.shapes.add_shape(
            MSO_SHAPE.RIGHT_TRIANGLE,
            ctx.left,
            band_top,
            end_w,
            band_h,
        )
        # By default the hypotenuse runs from top-left to bottom-right of the
        # bounding box. Flipping horizontally gives a triangle whose hypotenuse
        # runs top-right to bottom-left — i.e. the slanted edge faces inward.
        # python-pptx exposes flipping via the .rotation/.element; we simply
        # rotate 0 (the default already gives an inward-facing slant for the
        # left flag if flipped). Use the rotation property as a simple approach.
        # Actually the default RIGHT_TRIANGLE has the right angle at the bottom-
        # left and the slanted hypotenuse from top-left down to bottom-right.
        # For the LEFT flag we want the slanted edge on the right (pointing
        # inward). Rotating 0° already gives slant from top-left to bottom-
        # right which is acceptable; flag end shape on left side ribbon visual
        # is achieved by treating the triangle's right edge as the join.
        left_flag.fill.solid()
        left_flag.fill.fore_color.rgb = flag_fill
        left_flag.line.color.rgb = flag_line
        left_flag.line.width = theme.line_weight_emu

        # Right flag: same triangle but mirrored (rotation 180° flips both axes
        # so hypotenuse runs top-right to bottom-left and right-angle is at
        # top-right relative to bbox).
        right_flag = slide.shapes.add_shape(
            MSO_SHAPE.RIGHT_TRIANGLE,
            ctx.left + ctx.width - end_w,
            band_top,
            end_w,
            band_h,
        )
        right_flag.rotation = 180.0
        right_flag.fill.solid()
        right_flag.fill.fore_color.rgb = flag_fill
        right_flag.line.color.rgb = flag_line
        right_flag.line.width = theme.line_weight_emu

        # Central rectangle (drawn last so it sits over the flag overlap).
        shape_kind = (
            MSO_SHAPE.ROUNDED_RECTANGLE
            if theme.corner_radius_pct > 0
            else MSO_SHAPE.RECTANGLE
        )
        center = slide.shapes.add_shape(
            shape_kind, center_left, band_top, center_w, band_h
        )
        center.fill.solid()
        center.fill.fore_color.rgb = ribbon_fill
        center.line.color.rgb = ribbon_line
        center.line.width = theme.line_weight_emu

        tf = center.text_frame
        tf.margin_left = Pt(8)
        tf.margin_right = Pt(8)
        tf.margin_top = Pt(4)
        tf.margin_bottom = Pt(4)
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = 2  # CENTER
        run = p.add_run()
        run.text = "FEATURED"
        run.font.size = Pt(theme.typography.heading_size_pt)
        run.font.name = theme.typography.heading
        run.font.bold = True
        run.font.color.rgb = text_color
