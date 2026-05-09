"""Atelier Copilot — sticky right-rail chat that emits scoped diff edits, not
full deck regenerations.

Design:
- The user types into a persistent textarea anchored to the wireframe page.
- Each turn carries a `selection` (slide / shape / theme). The route module
  passes ONLY the relevant slice of deck.json to Claude — never the whole
  deck — so Claude returns a tight JSON patch describing the change.
- A bespoke patch applier (see `apply_patch`) walks the patch and mutates the
  deck in place. Snapshots are taken by the caller before mutation so the
  user can undo/redo via the existing workflow_history machinery.
- A second helper, `rethink_slide`, asks Claude to suggest a more visually
  sophisticated AssetShape for a given slide's content (e.g. "those four
  bullets read like a 2x2 matrix").

NO live API calls happen at import time. All Anthropic calls go through
`make_client()` from claude_client.py and can be monkeypatched in tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

# Slash-shortcut parser ------------------------------------------------------

SelectionKind = Literal["slide", "shape", "theme", "deck"]


@dataclass
class ParsedPrompt:
    """Result of parsing a copilot prompt for slash shortcuts.

    `selection_override`: when the user typed `/slide 3` etc., this is what
    the route should use INSTEAD of whatever selection the JS state sent.
    """
    body: str
    selection_override: dict[str, Any] | None = None


def parse_prompt(raw: str) -> ParsedPrompt:
    """Strip a leading slash command (if any) and translate it into a selection.

    Supported shortcuts:
      /shape <id>       → selection = {"kind": "shape", "id": <id>}
      /theme            → selection = {"kind": "theme", "id": null}
      /slide N          → selection = {"kind": "slide", "id": int(N) - 1}
                          (1-based in the UI; 0-based internally)

    Anything else passes through with no override. Whitespace is collapsed at
    the head only — body content is preserved verbatim.
    """
    if not raw:
        return ParsedPrompt(body="")
    text = raw.lstrip()
    if not text.startswith("/"):
        return ParsedPrompt(body=raw)

    # Split into [cmd, rest]. The rest is everything after the first whitespace.
    head, _, tail = text.partition(" ")
    cmd = head.lower()
    rest = tail.lstrip()

    if cmd == "/shape":
        # /shape <id...>  — id may itself contain spaces; we take everything up
        # to the next newline as the id, the body is whatever follows on lines
        # below. Simple, predictable.
        if "\n" in rest:
            sid, _, body = rest.partition("\n")
            sid = sid.strip()
        else:
            sid, body = rest.strip(), ""
        return ParsedPrompt(
            body=body.strip(),
            selection_override={"kind": "shape", "id": sid or None},
        )

    if cmd == "/theme":
        return ParsedPrompt(
            body=rest.strip(),
            selection_override={"kind": "theme", "id": None},
        )

    if cmd == "/slide":
        # /slide N <body>   — N is 1-based for the user.
        if "\n" in rest:
            num_str, _, body = rest.partition("\n")
        else:
            num_str, _, body = rest.partition(" ")
        num_str = num_str.strip()
        try:
            n = int(num_str)
        except ValueError:
            # Unrecognized — pass through verbatim.
            return ParsedPrompt(body=raw)
        return ParsedPrompt(
            body=body.strip(),
            selection_override={"kind": "slide", "id": max(0, n - 1)},
        )

    # Unknown slash command — pass through.
    return ParsedPrompt(body=raw)


# Focused-slice builder ------------------------------------------------------

def build_focused_slice(deck: dict, selection: dict) -> dict:
    """Return a minimal dict containing ONLY the bits Claude needs.

    Selection shapes:
      {"kind": "slide",  "id": <int>}      → that slide only
      {"kind": "shape",  "id": <asset_id>} → registry entry for that shape
      {"kind": "theme",  "id": null}       → deck-level theme tokens
      {"kind": "deck",   "id": null}       → top-level metadata only

    The returned dict is what we serialize into the user message. Keeping it
    small is what makes scoped edits cheap and safe.
    """
    kind = selection.get("kind")
    if kind == "slide":
        idx = int(selection.get("id") or 0)
        slides = deck.get("slides") or []
        if 0 <= idx < len(slides):
            return {
                "kind": "slide",
                "slide_index": idx,
                "slide": slides[idx],
                "deck_title": deck.get("title", ""),
                "core_message": deck.get("core_message", ""),
            }
        return {"kind": "slide", "slide_index": idx, "slide": None}

    if kind == "shape":
        return {
            "kind": "shape",
            "shape_id": selection.get("id"),
        }

    if kind == "theme":
        return {
            "kind": "theme",
            "title": deck.get("title", ""),
            "subtitle": deck.get("subtitle", ""),
            "core_message": deck.get("core_message", ""),
            "narrative_arc": deck.get("narrative_arc", ""),
        }

    # Default: deck-level summary.
    return {
        "kind": "deck",
        "title": deck.get("title", ""),
        "subtitle": deck.get("subtitle", ""),
        "core_message": deck.get("core_message", ""),
        "narrative_arc": deck.get("narrative_arc", ""),
        "slide_count": len(deck.get("slides") or []),
    }


# Patch model ---------------------------------------------------------------
#
# Bespoke shape — NOT JSONPatch RFC6902. Two reasons:
#   1) Most edits are at well-known paths ("slide.title", "deck.subtitle"); a
#      plain dict-of-fields is much cleaner than a list of {op, path, value}.
#   2) We want Claude to express intent ("set slide.body to N items") not
#      micro-ops ("replace /slides/3/body/0 with 'foo'").
#
# Schema:
#   {
#     "scope":  "slide" | "shape" | "theme" | "deck",
#     "target": <int slide_index | str shape_id | None>,
#     "set":    { "<dotted.path>": <new value> },   # absolute paths in deck
#     "rationale": "<one-line why>"                 # for the turn log
#   }


@dataclass
class CopilotPatch:
    scope: SelectionKind
    target: Any
    set: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "target": self.target,
            "set": dict(self.set),
            "rationale": self.rationale,
        }


def _set_dotted(root: dict, dotted: str, value: Any) -> None:
    """Walk a dotted path on a dict and set the leaf. Creates intermediate
    dicts as needed. Integer-looking segments index into lists.
    """
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        return
    cur: Any = root
    for i, part in enumerate(parts[:-1]):
        nxt_part = parts[i + 1]
        # Decide what shape the next container is by looking at the next segment.
        if part.isdigit():
            idx = int(part)
            if not isinstance(cur, list):
                return  # type mismatch — bail safely
            while len(cur) <= idx:
                cur.append({})
            if cur[idx] is None or (
                not isinstance(cur[idx], (dict, list))
            ):
                cur[idx] = [] if nxt_part.isdigit() else {}
            cur = cur[idx]
        else:
            if not isinstance(cur, dict):
                return
            if part not in cur or not isinstance(cur.get(part), (dict, list)):
                cur[part] = [] if nxt_part.isdigit() else {}
            cur = cur[part]

    leaf = parts[-1]
    if leaf.isdigit():
        idx = int(leaf)
        if isinstance(cur, list):
            while len(cur) <= idx:
                cur.append(None)
            cur[idx] = value
    else:
        if isinstance(cur, dict):
            cur[leaf] = value


def apply_patch(deck: dict, patch: CopilotPatch | dict) -> dict:
    """Apply a CopilotPatch to a deck dict in place. Returns the (mutated) deck.

    The patch's `set` is a flat dict of dotted paths → values. The path is
    interpreted relative to the deck root, BUT for slide-scoped patches we
    auto-prefix paths so Claude can write "title" instead of
    "slides.<idx>.title". The rules:

      scope = "slide":  paths starting with "slide." are rewritten to
                        "slides.<target>....". Paths starting with "deck." are
                        rewritten to root paths (deck.subtitle → subtitle).
                        Everything else is treated as a slide-relative key.

      scope = "theme":  paths starting with "deck." or "theme." are rewritten
                        to root paths.

      scope = "deck":   paths are root-relative as written.

      scope = "shape":  shape edits do not touch deck.json — apply_patch
                        returns the deck unchanged (caller logs the patch).
    """
    if isinstance(patch, dict):
        patch_obj = CopilotPatch(
            scope=patch.get("scope", "deck"),
            target=patch.get("target"),
            set=dict(patch.get("set") or {}),
            rationale=str(patch.get("rationale") or ""),
        )
    else:
        patch_obj = patch

    if patch_obj.scope == "shape":
        return deck

    for path, value in (patch_obj.set or {}).items():
        if patch_obj.scope == "slide":
            try:
                idx = int(patch_obj.target)
            except (TypeError, ValueError):
                continue
            if path.startswith("slide."):
                rewritten = f"slides.{idx}." + path[len("slide."):]
            elif path.startswith("deck."):
                rewritten = path[len("deck."):]
            elif path.startswith("slides."):
                rewritten = path
            else:
                rewritten = f"slides.{idx}.{path}"
        elif patch_obj.scope == "theme":
            if path.startswith("deck.") or path.startswith("theme."):
                rewritten = path.split(".", 1)[1]
            else:
                rewritten = path
        else:  # deck
            rewritten = path
        _set_dotted(deck, rewritten, value)

    return deck


# Anthropic-facing helpers --------------------------------------------------
#
# Both helpers below take an `anthropic_client` argument. Tests inject a fake
# object exposing only the call shape we use (`messages.create(...)`), so no
# network access happens in CI.

COPILOT_SYSTEM_PROMPT = """You are Atelier Copilot — a senior consulting design assistant embedded in a slide editor.

The user is working on a SINGLE slice of a deck (one slide, one shape, or the theme). They have asked you to make a SCOPED edit. You must:

1. Make the smallest change that satisfies the request. Do NOT regenerate unrelated content.
2. Preserve the consultant-grade voice: insight-led titles, MECE bullets, ≤14 words each, specifics over vague claims.
3. Return a JSON patch describing exactly what to change. Nothing else.

The patch shape is:

{
  "scope": "slide" | "shape" | "theme" | "deck",
  "target": <int slide index, or shape id, or null>,
  "set": { "<dotted.path>": <new value>, ... },
  "rationale": "<one short line on why this change>"
}

For slide edits, dotted paths are slide-relative (e.g. "title", "body", "strap"). For theme edits, paths are deck-relative.

Output ONLY a single JSON object. No prose, no markdown fences."""


SHAPE_RETHINK_SYSTEM_PROMPT = """You are an Atelier shape consultant. Given a slide's content and a registry of available shape IDs, propose 1–3 visual primitives that would represent the content better than plain bullets.

Each suggestion must include:
- shape_id: from the provided registry
- rationale: one line explaining why this shape fits THIS content (be specific — reference the bullets)
- confidence: "high" | "medium" | "low"

Output ONLY a JSON object: {"suggestions": [{...}, ...]}. No prose."""


def _extract_json(text: str) -> dict:
    """Pull the first balanced {...} from a model response. Tolerates fences."""
    if not text:
        return {}
    s = text.strip()
    if s.startswith("```"):
        # strip the first fence line and a trailing fence
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: -3]
        # If the fence had a language tag (json), the first line was eaten.
    # Find the first '{' and the matching '}' by depth-tracking.
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                blob = s[start : i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return {}
    return {}


def ask_copilot(
    anthropic_client: Any,
    *,
    model: str,
    prompt: str,
    selection: dict,
    focused_slice: dict,
) -> CopilotPatch:
    """Round-trip with Claude for one copilot turn. Returns a CopilotPatch.

    Tests inject a fake client whose `.messages.create(...)` returns an object
    with `.content[0].text == '<json string>'`.
    """
    user_msg = (
        f"Selection: {json.dumps(selection)}\n\n"
        f"Relevant slice of the deck:\n{json.dumps(focused_slice, indent=2)}\n\n"
        f"User request: {prompt}\n\n"
        "Return the JSON patch."
    )
    response = anthropic_client.messages.create(
        model=model,
        max_tokens=2048,
        system=COPILOT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = ""
    try:
        text = response.content[0].text  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        text = str(response)
    parsed = _extract_json(text)
    return CopilotPatch(
        scope=parsed.get("scope") or selection.get("kind") or "deck",
        target=parsed.get("target", selection.get("id")),
        set=dict(parsed.get("set") or {}),
        rationale=str(parsed.get("rationale") or ""),
    )


def rethink_slide(
    anthropic_client: Any,
    *,
    model: str,
    slide: dict,
    available_shape_ids: list[str],
) -> list[dict]:
    """Ask Claude to suggest better visual shapes for a slide's content.

    Returns a list of {shape_id, rationale, confidence}. Empty list on parse
    failure — never raises.
    """
    user_msg = (
        f"Available shape IDs:\n{json.dumps(available_shape_ids[:80])}\n\n"
        f"Slide content:\n{json.dumps({k: slide.get(k) for k in ('layout','title','strap','body','body_left','body_right')}, indent=2)}\n\n"
        "Suggest the 1–3 strongest visual shape options."
    )
    response = anthropic_client.messages.create(
        model=model,
        max_tokens=1024,
        system=SHAPE_RETHINK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = ""
    try:
        text = response.content[0].text  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        text = str(response)
    parsed = _extract_json(text)
    raw = parsed.get("suggestions") or []
    out: list[dict] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            sid = item.get("shape_id")
            if not sid:
                continue
            out.append({
                "shape_id": str(sid),
                "rationale": str(item.get("rationale") or ""),
                "confidence": str(item.get("confidence") or "medium"),
            })
    return out


# Turn log ------------------------------------------------------------------

def append_turn_log(
    job_dir: Any,  # Path
    *,
    prompt: str,
    selection: dict,
    patch: CopilotPatch | dict,
    success: bool,
    error: str = "",
) -> None:
    """Append one JSON-line record to <job_dir>/copilot/turns.jsonl.

    The directory is created lazily. Each line is the full audit record so an
    operator can replay a session.
    """
    from datetime import datetime, timezone
    from pathlib import Path as _P

    job_dir = _P(job_dir)
    target = job_dir / "copilot" / "turns.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(patch, CopilotPatch):
        patch_dict = patch.to_dict()
    else:
        patch_dict = dict(patch or {})
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt,
        "selection": selection,
        "patch": patch_dict,
        "success": bool(success),
        "error": error or "",
    }
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
