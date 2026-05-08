"""Native asset framework — code-defined shapes, themes, and slide templates.

Three orthogonal concepts:

  AssetShape     — a single diagrammatic primitive (matrix, funnel, hexagon
                   grid, value chain, …). Defined in code using python-pptx
                   MSO_SHAPE primitives. Renders into an arbitrary rectangle
                   on a slide. Never uses freeform paths.

  Theme          — a styling pack: palette + line weight + corner radius +
                   typography + treatments (outline / fill / shadow / accent).
                   Applied ON TOP of any shape or template.

  SlideTemplate  — a pre-arranged full slide (hero, exec summary, KPI dashboard).
                   Composed of one or more AssetShapes plus its own layout logic.

Asset library = (Shape OR Template) × Theme. The catalog enumerates the cross
product; the user can swap themes without re-picking the structure.
"""
from .base import AssetShape, NativeRegistry, Palette, SlideTemplate, Theme
from .registry import build_registry

__all__ = [
    "AssetShape",
    "NativeRegistry",
    "Palette",
    "SlideTemplate",
    "Theme",
    "build_registry",
]
