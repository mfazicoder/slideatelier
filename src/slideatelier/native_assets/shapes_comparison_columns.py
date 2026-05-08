"""ComparisonColumns — 3 vertical option columns side by side.

Each column = header rectangle (colored) on top + body rectangle below for
bullet text. Three differentiated colors (primary, accent, muted) make the
options scan at a glance.
"""
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Pt

from .base import AssetShape, ShapeRenderContext
from .shapes_matrix import _lighten


_OPTIONS = ["Option A", "Option B", "Option C"]
_BULLETS = (
    "First differentiator\nSecond benefit\nKey constraint",
    "First differentiator\nSecond benefit\nKey constraint",
    "First differentiator\nSecond benefit\nKey constraint",
)


class ComparisonColumns(AssetShape):
    id = "comparison-columns"
    name = "Comparison Columns"
    description = "3 side-by-side option columns with header + body for fast comparison."
    style_tags = ("comparison", "structured", "framework", "consulting")
    aspect_ratio_hint = 1.5

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        outline_only = theme.accent_treatment == "outline" or theme.is_dark

        n = 3
        gap = int(ctx.width * 0.02)
        col_w = (ctx.width - gap * (n - 1)) // n
        header_h = int(ctx.height * 0.18)
        body_h = ctx.height - header_h

        shape_kind = (
            MSO_SHAPE.ROUNDED_RECTANGLE
            if theme.corner_radius_pct > 0
            else MSO_SHAPE.RECTANGLE
        )

        # Distinct color per column
        header_colors = [palette.primary, palette.accent, palette.muted]

        for i, label in enumerate(_OPTIONS):
            cleft = ctx.left + i * (col_w + gap)
            ctop = ctx.top

            base_color = header_colors[i]

            # Header
            hdr = slide.shapes.add_shape(
                shape_kind, cleft, ctop, col_w, header_h
            )
            hdr.fill.solid()
            if outline_only:
                hdr.fill.fore_color.rgb = palette.background
                hdr.line.color.rgb = base_color
                hdr_text_color = base_color
            else:
                hdr.fill.fore_color.rgb = base_color
                hdr.line.color.rgb = base_color
                hdr_text_color = palette.background
            hdr.line.width = theme.line_weight_emu

            htf = hdr.text_frame
            htf.margin_left = Pt(6)
            htf.margin_right = Pt(6)
            htf.margin_top = Pt(4)
            htf.margin_bottom = Pt(4)
            htf.word_wrap = True
            hp = htf.paragraphs[0]
            hp.alignment = 2  # CENTER
            hrun = hp.add_run()
            hrun.text = label
            hrun.font.size = Pt(theme.typography.heading_size_pt)
            hrun.font.name = theme.typography.heading
            hrun.font.bold = True
            hrun.font.color.rgb = hdr_text_color

            # Body
            body = slide.shapes.add_shape(
                shape_kind, cleft, ctop + header_h, col_w, body_h
            )
            body.fill.solid()
            if outline_only:
                body.fill.fore_color.rgb = palette.background
                body.line.color.rgb = palette.muted
                body_text_color = palette.text
            else:
                body.fill.fore_color.rgb = _lighten(base_color, 0.88)
                body.line.color.rgb = palette.muted
                body_text_color = palette.text
            body.line.width = theme.line_weight_emu

            btf = body.text_frame
            btf.margin_left = Pt(8)
            btf.margin_right = Pt(8)
            btf.margin_top = Pt(8)
            btf.margin_bottom = Pt(8)
            btf.word_wrap = True

            bullets = _BULLETS[i].split("\n")
            for j, bullet in enumerate(bullets):
                if j == 0:
                    bp = btf.paragraphs[0]
                else:
                    bp = btf.add_paragraph()
                bp.alignment = 1  # LEFT
                brun = bp.add_run()
                brun.text = f"• {bullet}"
                brun.font.size = Pt(theme.typography.body_size_pt)
                brun.font.name = theme.typography.body
                brun.font.color.rgb = body_text_color
