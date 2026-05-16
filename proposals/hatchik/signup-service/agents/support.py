"""
Support agent — Tier-1 customer support triage.

Listens for inbound support tickets (event-driven: `event=support_ticket`)
and:

  1. Loads the customer's knowledge base (from substrate-template's docs/
     directory + recent resolved tickets).
  2. Classifies the ticket: faq | bug | billing | feature_request | escalate.
  3. For faq with high confidence → drafts a reply and emits a
     `send_email` action with requires_approval=False (auto-sends).
  4. For everything else → emits a `post_message` action with
     requires_approval=True so the founder sees + decides in
     /account → Agents → Pending.

State stored:
  * last_seen_ticket_id — so periodic sweeps catch tickets that arrived
    while the agent was offline.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from agents.base import Agent, AgentAction, AgentContext, AgentResult
from agents.registry import register

log = logging.getLogger("agents.support")


SYSTEM_PROMPT = """You are a tier-1 customer support agent for a SaaS
product built on Hatchik. The customer (your operator) has given you
their FAQ + recent resolved tickets as context. Your job:

1. Read the inbound ticket.
2. Classify it into ONE of:
     faq             — answerable from the FAQ verbatim
     bug             — clear product issue, needs founder
     billing         — money question, needs founder
     feature_request — wants a new feature, needs founder
     escalate        — unclear, urgent, or emotional — needs founder

3. If faq, draft a polite, concise reply (<150 words) in the founder's
   first-person voice. Reference the FAQ section that resolved it.
4. Output exactly this JSON shape — no prose around it:

{
  "classification": "faq|bug|billing|feature_request|escalate",
  "confidence": 0.0..1.0,
  "rationale": "one sentence why",
  "subject": "Re: <original subject>",
  "draft_reply_md": "..."   // empty string for non-faq
}

Rules:
- Never invent product capabilities not in the FAQ.
- Never promise refunds or timelines.
- Be human, never robotic. Sign off with the founder's first name.
- Confidence < 0.8 should always classify as escalate.
"""


@register
class SupportAgent(Agent):
    NAME = "support_triage"
    DESCRIPTION = (
        "Triages inbound support tickets. Auto-replies to FAQ matches "
        "above 0.85 confidence, escalates everything else to you."
    )
    SCHEDULE = "15m"  # also runs on every inbound-ticket webhook
    DEFAULT_ON_TIERS = ("launch", "growth")
    EVENTS = {"support_ticket"}

    def run(self, ctx: AgentContext) -> AgentResult:
        # Find new tickets since the last run.
        new_tickets = self._fetch_new_tickets(ctx)
        if not new_tickets:
            return AgentResult(notes="No new tickets.",
                               metrics={"tickets_seen": 0})

        faq = self._load_faq(ctx)
        founder_first_name = ctx.state_get("founder_first_name", "")
        actions: list[AgentAction] = []
        n_auto = n_escalated = 0

        for ticket in new_tickets:
            classification = self._classify(ctx, ticket, faq, founder_first_name)
            kind = classification.get("classification", "escalate")
            conf = float(classification.get("confidence") or 0)
            rationale = classification.get("rationale", "")

            if kind == "faq" and conf >= 0.85:
                # Auto-reply (no approval needed).
                actions.append(AgentAction(
                    kind="send_email", requires_approval=False,
                    summary=f"Auto-reply to FAQ ticket from {ticket['from_email']}",
                    payload={
                        "to": ticket["from_email"],
                        "subject": classification.get("subject") or f"Re: {ticket['subject']}",
                        "body_md": classification.get("draft_reply_md", ""),
                        "from_name": founder_first_name or "Support",
                        "__slug": ctx.slug, "__ticket_id": ticket["id"],
                    },
                    confidence=conf, rationale=rationale,
                ))
                n_auto += 1
            else:
                # Escalate — pending approval, founder sees the draft.
                actions.append(AgentAction(
                    kind="send_email", requires_approval=True,
                    summary=(
                        f"[{kind}] ticket from {ticket['from_email']}: "
                        f"{ticket['subject'][:60]}"
                    ),
                    payload={
                        "to": ticket["from_email"],
                        "subject": classification.get("subject") or f"Re: {ticket['subject']}",
                        "body_md": classification.get("draft_reply_md") or
                                   "[ticket needs your attention — no draft]",
                        "from_name": founder_first_name or "Support",
                        "__slug": ctx.slug, "__ticket_id": ticket["id"],
                        "__classification": kind,
                    },
                    confidence=conf, rationale=rationale,
                ))
                n_escalated += 1

            # Mark ticket as triaged so we don't re-run.
            ctx.state_set("last_seen_ticket_id", ticket["id"])

        notes = f"Triaged {len(new_tickets)} ticket(s): {n_auto} auto, {n_escalated} escalated."
        return AgentResult(
            actions=actions, notes=notes,
            metrics={"tickets_seen": len(new_tickets),
                     "auto_replied": n_auto, "escalated": n_escalated},
        )

    # ─── Helpers ──────────────────────────────────────────────────────
    def _fetch_new_tickets(self, ctx: AgentContext) -> list[dict[str, Any]]:
        """Read the substrate's support_tickets table for new entries.

        The substrate-template creates this table at provisioning (see
        substrate-template/apps/api/migrations/*_support_tickets.sql).
        Schema: id, from_email, subject, body, received_at, status.
        """
        last_id = int(ctx.state_get("last_seen_ticket_id", 0) or 0)
        rows = ctx.tenant_db(
            "SELECT id, from_email, subject, body, received_at "
            "FROM support_tickets WHERE id > {0} AND status = 'open' "
            "ORDER BY id LIMIT 20", (last_id,),
        )
        tickets: list[dict[str, Any]] = []
        for r in rows:
            if "row" not in r:
                continue
            cols = r["row"]
            if len(cols) < 5:
                continue
            try:
                tickets.append({
                    "id": int(cols[0]),
                    "from_email": cols[1],
                    "subject": cols[2],
                    "body": cols[3],
                    "received_at": cols[4],
                })
            except (ValueError, IndexError):
                continue
        # Also consume any synthetic event payload (event-driven dispatch).
        evt = ctx.state_get("__event:support_ticket")
        if evt and isinstance(evt, dict) and evt.get("id"):
            try:
                if int(evt["id"]) > last_id:
                    tickets.append({
                        "id": int(evt["id"]),
                        "from_email": evt.get("from_email", ""),
                        "subject": evt.get("subject", ""),
                        "body": evt.get("body", ""),
                        "received_at": evt.get("received_at", ""),
                    })
            except (ValueError, TypeError):
                pass
            ctx.state_set("__event:support_ticket", None)
        # Dedup by id, preserve order.
        seen = set()
        out = []
        for t in tickets:
            if t["id"] in seen:
                continue
            seen.add(t["id"])
            out.append(t)
        return out

    def _load_faq(self, ctx: AgentContext) -> str:
        """Pull the customer's FAQ + recent resolved tickets as context.

        v1: only the FAQ table. v2 adds embeddings over resolved tickets.
        """
        rows = ctx.tenant_db(
            "SELECT question, answer FROM faq ORDER BY priority DESC LIMIT 50",
        )
        items = []
        for r in rows:
            cols = r.get("row") or []
            if len(cols) >= 2:
                items.append(f"Q: {cols[0]}\nA: {cols[1]}")
        return "\n\n".join(items) if items else "(no FAQ entries yet)"

    def _classify(
        self, ctx: AgentContext, ticket: dict[str, Any],
        faq: str, founder_first_name: str,
    ) -> dict[str, Any]:
        user_prompt = (
            f"FOUNDER FIRST NAME: {founder_first_name or '(unknown)'}\n\n"
            f"FAQ:\n{faq}\n\n"
            f"INBOUND TICKET:\n"
            f"From: {ticket['from_email']}\n"
            f"Subject: {ticket['subject']}\n"
            f"Body:\n{ticket['body'][:4000]}"
        )
        try:
            response = ctx.llm(
                system=SYSTEM_PROMPT, user=user_prompt,
                model="claude-haiku-4-5", max_tokens=600,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("LLM call failed for ticket %s: %s", ticket["id"], e)
            return {"classification": "escalate", "confidence": 0,
                    "rationale": f"llm_error: {e}", "subject": "", "draft_reply_md": ""}

        text = self._extract_text(response)
        # Extract JSON from the response body — tolerant to leading prose.
        try:
            payload = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            payload = {"classification": "escalate", "confidence": 0,
                       "rationale": "could not parse llm output",
                       "subject": "", "draft_reply_md": ""}
        return payload

    def _extract_text(self, response: dict[str, Any]) -> str:
        # Anthropic shape.
        content = response.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                return first.get("text", "")
        # OpenAI shape.
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") or {}
            return msg.get("content", "")
        return ""
