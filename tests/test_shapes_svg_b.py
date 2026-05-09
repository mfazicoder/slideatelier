"""Sprint J.B — SVG rendering tests for HubSpoke, Iceberg, LayeredCircles,
KpiHero, Ribbon. Each shape exposes a `render_svg(ctx, w, h)` returning a
self-contained inline SVG document.

Tests verify:
 - output begins with `<svg` and ends with `</svg>`,
 - parses as XML,
 - declared width/height match what we asked for,
 - the SVG mirrors the python-pptx geometry (correct primitive types & counts),
 - at least one theme palette colour is used as a fill.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from slideatelier.native_assets.base import ShapeRenderContext
from slideatelier.native_assets.shapes_hub_spoke import HubSpoke
from slideatelier.native_assets.shapes_iceberg import Iceberg
from slideatelier.native_assets.shapes_kpi_hero import KpiHero
from slideatelier.native_assets.shapes_layered_circles import LayeredCircles
from slideatelier.native_assets.shapes_ribbon import Ribbon
from slideatelier.native_assets.themes import THEMES

SVG_NS = "{http://www.w3.org/2000/svg}"
WIDTH = 800
HEIGHT = 600


def _ctx(theme):
    # Bounding box values are in EMU for the python-pptx render path; the
    # SVG path takes width/height_px args separately, so the EMU values
    # here just need to be non-zero.
    return ShapeRenderContext(left=0, top=0, width=9144000, height=6858000, theme=theme)


def _theme():
    return THEMES[0]  # consulting-corporate — well-defined palette


def _hex_palette_set(theme):
    p = theme.palette
    return {
        "#{:02X}{:02X}{:02X}".format(c[0], c[1], c[2])
        for c in (
            p.primary, p.accent, p.text, p.muted,
            p.background, p.success, p.warning, p.danger,
        )
    }


def _assert_common(svg: str, theme):
    # Starts/ends correctly
    assert svg.startswith("<svg"), "SVG must start with <svg"
    assert svg.rstrip().endswith("</svg>"), "SVG must end with </svg>"
    # Parses as XML
    root = ET.fromstring(svg)
    assert root.tag == f"{SVG_NS}svg"
    # Declared dimensions
    assert root.get("width") == str(WIDTH)
    assert root.get("height") == str(HEIGHT)
    assert root.get("viewBox") == f"0 0 {WIDTH} {HEIGHT}"
    # Palette colour appears somewhere as a fill
    palette_hexes = _hex_palette_set(theme)
    fills_used = set(re.findall(r'fill="(#[0-9A-Fa-f]{6})"', svg))
    fills_used = {f.upper() for f in fills_used}
    assert fills_used & palette_hexes, (
        f"Expected at least one palette colour in fills; got {fills_used}, "
        f"palette {palette_hexes}"
    )
    return root


def test_hub_spoke_render_svg():
    theme = _theme()
    svg = HubSpoke().render_svg(_ctx(theme), WIDTH, HEIGHT)
    root = _assert_common(svg, theme)

    circles = root.findall(f".//{SVG_NS}circle")
    lines = root.findall(f".//{SVG_NS}line")
    # Hub (1) + 6 spokes = 7 circles total; spec requires >= 4
    assert len(circles) >= 1 + 3, f"expected >=4 circles, got {len(circles)}"
    # 6 connectors expected; spec requires >= 3
    assert len(lines) >= 3, f"expected >=3 lines, got {len(lines)}"


def test_iceberg_render_svg():
    theme = _theme()
    svg = Iceberg().render_svg(_ctx(theme), WIDTH, HEIGHT)
    root = _assert_common(svg, theme)

    polygons = root.findall(f".//{SVG_NS}polygon")
    assert len(polygons) >= 1, f"expected >=1 polygon, got {len(polygons)}"
    # Horizontal divider — waterline rect spanning the full width.
    rects = root.findall(f".//{SVG_NS}rect")
    waterline = [r for r in rects if r.get("width") == str(WIDTH)]
    # The background is also a full-width rect; ensure at least one *non*
    # background full-width rect exists (the waterline).
    assert len(waterline) >= 2, (
        f"expected background + waterline rects spanning full width, got "
        f"{len(waterline)}"
    )


def test_layered_circles_render_svg():
    theme = _theme()
    svg = LayeredCircles().render_svg(_ctx(theme), WIDTH, HEIGHT)
    root = _assert_common(svg, theme)

    circles = root.findall(f".//{SVG_NS}circle")
    assert len(circles) >= 3, f"expected >=3 circles, got {len(circles)}"


def test_kpi_hero_render_svg():
    theme = _theme()
    svg = KpiHero().render_svg(_ctx(theme), WIDTH, HEIGHT)
    root = _assert_common(svg, theme)

    texts = root.findall(f".//{SVG_NS}text")
    assert len(texts) >= 2, f"expected >=2 text elements, got {len(texts)}"
    # Hero number should be present somewhere.
    text_strings = "".join(t.text or "" for t in texts)
    assert "42%" in text_strings or any("42" in (t.text or "") for t in texts)


def test_ribbon_render_svg():
    theme = _theme()
    svg = Ribbon().render_svg(_ctx(theme), WIDTH, HEIGHT)
    root = _assert_common(svg, theme)

    polygons = root.findall(f".//{SVG_NS}polygon")
    texts = root.findall(f".//{SVG_NS}text")
    assert len(polygons) >= 1, f"expected >=1 polygon, got {len(polygons)}"
    assert len(texts) >= 1, f"expected >=1 text, got {len(texts)}"
