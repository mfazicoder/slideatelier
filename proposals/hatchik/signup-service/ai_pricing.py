"""
ai_pricing.py — input/output token rates for the AI passthrough proxy.

Pricing-policy reference: PRODUCT_OFFERING.md §3.1
- Pass-through cost × 1.6 (40% Hatchik net + Paddle fees baked in).
- The "cost" we charge for is what Anthropic/OpenAI charge us per
  million tokens. Once we cross enterprise volume, the master-key
  contract rate can drop — adjust the tables here without changing any
  customer-facing copy.

Rates are stored in **micropounds per million tokens** (the smallest
unit the rest of the stack speaks; ``ai_credit.record_event`` accepts
``cost_pence`` and applies the × 1.6 markup). Stored as int so there's
no float drift across millions of calls.

GBP rates are derived from the provider's USD list at FX_USD_GBP. Re-run
``python -m signup-service.ai_pricing --print`` when rates or FX shift —
it just prints the table for review.

Add new models by extending the dict — no other code changes needed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

Provider = Literal["anthropic", "openai"]

FX_USD_GBP = float(os.environ.get("HATCHIK_USD_GBP_FX", "0.78"))


@dataclass(frozen=True)
class ModelRate:
    """Per-million-token rates in USD. We convert at the FX in effect."""
    input_usd_per_mtok: float
    output_usd_per_mtok: float


# ─── Anthropic rates ─────────────────────────────────────────────────────
# Source: anthropic.com/pricing (Claude 4.x family).
ANTHROPIC: dict[str, ModelRate] = {
    "claude-opus-4-7":         ModelRate(15.00, 75.00),
    "claude-opus-4-6":         ModelRate(15.00, 75.00),
    "claude-sonnet-4-6":       ModelRate(3.00, 15.00),
    "claude-sonnet-4-5":       ModelRate(3.00, 15.00),
    "claude-haiku-4-5":        ModelRate(0.80, 4.00),
    # Aliases customers commonly type with the date suffix.
    "claude-opus-4-7-20251001":   ModelRate(15.00, 75.00),
    "claude-sonnet-4-6-20251001": ModelRate(3.00, 15.00),
    "claude-haiku-4-5-20251001":  ModelRate(0.80, 4.00),
}

# ─── OpenAI rates ────────────────────────────────────────────────────────
# Source: openai.com/api/pricing (GPT-5 family + legacy 4.x).
OPENAI: dict[str, ModelRate] = {
    "gpt-5":          ModelRate(5.00, 20.00),
    "gpt-5-mini":     ModelRate(0.50, 2.00),
    "gpt-5-nano":     ModelRate(0.10, 0.40),
    "gpt-4o":         ModelRate(2.50, 10.00),
    "gpt-4o-mini":    ModelRate(0.15, 0.60),
    "o1":             ModelRate(15.00, 60.00),
    "o1-mini":        ModelRate(3.00, 12.00),
}


# ─── Public helper ───────────────────────────────────────────────────────
def cost_pence(
    provider: Provider, model: str,
    tokens_in: int, tokens_out: int,
) -> int:
    """Compute the **raw provider cost** in pence (before markup).

    Returns the cost without Hatchik's × 1.6 markup — `ai_credit.record_event`
    applies that. Unknown models bill at a conservative "Opus-equivalent"
    rate so we don't silently undercount; logs are emitted for the
    operator to add the missing row.
    """
    rates = ANTHROPIC if provider == "anthropic" else OPENAI
    rate = rates.get(model) or _fallback_rate(provider, model)
    usd = (
        (tokens_in / 1_000_000.0) * rate.input_usd_per_mtok
        + (tokens_out / 1_000_000.0) * rate.output_usd_per_mtok
    )
    gbp = usd * FX_USD_GBP
    return max(0, int(round(gbp * 100)))


def _fallback_rate(provider: Provider, model: str) -> ModelRate:
    """Defensive default for an unknown model — bills at flagship rate."""
    import logging
    logging.getLogger("ai_pricing").warning(
        "Unknown model %r for provider %s — billing at flagship rate",
        model, provider,
    )
    if provider == "anthropic":
        return ANTHROPIC["claude-opus-4-7"]
    return OPENAI["gpt-5"]


# ─── CLI: print the table for review ─────────────────────────────────────
def _print_table() -> None:
    print(f"FX_USD_GBP = {FX_USD_GBP}\n")
    for label, table in (("ANTHROPIC", ANTHROPIC), ("OPENAI", OPENAI)):
        print(f"## {label}")
        print(f"  {'model':<32} {'$/Mt in':>10} {'$/Mt out':>10}")
        for m, r in table.items():
            print(f"  {m:<32} {r.input_usd_per_mtok:>10.2f} {r.output_usd_per_mtok:>10.2f}")
        print()


if __name__ == "__main__":
    _print_table()
