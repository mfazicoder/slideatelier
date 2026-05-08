"""Iceberg — visible tip + submerged depths model.

Above the waterline: an MSO_SHAPE.ISOSCELES_TRIANGLE pointing up (the visible
tip, "Visible"). Below: an MSO_SHAPE.PENTAGON. python-pptx's PENTAGON points
upward by default; we render the lower mass as an inverted pentagon by
rotating it 180° via the rotation property. A horizontal RECTANGLE acts as
the waterline. Sub-categories label the underwater region.
"""
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Pt

from .base import AssetShape, ShapeRenderContext
from .shapes_matrix import _lighten


_UNDERWATER_LABELS = ["Beliefs", "Values", "Assumptions", "Behaviors"]


class Iceberg(AssetShape):
    id = "iceberg"
    name = "Iceberg Model"
    description = "Visible tip above the waterline, hidden depths below — beliefs/values/assumptions/behaviors."
    style_tags = ("framework", "depth", "consulting", "graphics-heavy")
    aspect_ratio_hint = 0.9

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        outline_only = theme.accent_treatment == "outline" or theme.is_dark

        # Vertical layout: top 30% above water, 6% waterline, 64% below water.
        above_h = int(ctx.height * 0.30)
        line_h = int(ctx.height * 0.04)
        below_h = ctx.height - above_h - line_h

        # Above-water tip — isosceles triangle, narrower than the underwater mass.
        tip_w = int(ctx.width * 0.42)
        tip_left = ctx.left + (ctx.width - tip_w) // 2
        tip_top = ctx.top
        tip = slide.shapes.add_shape(
            MSO_SHAPE.ISOSCELES_TRIANGLE,
            tip_left,
            tip_top,
            tip_w,
            above_h,
        )
        tip.fill.solid()
        if outline_only:
            tip.fill.fore_color.rgb = palette.background
            tip.line.color.rgb = palette.primary
            tip_text = palette.text
        else:
            tip.fill.fore_color.rgb = palette.primary
            tip.line.color.rgb = palette.primary
            tip_text = palette.background
        tip.line.width = theme.line_weight_emu

        ttf = tip.text_frame
        ttf.margin_left = Pt(2)
        ttf.margin_right = Pt(2)
        ttf.margin_top = Pt(2)
        ttf.margin_bottom = Pt(2)
        tp = ttf.paragraphs[0]
        tp.alignment = 2  # CENTER
        trun = tp.add_run()
        trun.text = "Visible"
        trun.font.size = Pt(theme.typography.caption_size_pt)
        trun.font.name = theme.typography.heading
        trun.font.bold = True
        trun.font.color.rgb = tip_text

        # Waterline rectangle — full width, accent color.
        line_top = ctx.top + above_h
        waterline = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            ctx.left,
            line_top,
            ctx.width,
            line_h,
        )
        waterline.fill.solid()
        if outline_only:
            waterline.fill.fore_color.rgb = palette.background
            waterline.line.color.rgb = palette.accent
        else:
            waterline.fill.fore_color.rgb = _lighten(palette.accent, 0.55)
            waterline.line.color.rgb = palette.accent
        waterline.line.width = theme.line_weight_emu

        wtf = waterline.text_frame
        wtf.margin_left = Pt(4)
        wtf.margin_right = Pt(4)
        wtf.margin_top = Pt(0)
        wtf.margin_bottom = Pt(0)
        wp = wtf.paragraphs[0]
        wp.alignment = 1  # LEFT
        wrun = wp.add_run()
        wrun.text = "Waterline"
        wrun.font.size = Pt(theme.typography.caption_size_pt)
        wrun.font.name = theme.typography.body
        wrun.font.color.rgb = palette.muted

        # Below-water pentagon (inverted). PENTAGON's default orientation is
        # pointed up; rotating 180° flips it to point down — the classic
        # iceberg-under-water silhouette.
        below_w = int(ctx.width * 0.78)
        below_left = ctx.left + (ctx.width - below_w) // 2
        below_top = line_top + line_h
        below = slide.shapes.add_shape(
            MSO_SHAPE.PENTAGON,
            below_left,
            below_top,
            below_w,
            below_h,
        )
        below.rotation = 180.0
        below.fill.solid()
        if outline_only:
            below.fill.fore_color.rgb = palette.background
            below.line.color.rgb = palette.primary
            below_text = palette.text
        else:
            below.fill.fore_color.rgb = _lighten(palette.primary, 0.55)
            below.line.color.rgb = palette.primary
            below_text = palette.text
        below.line.width = theme.line_weight_emu

        # Don't put text directly inside the rotated pentagon (it would also
        # rotate). Instead use 4 textboxes stacked vertically over the pentagon
        # area, each labelled with one underwater concept.
        labels_top = below_top + int(below_h * 0.10)
        labels_h = int(below_h * 0.80)
        each_h = labels_h // len(_UNDERWATER_LABELS)
        for i, lbl in enumerate(_UNDERWATER_LABELS):
            tb = slide.shapes.add_textbox(
                below_left,
                labels_top + i * each_h,
                below_w,
                each_h,
            )
            ltf = tb.text_frame
            ltf.word_wrap = True
            ltf.margin_left = Pt(4)
            ltf.margin_right = Pt(4)
            ltf.margin_top = Pt(2)
            ltf.margin_bottom = Pt(2)
            lp = ltf.paragraphs[0]
            lp.alignment = 2  # CENTER
            lrun = lp.add_run()
            lrun.text = lbl
            lrun.font.size = Pt(theme.typography.caption_size_pt)
            lrun.font.name = theme.typography.heading
            lrun.font.color.rgb = below_text
