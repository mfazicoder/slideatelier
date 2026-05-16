"""marketing_agent_runs CRUD helpers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_run(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    layer: str,
    model: str,
    input_payload: dict[str, Any],
    prompt_name: str | None = None,
    prompt_version: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO marketing_agent_runs
            (tenant_id, layer, prompt_name, prompt_version, model, input_json,
             status, started_at)
        VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
        """,
        (
            tenant_id,
            layer,
            prompt_name,
            prompt_version,
            model,
            json.dumps(input_payload, ensure_ascii=False),
            _now(),
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    output_payload: dict[str, Any] | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE marketing_agent_runs
        SET status = ?,
            output_json = ?,
            tokens_in = ?,
            tokens_out = ?,
            cost_usd = ?,
            error = ?,
            completed_at = ?
        WHERE id = ?
        """,
        (
            status,
            json.dumps(output_payload, ensure_ascii=False) if output_payload is not None else None,
            tokens_in,
            tokens_out,
            cost_usd,
            error,
            _now(),
            run_id,
        ),
    )
