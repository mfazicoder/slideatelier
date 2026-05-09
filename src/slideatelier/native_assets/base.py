"""Base classes for the native asset framework.

Design principles:
- All shapes use python-pptx native primitives (MSO_SHAPE.RECTANGLE, OVAL,
  HEXAGON, CHEVRON, RIGHT_ARROW, ROUND_RECTANGLE, …). NEVER freeform paths.
- All shapes accept a Palette (theme-resolved colors). Same shape + different
  theme = different visual treatment.
- Shapes render into an arbitrary rectangle (left, top, width, height in EMU)
  so they can be placed on any slide as an "extra" or used to build templates.
- Output is fully editable in PowerPoint.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, TYPE_CHECKING

from pptx.dml.color import RGBColor

if TYPE_CHECKING:  # pragma: no cover — avoid runtime cycle
    from ..models import BrandKit


# ---------------------------------------------------------------------------
# Palette + Theme
# ---------------------------------------------------------------------------

def _hex_to_rgb(hex_str: str) -> RGBColor:
    s = hex_str.lstrip("#")
    return RGBColor(int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


@dataclass(frozen=True)
class Palette:
    """Resolved color palette. All values are RGBColor (already parsed)."""
    primary: RGBColor
    accent: RGBColor
    text: RGBColor
    muted: RGBColor
    background: RGBColor
    success: RGBColor
    warning: RGBColor
    danger: RGBColor

    @classmethod
    def from_hex(
        cls,
        primary: str = "#1F3A5F",
        accent: str = "#C86E3C",
        text: str = "#222222",
        muted: str = "#6B6B6B",
        background: str = "#FFFFFF",
        success: str = "#2E7D5B",
        warning: str = "#C9941F",
        danger: str = "#B23A3A",
    ) -> "Palette":
        return cls(
            primary=_hex_to_rgb(primary),
            accent=_hex_to_rgb(accent),
            text=_hex_to_rgb(text),
            muted=_hex_to_rgb(muted),
            background=_hex_to_rgb(background),
            success=_hex_to_rgb(success),
            warning=_hex_to_rgb(warning),
            danger=_hex_to_rgb(danger),
        )


@dataclass(frozen=True)
class Typography:
    heading: str = "Calibri"
    body: str = "Calibri"
    title_size_pt: int = 32
    heading_size_pt: int = 20
    body_size_pt: int = 12
    caption_size_pt: int = 10


@dataclass(frozen=True)
class Theme:
    """Visual styling pack applied on top of shapes/templates.

    A Theme is orthogonal to the asset structure — same matrix shape rendered
    with consulting-corporate vs scaffold-dark looks completely different but
    has the same MECE structure underneath.
    """
    id: str
    name: str
    description: str
    palette: Palette
    typography: Typography = field(default_factory=Typography)

    # Visual treatments
    is_dark: bool = False              # dark-mode rendering (background ≠ light)
    line_weight_emu: int = 12700       # ~ 1pt
    corner_radius_pct: float = 0.0     # 0 = sharp corners; 0.15 = rounded
    accent_treatment: str = "fill"     # "fill" | "outline" | "underline" | "shadow"
    grid_overlay: bool = False         # scaffold themes draw a faint grid
    image_overlay_opacity: float = 0.4 # for full-bleed image backgrounds

    style_tags: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# AssetShape + SlideTemplate
# ---------------------------------------------------------------------------

@dataclass
class ShapeRenderContext:
    """Bounding rectangle (in EMU) and the resolved theme/palette for a render.

    When `brand_kit` is set, color and font lookups via `palette`,
    `typography`, and the `effective_*()` helpers return brand-kit overrides
    in place of the theme's values. Shapes already read these properties, so
    a single brand-kit swap rebrands the entire deck — no per-shape rewrite.
    """
    left: int
    top: int
    width: int
    height: int
    theme: Theme
    brand_kit: "BrandKit | None" = None

    @property
    def palette(self) -> Palette:
        """Effective palette — brand-kit overrides applied on top of theme."""
        if self.brand_kit is None:
            return self.theme.palette
        base = self.theme.palette
        bk = self.brand_kit
        # color_secondary is the user's secondary brand color; we map it onto
        # the palette `accent` slot when no explicit color_accent override
        # exists. This keeps "primary + secondary" the meaningful pairing
        # surfaced to the user while preserving accent as a 3rd-level color.
        accent_hex = bk.color_accent or bk.color_secondary
        return Palette(
            primary=_hex_to_rgb(bk.color_primary),
            accent=_hex_to_rgb(accent_hex),
            text=_hex_to_rgb(bk.color_text),
            muted=base.muted,
            background=_hex_to_rgb(bk.color_bg),
            success=base.success,
            warning=base.warning,
            danger=base.danger,
        )

    @property
    def typography(self) -> Typography:
        """Effective typography — brand-kit fonts override theme fonts when set."""
        if self.brand_kit is None:
            return self.theme.typography
        base = self.theme.typography
        return Typography(
            heading=self.brand_kit.type_display or base.heading,
            body=self.brand_kit.type_body or base.body,
            title_size_pt=base.title_size_pt,
            heading_size_pt=base.heading_size_pt,
            body_size_pt=base.body_size_pt,
            caption_size_pt=base.caption_size_pt,
        )

    # -----------------------------------------------------------------
    # Effective-* helpers — explicit accessors for shapes that prefer to
    # call a method instead of unpacking the palette. Each returns the
    # brand-kit override when present, else the theme value.
    # -----------------------------------------------------------------

    def effective_primary(self) -> RGBColor:
        return self.palette.primary

    def effective_secondary(self) -> RGBColor:
        # Secondary maps to the palette `accent` slot (see `palette` above).
        return self.palette.accent

    def effective_accent(self) -> RGBColor:
        return self.palette.accent

    def effective_bg(self) -> RGBColor:
        return self.palette.background

    def effective_text(self) -> RGBColor:
        return self.palette.text

    def effective_muted(self) -> RGBColor:
        return self.palette.muted

    def effective_display_font(self) -> str:
        return self.typography.heading

    def effective_body_font(self) -> str:
        return self.typography.body

    def effective_mono_font(self) -> str:
        if self.brand_kit is not None and self.brand_kit.type_mono:
            return self.brand_kit.type_mono
        # Theme has no dedicated mono slot; fall back to the body font.
        return self.typography.body


class AssetShape(ABC):
    """A single diagrammatic primitive renderable into any rectangle on a slide."""
    id: str = ""
    name: str = ""
    description: str = ""
    style_tags: tuple[str, ...] = ()  # structural tags: "framework", "process", "comparison"
    aspect_ratio_hint: float = 16 / 9  # natural aspect; renderer can scale

    @abstractmethod
    def render(self, slide, ctx: ShapeRenderContext) -> None:
        """Add native MSO_SHAPE primitives to the given slide within the
        bounding rectangle defined by ctx. Implementations MUST use native
        shape primitives only — no freeform paths."""
        raise NotImplementedError

    def render_into_full_slide(
        self,
        slide,
        theme: Theme,
        *,
        slide_width_emu: int,
        slide_height_emu: int,
        brand_kit: "BrandKit | None" = None,
    ) -> None:
        """Convenience: render the shape filling 80% of the slide, centered.
        Used by the catalog generator to produce a per-shape preview .pptx.
        Optionally accepts a `brand_kit` to apply token overrides."""
        margin_x = int(slide_width_emu * 0.10)
        margin_y = int(slide_height_emu * 0.10)
        ctx = ShapeRenderContext(
            left=margin_x,
            top=margin_y,
            width=slide_width_emu - 2 * margin_x,
            height=slide_height_emu - 2 * margin_y,
            theme=theme,
            brand_kit=brand_kit,
        )
        self.render(slide, ctx)


class SlideTemplate(ABC):
    """A pre-designed full slide composed of one or more AssetShapes.

    SlideTemplates accept content (title, bullets, etc.) and the theme. They
    take responsibility for whole-slide layout decisions (margins, hierarchy,
    background treatment) — different from AssetShapes which only fill a
    given rectangle.
    """
    id: str = ""
    name: str = ""
    description: str = ""
    style_tags: tuple[str, ...] = ()
    requires_image: bool = False  # e.g. hero-fullbleed needs a background image

    @abstractmethod
    def render(
        self,
        slide,
        theme: Theme,
        content: dict,
        *,
        slide_width_emu: int,
        slide_height_emu: int,
    ) -> None:
        """Render the template onto the slide. `content` is template-specific
        (e.g. {'title': '…', 'subtitle': '…', 'bullets': [...], 'image_path': …})."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class NativeRegistry:
    """Singleton registry of all native shapes, themes, and templates."""

    def __init__(self) -> None:
        self._shapes: dict[str, AssetShape] = {}
        self._themes: dict[str, Theme] = {}
        self._templates: dict[str, SlideTemplate] = {}

    def register_shape(self, shape: AssetShape) -> None:
        if not shape.id:
            raise ValueError("AssetShape must have a non-empty id")
        self._shapes[shape.id] = shape

    def register_theme(self, theme: Theme) -> None:
        if not theme.id:
            raise ValueError("Theme must have a non-empty id")
        self._themes[theme.id] = theme

    def register_template(self, template: SlideTemplate) -> None:
        if not template.id:
            raise ValueError("SlideTemplate must have a non-empty id")
        self._templates[template.id] = template

    def shapes(self) -> list[AssetShape]:
        return sorted(self._shapes.values(), key=lambda s: s.id)

    def themes(self) -> list[Theme]:
        return sorted(self._themes.values(), key=lambda t: t.id)

    def templates(self) -> list[SlideTemplate]:
        return sorted(self._templates.values(), key=lambda t: t.id)

    def shape(self, id: str) -> AssetShape | None:
        return self._shapes.get(id)

    def theme(self, id: str) -> Theme | None:
        return self._themes.get(id)

    def template(self, id: str) -> SlideTemplate | None:
        return self._templates.get(id)
