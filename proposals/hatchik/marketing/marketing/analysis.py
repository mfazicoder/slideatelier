"""Analysis report schema (Layer 4 output).

`AnalysisReport` is what the analyze agent emits. It bundles:
  - a quantitative summary
  - winners and losers from the past week with lessons
  - hypotheses to test
  - explicit strategy_changes (deltas)
  - a full updated_strategy (the new replacement Strategy JSON)

The agent saves updated_strategy as a new strategy version. The full
report is stored in `marketing_agent_runs.output_json` so subsequent
`analysis show` calls can pull it back out without a separate table.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import BaseModel, ValidationError

from .strategy import Strategy


class _Note(BaseModel):
    distribution_id: int | None = None
    pillar: str | None = None
    what: str
    lesson: str


class StrategyChanges(BaseModel):
    voice_do_additions: list[str] = []
    voice_dont_additions: list[str] = []
    pillars_to_amplify: list[str] = []
    pillars_to_deprecate: list[str] = []
    new_angles_per_pillar: dict[str, list[str]] = {}
    icp_refinements: list[str] = []


class AnalysisReport(BaseModel):
    summary: dict[str, Any]
    winners: list[_Note]
    losers: list[_Note]
    hypotheses: list[str]
    strategy_changes: StrategyChanges
    updated_strategy: Strategy


class AnalysisParseError(Exception):
    pass


def parse(raw: str) -> AnalysisReport:
    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[: -3].rstrip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnalysisParseError(f"invalid JSON: {exc}") from exc
    try:
        return AnalysisReport.model_validate(data)
    except ValidationError as exc:
        raise AnalysisParseError(f"schema violation: {exc}") from exc


def latest_report(
    conn: sqlite3.Connection, *, tenant_id: int
) -> tuple[int, AnalysisReport] | None:
    """Return (run_id, report) for the most recent successful analyze
    run, or None if there isn't one yet."""
    row = conn.execute(
        """
        SELECT id, output_json
        FROM marketing_agent_runs
        WHERE tenant_id = ? AND layer = 'analyze' AND status = 'success'
        ORDER BY id DESC
        LIMIT 1
        """,
        (tenant_id,),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["output_json"])
    text = payload.get("text", "")
    try:
        report = parse(text)
    except AnalysisParseError:
        return None
    return row["id"], report
