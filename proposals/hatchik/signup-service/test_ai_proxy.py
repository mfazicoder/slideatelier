"""Unit tests for ai_pricing.cost_pence and ai_proxy token management.

Run with: python -m pytest signup-service/test_ai_proxy.py
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class AiPricingTests(unittest.TestCase):
    def test_anthropic_opus_input_only(self) -> None:
        import ai_pricing
        # 1M input tokens of Opus at $15/Mt @ FX 0.78 → $15 × 0.78 = £11.70 = 1170p
        with patch.object(ai_pricing, "FX_USD_GBP", 0.78):
            pence = ai_pricing.cost_pence("anthropic", "claude-opus-4-7", 1_000_000, 0)
            self.assertEqual(pence, 1170)

    def test_anthropic_opus_output_dominates(self) -> None:
        import ai_pricing
        with patch.object(ai_pricing, "FX_USD_GBP", 0.78):
            # 100k input @ $15/Mt + 100k output @ $75/Mt
            # = 100/1000 × $15 + 100/1000 × $75 = $1.50 + $7.50 = $9.00
            # × 0.78 = £7.02 = 702p
            pence = ai_pricing.cost_pence("anthropic", "claude-opus-4-7", 100_000, 100_000)
            self.assertEqual(pence, 702)

    def test_openai_haiku_zero_input(self) -> None:
        import ai_pricing
        with patch.object(ai_pricing, "FX_USD_GBP", 0.78):
            pence = ai_pricing.cost_pence("openai", "gpt-5-mini", 0, 1_000_000)
            # $2 × 0.78 = £1.56 = 156p
            self.assertEqual(pence, 156)

    def test_unknown_model_falls_back_to_flagship(self) -> None:
        import ai_pricing
        with patch.object(ai_pricing, "FX_USD_GBP", 0.78):
            pence = ai_pricing.cost_pence("anthropic", "claude-mystery-99", 1_000_000, 0)
            # Falls back to opus-4-7 rate
            self.assertEqual(pence, 1170)

    def test_zero_tokens_zero_cost(self) -> None:
        import ai_pricing
        self.assertEqual(
            ai_pricing.cost_pence("anthropic", "claude-opus-4-7", 0, 0), 0,
        )


class AiTokenLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        # Each test gets its own sqlite db so token state doesn't leak.
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        os.environ["HATCHIK_SIGNUP_DB"] = self._tmp.name
        # Reload the module so DB_PATH constant picks up the new env.
        import importlib
        import ai_proxy
        importlib.reload(ai_proxy)
        # Seed a minimal signups table so _tier_of works.
        import sqlite3
        with sqlite3.connect(self._tmp.name) as db:
            db.execute(
                """CREATE TABLE signups (
                    id INTEGER PRIMARY KEY, email TEXT, tier TEXT
                )"""
            )
            db.execute(
                "INSERT INTO signups (id, email, tier) VALUES (1, 'a@example.com', 'launch')"
            )
            db.commit()

    def tearDown(self) -> None:
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_issue_then_resolve_roundtrip(self) -> None:
        import ai_proxy
        raw, meta = ai_proxy.issue_token(1, "test label", cap_pence=2000)
        self.assertTrue(raw.startswith("hk_ai_"))
        self.assertEqual(meta["label"], "test label")
        info = ai_proxy.resolve_token(raw)
        self.assertIsNotNone(info)
        self.assertEqual(info.signup_id, 1)  # type: ignore[union-attr]
        self.assertEqual(info.cap_pence, 2000)  # type: ignore[union-attr]

    def test_resolve_rejects_garbage(self) -> None:
        import ai_proxy
        self.assertIsNone(ai_proxy.resolve_token("not_a_real_token"))
        self.assertIsNone(ai_proxy.resolve_token(""))
        self.assertIsNone(ai_proxy.resolve_token("hk_ai_does_not_exist"))

    def test_revoke_then_resolve_fails(self) -> None:
        import ai_proxy
        raw, meta = ai_proxy.issue_token(1, "to revoke")
        ok = ai_proxy.revoke_token(1, meta["id"])
        self.assertTrue(ok)
        self.assertIsNone(ai_proxy.resolve_token(raw))

    def test_revoke_wrong_signup_fails(self) -> None:
        import ai_proxy
        raw, meta = ai_proxy.issue_token(1, "mine")
        ok = ai_proxy.revoke_token(2, meta["id"])  # someone else
        self.assertFalse(ok)
        self.assertIsNotNone(ai_proxy.resolve_token(raw))

    def test_list_orders_newest_first(self) -> None:
        import ai_proxy, time
        ai_proxy.issue_token(1, "first")
        time.sleep(0.01)  # ensure different created_at
        ai_proxy.issue_token(1, "second")
        rows = ai_proxy.list_for_signup(1)
        self.assertEqual(rows[0]["label"], "second")
        self.assertEqual(rows[1]["label"], "first")


if __name__ == "__main__":
    unittest.main()
