"""Sprint V — Deck Audit (linter).

Site-wide-equivalent linter for slide decks: a pure analysis pass over a
SlideDeck (+ Template, optionally a BrandKit) that flags problems
slide-by-slide and offers per-issue + bulk fix actions.

The module exports:
    AuditIssue              — pydantic model for a single finding
    audit_deck(deck, tpl, *, brand_kit=None) -> list[AuditIssue]
    apply_audit_fixes(deck, issues, *, codes=None) -> tuple[SlideDeck, list[int]]
    AUDIT_CODES             — registry: code -> (severity, description)

Lint codes (v1):
    BODY_TOO_LONG          — slide body > 40 words                    (warning)
    HEADING_HIERARCHY      — heading shape changes between adjacent
                              slides without a section_divider        (info)
    OFF_THEME_COLOR        — block_style color not in theme palette
                              and not in brand_kit                    (warning)
    UNUSED_ASSET_SLOT      — two_column slide with only one column
                              populated                                (warning)
    DUPLICATE_CONTENT      — two slides share an identical title or
                              identical first bullet                   (warning)
    MISSING_SPEAKER_NOTES  — final/closing slide has no speaker notes (info)
    LOW_CONTRAST           — fg/bg contrast < 4.5 (WCAG AA)           (error)
    EMPTY_BODY             — content slide with body=[] and no extras (error)
    OVERFLOWING_BBOX       — block_bbox + estimated text length
                              doesn't fit (rough heuristic)            (warning)

Auto-fixable codes (apply_audit_fixes will mutate the deck):
    MISSING_SPEAKER_NOTES  — adds a stub note
    DUPLICATE_CONTENT      — appends '(2)' to the duplicate title
    UNUSED_ASSET_SLOT      — converts to single-column 'content' layout

Non-auto-fixable codes still surface in the UI; the user must edit by hand
because we don't want to make up content (no LLM call here).
"""
from __future__ import annotations

from typing import Literal

from .models import AuditIssue, BrandKit, SlideDeck
from .template import Template, parse_hex


# ---------------------------------------------------------------------------
# Public schema
# ---------------------------------------------------------------------------

Severity = Literal["error", "warning", "info"]

# Re-export AuditIssue for convenience (defined in models.py to keep the
# schema shared with downstream consumers).
__all__ = [
    "AuditIssue",
    "AUDIT_CODES",
    "AUTO_FIXABLE_CODES",
    "audit_deck",
    "apply_audit_fixes",
]


# code -> (severity, short human description). Surfaced in the UI legend.
AUDIT_CODES: dict[str, tuple[Severity, str]] = {
    "BODY_TOO_LONG": ("warning", "Slide body exceeds 40 words."),
    "HEADING_HIERARCHY": (
        "info",
        "Heading shape changes between adjacent slides without a section_divider.",
    ),
    "OFF_THEME_COLOR": (
        "warning",
        "Block color is not from the theme palette or brand kit.",
    ),
    "UNUSED_ASSET_SLOT": (
        "warning",
        "Two-column slide has only one column populated.",
    ),
    "DUPLICATE_CONTENT": (
        "warning",
        "Two slides share an identical title or first bullet.",
    ),
    "MISSING_SPEAKER_NOTES": (
        "info",
        "Final slide has no speaker notes.",
    ),
    "LOW_CONTRAST": (
        "error",
        "Foreground/background contrast below WCAG AA (4.5:1).",
    ),
    "EMPTY_BODY": (
        "error",
        "Content slide has empty body and no extras.",
    ),
    "OVERFLOWING_BBOX": (
        "warning",
        "Estimated text length doesn't fit the block_bbox.",
    ),
}

# Codes apply_audit_fixes() can resolve automatically (no user input needed).
AUTO_FIXABLE_CODES = frozenset({
    "MISSING_SPEAKER_NOTES",
    "DUPLICATE_CONTENT",
    "UNUSED_ASSET_SLOT",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORD_LIMIT_BODY = 40

# Layouts that conceptually live at the same "heading level".
# A jump between groups (without a section_divider in between) is a hierarchy
# warning. Title and closing are book-ends; everything else is body matter.
_HEADING_GROUP: dict[str, str] = {
    "title": "title",
    "closing": "closing",
    "section_divider": "divider",
    "key_takeaway": "key",
    "content": "body",
    "bullet_list": "body",
    "two_column": "body",
}


def _word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w.strip()])


def _body_word_count(slide) -> int:
    n = 0
    for s in slide.body or []:
        n += _word_count(s)
    for s in slide.body_left or []:
        n += _word_count(s)
    for s in slide.body_right or []:
        n += _word_count(s)
    if slide.strap:
        n += _word_count(slide.strap)
    return n


def _hex_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parse_hex(value)
    except Exception:  # noqa: BLE001
        return None


def _theme_palette(tpl: Template) -> set[str]:
    c = tpl.colors
    return {
        parse_hex(c.primary),
        parse_hex(c.accent),
        parse_hex(c.text),
        parse_hex(c.muted),
        parse_hex(c.background),
        parse_hex(c.success),
        parse_hex(c.warning),
        parse_hex(c.danger),
    }


def _brand_palette(brand_kit: BrandKit | None) -> set[str]:
    if brand_kit is None:
        return set()
    out = {
        parse_hex(brand_kit.color_primary),
        parse_hex(brand_kit.color_secondary),
        parse_hex(brand_kit.color_bg),
        parse_hex(brand_kit.color_text),
    }
    if brand_kit.color_accent:
        out.add(parse_hex(brand_kit.color_accent))
    return out


def _relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance for a #RRGGBB color."""
    s = hex_color.lstrip("#")
    rgb = [int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]

    def _chan(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (_chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = _relative_luminance(fg_hex)
    l2 = _relative_luminance(bg_hex)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _estimate_chars_fit(bbox: dict, font_size: int = 16) -> int:
    """Rough estimate: how many characters fit in a bbox at the given font size.

    Uses a conservative 0.5 em average glyph width and 1.2 line-height.
    bbox is normalized 0..1 over a 13.333 x 7.5 inch slide.
    Returns total character capacity (lines * chars-per-line).
    """
    slide_w_in = 13.333
    slide_h_in = 7.5
    width_in = max(0.01, float(bbox.get("width", 0.0))) * slide_w_in
    height_in = max(0.01, float(bbox.get("height", 0.0))) * slide_h_in
    # 1pt = 1/72in. Average glyph ~0.5em wide; line-height ~1.2em.
    em_in = font_size / 72.0
    chars_per_line = max(1, int(width_in / (0.5 * em_in)))
    lines = max(1, int(height_in / (1.2 * em_in)))
    return chars_per_line * lines


def _estimated_text_length(slide, block_name: str) -> int:
    """Total character count of the text rendered in the named block."""
    if block_name == "title":
        return len(slide.title or "")
    if block_name == "strap":
        return len(slide.strap or "")
    if block_name == "body":
        return sum(len(b) + 2 for b in (slide.body or []))  # +2 for bullet
    if block_name == "body_left":
        return sum(len(b) + 2 for b in (slide.body_left or []))
    if block_name == "body_right":
        return sum(len(b) + 2 for b in (slide.body_right or []))
    return 0


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def audit_deck(
    deck: SlideDeck,
    tpl: Template,
    *,
    brand_kit: BrandKit | None = None,
) -> list[AuditIssue]:
    """Run all linters on the deck. Pure: no I/O, no mutation.

    Returns issues sorted by (slide_idx, severity_rank, code).
    """
    issues: list[AuditIssue] = []

    palette = _theme_palette(tpl) | _brand_palette(brand_kit)
    bg_hex = parse_hex(tpl.colors.background)

    titles_seen: dict[str, int] = {}
    first_bullets_seen: dict[str, int] = {}

    for idx, slide in enumerate(deck.slides):
        # ---- BODY_TOO_LONG ----
        wc = _body_word_count(slide)
        if wc > WORD_LIMIT_BODY:
            issues.append(AuditIssue(
                slide_idx=idx,
                severity=AUDIT_CODES["BODY_TOO_LONG"][0],
                code="BODY_TOO_LONG",
                message=f"Body has {wc} words — keep it ≤ {WORD_LIMIT_BODY} for readability.",
                fix=None,
            ))

        # ---- HEADING_HIERARCHY ----
        if idx > 0:
            prev = deck.slides[idx - 1]
            prev_g = _HEADING_GROUP.get(prev.layout, "body")
            cur_g = _HEADING_GROUP.get(slide.layout, "body")
            # A jump from body -> title (or any non-divider, non-adjacent group
            # change beyond title->body or body->closing) without a divider.
            jump = (
                prev_g == "body" and cur_g == "title"
            ) or (
                prev_g == "key" and cur_g == "title"
            ) or (
                prev_g == "closing" and cur_g != "closing"
            )
            if jump:
                issues.append(AuditIssue(
                    slide_idx=idx,
                    severity=AUDIT_CODES["HEADING_HIERARCHY"][0],
                    code="HEADING_HIERARCHY",
                    message=(
                        f"Layout shape jumped from '{prev.layout}' to '{slide.layout}' "
                        f"without a section_divider between them."
                    ),
                    fix=None,
                ))

        # ---- OFF_THEME_COLOR ----
        for block_name, style in (slide.block_style or {}).items():
            color = _hex_or_none(style.get("color")) if isinstance(style, dict) else None
            if color and color not in palette:
                issues.append(AuditIssue(
                    slide_idx=idx,
                    severity=AUDIT_CODES["OFF_THEME_COLOR"][0],
                    code="OFF_THEME_COLOR",
                    message=(
                        f"Block '{block_name}' uses {color}, which is not in the theme "
                        f"palette or brand kit."
                    ),
                    fix=None,
                ))

        # ---- UNUSED_ASSET_SLOT ----
        if slide.layout == "two_column":
            l = bool(slide.body_left)
            r = bool(slide.body_right)
            if l ^ r:  # exactly one populated
                issues.append(AuditIssue(
                    slide_idx=idx,
                    severity=AUDIT_CODES["UNUSED_ASSET_SLOT"][0],
                    code="UNUSED_ASSET_SLOT",
                    message=(
                        "Two-column layout but only "
                        f"{'left' if l else 'right'} column has content."
                    ),
                    fix={"action": "convert_to_content"},
                ))

        # ---- DUPLICATE_CONTENT ----
        title_key = (slide.title or "").strip().lower()
        if title_key:
            if title_key in titles_seen:
                first_idx = titles_seen[title_key]
                issues.append(AuditIssue(
                    slide_idx=idx,
                    severity=AUDIT_CODES["DUPLICATE_CONTENT"][0],
                    code="DUPLICATE_CONTENT",
                    message=(
                        f"Title '{slide.title}' is identical to slide #{first_idx + 1}."
                    ),
                    fix={"action": "append_suffix", "field": "title"},
                ))
            else:
                titles_seen[title_key] = idx

        first_bullet = ""
        if slide.body:
            first_bullet = (slide.body[0] or "").strip().lower()
        elif slide.body_left:
            first_bullet = (slide.body_left[0] or "").strip().lower()
        if first_bullet:
            if first_bullet in first_bullets_seen:
                first_idx = first_bullets_seen[first_bullet]
                issues.append(AuditIssue(
                    slide_idx=idx,
                    severity=AUDIT_CODES["DUPLICATE_CONTENT"][0],
                    code="DUPLICATE_CONTENT",
                    message=(
                        f"First bullet duplicates slide #{first_idx + 1}."
                    ),
                    fix={"action": "append_suffix", "field": "first_bullet"},
                ))
            else:
                first_bullets_seen[first_bullet] = idx

        # ---- MISSING_SPEAKER_NOTES (final / closing slide) ----
        is_final = (idx == len(deck.slides) - 1) or slide.layout == "closing"
        if is_final and not (slide.speaker_notes or "").strip():
            issues.append(AuditIssue(
                slide_idx=idx,
                severity=AUDIT_CODES["MISSING_SPEAKER_NOTES"][0],
                code="MISSING_SPEAKER_NOTES",
                message="Closing slide has no speaker notes.",
                fix={"action": "add_stub_note"},
            ))

        # ---- LOW_CONTRAST ----
        for block_name, style in (slide.block_style or {}).items():
            fg = _hex_or_none(style.get("color")) if isinstance(style, dict) else None
            if not fg:
                continue
            ratio = _contrast_ratio(fg, bg_hex)
            if ratio < 4.5:
                issues.append(AuditIssue(
                    slide_idx=idx,
                    severity=AUDIT_CODES["LOW_CONTRAST"][0],
                    code="LOW_CONTRAST",
                    message=(
                        f"Block '{block_name}' contrast is {ratio:.2f}:1 against "
                        f"background {bg_hex} — WCAG AA requires ≥ 4.5:1."
                    ),
                    fix=None,
                ))

        # ---- EMPTY_BODY ----
        body_visible_layouts = {"content", "bullet_list", "key_takeaway", "closing"}
        if (
            slide.layout in body_visible_layouts
            and not slide.body
            and not slide.body_left
            and not slide.body_right
            and not slide.extras
            and not slide.asset_ref
        ):
            issues.append(AuditIssue(
                slide_idx=idx,
                severity=AUDIT_CODES["EMPTY_BODY"][0],
                code="EMPTY_BODY",
                message=(
                    f"'{slide.layout}' slide has no body, no columns, and no extras."
                ),
                fix=None,
            ))

        # ---- OVERFLOWING_BBOX ----
        for block_name, bbox in (slide.block_bbox or {}).items():
            if not isinstance(bbox, dict) or not bbox:
                continue
            cap = _estimate_chars_fit(bbox, font_size=tpl.fonts.sizes.body)
            est = _estimated_text_length(slide, block_name)
            if est > cap * 1.15:  # 15% slop before flagging
                issues.append(AuditIssue(
                    slide_idx=idx,
                    severity=AUDIT_CODES["OVERFLOWING_BBOX"][0],
                    code="OVERFLOWING_BBOX",
                    message=(
                        f"Block '{block_name}' has ~{est} chars but the bbox fits "
                        f"~{cap} — text will overflow."
                    ),
                    fix=None,
                ))

    severity_rank = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (i.slide_idx, severity_rank.get(i.severity, 9), i.code))
    return issues


# ---------------------------------------------------------------------------
# Auto-fix
# ---------------------------------------------------------------------------

def apply_audit_fixes(
    deck: SlideDeck,
    issues: list[AuditIssue],
    *,
    codes: set[str] | None = None,
) -> tuple[SlideDeck, list[int]]:
    """Apply auto-fixable issues to a copy of the deck.

    Args:
        deck: source deck (unchanged).
        issues: results from audit_deck.
        codes: limit to these codes, or None for ALL auto-fixable codes.

    Returns:
        (new_deck, applied_issue_indexes) where applied_issue_indexes are the
        positions in `issues` that were applied.
    """
    target_codes = (codes or AUTO_FIXABLE_CODES) & AUTO_FIXABLE_CODES
    new_deck = deck.model_copy(deep=True)
    applied: list[int] = []

    for i, issue in enumerate(issues):
        if issue.code not in target_codes:
            continue
        if issue.slide_idx < 0 or issue.slide_idx >= len(new_deck.slides):
            continue
        slide = new_deck.slides[issue.slide_idx]
        fix = issue.fix or {}
        action = fix.get("action")

        if issue.code == "MISSING_SPEAKER_NOTES" and action == "add_stub_note":
            slide.speaker_notes = (
                f"[TODO: notes for closing slide '{slide.title}'.]"
            )
            applied.append(i)

        elif issue.code == "DUPLICATE_CONTENT" and action == "append_suffix":
            field = fix.get("field")
            if field == "title" and slide.title:
                slide.title = f"{slide.title} (cont.)"
                applied.append(i)
            elif field == "first_bullet":
                if slide.body:
                    slide.body[0] = f"{slide.body[0]} (cont.)"
                    applied.append(i)
                elif slide.body_left:
                    slide.body_left[0] = f"{slide.body_left[0]} (cont.)"
                    applied.append(i)

        elif issue.code == "UNUSED_ASSET_SLOT" and action == "convert_to_content":
            # Merge whichever column is populated into body and switch layout.
            if slide.body_left and not slide.body_right:
                slide.body = list(slide.body_left)
            elif slide.body_right and not slide.body_left:
                slide.body = list(slide.body_right)
            slide.body_left = []
            slide.body_right = []
            slide.layout = "content"
            applied.append(i)

    return new_deck, applied
