"""ChartDonut — native PowerPoint doughnut chart.

Uses slide.shapes.add_chart() with XL_CHART_TYPE.DOUGHNUT. Each data point
gets a different palette color so the slices are visually distinct under any
theme.
"""
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION

from .base import AssetShape, ShapeRenderContext
from .shapes_matrix import _lighten


class ChartDonut(AssetShape):
    id = "chart-donut"
    name = "Donut Chart"
    description = "Native doughnut chart with 4 categorical slices, themed colors per slice."
    style_tags = ("data", "chart", "share", "contemporary")
    aspect_ratio_hint = 1.0

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        cd = CategoryChartData()
        cd.categories = ["A", "B", "C", "D"]
        cd.add_series("Share", (35, 25, 22, 18))

        chart_shape = slide.shapes.add_chart(
            XL_CHART_TYPE.DOUGHNUT,
            ctx.left,
            ctx.top,
            ctx.width,
            ctx.height,
            cd,
        )
        chart = chart_shape.chart
        chart.has_title = False
        chart.has_legend = True
        try:
            chart.legend.position = XL_LEGEND_POSITION.RIGHT
            chart.legend.include_in_layout = False
        except Exception:
            pass

        # Theme palette per slice. Use 4 distinct shades:
        slice_colors = [
            palette.primary,
            palette.accent,
            _lighten(palette.primary, 0.55),
            _lighten(palette.accent, 0.55),
        ]

        plot = chart.plots[0]
        try:
            series = plot.series[0]
            for i, point in enumerate(series.points):
                fill = point.format.fill
                fill.solid()
                fill.fore_color.rgb = slice_colors[i % len(slice_colors)]
                line = point.format.line
                line.color.rgb = palette.background
        except Exception:
            # Fallback: just color the series uniformly.
            try:
                fill = plot.series[0].format.fill
                fill.solid()
                fill.fore_color.rgb = palette.primary
            except Exception:
                pass
