"""Anthropic SDK wrapper.

Single entry point — `complete()` — that:
  1. Checks the per-tenant daily spend cap.
  2. Inserts a `running` row in marketing_agent_runs.
  3. Calls the Anthropic API.
  4. Finalizes the row with tokens/cost/output (or error/over_budget).
  5. Returns the assistant text.

Prompts live in version-controlled files (see prompts.py). This module
deliberately does not own prompt content — callers pass the prompt name
+ version + rendered body in.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import anthropic

from . import budget, config, runs


class MissingAPIKey(RuntimeError):
    pass


def _cost_usd(
    model: str,
    tokens_in: int,
    tokens_out: int,
    *,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> float:
    """Cost in USD. Cache writes are billed 1.25× input; cache reads 0.1×.
    Best-effort — exact billing comes from the Anthropic console."""
    pricing = config.PRICING_PER_1M.get(model)
    if pricing is None:
        return 0.0
    inp = pricing["input"]
    out = pricing["output"]
    return (
        (tokens_in / 1_000_000) * inp
        + (cache_creation / 1_000_000) * inp * 1.25
        + (cache_read / 1_000_000) * inp * 0.10
        + (tokens_out / 1_000_000) * out
    )


SystemParam = str | list[dict[str, Any]] | None
UserContent = str | list[dict[str, Any]]


def complete(
    conn: sqlite3.Connection,
    *,
    tenant_id: int,
    tenant_cap_usd: float,
    layer: str,
    model: str,
    system: SystemParam,
    user_message: UserContent,
    max_tokens: int = 1024,
    prompt_name: str | None = None,
    prompt_version: int | None = None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make one LLM call. Returns dict with `text`, `tokens_in`, `tokens_out`, `cost_usd`, `run_id`."""
    if not config.ANTHROPIC_API_KEY:
        raise MissingAPIKey(
            "No Anthropic key in env. Set HATCHIK_ANTHROPIC_MASTER_KEY "
            "(or ANTHROPIC_API_KEY as fallback). Populate "
            "proposals/hatchik/marketing/.env for local dev, or "
            "/etc/hatchik/signup.env on the VPS."
        )

    budget.assert_within_cap(conn, tenant_id, tenant_cap_usd)

    input_payload: dict[str, Any] = {
        "system": system,
        "user_message": user_message,
        "max_tokens": max_tokens,
        "extra_params": extra_params or {},
    }
    run_id = runs.start_run(
        conn,
        tenant_id=tenant_id,
        layer=layer,
        model=model,
        input_payload=input_payload,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system if system else anthropic.NOT_GIVEN,
            messages=[{"role": "user", "content": user_message}],
            **(extra_params or {}),
        )
    except Exception as exc:
        runs.finish_run(conn, run_id, status="error", error=f"{type(exc).__name__}: {exc}")
        raise

    text = "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )
    tokens_in = message.usage.input_tokens
    tokens_out = message.usage.output_tokens
    cache_creation = getattr(message.usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(message.usage, "cache_read_input_tokens", 0) or 0
    cost = _cost_usd(
        model,
        tokens_in,
        tokens_out,
        cache_creation=cache_creation,
        cache_read=cache_read,
    )

    runs.finish_run(
        conn,
        run_id,
        status="success",
        output_payload={
            "text": text,
            "stop_reason": message.stop_reason,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
        },
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
    )

    return {
        "run_id": run_id,
        "text": text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_creation": cache_creation,
        "cache_read": cache_read,
        "cost_usd": cost,
    }
