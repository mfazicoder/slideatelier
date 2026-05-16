"""Prompt loader.

Prompts live as Markdown files under `prompts/` with optional YAML-ish
front matter for `model` + `params`. Files are the source of truth;
`marketing_prompt_versions` mirrors them so `agent_runs` rows can join
to the exact body that produced them.

File layout:    prompts/<name>/v<N>.md
Body format:    optional `---` frontmatter block, then the prompt body.

Example:
    ---
    model: claude-opus-4-7
    params:
      max_tokens: 1024
    ---
    You are a positioning copywriter...
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = _REPO_ROOT / "prompts"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_FILENAME_RE = re.compile(r"^v(\d+)\.md$")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    model: str
    body: str
    params: dict[str, Any]


def _parse(path: Path) -> tuple[dict[str, Any], str]:
    """Parse YAML-ish frontmatter. Only supports `key: value` and one
    nested `params:` block with `  key: value` lines. Kept minimal to
    avoid a yaml dependency for a one-screen need."""
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    raw_fm, body = match.group(1), match.group(2)
    meta: dict[str, Any] = {}
    params: dict[str, Any] = {}
    in_params = False
    for line in raw_fm.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("params:"):
            in_params = True
            continue
        if in_params and line.startswith("  "):
            k, _, v = line.strip().partition(":")
            params[k.strip()] = _coerce(v.strip())
            continue
        in_params = False
        k, _, v = line.partition(":")
        meta[k.strip()] = _coerce(v.strip())

    if params:
        meta["params"] = params
    return meta, body


def _coerce(raw: str) -> Any:
    if raw.isdigit():
        return int(raw)
    try:
        return float(raw)
    except ValueError:
        pass
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    return raw.strip('"').strip("'")


def load(name: str, version: int | None = None) -> Prompt:
    """Load a prompt from disk. If version is None, returns the highest."""
    dir_ = PROMPTS_DIR / name
    if not dir_.is_dir():
        raise FileNotFoundError(f"No prompt directory {dir_}")
    versions: dict[int, Path] = {}
    for entry in dir_.iterdir():
        m = _FILENAME_RE.match(entry.name)
        if m:
            versions[int(m.group(1))] = entry
    if not versions:
        raise FileNotFoundError(f"No v<N>.md files in {dir_}")
    v = version if version is not None else max(versions)
    if v not in versions:
        raise FileNotFoundError(f"Prompt {name} v{v} not found (have {sorted(versions)})")

    meta, body = _parse(versions[v])
    return Prompt(
        name=name,
        version=v,
        model=str(meta.get("model", "")),
        body=body.strip(),
        params=meta.get("params", {}),
    )


def mirror_to_db(conn: sqlite3.Connection, prompt: Prompt) -> None:
    """Upsert this prompt version into marketing_prompt_versions. Idempotent."""
    import json

    existing = conn.execute(
        "SELECT id FROM marketing_prompt_versions WHERE name = ? AND version = ?",
        (prompt.name, prompt.version),
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        """
        INSERT INTO marketing_prompt_versions (name, version, model, body, params_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            prompt.name,
            prompt.version,
            prompt.model,
            prompt.body,
            json.dumps(prompt.params, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
