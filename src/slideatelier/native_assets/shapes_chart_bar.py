"""ChartBar — native PowerPoint clustered column chart.

Uses slide.shapes.add_chart() with XL_CHART_TYPE.COLUMN_CLUSTERED. Theme
palette is applied to the data series fill so the chart picks up the active
theme. Output is a real chart (shape_type=CHART, integer 3) — fully editable
in PowerPoint, not a freeform.
"""
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION

from .base import AssetShape, ShapeRenderContext


class ChartBar(AssetShape):
    id = "chart-bar"
    name = "Bar Chart"
    description = "Native clustered column chart with one series across 4 categories."
    style_tags = ("data", "chart", "comparison", "framework")
    aspect_ratio_hint = 1.6

    def render(self, slide, ctx: ShapeRenderContext) -> None:
        theme = ctx.theme
        palette = ctx.palette

        cd = CategoryChartData()
        cd.categories = ["Q1", "Q2", "Q3", "Q4"]
        cd.add_series("Revenue", (12, 18, 24, 31))

        chart_shape = slide.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            ctx.left,
            ctx.top,
            ctx.width,
            ctx.height,
            cd,
        )
        chart = chart_shape.chart
        chart.has_title = False
        chart.has_legend = False  # single series; legend redundant

        # Apply theme color to the series.
        plot = chart.plots[0]
        try:
            series = plot.series[0]
            fill = series.format.fill
            fill.solid()
            fill.fore_color.rgb = palette.primary
            line = series.format.line
            line.color.rgb = palette.primary
        except Exception:
            # If python-pptx version doesn't support series.format.line, skip.
            pass
