"""Sprint J.A — SVG render tests for the first batch of native shapes.

Each shape under test must:
  * expose a `render_svg(ctx, width_px, height_px) -> str` method
  * emit a self-contained, well-formed <svg>…</svg> document
  * declare width/height/viewBox matching the requested pixel dimensions
  * use exact native SVG primitives (rect / polygon) — no <path>
  * embed at least one fill colour drawn from the resolved theme palette
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from slideatelier.native_assets.base import ShapeRenderContext
from slideatelier.native_assets.shapes_funnel import Funnel
from slideatelier.native_assets.shapes_hexagon import HexagonGrid
from slideatelier.native_assets.shapes_matrix import Matrix2x2
from slideatelier.native_assets.shapes_pyramid import Pyramid
from slideatelier.native_assets.shapes_value_chain import ValueChain
from slideatelier.native_assets.themes import THEMES


SVG_NS = "{http://www.w3.org/2000/svg}"

WIDTH = 800
HEIGHT = 600


def _ctx(theme):
    """The SVG renderers ignore EMU-space coords — they work in pixels via
    the width_px/height_px params. We still need a ShapeRenderContext with
    the theme attached, so we pass a placeholder bbox."""
    return ShapeRenderContext(
        left=0,
        top=0,
        width=10_000_000,
        height=10_000_000,
        theme=theme,
    )


def _palette_hex_set(palette) -> set[str]:
    """Hex strings (uppercase) for every palette colour."""
    out = set()
    for attr in ("primary", "accent", "text", "muted", "background",
                 "success", "warning", "danger"):
        c = getattr(palette, attr)
        out.add("#{:02X}{:02X}{:02X}".format(c[0], c[1], c[2]))
    return out


def _assert_basic_svg(svg: str, width: int, height: int) -> ET.Element:
    """Common assertions: starts/ends correctly, parses, declares dimensions."""
    assert svg.startswith("<svg"), f"SVG must start with <svg, got: {svg[:40]!r}"
    assert svg.rstrip().endswith("</svg>"), f"SVG must end with </svg>, got tail: {svg[-40:]!r}"
    root = ET.fromstring(svg)
    assert root.tag == f"{SVG_NS}svg"
    # width/height attributes match the requested pixel dimensions.
    assert root.get("width") == str(width)
    assert root.get("height") == str(height)
    # viewBox should be 0 0 W H so SVG coordinates are in pixels.
    assert root.get("viewBox") == f"0 0 {width} {height}"
    return root


def _assert_palette_color_present(svg: str, palette) -> None:
    """At least one fill colour from the palette must appear in the SVG."""
    palette_colors = _palette_hex_set(palette)
    upper = svg.upper()
    matches = [c for c in palette_colors if c in upper]
    assert matches, (
        f"Expected at least one palette colour in SVG; "
        f"palette = {sorted(palette_colors)}"
    )


def _count_local(root: ET.Element, local_name: str) -> int:
    return sum(1 for el in root.iter() if el.tag == f"{SVG_NS}{local_name}")


# Use the consulting-corporate theme (filled fills) so it's easier to assert
# that palette colours actually appear.
@pytest.fixture
def theme():
    return THEMES[0]


def test_matrix2x2_render_svg(theme):
    shape = Matrix2x2()
    svg = shape.render_svg(_ctx(theme), WIDTH, HEIGHT)
    root = _assert_basic_svg(svg, WIDTH, HEIGHT)
    # 1 background <rect> + 4 quadrant <rect>s = at least 4 rects in the grid
    rects = _count_local(root, "rect")
    assert rects >= 4, f"expected >=4 <rect>s, got {rects}"
    # 2 axis labels + 4 quadrant labels = at least 2 <text>s
    texts = _count_local(root, "text")
    assert texts >= 2, f"expected >=2 <text>s for axis labels, got {texts}"
    _assert_palette_color_present(svg, theme.palette)
    # Typography font-family should be referenced.
    assert theme.typography.heading in svg or theme.typography.body in svg


def test_funnel_render_svg(theme):
    shape = Funnel()
    svg = shape.render_svg(_ctx(theme), WIDTH, HEIGHT)
    root = _assert_basic_svg(svg, WIDTH, HEIGHT)
    polygons = _count_local(root, "polygon")
    assert polygons >= 4, f"expected >=4 <polygon>s, got {polygons}"
    _assert_palette_color_present(svg, theme.palette)
    assert theme.typography.heading in svg or theme.typography.body in svg


def test_hexagon_grid_render_svg(theme):
    shape = HexagonGrid()
    svg = shape.render_svg(_ctx(theme), WIDTH, HEIGHT)
    root = _assert_basic_svg(svg, WIDTH, HEIGHT)
    hexagons = [el for el in root.iter() if el.tag == f"{SVG_NS}polygon"]
    assert len(hexagons) >= 6, f"expected >=6 hexagon <polygon>s, got {len(hexagons)}"
    # Each hexagon polygon should have exactly 6 points.
    for poly in hexagons:
        pts = poly.get("points", "").split()
        assert len(pts) == 6, (
            f"hexagon polygon should have 6 points, got {len(pts)}: {pts}"
        )
    _assert_palette_color_present(svg, theme.palette)
    assert theme.typography.heading in svg or theme.typography.body in svg


def test_value_chain_render_svg(theme):
    shape = ValueChain()
    svg = shape.render_svg(_ctx(theme), WIDTH, HEIGHT)
    root = _assert_basic_svg(svg, WIDTH, HEIGHT)
    polygons = _count_local(root, "polygon")
    # 5 chevrons + 1 MARGIN arrow = 6 polygons
    assert polygons >= 4, f"expected >=4 <polygon>s, got {polygons}"
    rects = _count_local(root, "rect")
    # 1 background + 4 support boxes = 5 rects
    assert rects >= 4, f"expected >=4 <rect>s for support row, got {rects}"
    _assert_palette_color_present(svg, theme.palette)
    assert theme.typography.heading in svg or theme.typography.body in svg


def test_pyramid_render_svg(theme):
    shape = Pyramid()
    svg = shape.render_svg(_ctx(theme), WIDTH, HEIGHT)
    root = _assert_basic_svg(svg, WIDTH, HEIGHT)
    polygons = _count_local(root, "polygon")
    assert polygons >= 3, f"expected >=3 stacked tier <polygon>s, got {polygons}"
    _assert_palette_color_present(svg, theme.palette)
    assert theme.typography.heading in svg or theme.typography.body in svg


def test_all_five_render_with_every_theme():
    """Smoke test across all 4 starter themes — guards against theme-specific
    code paths (outline-only, dark mode, rounded corners) that may emit
    malformed SVG."""
    shapes = [Matrix2x2(), Funnel(), HexagonGrid(), ValueChain(), Pyramid()]
    for theme in THEMES:
        for shape in shapes:
            svg = shape.render_svg(_ctx(theme), 400, 300)
            assert svg.startswith("<svg")
            assert svg.rstrip().endswith("</svg>")
            ET.fromstring(svg)  # must parse
