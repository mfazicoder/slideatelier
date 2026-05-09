"""Sprint J.C — SVG rendering tests for chart-like AssetShapes.

Covers ChartBar, ChartDonut, ComparisonColumns, TimelineDots. Each test
instantiates the shape, builds a ShapeRenderContext from THEMES[0], calls
render_svg(ctx, 800, 600), and asserts on structure, primitive counts, and
that at least one theme palette colour appears in the markup.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from slideatelier.native_assets.base import ShapeRenderContext
from slideatelier.native_assets.shapes_chart_bar import ChartBar
from slideatelier.native_assets.shapes_chart_donut import ChartDonut
from slideatelier.native_assets.shapes_comparison_columns import ComparisonColumns
from slideatelier.native_assets.shapes_timeline_dots import TimelineDots
from slideatelier.native_assets.themes import THEMES


SVG_NS = "{http://www.w3.org/2000/svg}"


def _ctx() -> ShapeRenderContext:
    theme = THEMES[0]
    # EMU bounds are not used by render_svg, but the dataclass requires them.
    return ShapeRenderContext(left=0, top=0, width=10_000_000, height=7_500_000, theme=theme)


def _palette_hexes(theme) -> list[str]:
    p = theme.palette
    return [
        "#{:02X}{:02X}{:02X}".format(c[0], c[1], c[2])
        for c in (p.primary, p.accent, p.text, p.muted, p.background)
    ]


def _assert_common(svg: str, width: int = 800, height: int = 600) -> ET.Element:
    """Common assertions: starts/ends correct, parses, declares correct dims."""
    assert svg.startswith("<svg"), "SVG must start with <svg"
    assert svg.rstrip().endswith("</svg>"), "SVG must end with </svg>"

    root = ET.fromstring(svg)
    # ElementTree namespaces the tag.
    assert root.tag == f"{SVG_NS}svg"
    assert root.attrib.get("width") == str(width)
    assert root.attrib.get("height") == str(height)
    assert root.attrib.get("viewBox") == f"0 0 {width} {height}"
    return root


def _has_palette_fill(root: ET.Element, theme) -> bool:
    """At least one element uses a palette hex as its fill (case-insensitive)."""
    palette_set = {h.upper() for h in _palette_hexes(theme)}
    for el in root.iter():
        fill = el.attrib.get("fill")
        if fill and fill.upper() in palette_set:
            return True
    return False


def test_chart_bar_render_svg() -> None:
    ctx = _ctx()
    shape = ChartBar()
    svg = shape.render_svg(ctx, 800, 600)
    root = _assert_common(svg)

    rects = root.findall(f".//{SVG_NS}rect")
    # Background rect + at least 3 bar rects (placeholder values has 4 bars).
    bar_rects = [r for r in rects if r.attrib.get("x") not in (None, "0")]
    assert len(bar_rects) >= 3, f"expected >=3 bar <rect>s, got {len(bar_rects)}"

    texts = root.findall(f".//{SVG_NS}text")
    assert len(texts) >= 3, "expected category labels as <text> elements"

    assert _has_palette_fill(root, ctx.theme)


def test_chart_donut_render_svg() -> None:
    ctx = _ctx()
    shape = ChartDonut()
    svg = shape.render_svg(ctx, 800, 600)
    root = _assert_common(svg)

    paths = root.findall(f".//{SVG_NS}path")
    assert len(paths) >= 2, f"expected >=2 donut slice <path>s, got {len(paths)}"

    # Each slice path MUST use the elliptical arc 'A' command — no line approx.
    for p in paths:
        d = p.attrib.get("d", "")
        assert " A " in d or d.lstrip().startswith("A "), (
            f"donut slice path missing arc 'A' command: {d!r}"
        )

    assert _has_palette_fill(root, ctx.theme)


def test_comparison_columns_render_svg() -> None:
    ctx = _ctx()
    shape = ComparisonColumns()
    svg = shape.render_svg(ctx, 800, 600)
    root = _assert_common(svg)

    rects = root.findall(f".//{SVG_NS}rect")
    # 1 background + 3 headers + 3 bodies = 7 minimum, but spec asks >=4.
    assert len(rects) >= 4, f"expected >=4 <rect>s, got {len(rects)}"

    lines = root.findall(f".//{SVG_NS}line")
    assert len(lines) >= 1, f"expected at least 1 separating <line>, got {len(lines)}"

    assert _has_palette_fill(root, ctx.theme)


def test_timeline_dots_render_svg() -> None:
    ctx = _ctx()
    shape = TimelineDots()
    svg = shape.render_svg(ctx, 800, 600)
    root = _assert_common(svg)

    lines = root.findall(f".//{SVG_NS}line")
    assert len(lines) >= 1, f"expected >=1 baseline <line>, got {len(lines)}"

    circles = root.findall(f".//{SVG_NS}circle")
    assert len(circles) >= 3, f"expected >=3 <circle> dots, got {len(circles)}"

    assert _has_palette_fill(root, ctx.theme)


@pytest.mark.parametrize("shape_cls", [ChartBar, ChartDonut, ComparisonColumns, TimelineDots])
def test_render_svg_xml_parses(shape_cls) -> None:
    """Sanity check across all four — output must be valid XML."""
    ctx = _ctx()
    svg = shape_cls().render_svg(ctx, 800, 600)
    # Should not raise.
    ET.fromstring(svg)
