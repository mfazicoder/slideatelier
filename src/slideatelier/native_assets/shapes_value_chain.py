"""Value Chain — Porter's value chain framework as native shapes.

Five primary-activity chevrons flow left → right (top 2/3 of the bounding rect),
with four wider support-activity rectangles below (bottom 1/3). A small
right-arrow marked "MARGIN" caps the far-right edge.

All shapes are native python-pptx primitives (CHEVRON, RECTANGLE,
ROUNDED_RECTANGLE, RIGHT_ARROW) so the deck remains fully editable.
"""
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Pt

from .base import AssetShape, ShapeRenderContext
from .shapes_matrix import _lighten


PRIMARY_LABELS = (
    "Inbound logistics",
    "Operations",
    "Outbound logistics",
    "Marketing & sales",
    "Service",
)

SUPPORT_LABELS = (
    "Firm infrastructure",
    "HR management",
    "Tech development",
    "Procurement",
)


class ValueChain(AssetShape):
    id = "value-chain"
    name = "Value Chain"
    description = "Porter's value chain — 5 primary activities + 4 support activities."
    style_tags = ("process", "consulting", "porter", "framework")
    aspect_ratio_hint = 2.0  # wider than tall

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        # Vertical split: top 55% primary chevrons, gap 5%, bottom 30% support
        # Reserve ~10% on the right for the MARGIN arrow.
        margin_w = int(ctx.width * 0.08)
        chain_left = ctx.left
        chain_w = ctx.width - margin_w - int(ctx.width * 0.01)  # tiny gap before MARGIN

        primary_h = int(ctx.height * 0.55)
        gap_h = int(ctx.height * 0.05)
        support_h = ctx.height - primary_h - gap_h

        # ----- Primary chevrons (top row) -----
        n_primary = 5
        # 5 chevrons each 18% of chain width with 2% overlap (so total ~ 5*18 - 4*2 = 82%).
        # Compute by allocating equal stride with overlap.
        # Effective stride = (chain_w - chevron_w) / (n - 1) for full coverage.
        chevron_w = int(chain_w * 0.22)
        if n_primary > 1:
            stride = (chain_w - chevron_w) // (n_primary - 1)
        else:
            stride = chain_w

        # Treatment-driven coloring
        outline_only = theme.accent_treatment == "outline"
        is_dark = theme.is_dark

        for i, label in enumerate(PRIMARY_LABELS):
            cleft = chain_left + i * stride
            ctop = ctx.top
            chev = slide.shapes.add_shape(
                MSO_SHAPE.CHEVRON,
                cleft,
                ctop,
                chevron_w,
                primary_h,
            )
            chev.fill.solid()

            # Last chevron uses accent so the chain "graduates" toward margin.
            base_color = palette.accent if i == n_primary - 1 else palette.primary

            if outline_only:
                chev.fill.fore_color.rgb = palette.background
                chev.line.color.rgb = base_color
                label_color = base_color
            elif is_dark:
                chev.fill.fore_color.rgb = base_color
                chev.line.color.rgb = base_color
                label_color = palette.background if _is_lightish(palette.background) else palette.text
                # palette.background on dark theme is dark; we want light text on dark fill
                label_color = palette.text
            else:
                chev.fill.fore_color.rgb = base_color
                chev.line.color.rgb = base_color
                label_color = palette.background  # white-ish text on filled chevron

            chev.line.width = theme.line_weight_emu

            tf = chev.text_frame
            tf.margin_left = Pt(4)
            tf.margin_right = Pt(4)
            tf.margin_top = Pt(4)
            tf.margin_bottom = Pt(4)
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = 2  # CENTER (PP_ALIGN.CENTER == 2 in python-pptx? actually CENTER=2)
            # Note: PP_ALIGN.CENTER is 2 in python-pptx enum
            run = p.add_run()
            run.text = label
            run.font.size = Pt(theme.typography.body_size_pt)
            run.font.name = theme.typography.heading
            run.font.bold = True
            run.font.color.rgb = label_color

        # ----- Support rectangles (bottom row) -----
        n_support = 4
        support_top = ctx.top + primary_h + gap_h
        support_total_w = chain_w
        support_gap = int(support_total_w * 0.015)
        support_w = (support_total_w - support_gap * (n_support - 1)) // n_support

        # Support fill/treatment
        if outline_only:
            support_fill = palette.background
            support_line = palette.muted
            support_text_color = palette.text
        elif is_dark:
            support_fill = _darken(palette.background, 0.15)
            support_line = palette.muted
            support_text_color = palette.text
        else:
            support_fill = _lighten(palette.muted, 0.85)
            support_line = palette.muted
            support_text_color = palette.text

        shape_kind = (
            MSO_SHAPE.ROUNDED_RECTANGLE
            if theme.corner_radius_pct > 0
            else MSO_SHAPE.RECTANGLE
        )

        for i, label in enumerate(SUPPORT_LABELS):
            sleft = chain_left + i * (support_w + support_gap)
            rect = slide.shapes.add_shape(
                shape_kind,
                sleft,
                support_top,
                support_w,
                support_h,
            )
            rect.fill.solid()
            rect.fill.fore_color.rgb = support_fill
            rect.line.color.rgb = support_line
            rect.line.width = theme.line_weight_emu

            tf = rect.text_frame
            tf.margin_left = Pt(6)
            tf.margin_right = Pt(6)
            tf.margin_top = Pt(4)
            tf.margin_bottom = Pt(4)
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = 2  # CENTER
            run = p.add_run()
            run.text = label
            run.font.size = Pt(theme.typography.caption_size_pt)
            run.font.name = theme.typography.body
            run.font.bold = False
            run.font.color.rgb = support_text_color

        # ----- MARGIN right-arrow accent -----
        margin_left = chain_left + chain_w + int(ctx.width * 0.005)
        margin_top = ctx.top + int(primary_h * 0.15)
        margin_height = int(primary_h * 0.7)
        arrow = slide.shapes.add_shape(
            MSO_SHAPE.RIGHT_ARROW,
            margin_left,
            margin_top,
            margin_w,
            margin_height,
        )
        arrow.fill.solid()
        if outline_only:
            arrow.fill.fore_color.rgb = palette.background
            arrow.line.color.rgb = palette.accent
            margin_text_color = palette.accent
        else:
            arrow.fill.fore_color.rgb = palette.accent
            arrow.line.color.rgb = palette.accent
            margin_text_color = palette.background
        arrow.line.width = theme.line_weight_emu

        atf = arrow.text_frame
        atf.margin_left = Pt(2)
        atf.margin_right = Pt(2)
        atf.margin_top = Pt(2)
        atf.margin_bottom = Pt(2)
        ap = atf.paragraphs[0]
        ap.alignment = 2  # CENTER
        arun = ap.add_run()
        arun.text = "MARGIN"
        arun.font.size = Pt(theme.typography.caption_size_pt)
        arun.font.name = theme.typography.heading
        arun.font.bold = True
        arun.font.color.rgb = margin_text_color


def _is_lightish(color) -> bool:
    """Rough luminance check — True if color is closer to white than black."""
    return (color[0] + color[1] + color[2]) / 3 > 128


def _darken(color, factor: float):
    """Blend an RGBColor toward black. factor=0 → original, factor=1 → black."""
    from pptx.dml.color import RGBColor
    r = max(0, int(color[0] * (1 - factor)))
    g = max(0, int(color[1] * (1 - factor)))
    b = max(0, int(color[2] * (1 - factor)))
    return RGBColor(r, g, b)
