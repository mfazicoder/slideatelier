"""
Hatchik operator agents — the "AI back-office" surface that turns
Product → Business for non-technical founders.

Each agent:
  - reads from the unified data layer (signups.db + per-tenant Postgres
    + Stripe + Resend + GitHub + Sentry + ai_proxy usage events)
  - emits structured Actions (send email, draft reply, run query, …)
  - some Actions auto-execute, some require founder approval
  - every run is recorded in mcp_audit_log + agent_runs

Agents are scheduled (cron-style) or event-driven (webhook, ticket
arrival). The runtime (`runtime.py`) pulls due agents and invokes them.

The framework is intentionally simple: one Agent class, one Action
class, one runtime loop. We're not building a generic workflow engine
— we're building a curated portfolio of high-leverage agents that
share infrastructure.
"""

from agents.base import Agent, AgentAction, AgentContext, AgentResult, ApprovalRequired
from agents.registry import REGISTRY, register, init_schema

__all__ = [
    "Agent", "AgentAction", "AgentContext", "AgentResult",
    "ApprovalRequired", "REGISTRY", "register", "init_schema",
]
