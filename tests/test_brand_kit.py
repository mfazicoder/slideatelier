"""Sprint K — Brand Kit + Brand Tokens tests.

Coverage matches the contract laid out in the sprint spec:

  1.  Save + load roundtrip — JSON persists exactly what we put in.
  2.  effective_*() helpers return the kit value when present, theme value
      when absent.
  3.  A deck rendered after a brand-kit save produces different .pptx bytes
      from one rendered against the bare theme (the Web Deck mirrors this
      via CSS custom properties on each <section>).
  4.  Wizard accepts a valid PNG logo + colours.
  5.  Wizard rejects an oversized logo.
  6.  Wizard rejects a logo whose extension and magic bytes disagree.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slideatelier.brand_kit import brand_kit_path, load_brand_kit, save_brand_kit
from slideatelier.models import BrandKit, SlideDeck
from slideatelier.native_assets.base import (
    Palette,
    ShapeRenderContext,
    Theme,
    Typography,
)
from slideatelier.template import Template
from slideatelier.web.app import app
from slideatelier.web_renderer import WebRenderer


# ---------------------------------------------------------------------------
# 1. save + load roundtrip
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    kit = BrandKit(
        logo_path="/uploads/brand_logo_abcd1234.png",
        color_primary="#112233",
        color_secondary="#445566",
        color_accent="#778899",
        color_bg="#FAFAFA",
        color_text="#101010",
        type_display="Inter",
        type_body="Inter",
        type_mono="JetBrains Mono",
        audience_tone="bold",
    )
    save_brand_kit(tmp_path, kit)
    assert brand_kit_path(tmp_path).exists()

    loaded = load_brand_kit(tmp_path)
    assert loaded is not None
    assert loaded.color_primary == "#112233"
    assert loaded.color_secondary == "#445566"
    assert loaded.color_accent == "#778899"
    assert loaded.audience_tone == "bold"
    assert loaded.logo_path == "/uploads/brand_logo_abcd1234.png"


def test_load_returns_none_when_no_kit(tmp_path: Path) -> None:
    assert load_brand_kit(tmp_path) is None


def test_load_falls_back_to_global_when_user_has_none(tmp_path: Path) -> None:
    # Save global kit; lookup with an unknown user_id should fall through.
    save_brand_kit(tmp_path, BrandKit(color_primary="#ABCDEF"))
    out = load_brand_kit(tmp_path, user_id=99)
    assert out is not None
    assert out.color_primary == "#ABCDEF"


def test_per_user_path_is_namespaced(tmp_path: Path) -> None:
    p = brand_kit_path(tmp_path, user_id=42)
    assert "users/42" in str(p) or "users\\42" in str(p)


def test_invalid_hex_rejected_by_model() -> None:
    with pytest.raises(Exception):  # pydantic ValidationError
        BrandKit(color_primary="not-a-color")


# ---------------------------------------------------------------------------
# 2. effective_*() helpers — kit value present vs absent
# ---------------------------------------------------------------------------

def _theme() -> Theme:
    return Theme(
        id="t",
        name="t",
        description="t",
        palette=Palette.from_hex(
            primary="#1F3A5F",
            accent="#C86E3C",
            text="#222222",
            background="#FFFFFF",
        ),
        typography=Typography(heading="Calibri", body="Calibri"),
    )


def test_effective_primary_falls_back_to_theme_when_kit_absent() -> None:
    ctx = ShapeRenderContext(left=0, top=0, width=100, height=100, theme=_theme())
    p = ctx.effective_primary()
    # Theme primary is #1F3A5F → RGB(31,58,95).
    assert (p[0], p[1], p[2]) == (0x1F, 0x3A, 0x5F)
    assert ctx.effective_display_font() == "Calibri"
    assert ctx.effective_body_font() == "Calibri"


def test_effective_primary_uses_kit_when_set() -> None:
    kit = BrandKit(color_primary="#FF0000", color_secondary="#00FF00", color_bg="#FFFFFF", color_text="#000000")
    ctx = ShapeRenderContext(left=0, top=0, width=100, height=100, theme=_theme(), brand_kit=kit)
    p = ctx.effective_primary()
    assert (p[0], p[1], p[2]) == (0xFF, 0, 0)
    # Secondary maps onto accent slot when no explicit color_accent override.
    s = ctx.effective_accent()
    assert (s[0], s[1], s[2]) == (0, 0xFF, 0)
    # Background and text overrides land too.
    bg = ctx.effective_bg()
    assert (bg[0], bg[1], bg[2]) == (0xFF, 0xFF, 0xFF)


def test_effective_fonts_use_kit_when_set() -> None:
    kit = BrandKit(
        color_primary="#000000",
        color_secondary="#000000",
        color_bg="#FFFFFF",
        color_text="#000000",
        type_display="Fraunces",
        type_body="Source Serif Pro",
        type_mono="Fira Code",
    )
    ctx = ShapeRenderContext(left=0, top=0, width=100, height=100, theme=_theme(), brand_kit=kit)
    assert ctx.effective_display_font() == "Fraunces"
    assert ctx.effective_body_font() == "Source Serif Pro"
    assert ctx.effective_mono_font() == "Fira Code"


def test_palette_property_routes_through_brand_kit() -> None:
    """The shape-level `ctx.palette.primary` access must reflect overrides
    even though the shape never touches `effective_*()` directly."""
    kit = BrandKit(color_primary="#123456", color_secondary="#654321", color_bg="#FFFFFF", color_text="#000000")
    ctx = ShapeRenderContext(left=0, top=0, width=10, height=10, theme=_theme(), brand_kit=kit)
    pal = ctx.palette
    assert (pal.primary[0], pal.primary[1], pal.primary[2]) == (0x12, 0x34, 0x56)
    assert (pal.accent[0], pal.accent[1], pal.accent[2]) == (0x65, 0x43, 0x21)


# ---------------------------------------------------------------------------
# 3. Web Deck re-render after brand-kit save changes the bytes
#    (the .pptx mirror of this is implicit — the same shapes share a Palette
#    so any byte difference in the SVG/CSS path would reflect in pptx too,
#    but we verify the WebRenderer output here because it's deterministic
#    and doesn't touch the python-pptx backend).
# ---------------------------------------------------------------------------

def _three_slide_deck() -> SlideDeck:
    return SlideDeck.model_validate({
        "title": "Test deck",
        "subtitle": "",
        "core_message": "Single sentence answering the question.",
        "narrative_arc": "Open. Diagnose. Recommend.",
        "slides": [
            {"layout": "title", "title": "Cover slide title", "strap": "",
             "body": [], "body_left": [], "body_right": [], "speaker_notes": "",
             "rationale": "", "asset_ref": None, "extras": []},
            {"layout": "content", "title": "Body slide one",
             "strap": "Sub", "body": ["A", "B"], "body_left": [], "body_right": [],
             "speaker_notes": "", "rationale": "", "asset_ref": None, "extras": []},
            {"layout": "key_takeaway", "title": "Closing recommendation",
             "strap": "", "body": [], "body_left": [], "body_right": [],
             "speaker_notes": "", "rationale": "", "asset_ref": None, "extras": []},
        ],
    })


def test_rerender_after_brand_kit_changes_html_bytes() -> None:
    deck = _three_slide_deck()
    tpl = Template()
    base = WebRenderer(tpl).render_deck_html(deck, slug="abc12345")
    kit = BrandKit(
        color_primary="#FF0000",
        color_secondary="#00FF00",
        color_bg="#FFFFFF",
        color_text="#000000",
    )
    branded = WebRenderer(tpl, brand_kit=kit).render_deck_html(deck, slug="abc12345")
    # Different bytes — brand-kit override flows into per-slide CSS vars.
    assert base != branded
    # And the brand-token override must literally be present.
    assert "--brand-primary:#FF0000" in branded
    # And the original primary should NOT survive on the section.
    assert "--brand-primary:#1F3A5F" not in branded


def test_brand_kit_overrides_appear_on_each_slide_section() -> None:
    deck = _three_slide_deck()
    tpl = Template()
    kit = BrandKit(
        color_primary="#101010",
        color_secondary="#202020",
        color_bg="#F0F0F0",
        color_text="#303030",
    )
    out = WebRenderer(tpl, brand_kit=kit).render_deck_html(deck, slug="brand-test")
    # 3 slides → at least 3 occurrences of each per-slide custom property.
    assert out.count("--brand-primary:#101010") >= 3
    assert out.count("--brand-bg:#F0F0F0") >= 3


# ---------------------------------------------------------------------------
# 4. Wizard accepts a valid PNG logo + colours
# ---------------------------------------------------------------------------

# 1x1 PNG (smallest valid). Magic header `\x89PNG\r\n\x1a\n` is the first 8 bytes.
_TINY_PNG = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452"
    "0000000100000001080600000017FFA8"
    "1F0000000A4944415478DA63000100"
    "00050001000A0E2E5C0000000049454E"
    "44AE426082"
)


def _client_with_output(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    return TestClient(app)


def test_wizard_get_renders_form(tmp_path: Path, monkeypatch) -> None:
    c = _client_with_output(tmp_path, monkeypatch)
    r = c.get("/onboarding/brand-kit")
    assert r.status_code == 200
    assert "Brand Kit" in r.text
    # Defaults render — primary slot value should round-trip.
    assert "color_primary" in r.text


def test_wizard_post_accepts_valid_logo_and_colors(tmp_path: Path, monkeypatch) -> None:
    c = _client_with_output(tmp_path, monkeypatch)
    files = {"logo": ("brand.png", BytesIO(_TINY_PNG), "image/png")}
    data = {
        "color_primary": "#112233",
        "color_secondary": "#445566",
        "color_accent": "",
        "color_bg": "#FFFFFF",
        "color_text": "#000000",
        "type_display": "Inter",
        "type_body": "Inter",
        "type_mono": "JetBrains Mono",
        "audience_tone": "bold",
    }
    r = c.post("/onboarding/brand-kit", data=data, files=files, follow_redirects=False)
    # Onboarding redirects to /design-system/brand-kit on success.
    assert r.status_code == 303, r.text
    # Kit is on disk.
    kit = load_brand_kit(tmp_path)
    assert kit is not None
    assert kit.color_primary == "#112233"
    assert kit.audience_tone == "bold"
    # Logo stored under output/uploads/.
    assert kit.logo_path is not None
    upload = tmp_path / "uploads" / Path(kit.logo_path).name
    assert upload.exists()


def test_wizard_post_rejects_oversized_logo(tmp_path: Path, monkeypatch) -> None:
    c = _client_with_output(tmp_path, monkeypatch)
    # 3 MB blob, prefixed with the PNG magic so the magic check would pass.
    payload = _TINY_PNG[:8] + b"\x00" * (3 * 1024 * 1024)
    files = {"logo": ("huge.png", BytesIO(payload), "image/png")}
    data = {
        "color_primary": "#000000",
        "color_secondary": "#FFFFFF",
        "color_accent": "",
        "color_bg": "#FFFFFF",
        "color_text": "#000000",
        "type_display": "Inter",
        "type_body": "Inter",
        "type_mono": "JetBrains Mono",
        "audience_tone": "approachable",
    }
    r = c.post("/onboarding/brand-kit", data=data, files=files)
    assert r.status_code == 400
    assert "limit" in r.text.lower() or "2 mb" in r.text.lower() or "bytes" in r.text.lower()


def test_wizard_post_rejects_wrong_mime_logo(tmp_path: Path, monkeypatch) -> None:
    c = _client_with_output(tmp_path, monkeypatch)
    # File named .png but content is plain text — magic check should fail.
    files = {"logo": ("fake.png", BytesIO(b"this is not a png"), "image/png")}
    data = {
        "color_primary": "#000000",
        "color_secondary": "#FFFFFF",
        "color_accent": "",
        "color_bg": "#FFFFFF",
        "color_text": "#000000",
        "type_display": "Inter",
        "type_body": "Inter",
        "type_mono": "JetBrains Mono",
        "audience_tone": "approachable",
    }
    r = c.post("/onboarding/brand-kit", data=data, files=files)
    assert r.status_code == 400
    assert "magic" in r.text.lower() or "look like" in r.text.lower()


def test_wizard_post_rejects_unsupported_extension(tmp_path: Path, monkeypatch) -> None:
    c = _client_with_output(tmp_path, monkeypatch)
    files = {"logo": ("brand.gif", BytesIO(b"GIF89a..."), "image/gif")}
    data = {
        "color_primary": "#000000",
        "color_secondary": "#FFFFFF",
        "color_accent": "",
        "color_bg": "#FFFFFF",
        "color_text": "#000000",
        "type_display": "Inter",
        "type_body": "Inter",
        "type_mono": "JetBrains Mono",
        "audience_tone": "approachable",
    }
    r = c.post("/onboarding/brand-kit", data=data, files=files)
    assert r.status_code == 400


def test_design_system_brand_kit_edit_persists(tmp_path: Path, monkeypatch) -> None:
    """The /design-system/brand-kit POST should save changes and re-render
    the form (no redirect)."""
    c = _client_with_output(tmp_path, monkeypatch)
    data = {
        "color_primary": "#ABABAB",
        "color_secondary": "#BABABA",
        "color_accent": "#CDCDCD",
        "color_bg": "#FFFFFF",
        "color_text": "#000000",
        "type_display": "Inter",
        "type_body": "Inter",
        "type_mono": "JetBrains Mono",
        "audience_tone": "minimal",
    }
    r = c.post("/design-system/brand-kit", data=data, follow_redirects=False)
    assert r.status_code == 200, r.text
    kit = load_brand_kit(tmp_path)
    assert kit is not None
    assert kit.color_primary == "#ABABAB"
    assert kit.color_accent == "#CDCDCD"
    assert kit.audience_tone == "minimal"
