"""Strategy schema + marketing_strategies CRUD.

The strategy is the output of Layer 1 (persona/strategy agent). It
describes who we market to, in what voice, around which themes — and
is the input to every downstream layer. There's exactly one current
strategy per tenant; new strategies bump the version and flip
is_current off on prior rows.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


FormatHint = Literal["x_thread", "x_tweet", "linkedin", "blog", "email"]


class Angle(BaseModel):
    hook: str = Field(min_length=8)
    format_hint: FormatHint


class Pillar(BaseModel):
    name: str = Field(min_length=2)
    description: str
    why_it_matters: str
    angles: list[Angle]

    @field_validator("angles")
    @classmethod
    def at_least_eight_angles(cls, v: list[Angle]) -> list[Angle]:
        if len(v) < 8:
            raise ValueError(f"need at least 8 angles per pillar, got {len(v)}")
        return v


class ICP(BaseModel):
    primary: str
    company_type: str | None = None
    team_size: str | None = None
    stage: str | None = None
    geo: str | None = None
    pain_points: list[str]
    buying_triggers: list[str]
    excludes: list[str] = []


class SubPersona(BaseModel):
    name: str
    role: str
    context: str
    objection: str
    hook: str


class Voice(BaseModel):
    tone_attributes: list[str]
    do: list[str]
    dont: list[str]
    example_phrases: list[str] = []


class Strategy(BaseModel):
    icp: ICP
    sub_personas: list[SubPersona]
    voice: Voice
    pillars: list[Pillar]

    @model_validator(mode="after")
    def _shape_constraints(self) -> "Strategy":
        if not (2 <= len(self.sub_personas) <= 4):
            raise ValueError(f"sub_personas: want 2-4, got {len(self.sub_personas)}")
        if not (4 <= len(self.pillars) <= 6):
            raise ValueError(f"pillars: want 4-6, got {len(self.pillars)}")
        return self


class StrategyParseError(Exception):
    """Raised when the LLM output can't be parsed/validated into a Strategy."""


def parse(raw: str) -> Strategy:
    """Parse an LLM response into a Strategy. Tolerant of code fences."""
    text = raw.strip()
    # Strip a single ```json … ``` fence if present.
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[: -3].rstrip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StrategyParseError(f"invalid JSON: {exc}") from exc

    try:
        return Strategy.model_validate(data)
    except ValidationError as exc:
        raise StrategyParseError(f"schema violation: {exc}") from exc


def save(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    strategy: Strategy,
    source_run_id: int | None,
) -> int:
    """Insert a new strategy version; flip prior is_current to 0. Returns new row id."""
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 AS next_v FROM marketing_strategies WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    next_version = int(row["next_v"])

    conn.execute(
        "UPDATE marketing_strategies SET is_current = 0 WHERE tenant_id = ? AND is_current = 1",
        (tenant_id,),
    )
    cur = conn.execute(
        """
        INSERT INTO marketing_strategies
            (tenant_id, version, payload_json, source_run_id, is_current, generated_at)
        VALUES (?, ?, ?, ?, 1, ?)
        """,
        (
            tenant_id,
            next_version,
            strategy.model_dump_json(),
            source_run_id,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def current(conn: sqlite3.Connection, tenant_id: int) -> tuple[int, Strategy] | None:
    """Return (version, strategy) for the tenant's current strategy, or None."""
    row = conn.execute(
        """
        SELECT version, payload_json
        FROM marketing_strategies
        WHERE tenant_id = ? AND is_current = 1
        ORDER BY version DESC
        LIMIT 1
        """,
        (tenant_id,),
    ).fetchone()
    if row is None:
        return None
    return int(row["version"]), Strategy.model_validate_json(row["payload_json"])
