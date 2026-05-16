"""Runtime configuration.

Reads env vars (with `.env` autoloaded for local dev). On prod the
systemd unit's EnvironmentFile supplies the same vars.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_REPO_ROOT / ".env", override=False)


# Hatchik-wide convention (set in /etc/hatchik/signup.env, consumed by
# ai_proxy.py too). Fall back to the SDK-standard name for portability —
# the Anthropic Python SDK reads ANTHROPIC_API_KEY automatically when no
# api_key is passed.
ANTHROPIC_API_KEY: str = (
    os.environ.get("HATCHIK_ANTHROPIC_MASTER_KEY")
    or os.environ.get("ANTHROPIC_API_KEY", "")
)

# Shared with signup-service. On prod = /var/lib/hatchik/signups.db.
DB_PATH: Path = Path(os.environ.get("HATCHIK_SIGNUP_DB", _REPO_ROOT / "marketing.dev.db"))

DAILY_CAP_USD: float = float(os.environ.get("MARKETING_DAILY_CAP_USD", "5.00"))


# Canonical model IDs. Pinned versions, not aliases, so behavior is
# reproducible from agent_runs logs even after model deprecations.
MODEL_REASONING: str = "claude-opus-4-7"   # Layers 1, 4
MODEL_CONTENT: str = "claude-sonnet-4-6"   # Layer 2 (high volume)


# USD per 1M tokens. Authoritative table lives in
# proposals/hatchik/signup-service/ai_pricing.py (consumed by the AI
# passthrough proxy). Keep this dict in sync — the marketing agent and
# the proxy agree on costs so cross-system reports reconcile.
PRICING_PER_1M: dict[str, dict[str, float]] = {
    "claude-opus-4-7":              {"input": 15.0, "output": 75.0},
    "claude-opus-4-6":              {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6":            {"input":  3.0, "output": 15.0},
    "claude-sonnet-4-5":            {"input":  3.0, "output": 15.0},
    "claude-haiku-4-5":             {"input":  0.80, "output": 4.0},
    "claude-haiku-4-5-20251001":    {"input":  0.80, "output": 4.0},
}
