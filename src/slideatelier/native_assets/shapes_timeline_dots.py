"""TimelineDots — horizontal line with 5 numbered milestones, alternating
above/below callouts for the labels.

Native: MSO_CONNECTOR.STRAIGHT for the spine, MSO_SHAPE.OVAL for milestones,
textboxes for the callouts.
"""
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.util import Pt

from .base import AssetShape, ShapeRenderContext
from .shapes_matrix import _lighten


_MILESTONES = [
    ("Q1 '25", "Discovery"),
    ("Q2 '25", "Pilot"),
    ("Q3 '25", "Scale"),
    ("Q4 '25", "Optimize"),
    ("Q1 '26", "Renew"),
]


class TimelineDots(AssetShape):
    id = "timeline-dots"
    name = "Timeline Dots"
    description = "Horizontal timeline with 5 numbered milestones and alternating callouts."
    style_tags = ("process", "sequential", "modern", "contemporary")
    aspect_ratio_hint = 2.4

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        outline_only = theme.accent_treatment == "outline" or theme.is_dark

        n = len(_MILESTONES)

        # Vertical layout: top callout band, spine line, bottom callout band.
        # Spine sits at vertical center.
        spine_y = ctx.top + ctx.height // 2

        # Dot diameter ~ 9% of bbox height (or 6% of width, whichever fits).
        dot_d = int(min(ctx.height * 0.18, ctx.width * 0.06))
        dot_d = max(dot_d, 200000)  # min ~22pt for legibility

        # Compute x positions for the n dots, evenly spaced inside a margin.
        margin_x = int(ctx.width * 0.05)
        usable_w = ctx.width - 2 * margin_x
        if n > 1:
            stride = usable_w // (n - 1)
        else:
            stride = 0
        first_cx = ctx.left + margin_x

        dot_centres = [first_cx + i * stride for i in range(n)]

        # Spine connector — extend slightly past first/last dots for visual
        # finish.
        spine_left = ctx.left + int(margin_x * 0.5)
        spine_right = ctx.left + ctx.width - int(margin_x * 0.5)
        spine = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT,
            spine_left,
            spine_y,
            spine_right,
            spine_y,
        )
        spine.line.color.rgb = palette.muted if not outline_only else palette.primary
        spine.line.width = max(theme.line_weight_emu, 19050)  # at least ~1.5pt

        # Callout band heights (above and below the spine).
        callout_h = (ctx.height - dot_d) // 2 - int(ctx.height * 0.02)

        for i, (date, label) in enumerate(_MILESTONES):
            cx = dot_centres[i]
            # Dot oval centred on the spine.
            dot = slide.shapes.add_shape(
                MSO_SHAPE.OVAL,
                cx - dot_d // 2,
                spine_y - dot_d // 2,
                dot_d,
                dot_d,
            )
            dot.fill.solid()
            if outline_only:
                if theme.is_dark:
                    dot.fill.fore_color.rgb = palette.primary
                    dot_text_color = palette.background
                else:
                    dot.fill.fore_color.rgb = palette.background
                    dot_text_color = palette.primary
                dot.line.color.rgb = palette.primary
            else:
                dot.fill.fore_color.rgb = palette.primary
                dot.line.color.rgb = palette.primary
                dot_text_color = palette.background
            dot.line.width = theme.line_weight_emu

            dtf = dot.text_frame
            dtf.margin_left = Pt(0)
            dtf.margin_right = Pt(0)
            dtf.margin_top = Pt(0)
            dtf.margin_bottom = Pt(0)
            dp = dtf.paragraphs[0]
            dp.alignment = 2  # CENTER
            drun = dp.add_run()
            drun.text = str(i + 1)
            drun.font.size = Pt(theme.typography.caption_size_pt)
            drun.font.name = theme.typography.heading
            drun.font.bold = True
            drun.font.color.rgb = dot_text_color

            # Callout — alternate above/below.
            callout_w = stride - int(stride * 0.15) if n > 1 else int(ctx.width * 0.18)
            callout_w = max(callout_w, dot_d * 3)
            cleft = cx - callout_w // 2
            if i % 2 == 0:
                # Above the spine
                ctop = ctx.top
                ch = callout_h
            else:
                # Below the spine
                ctop = spine_y + dot_d // 2 + int(ctx.height * 0.02)
                ch = callout_h

            cbox = slide.shapes.add_textbox(cleft, ctop, callout_w, ch)
            ctf = cbox.text_frame
            ctf.word_wrap = True
            ctf.margin_left = Pt(2)
            ctf.margin_right = Pt(2)
            ctf.margin_top = Pt(2)
            ctf.margin_bottom = Pt(2)

            # First paragraph: date
            cp = ctf.paragraphs[0]
            cp.alignment = 2  # CENTER
            crun = cp.add_run()
            crun.text = date
            crun.font.size = Pt(theme.typography.body_size_pt)
            crun.font.name = theme.typography.heading
            crun.font.bold = True
            crun.font.color.rgb = palette.text

            # Second paragraph: label
            lp = ctf.add_paragraph()
            lp.alignment = 2  # CENTER
            lrun = lp.add_run()
            lrun.text = label
            lrun.font.size = Pt(theme.typography.caption_size_pt)
            lrun.font.name = theme.typography.body
            lrun.font.color.rgb = palette.muted
