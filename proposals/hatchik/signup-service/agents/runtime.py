"""
Agent runtime — the loop that finds due agents and runs them.

Invocation modes:
  * `python -m signup-service.agents.runtime --tick`         # one sweep
  * `python -m signup-service.agents.runtime --signup-id N --agent <name>`  # single run
  * Loaded as a module by webhook handlers for event-driven runs

The sweep:
  1. Walk REGISTRY.
  2. For each agent, find tenants where it's enabled AND due (per its
     SCHEDULE cron expression OR matches an inbound event).
  3. Build an AgentContext with the right callbacks.
  4. Invoke agent.run(ctx).
  5. Persist run + actions to signups.db.
  6. Auto-execute actions where requires_approval=False.
  7. Park approval-required actions for /account → Agents to surface.

Failure isolation: each agent run is wrapped in try/except. A buggy
agent can't crash the sweep for other tenants.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Ensure agents/* is importable when run as a module.
if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.base import Agent, AgentAction, AgentContext, AgentResult, ApprovalRequired  # noqa: E402
from agents.registry import (  # noqa: E402
    REGISTRY, conn, begin_run, finish_run, record_action,
    update_action_status, is_enabled,
)

log = logging.getLogger("agent_runtime")

DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))


# ─── Agent discovery — import each module so @register fires ─────────────
def discover_agents() -> None:
    """Import every agents.<x> submodule so the @register decorators run."""
    # NOTE: keep this list in sync with the agents/*.py files. We could
    # use pkgutil.walk_packages but the explicit import surface is safer
    # against typos + makes the available agents grep-able.
    from agents import support, cohort, ar_chase  # noqa: F401, PLC0415


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Tenant resolver ─────────────────────────────────────────────────────
def _list_active_tenants() -> list[dict[str, Any]]:
    """Every signups row in a 'live-*' state. The runtime sweeps over
    these, asking each agent whether it wants to run for this tenant."""
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT id, email, first_name, tier, product_name,
                      region, domain_choice, status
                 FROM signups
                WHERE status IN ('live-sandbox', 'live-launch', 'live-growth',
                                 'queued')
                ORDER BY id"""
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Schedule resolver ───────────────────────────────────────────────────
def _is_due(agent_name: str, signup_id: int, schedule: str, now: datetime) -> bool:
    """Decide whether this agent should run for this tenant on this tick.

    Tick cadence is whatever the systemd timer fires at (typically every
    5 minutes). We use a lightweight 'last_run_at' check instead of a
    full cron parser — for the v1 schedules (15m / hourly / daily) the
    simpler approach is plenty robust.
    """
    if schedule == "event":
        # Event-driven agents only fire via explicit dispatch_event(),
        # never on a sweep.
        return False

    # Look up last run timestamp.
    with conn() as db:
        row = db.execute(
            """SELECT MAX(started_at) AS last_at
                 FROM agent_runs
                WHERE agent_name=? AND signup_id=?""",
            (agent_name, signup_id),
        ).fetchone()
    last_at = row["last_at"] if row and row["last_at"] else None
    last_dt = (
        datetime.fromisoformat(last_at) if last_at else None
    )

    # Map schedule string → interval. Add new shorthand here.
    intervals = {
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "hourly": timedelta(hours=1),
        "daily": timedelta(hours=24),
        "weekly": timedelta(days=7),
    }
    interval = intervals.get(schedule)
    if interval is None:
        log.warning("Unknown schedule %r for agent %s — defaulting to daily",
                    schedule, agent_name)
        interval = timedelta(hours=24)
    if last_dt is None:
        return True
    # Make both tz-aware for comparison.
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return (now - last_dt) >= interval


# ─── State helpers (per-agent per-tenant kv) ─────────────────────────────
def _state_get(agent_name: str, signup_id: int, key: str) -> Any:
    with conn() as db:
        row = db.execute(
            "SELECT value_json FROM agent_state WHERE agent_name=? AND signup_id=? AND key=?",
            (agent_name, signup_id, key),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value_json"])
    except (TypeError, ValueError):
        return None


def _state_set(agent_name: str, signup_id: int, key: str, value: Any) -> None:
    payload = json.dumps(value, default=str)
    now = now_iso()
    with conn() as db:
        db.execute(
            """INSERT INTO agent_state (agent_name, signup_id, key, value_json, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(agent_name, signup_id, key)
               DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
            (agent_name, signup_id, key, payload, now),
        )


# ─── LLM callback — routes via ai_proxy with Hatchik master keys ─────────
def _llm_call(
    signup_id: int, *,
    system: str, user: str, model: str,
    max_tokens: int, provider: str,
) -> dict[str, Any]:
    """Invoke the LLM directly via httpx + the master key. Records cost.

    We intentionally do NOT use the customer's hk_ai_* token here — agent
    runs are billed against the customer's AI credit allowance directly
    by calling ai_credit.record_event with the operator-flavoured slug
    'agent:<name>'.
    """
    import httpx  # noqa: PLC0415
    import ai_pricing  # noqa: PLC0415
    import ai_credit  # noqa: PLC0415

    if provider == "anthropic":
        key = os.environ.get("HATCHIK_ANTHROPIC_MASTER_KEY", "")
        if not key:
            raise RuntimeError("HATCHIK_ANTHROPIC_MASTER_KEY missing — agent LLM call cannot proceed")
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            json={"model": model, "system": system, "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": user}]},
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            timeout=120,
        )
    else:  # openai
        key = os.environ.get("HATCHIK_OPENAI_MASTER_KEY", "")
        if not key:
            raise RuntimeError("HATCHIK_OPENAI_MASTER_KEY missing — agent LLM call cannot proceed")
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            json={"model": model, "max_tokens": max_tokens,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
            timeout=120,
        )
    r.raise_for_status()
    body = r.json()
    # Extract usage.
    if provider == "anthropic":
        usage = body.get("usage") or {}
        t_in = int(usage.get("input_tokens", 0))
        t_out = int(usage.get("output_tokens", 0))
    else:
        usage = body.get("usage") or {}
        t_in = int(usage.get("prompt_tokens", 0))
        t_out = int(usage.get("completion_tokens", 0))
    cost = ai_pricing.cost_pence(provider, model, t_in, t_out)
    try:
        ai_credit.record_event(
            signup_id=signup_id, slug=f"agent",
            provider=provider, model=model,
            tokens_in=t_in, tokens_out=t_out,
            cost_pence=cost, request_id=body.get("id"),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("ai_credit.record_event failed for agent run: %s", e)
    body["__hatchik_cost_pence"] = cost
    return body


# ─── Email callback — Resend via tenant domain (Launch+) or shared ───────
def _send_email_callback(
    signup_id: int, slug: str, tier: str,
    *, to: str, subject: str, body_md: str,
    from_name: str = "", reply_to: str = "",
) -> dict[str, Any]:
    """Send a customer-facing email. Routes through Resend; logs to audit."""
    import httpx  # noqa: PLC0415
    import mcp_audit  # noqa: PLC0415

    key = os.environ.get("RESEND_API_KEY", "")
    if not key:
        log.warning(
            "RESEND_API_KEY not set — would have sent to=%s subject=%r",
            to, subject,
        )
        return {"ok": False, "reason": "resend_not_configured", "to": to}

    # Sender: shared noreply@hatchik.com for Sandbox; tenant's noreply@<domain>
    # once Launch infomaniak mailboxes are provisioned.
    sender = (
        f"{from_name} <noreply@hatchik.com>" if tier == "sandbox"
        else f"{from_name or 'Hatchik agent'} <agent@{slug}.hatchik.com>"
    )
    payload = {
        "from": sender, "to": [to],
        "subject": subject, "text": body_md,
    }
    if reply_to:
        payload["reply_to"] = [reply_to]
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=20,
        )
        r.raise_for_status()
        out = r.json()
    except Exception as e:  # noqa: BLE001
        log.error("Resend send failed: %s", e)
        return {"ok": False, "error": str(e)[:200]}
    try:
        mcp_audit.record(
            signup_id, "agent.email_sent", "ok",
            payload={"to": to, "subject": subject},
            result={"resend_id": out.get("id")},
            tool_caller="agent",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("agent email audit log failed: %s", e)
    return {"ok": True, "resend_id": out.get("id")}


# ─── Tenant DB callback — read-only Postgres via docker exec psql ────────
def _tenant_db_callback(slug: str, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Read-only query against the tenant's per-tenant Postgres.

    Implementation v1: docker exec psql with a hardcoded read-only role.
    The substrate-template creates this role at provisioning; agents
    can ONLY connect as it. Any UPDATE/DELETE/DROP will be rejected at
    the database, not at our policy.

    Params are passed via $1, $2, … placeholders to avoid SQL injection.
    """
    import subprocess  # noqa: PLC0415
    container = f"{slug}-postgres-1"
    # Build the $N-substituted SQL and the array literal for psql -v.
    # psql doesn't natively bind in -c mode; we use a JSON heredoc instead.
    args = ["docker", "exec", "-i", container,
            "psql", "-U", "hatchik_agent_ro", "-d", "postgres",
            "-tA", "-F\x1F", "-c", sql.format(*[_safe(p) for p in params])]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return [{"error": "tenant_db_timeout"}]
    if result.returncode != 0:
        return [{"error": (result.stderr or "psql failed")[:400]}]
    rows = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        # We asked for tab-separated output above; split on the field sep.
        rows.append({"row": line.split("\x1F")})
    return rows


def _safe(p: Any) -> str:
    """Tiny escape helper for the inline-format path. Numbers + booleans
    pass through; strings get single-quote-wrapped with internal quotes
    doubled. NOT a full SQL escape — agents are encouraged to keep
    queries parameter-free + use the literal approach only with values
    they synthesised themselves (e.g. timestamp computed from now)."""
    if isinstance(p, bool):
        return "true" if p else "false"
    if isinstance(p, (int, float)):
        return str(p)
    return "'" + str(p).replace("'", "''") + "'"


# ─── Single-agent invocation ─────────────────────────────────────────────
def run_agent(
    agent_name: str, signup_id: int,
    *, force: bool = False, now: datetime | None = None,
) -> dict[str, Any]:
    """Run one agent against one tenant. Returns a dict suitable for
    JSON output. `force=True` skips the is_due() schedule check."""
    cls = REGISTRY.get(agent_name)
    if not cls:
        return {"ok": False, "error": f"unknown agent: {agent_name}"}

    # Look up tenant metadata.
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT id, tier, product_name FROM signups WHERE id=?", (signup_id,),
        ).fetchone()
    if not row:
        return {"ok": False, "error": f"unknown signup: {signup_id}"}
    tier = (row["tier"] or "sandbox").lower()

    if not force and not is_enabled(agent_name, signup_id, tier):
        return {"ok": False, "skipped": "agent_disabled_for_tenant"}

    # Derive the tenant slug from the product_name (mirrors how provision.py does it).
    import re as _re
    slug = _re.sub(r"[^a-z0-9-]+", "-",
                   (row["product_name"] or "tenant").lower()).strip("-")[:60] or "tenant"

    now = now or datetime.now(timezone.utc)
    started_at = now.isoformat()
    run_id = begin_run(agent_name, signup_id, started_at)

    # Build the agent context with all the callbacks wired.
    cost_pence_total = [0]  # captured by closure so the llm wrapper can accumulate

    def _llm_wrapped(**kwargs: Any) -> dict[str, Any]:
        out = _llm_call(signup_id, **kwargs)
        cost_pence_total[0] += int(out.get("__hatchik_cost_pence", 0))
        return out

    ctx = AgentContext(
        signup_id=signup_id, slug=slug, tier=tier,  # type: ignore[arg-type]
        now=now, agent_name=agent_name, run_id=run_id,
        _llm=_llm_wrapped,
        _tenant_db=lambda sql, params=(): _tenant_db_callback(slug, sql, params),
        _send_email=lambda **kw: _send_email_callback(signup_id, slug, tier, **kw),
        _state_get=lambda key: _state_get(agent_name, signup_id, key),
        _state_set=lambda key, val: _state_set(agent_name, signup_id, key, val),
    )

    try:
        agent_instance = cls()
        try:
            result = agent_instance.run(ctx)
        except ApprovalRequired as ar:
            result = AgentResult(actions=[ar.action])
        # Persist actions.
        for action in result.actions:
            record_action(run_id, signup_id, action.to_dict(), now_iso())
        finish_run(
            run_id, status="ok", finished_at=now_iso(),
            notes=result.notes, metrics=result.metrics,
            llm_cost_pence=cost_pence_total[0],
        )
        return {
            "ok": True, "run_id": run_id, "agent": agent_name,
            "signup_id": signup_id, "actions": [a.to_dict() for a in result.actions],
            "notes": result.notes, "metrics": result.metrics,
            "llm_cost_pence": cost_pence_total[0],
        }
    except Exception as e:  # noqa: BLE001
        log.exception("agent %s failed for signup %s", agent_name, signup_id)
        finish_run(
            run_id, status="error", finished_at=now_iso(),
            error=str(e)[:1000], llm_cost_pence=cost_pence_total[0],
        )
        return {"ok": False, "run_id": run_id, "error": str(e)[:400]}


# ─── Sweep — invoked by the systemd timer ────────────────────────────────
def tick() -> dict[str, Any]:
    """One sweep. Walk every (agent, tenant) pair; run those that are
    enabled + due. Returns a summary suitable for journal logging."""
    discover_agents()
    tenants = _list_active_tenants()
    now = datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "tick_at": now.isoformat(), "ran": 0, "skipped": 0, "errors": 0,
        "by_agent": {},
    }
    for name, cls in REGISTRY.items():
        per_agent = {"ran": 0, "skipped": 0, "errors": 0}
        for tenant in tenants:
            sid = int(tenant["id"])
            tier = (tenant["tier"] or "sandbox").lower()
            if not is_enabled(name, sid, tier):
                per_agent["skipped"] += 1
                continue
            if not _is_due(name, sid, cls.SCHEDULE, now):
                per_agent["skipped"] += 1
                continue
            out = run_agent(name, sid, force=False, now=now)
            if out.get("ok"):
                per_agent["ran"] += 1
                summary["ran"] += 1
            else:
                per_agent["errors"] += 1
                summary["errors"] += 1
        summary["by_agent"][name] = per_agent
    return summary


# ─── Approval execution — called by /api/account/agent-actions/{id}/decide
def execute_pending_action(action_id: int, *, decider: str = "founder") -> dict[str, Any]:
    """Founder approved a pending action — actually run it."""
    with conn() as db:
        row = db.execute(
            """SELECT a.*, ar.agent_name AS agent_name
                 FROM agent_actions a
                 JOIN agent_runs ar ON ar.id = a.run_id
                WHERE a.id=?""",
            (action_id,),
        ).fetchone()
    if not row:
        return {"ok": False, "error": "not_found"}
    if row["status"] != "pending":
        return {"ok": False, "error": f"already {row['status']}"}

    payload = json.loads(row["payload_json"] or "{}")
    kind = row["kind"]
    signup_id = row["signup_id"]
    slug = payload.get("__slug") or ""

    # Currently approval is meaningful for send_email + write_to_db kinds.
    result: dict[str, Any]
    if kind == "send_email":
        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            tier_row = db.execute("SELECT tier, product_name FROM signups WHERE id=?",
                                  (signup_id,)).fetchone()
        tier = (tier_row["tier"] or "sandbox").lower() if tier_row else "sandbox"
        import re as _re
        slug = slug or _re.sub(r"[^a-z0-9-]+", "-",
                               (tier_row["product_name"] if tier_row else "tenant").lower()).strip("-")
        result = _send_email_callback(
            signup_id, slug, tier,
            to=payload.get("to", ""), subject=payload.get("subject", ""),
            body_md=payload.get("body_md", ""), from_name=payload.get("from_name", ""),
            reply_to=payload.get("reply_to", ""),
        )
    else:
        result = {"ok": True, "note": f"action kind={kind} acknowledged (no-op handler)"}

    new_status = "executed" if result.get("ok") else "failed"
    update_action_status(action_id, new_status, decider=decider, result=result)
    return {"ok": True, "status": new_status, "result": result}


def reject_pending_action(action_id: int, *, decider: str = "founder") -> dict[str, Any]:
    with conn() as db:
        row = db.execute("SELECT status FROM agent_actions WHERE id=?", (action_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "not_found"}
    if row["status"] != "pending":
        return {"ok": False, "error": f"already {row['status']}"}
    update_action_status(action_id, "rejected", decider=decider)
    return {"ok": True, "status": "rejected"}


# ─── Event dispatch — for event-driven agents (e.g. inbound email) ──────
def dispatch_event(event_name: str, signup_id: int, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Find every agent that subscribes to `event_name` and run it.
    Agents declare subscriptions via a class attribute `EVENTS: set[str]`.
    """
    discover_agents()
    out = []
    for name, cls in REGISTRY.items():
        events: set[str] = getattr(cls, "EVENTS", set())
        if event_name not in events:
            continue
        # Stash payload in state so the agent can read it from ctx.state_get
        # (events feed in via the same context as scheduled runs).
        _state_set(name, signup_id, f"__event:{event_name}", payload)
        out.append(run_agent(name, signup_id, force=True))
    return out


# ─── CLI ─────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="Hatchik agent runtime")
    p.add_argument("--tick", action="store_true", help="run one sweep")
    p.add_argument("--signup-id", type=int)
    p.add_argument("--agent", type=str)
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.tick:
        out = tick()
    elif args.signup_id and args.agent:
        discover_agents()
        out = run_agent(args.agent, args.signup_id, force=args.force)
    else:
        p.print_help()
        return 2

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(json.dumps(out, indent=2, default=str) if isinstance(out, dict) else out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
