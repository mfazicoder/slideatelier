import json
import re
from pathlib import Path
from typing import Literal

from pptx.dml.color import RGBColor
from pydantic import BaseModel, Field, field_validator

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_hex(value: str) -> str:
    """Normalize hex string to '#RRGGBB' form. Accepts '1F3A5F', '#1F3A5F', '1f3a5f'."""
    m = HEX_RE.match(value.strip())
    if not m:
        raise ValueError(f"Invalid hex color: {value!r}. Expected '#RRGGBB' or 'RRGGBB'.")
    return "#" + m.group(1).upper()


def is_valid_hex(value: str) -> bool:
    return bool(HEX_RE.match(value.strip()))


def hex_to_rgb(value: str) -> RGBColor:
    """Convert '#RRGGBB' to a python-pptx RGBColor."""
    s = parse_hex(value).lstrip("#")
    return RGBColor(int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


class TemplateColors(BaseModel):
    primary: str = "#1F3A5F"
    accent: str = "#C86E3C"
    text: str = "#222222"
    muted: str = "#6B6B6B"
    background: str = "#FFFFFF"
    success: str = "#2E7D5B"
    warning: str = "#C9941F"
    danger: str = "#B23A3A"

    @field_validator("primary", "accent", "text", "muted", "background", "success", "warning", "danger")
    @classmethod
    def _validate(cls, v: str) -> str:
        return parse_hex(v)


class TemplateFontSizes(BaseModel):
    title_slide_title: int = 44
    title_slide_subtitle: int = 20
    section_divider: int = 36
    slide_title: int = 24
    body: int = 16
    key_takeaway_title: int = 32
    key_takeaway_body: int = 18


class TemplateFonts(BaseModel):
    heading: str = "Calibri"
    body: str = "Calibri"
    sizes: TemplateFontSizes = Field(default_factory=TemplateFontSizes)


class TemplateLogo(BaseModel):
    path: str | None = None
    position: Literal["top-left", "top-right", "bottom-left", "bottom-right", "none"] = "none"
    width_inches: float = 1.0


class TemplateFooter(BaseModel):
    enabled: bool = False
    text: str = ""
    show_page_numbers: bool = True


# Default mapping for masters that follow PowerPoint's standard layout names/order.
# Override in template JSON if your master uses a different structure.
DEFAULT_LAYOUT_MAP: dict[str, int] = {
    "title": 0,            # Title Slide
    "section_divider": 2,  # Section Header
    "content": 1,          # Title and Content
    "bullet_list": 1,      # Title and Content
    "two_column": 3,       # Two Content
    "key_takeaway": 5,     # Title Only
    "closing": 1,          # Title and Content
}


class Template(BaseModel):
    name: str = "Default"
    description: str = ""

    # MASTER MODE — preferred. If set, the renderer opens this .pptx and adds
    # slides using its master layouts. All visual decisions (colors, fonts,
    # logos, footers) are inherited from the master file. The colors/fonts
    # fields below become irrelevant in this mode.
    master_pptx: str | None = None
    layout_map: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_LAYOUT_MAP))

    # JSON MODE — fallback for users without a master file. The renderer builds
    # slides from blank, applying these styles directly. Used only when
    # master_pptx is None.
    colors: TemplateColors = Field(default_factory=TemplateColors)
    fonts: TemplateFonts = Field(default_factory=TemplateFonts)
    logo: TemplateLogo = Field(default_factory=TemplateLogo)
    footer: TemplateFooter = Field(default_factory=TemplateFooter)
    slide_width_inches: float = 13.333
    slide_height_inches: float = 7.5


def load_template(path: Path) -> Template:
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return Template.model_validate_json(path.read_text())


def load_default_template(templates_dir: Path) -> Template:
    """Load templates/default.json if it exists; otherwise return a built-in default."""
    default_path = templates_dir / "default.json"
    if default_path.exists():
        return load_template(default_path)
    return Template()


def save_template(template: Template, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(template.model_dump(), indent=2))
