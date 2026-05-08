"""Pyramid — 4-level hierarchy (Foundation → Vision).

Implemented as a stack of native MSO_SHAPE.TRAPEZOID primitives, progressively
narrower from bottom to top. python-pptx's TRAPEZOID renders narrower at the
top by default (which is what we want), so each tier is just a trapezoid with
shrinking width.
"""
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Pt

from .base import AssetShape, ShapeRenderContext
from .shapes_matrix import _lighten


# Bottom → top
_TIERS = [
    "Foundation",
    "Structure",
    "Strategy",
    "Vision",
]
_WIDTH_PCTS = [1.00, 0.78, 0.55, 0.32]


class Pyramid(AssetShape):
    id = "pyramid"
    name = "Pyramid"
    description = "4-level hierarchy pyramid (Foundation → Vision)."
    style_tags = ("hierarchy", "structured", "consulting", "framework")
    aspect_ratio_hint = 1.4

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        n = len(_TIERS)
        # 30% on the right reserved for tier captions.
        caption_w = int(ctx.width * 0.28)
        pyramid_area_w = ctx.width - caption_w
        gap_emu = int(ctx.height * 0.015)
        total_gap = gap_emu * (n - 1)
        tier_h = (ctx.height - total_gap) // n

        outline_only = theme.accent_treatment == "outline" or theme.is_dark

        for i in range(n):
            # i=0 is bottom, i=3 is top (Vision).
            tier_idx_top_first = n - 1 - i  # 3,2,1,0 for top→bottom drawing
            label = _TIERS[i]
            w_pct = _WIDTH_PCTS[i]

            top = ctx.top + tier_idx_top_first * (tier_h + gap_emu)
            w = int(pyramid_area_w * w_pct)
            left = ctx.left + (pyramid_area_w - w) // 2

            # Top tier uses TRAPEZOID for the pyramidal apex; lower tiers
            # use trapezoids too so the silhouette stays pyramidal.
            shape_kind = MSO_SHAPE.TRAPEZOID

            tier = slide.shapes.add_shape(shape_kind, left, top, w, tier_h)
            tier.fill.solid()

            if outline_only:
                tier.fill.fore_color.rgb = palette.background
                tier.line.color.rgb = palette.primary
                label_color = palette.text
            else:
                # Bottom = lightest, top = solid primary. Index i goes 0..3 where
                # i=3 is top → most saturated.
                tint = 0.78 - i * 0.20  # 0.78, 0.58, 0.38, 0.18
                tint = max(0.0, tint)
                tier.fill.fore_color.rgb = _lighten(palette.primary, tint)
                tier.line.color.rgb = palette.primary
                # White-ish text for top 2 tiers (dark fill), dark text for bottom.
                if i >= 2:
                    label_color = palette.background
                else:
                    label_color = palette.text

            tier.line.width = theme.line_weight_emu

            tf = tier.text_frame
            tf.margin_left = Pt(4)
            tf.margin_right = Pt(4)
            tf.margin_top = Pt(4)
            tf.margin_bottom = Pt(4)
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = 2  # CENTER
            run = p.add_run()
            run.text = label
            run.font.size = Pt(theme.typography.body_size_pt)
            run.font.name = theme.typography.heading
            run.font.bold = True
            run.font.color.rgb = label_color

            # Caption to the right of each tier.
            cap = slide.shapes.add_textbox(
                ctx.left + pyramid_area_w + int(ctx.width * 0.01),
                top,
                caption_w - int(ctx.width * 0.01),
                tier_h,
            )
            ctf = cap.text_frame
            ctf.word_wrap = True
            ctf.margin_left = Pt(4)
            ctf.margin_right = Pt(4)
            cp = ctf.paragraphs[0]
            cp.alignment = 1  # LEFT
            crun = cp.add_run()
            crun.text = f"Tier {n - i}"
            crun.font.size = Pt(theme.typography.caption_size_pt)
            crun.font.name = theme.typography.body
            crun.font.color.rgb = palette.muted
