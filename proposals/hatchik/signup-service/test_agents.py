"""Unit tests for the operator-agent framework.

Tests the framework + each agent's run() logic against a stubbed
AgentContext that mocks LLM, tenant_db, send_email. No real Postgres,
no real Anthropic / OpenAI calls.

Run with: python -m unittest signup-service.test_agents
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make signup-service importable as a flat package.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class AgentFrameworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        os.environ["HATCHIK_SIGNUP_DB"] = self._tmp.name
        # Seed a minimal signups row + the schemas the agent runtime
        # touches (ai_credit + mcp_audit init their own tables).
        import sqlite3
        with sqlite3.connect(self._tmp.name) as db:
            db.execute(
                "CREATE TABLE signups (id INTEGER PRIMARY KEY, email TEXT, "
                "first_name TEXT, tier TEXT, product_name TEXT, region TEXT, "
                "domain_choice TEXT, status TEXT)"
            )
            db.execute(
                "INSERT INTO signups (id, email, first_name, tier, product_name, status) "
                "VALUES (1, 'a@x.com', 'Alex', 'launch', 'MealMate', 'live-launch')"
            )
            db.commit()
        # Force re-import so DB_PATH constants pick up the new env.
        for m in [
            "agents", "agents.base", "agents.registry", "agents.runtime",
            "agents.support", "agents.cohort", "agents.ar_chase",
        ]:
            sys.modules.pop(m, None)

    def tearDown(self) -> None:
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_register_decorator_populates_REGISTRY(self) -> None:
        from agents.registry import REGISTRY, register
        from agents.base import Agent, AgentContext, AgentResult

        class Dummy(Agent):
            NAME = "dummy"; DESCRIPTION = "test"; SCHEDULE = "hourly"
            def run(self, ctx: AgentContext) -> AgentResult:
                return AgentResult(notes="hi")
        register(Dummy)
        self.assertIn("dummy", REGISTRY)
        self.assertIs(REGISTRY["dummy"], Dummy)

    def test_register_rejects_nameless_agent(self) -> None:
        from agents.registry import register
        from agents.base import Agent, AgentContext, AgentResult

        class Nameless(Agent):
            NAME = ""; DESCRIPTION = "x"; SCHEDULE = "hourly"
            def run(self, ctx: AgentContext) -> AgentResult:
                return AgentResult()
        with self.assertRaises(ValueError):
            register(Nameless)

    def test_enabled_default_respects_DEFAULT_ON_TIERS(self) -> None:
        from agents.registry import REGISTRY, is_enabled, register
        from agents.base import Agent, AgentContext, AgentResult

        class LaunchOnly(Agent):
            NAME = "launch_only"; DESCRIPTION = "x"; SCHEDULE = "hourly"
            DEFAULT_ON_TIERS = ("launch", "growth")
            def run(self, ctx: AgentContext) -> AgentResult:
                return AgentResult()
        register(LaunchOnly)
        self.assertTrue(is_enabled("launch_only", 1, "launch"))
        self.assertTrue(is_enabled("launch_only", 1, "growth"))
        self.assertFalse(is_enabled("launch_only", 1, "sandbox"))

    def test_set_enabled_overrides_default(self) -> None:
        from agents.registry import REGISTRY, is_enabled, set_enabled, register
        from agents.base import Agent, AgentContext, AgentResult

        class X(Agent):
            NAME = "togglable"; DESCRIPTION = "x"; SCHEDULE = "daily"
            DEFAULT_ON_TIERS = ("launch",)
            def run(self, ctx: AgentContext) -> AgentResult:
                return AgentResult()
        register(X)
        set_enabled("togglable", 1, False)
        self.assertFalse(is_enabled("togglable", 1, "launch"))
        set_enabled("togglable", 1, True)
        self.assertTrue(is_enabled("togglable", 1, "sandbox"))  # override wins

    def test_agent_run_persists_run_and_actions(self) -> None:
        from agents.base import Agent, AgentAction, AgentContext, AgentResult
        from agents.registry import register
        from agents import runtime

        class Echo(Agent):
            NAME = "echo"; DESCRIPTION = "x"; SCHEDULE = "hourly"
            DEFAULT_ON_TIERS = ("launch",)
            def run(self, ctx: AgentContext) -> AgentResult:
                return AgentResult(
                    actions=[AgentAction(kind="log_only", summary="ran", payload={"v": 1})],
                    notes="ok", metrics={"n": 1},
                )
        register(Echo)

        with patch.object(runtime, "_llm_call", return_value={}):
            out = runtime.run_agent("echo", 1, force=True)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["agent"], "echo")
        self.assertEqual(len(out["actions"]), 1)
        # Verify it persisted.
        from agents.registry import recent_runs_for, conn
        runs = recent_runs_for(1)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["agent_name"], "echo")
        self.assertEqual(runs[0]["status"], "ok")
        with conn() as db:
            n = db.execute("SELECT COUNT(*) FROM agent_actions WHERE run_id=?",
                           (runs[0]["id"],)).fetchone()
        self.assertEqual(n[0], 1)

    def test_pending_actions_filtered_correctly(self) -> None:
        from agents.base import Agent, AgentAction, AgentContext, AgentResult
        from agents.registry import register, pending_actions_for
        from agents import runtime

        class Mixed(Agent):
            NAME = "mixed"; DESCRIPTION = "x"; SCHEDULE = "hourly"
            DEFAULT_ON_TIERS = ("launch",)
            def run(self, ctx: AgentContext) -> AgentResult:
                return AgentResult(actions=[
                    AgentAction(kind="log_only", summary="auto",
                                requires_approval=False),
                    AgentAction(kind="send_email", summary="needs-ok",
                                requires_approval=True,
                                payload={"to": "a@x.com"}),
                ])
        register(Mixed)
        with patch.object(runtime, "_llm_call", return_value={}):
            runtime.run_agent("mixed", 1, force=True)
        pending = pending_actions_for(1)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["kind"], "send_email")

    def test_error_in_agent_does_not_crash_runtime(self) -> None:
        from agents.base import Agent, AgentContext, AgentResult
        from agents.registry import register, recent_runs_for
        from agents import runtime

        class Broken(Agent):
            NAME = "broken"; DESCRIPTION = "x"; SCHEDULE = "hourly"
            DEFAULT_ON_TIERS = ("launch",)
            def run(self, ctx: AgentContext) -> AgentResult:
                raise RuntimeError("kaboom")
        register(Broken)
        out = runtime.run_agent("broken", 1, force=True)
        self.assertFalse(out["ok"])
        self.assertIn("kaboom", out["error"])
        runs = recent_runs_for(1)
        broken_runs = [r for r in runs if r["agent_name"] == "broken"]
        self.assertEqual(broken_runs[0]["status"], "error")


class SupportAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        os.environ["HATCHIK_SIGNUP_DB"] = self._tmp.name
        import sqlite3
        with sqlite3.connect(self._tmp.name) as db:
            db.execute(
                "CREATE TABLE signups (id INTEGER PRIMARY KEY, email TEXT, "
                "first_name TEXT, tier TEXT, product_name TEXT, status TEXT)"
            )
            db.execute(
                "INSERT INTO signups (id, email, first_name, tier, product_name, status) "
                "VALUES (1, 'alex@x.com', 'Alex', 'launch', 'MealMate', 'live-launch')"
            )
            db.commit()
        for m in list(sys.modules):
            if m.startswith("agents"):
                sys.modules.pop(m, None)

    def tearDown(self) -> None:
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_high_confidence_faq_auto_replies(self) -> None:
        from agents.support import SupportAgent
        from agents.base import AgentContext

        # Stub: one FAQ ticket, LLM returns faq classification with conf 0.95.
        def fake_db(sql, params=()):
            if "FROM support_tickets" in sql:
                return [{"row": ["100", "user@x.com", "How do I cancel?",
                                 "I'd like to cancel my subscription.",
                                 "2026-05-15T10:00:00Z"]}]
            if "FROM faq" in sql:
                return [{"row": ["How do I cancel?",
                                 "Visit /account → Danger zone to delete your account."]}]
            return []

        fake_llm_response = {
            "content": [{"type": "text", "text": json.dumps({
                "classification": "faq", "confidence": 0.95,
                "rationale": "FAQ match",
                "subject": "Re: How do I cancel?",
                "draft_reply_md": "Hi, you can cancel at /account..."
            })}]
        }

        state = {}
        ctx = AgentContext(
            signup_id=1, slug="mealmate", tier="launch",
            now=datetime.now(timezone.utc),
            agent_name="support_triage", run_id=0,
            _llm=lambda **kw: fake_llm_response,
            _tenant_db=fake_db,
            _send_email=lambda **kw: {"ok": True},
            _state_get=lambda k: state.get(k),
            _state_set=lambda k, v: state.update({k: v}),
        )
        result = SupportAgent().run(ctx)
        self.assertEqual(len(result.actions), 1)
        a = result.actions[0]
        self.assertEqual(a.kind, "send_email")
        self.assertFalse(a.requires_approval)  # auto-sends
        self.assertEqual(a.payload["to"], "user@x.com")
        self.assertGreaterEqual(a.confidence, 0.85)
        self.assertEqual(state.get("last_seen_ticket_id"), 100)

    def test_low_confidence_escalates(self) -> None:
        from agents.support import SupportAgent
        from agents.base import AgentContext

        def fake_db(sql, params=()):
            if "FROM support_tickets" in sql:
                return [{"row": ["101", "user@x.com", "Bug?",
                                 "Something broke", "2026-05-15T10:00:00Z"]}]
            return []
        fake_llm = {
            "content": [{"type": "text", "text": json.dumps({
                "classification": "bug", "confidence": 0.6,
                "rationale": "ambiguous", "subject": "Re: Bug?", "draft_reply_md": ""
            })}]
        }
        state = {}
        ctx = AgentContext(
            signup_id=1, slug="mealmate", tier="launch",
            now=datetime.now(timezone.utc),
            agent_name="support_triage", run_id=0,
            _llm=lambda **kw: fake_llm,
            _tenant_db=fake_db,
            _send_email=lambda **kw: {"ok": True},
            _state_get=lambda k: state.get(k),
            _state_set=lambda k, v: state.update({k: v}),
        )
        result = SupportAgent().run(ctx)
        self.assertEqual(len(result.actions), 1)
        self.assertTrue(result.actions[0].requires_approval)

    def test_no_new_tickets_emits_no_actions(self) -> None:
        from agents.support import SupportAgent
        from agents.base import AgentContext

        ctx = AgentContext(
            signup_id=1, slug="mealmate", tier="launch",
            now=datetime.now(timezone.utc),
            agent_name="support_triage", run_id=0,
            _llm=lambda **kw: {},
            _tenant_db=lambda sql, params=(): [],
            _send_email=lambda **kw: {"ok": True},
            _state_get=lambda k: None,
            _state_set=lambda k, v: None,
        )
        result = SupportAgent().run(ctx)
        self.assertEqual(result.actions, [])
        self.assertIn("No new", result.notes)


class ArChaseAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        for m in list(sys.modules):
            if m.startswith("agents"):
                sys.modules.pop(m, None)

    def test_day1_overdue_auto_sends(self) -> None:
        from agents.ar_chase import ArChaseAgent
        from agents.base import AgentContext

        def fake_db(sql, params=()):
            if "FROM invoices" in sql:
                return [{"row": ["inv_1", "buyer@x.com", "2500", "1"]}]
            return []
        fake_llm = {
            "content": [{"type": "text", "text": json.dumps({
                "subject": "Payment reminder", "body_md": "Hi! Quick reminder..."
            })}]
        }
        state = {}
        ctx = AgentContext(
            signup_id=1, slug="mealmate", tier="launch",
            now=datetime.now(timezone.utc),
            agent_name="ar_chase", run_id=0,
            _llm=lambda **kw: fake_llm,
            _tenant_db=fake_db,
            _send_email=lambda **kw: {"ok": True},
            _state_get=lambda k: state.get(k),
            _state_set=lambda k, v: state.update({k: v}),
        )
        result = ArChaseAgent().run(ctx)
        self.assertEqual(len(result.actions), 1)
        a = result.actions[0]
        self.assertEqual(a.kind, "send_email")
        self.assertFalse(a.requires_approval)
        self.assertEqual(result.metrics["overdue_amount_pence"], 2500)

    def test_day7_overdue_requires_approval(self) -> None:
        from agents.ar_chase import ArChaseAgent
        from agents.base import AgentContext

        def fake_db(sql, params=()):
            if "FROM invoices" in sql:
                return [{"row": ["inv_2", "buyer@x.com", "10000", "10"]}]
            return []
        fake_llm = {"content": [{"type": "text", "text": json.dumps({
            "subject": "Past due", "body_md": "This is now significantly overdue..."})}]}
        state = {}
        ctx = AgentContext(
            signup_id=1, slug="mealmate", tier="launch",
            now=datetime.now(timezone.utc),
            agent_name="ar_chase", run_id=0,
            _llm=lambda **kw: fake_llm,
            _tenant_db=fake_db,
            _send_email=lambda **kw: {"ok": True},
            _state_get=lambda k: state.get(k),
            _state_set=lambda k, v: state.update({k: v}),
        )
        result = ArChaseAgent().run(ctx)
        self.assertTrue(result.actions[0].requires_approval)


if __name__ == "__main__":
    unittest.main()
