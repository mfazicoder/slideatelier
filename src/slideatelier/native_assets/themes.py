"""Starter theme catalog. Themes are visual styling packs applied on top of
any AssetShape or SlideTemplate. Same structural shape + different theme = a
totally different look."""
from .base import Palette, Theme, Typography


THEMES: list[Theme] = [
    Theme(
        id="consulting-corporate",
        name="Consulting Corporate",
        description="MBB-style: navy primary, restrained accents, filled rectangles, sharp corners.",
        palette=Palette.from_hex(
            primary="#1F3A5F",
            accent="#C86E3C",
            text="#222222",
            muted="#6B6B6B",
            background="#FFFFFF",
        ),
        typography=Typography(
            heading="Calibri",
            body="Calibri",
            title_size_pt=32, heading_size_pt=20, body_size_pt=12, caption_size_pt=10,
        ),
        is_dark=False,
        corner_radius_pct=0.0,
        accent_treatment="fill",
        style_tags=("consulting", "corporate", "structured", "professional"),
    ),
    Theme(
        id="scaffold-dark",
        name="Scaffold Dark",
        description="Dark-mode grid layout — delta-ops aesthetic, monospace, faint grid overlay.",
        palette=Palette.from_hex(
            primary="#10B981",     # bright green accent
            accent="#FBBF24",      # warm amber
            text="#E5E7EB",        # near-white
            muted="#9CA3AF",
            background="#0B0F19",  # deep blue-black
            success="#22C55E",
            warning="#F59E0B",
            danger="#EF4444",
        ),
        typography=Typography(
            heading="JetBrains Mono",
            body="Inter",
            title_size_pt=28, heading_size_pt=18, body_size_pt=11, caption_size_pt=9,
        ),
        is_dark=True,
        corner_radius_pct=0.0,
        accent_treatment="outline",
        grid_overlay=True,
        style_tags=("scaffold", "dark-mode", "technical", "ops", "contemporary"),
    ),
    Theme(
        id="minimal-artistic",
        name="Minimal Artistic",
        description="Striking minimal: single strong shape, generous whitespace, serif typography.",
        palette=Palette.from_hex(
            primary="#1A1A1A",     # near-black
            accent="#D97757",      # warm terracotta
            text="#1A1A1A",
            muted="#737373",
            background="#FAF7F2",  # warm off-white
        ),
        typography=Typography(
            heading="Fraunces",
            body="Inter",
            title_size_pt=44, heading_size_pt=22, body_size_pt=13, caption_size_pt=11,
        ),
        is_dark=False,
        corner_radius_pct=0.0,
        accent_treatment="underline",
        style_tags=("minimal", "artistic", "editorial", "contemporary"),
    ),
    Theme(
        id="contemporary-creative",
        name="Contemporary Creative",
        description="Bold modern: rounded shapes, vibrant accents, sans serif throughout.",
        palette=Palette.from_hex(
            primary="#5B21B6",     # purple
            accent="#FB923C",      # vivid orange
            text="#1F2937",
            muted="#6B7280",
            background="#FFFFFF",
        ),
        typography=Typography(
            heading="Inter",
            body="Inter",
            title_size_pt=30, heading_size_pt=18, body_size_pt=12, caption_size_pt=10,
        ),
        is_dark=False,
        corner_radius_pct=0.20,
        accent_treatment="fill",
        style_tags=("creative", "contemporary", "vibrant", "modern"),
    ),
]


THEMES_BY_ID: dict[str, Theme] = {t.id: t for t in THEMES}
