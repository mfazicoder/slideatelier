"""
Agent base class + the shared types every agent emits / consumes.

Design rules:
  * Agents are STATELESS between runs. Whatever state needs to persist
    (last seen ticket id, last computed cohort table, …) lives in
    signups.db via the agent's `state_get` / `state_set` helpers.
  * Agent.run() returns a list of Actions. The runtime decides which
    auto-execute and which need approval based on Action.requires_approval.
  * Agents never call LLM providers directly — they go through
    `ctx.llm()` which routes via ai_proxy + meters the tokens against
    the customer's AI credit.
  * Agents never touch the customer's substrate directly — only via
    `ctx.tenant_db()` (read-only), `ctx.send_email()`, etc. The
    helpers enforce the boundary.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal


# ─── Action shapes ────────────────────────────────────────────────────────
ActionKind = Literal[
    "send_email",       # to a tenant's customer
    "post_message",     # to founder's /account inbox
    "schedule_followup",  # re-run agent at a specific time
    "write_to_db",      # mutate something in tenant_db (rare; approval required)
    "trigger_webhook",  # call a substrate-template webhook
    "log_only",         # just record, nothing to do
]


@dataclass
class AgentAction:
    kind: ActionKind
    summary: str                          # one-line description for the audit + approval UI
    payload: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False       # if True, runtime parks it; founder confirms in /account
    confidence: float = 1.0               # 0..1 — agent's self-assessed certainty
    rationale: str = ""                   # why the agent picked this action (LLM reasoning)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "summary": self.summary,
            "payload": self.payload, "requires_approval": self.requires_approval,
            "confidence": self.confidence, "rationale": self.rationale,
        }


@dataclass
class AgentResult:
    actions: list[AgentAction] = field(default_factory=list)
    notes: str = ""                       # free-text human-readable summary of the run
    metrics: dict[str, Any] = field(default_factory=dict)  # numeric outputs, dashboards consume these


class ApprovalRequired(Exception):
    """Raise this from inside an agent to short-circuit with one
    pending-approval action. More ergonomic than returning a list with
    a single approval entry."""

    def __init__(self, summary: str, payload: dict[str, Any], rationale: str = ""):
        super().__init__(summary)
        self.action = AgentAction(
            kind="post_message", summary=summary,
            payload=payload, requires_approval=True,
            rationale=rationale,
        )


# ─── Agent context — what the runtime hands to each agent ────────────────
@dataclass
class AgentContext:
    """Everything the agent needs to do its job, injected by the runtime.

    The fields starting with _ are runtime callbacks — they're set by
    the runtime before invoking the agent. Agents call the helpers
    (llm, send_email, …) rather than the underscore fields directly.
    """
    signup_id: int
    slug: str                             # tenant slug
    tier: Literal["sandbox", "launch", "growth"]
    now: datetime

    # Agent identity (set by runtime)
    agent_name: str = ""
    run_id: int = 0

    # Callbacks (set by runtime; see runtime.py)
    _llm: Callable[..., dict[str, Any]] | None = None
    _tenant_db: Callable[[str, tuple[Any, ...]], list[dict[str, Any]]] | None = None
    _send_email: Callable[..., dict[str, Any]] | None = None
    _state_get: Callable[[str], Any] | None = None
    _state_set: Callable[[str, Any], None] | None = None

    # ── LLM ──
    def llm(
        self, *,
        system: str, user: str,
        model: str = "claude-haiku-4-5",
        max_tokens: int = 1000,
        provider: Literal["anthropic", "openai"] = "anthropic",
    ) -> dict[str, Any]:
        """Call an LLM via ai_proxy. Returns the provider's response dict.

        Cost is automatically metered + billed against the customer's
        AI credit allowance (× 1.6 markup applied at billing).
        """
        if not self._llm:
            raise RuntimeError("AgentContext.llm not wired by runtime")
        return self._llm(
            system=system, user=user, model=model,
            max_tokens=max_tokens, provider=provider,
        )

    # ── Tenant Postgres (read-only) ──
    def tenant_db(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Read-only query against the tenant's Postgres. Writes raise.

        The runtime injects a connection with a read-only role so even
        if an agent's LLM hallucinates an UPDATE statement, it fails
        at the database, not at our policy layer.
        """
        if not self._tenant_db:
            raise RuntimeError("AgentContext.tenant_db not wired by runtime")
        return self._tenant_db(sql, params)

    # ── Email ──
    def send_email(
        self, *, to: str, subject: str, body_md: str,
        from_name: str = "", reply_to: str = "",
    ) -> dict[str, Any]:
        """Send an email via Resend on the tenant's domain (Launch+)
        or the shared noreply@hatchik.com sender (Sandbox).

        Auto-records the send into mcp_audit_log so the founder sees
        every customer-facing message the agent emitted.
        """
        if not self._send_email:
            raise RuntimeError("AgentContext.send_email not wired by runtime")
        return self._send_email(
            to=to, subject=subject, body_md=body_md,
            from_name=from_name, reply_to=reply_to,
        )

    # ── State ──
    def state_get(self, key: str, default: Any = None) -> Any:
        if not self._state_get:
            return default
        v = self._state_get(key)
        return default if v is None else v

    def state_set(self, key: str, value: Any) -> None:
        if not self._state_set:
            return
        self._state_set(key, value)


# ─── Agent base class ────────────────────────────────────────────────────
class Agent(ABC):
    """Subclass this, set NAME + DESCRIPTION + SCHEDULE, implement run()."""

    NAME: str = ""
    DESCRIPTION: str = ""
    # Cron-style schedule string OR 'event' for event-driven only.
    # The runtime parses this with croniter. 'event' means the agent
    # is only invoked by webhook / external trigger.
    SCHEDULE: str = ""
    # Default-on per tier — customers opt-out via /account → Agents.
    DEFAULT_ON_TIERS: tuple[str, ...] = ("launch", "growth")

    @abstractmethod
    def run(self, ctx: AgentContext) -> AgentResult:
        """Do the work. Return a result with actions + notes + metrics."""
        ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
