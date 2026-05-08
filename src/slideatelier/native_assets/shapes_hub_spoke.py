"""HubSpoke — central hub + 6 surrounding ovals connected by line connectors.

All native: MSO_SHAPE.OVAL for the hub and spokes, MSO_CONNECTOR.STRAIGHT for
the radial lines (which python-pptx returns as LINE shapes — not FREEFORM).
"""
import math

from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.util import Pt

from .base import AssetShape, ShapeRenderContext
from .shapes_matrix import _lighten


_SPOKE_LABELS = [
    "PEOPLE",
    "PROCESS",
    "DATA",
    "TECH",
    "CULTURE",
    "STRATEGY",
]


class HubSpoke(AssetShape):
    id = "hub-spoke"
    name = "Hub & Spoke"
    description = "Central hub with 6 surrounding nodes connected by straight line connectors."
    style_tags = ("network", "structured", "framework", "consulting")
    aspect_ratio_hint = 1.0

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        # Geometry: hub diameter ~22% of min(width, height); spokes ~16%.
        bbox_min = min(ctx.width, ctx.height)
        hub_d = int(bbox_min * 0.22)
        spoke_d = int(bbox_min * 0.16)

        cx = ctx.left + ctx.width / 2.0
        cy = ctx.top + ctx.height / 2.0

        # Place spokes on an ellipse so they fill the rect when not square.
        ring_rx = (ctx.width - spoke_d) / 2.0 - int(ctx.width * 0.02)
        ring_ry = (ctx.height - spoke_d) / 2.0 - int(ctx.height * 0.02)

        outline_only = theme.accent_treatment == "outline" or theme.is_dark

        # Compute spoke centres at 6 evenly spaced angles, starting top.
        spoke_centres = []
        for i in range(6):
            theta = -math.pi / 2.0 + i * (2 * math.pi / 6.0)
            sx = cx + ring_rx * math.cos(theta)
            sy = cy + ring_ry * math.sin(theta)
            spoke_centres.append((sx, sy))

        # Draw connectors first (so they sit behind the ovals visually). We
        # draw each connector from the hub's edge toward the spoke's edge by
        # using the ovals' outer points along the radial direction.
        for sx, sy in spoke_centres:
            dx = sx - cx
            dy = sy - cy
            dist = math.hypot(dx, dy) or 1.0
            ux = dx / dist
            uy = dy / dist

            # Start: hub edge along the radial direction.
            start_x = int(round(cx + ux * hub_d / 2.0))
            start_y = int(round(cy + uy * hub_d / 2.0))
            # End: spoke edge along the (reverse) radial direction.
            end_x = int(round(sx - ux * spoke_d / 2.0))
            end_y = int(round(sy - uy * spoke_d / 2.0))

            conn = slide.shapes.add_connector(
                MSO_CONNECTOR.STRAIGHT, start_x, start_y, end_x, end_y
            )
            conn.line.color.rgb = palette.muted if not outline_only else palette.primary
            conn.line.width = theme.line_weight_emu

        # Draw spoke ovals.
        for i, (sx, sy) in enumerate(spoke_centres):
            left = int(round(sx - spoke_d / 2.0))
            top = int(round(sy - spoke_d / 2.0))
            oval = slide.shapes.add_shape(
                MSO_SHAPE.OVAL, left, top, spoke_d, spoke_d
            )
            oval.fill.solid()
            if outline_only:
                oval.fill.fore_color.rgb = palette.background
                oval.line.color.rgb = palette.primary
                lbl_color = palette.text
            else:
                # Alternate primary/accent tints.
                if i % 2 == 0:
                    oval.fill.fore_color.rgb = _lighten(palette.primary, 0.78)
                else:
                    oval.fill.fore_color.rgb = _lighten(palette.accent, 0.70)
                oval.line.color.rgb = palette.primary
                lbl_color = palette.text
            oval.line.width = theme.line_weight_emu

            tf = oval.text_frame
            tf.margin_left = Pt(2)
            tf.margin_right = Pt(2)
            tf.margin_top = Pt(2)
            tf.margin_bottom = Pt(2)
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = 2  # CENTER
            run = p.add_run()
            run.text = _SPOKE_LABELS[i]
            run.font.size = Pt(theme.typography.caption_size_pt)
            run.font.name = theme.typography.heading
            run.font.bold = False
            run.font.color.rgb = lbl_color

        # Hub oval (drawn last so it stacks on top of connectors).
        hub_left = int(round(cx - hub_d / 2.0))
        hub_top = int(round(cy - hub_d / 2.0))
        hub = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, hub_left, hub_top, hub_d, hub_d
        )
        hub.fill.solid()
        if outline_only:
            if theme.is_dark:
                hub.fill.fore_color.rgb = palette.primary
                hub_text_color = palette.background
            else:
                hub.fill.fore_color.rgb = palette.background
                hub_text_color = palette.primary
            hub.line.color.rgb = palette.primary
        else:
            hub.fill.fore_color.rgb = palette.primary
            hub.line.color.rgb = palette.primary
            hub_text_color = palette.background
        hub.line.width = theme.line_weight_emu

        htf = hub.text_frame
        htf.margin_left = Pt(2)
        htf.margin_right = Pt(2)
        htf.margin_top = Pt(2)
        htf.margin_bottom = Pt(2)
        hp = htf.paragraphs[0]
        hp.alignment = 2  # CENTER
        hrun = hp.add_run()
        hrun.text = "CORE"
        hrun.font.size = Pt(theme.typography.body_size_pt)
        hrun.font.name = theme.typography.heading
        hrun.font.bold = True
        hrun.font.color.rgb = hub_text_color
