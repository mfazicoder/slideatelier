"""
Tests for the shared-host pickup logic in promote.py.

Stdlib unittest only. Covers:
  - _pick_launch_host reuses an existing active host below capacity
  - _pick_launch_host skips full / cordoned hosts
  - _pick_launch_host triggers _provision_launch_host when no active host
  - _allocate_port_base returns the lowest free slot (and raises when full)
  - _next_host_id is monotonic and skips existing ids
  - _decommission_tenant_on_host frees the port_base, removes the slug,
    flips 'full' back to 'active', and is idempotent
  - _execute_shared end-to-end places a tenant against a mocked
    Hetzner + mocked SSH layer and writes the registry

Run from this directory:
    python3 -m unittest test_promote_shared.py -v
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


THIS_DIR = Path(__file__).parent


class _Env:
    """Build an isolated env (DB + registry) and import promote fresh."""

    def __init__(self, tmpdir: Path) -> None:
        self.tmpdir = tmpdir
        self.db_path = tmpdir / "signups.db"
        self.launch_reg = tmpdir / "launch-registry.json"
        self.sandbox_reg = tmpdir / "sandbox-registry.json"
        self._init_db()
        self.sandbox_reg.write_text(json.dumps(
            {"tenants": {"prepsheet": {"signup_id": 1, "status": "live"}}}
        ))
        os.environ["HATCHIK_SIGNUP_DB"] = str(self.db_path)
        os.environ["HATCHIK_LAUNCH_REGISTRY"] = str(self.launch_reg)
        os.environ["HATCHIK_SANDBOX_REGISTRY"] = str(self.sandbox_reg)
        os.environ["RESEND_API_KEY"] = ""
        os.environ["HATCHIK_LOG_DIR"] = str(tmpdir / "logs")
        os.environ["HATCHIK_LAUNCH_MODE"] = "shared"
        os.environ["HATCHIK_SHARED_HOST_CAPACITY"] = "3"
        os.environ["HATCHIK_PORT_BASE_START"] = "18000"
        os.environ["HATCHIK_PORT_BASE_STRIDE"] = "100"
        sys.path.insert(0, str(THIS_DIR))
        if "promote" in sys.modules:
            del sys.modules["promote"]
        self.promote = importlib.import_module("promote")

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE signups (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at TEXT NOT NULL,
                  email TEXT NOT NULL,
                  first_name TEXT,
                  product_name TEXT,
                  description TEXT,
                  tier TEXT NOT NULL,
                  region TEXT,
                  domain_choice TEXT,
                  ip_address TEXT,
                  user_agent TEXT,
                  status TEXT NOT NULL DEFAULT 'new',
                  github_username TEXT,
                  country_code TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE tier_transitions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  signup_id INTEGER NOT NULL,
                  from_tier TEXT,
                  to_tier TEXT NOT NULL,
                  occurred_at TEXT NOT NULL,
                  paddle_event_id TEXT,
                  notes TEXT
                )
            """)
            conn.execute(
                "INSERT INTO signups (created_at, email, first_name, product_name, "
                "description, tier, region, domain_choice, status, github_username) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    "alice@example.com", "Alice", "PrepSheet", "test app",
                    "sandbox", "eu-central", "alice.dev", "live", "alicegit",
                ),
            )
            conn.commit()


class SharedHostRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = _Env(Path(self.tmp.name))
        self.promote = self.env.promote

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ─── _allocate_port_base ────────────────────────────────────────

    def test_allocate_port_base_returns_18000_for_empty_host(self):
        host = {"port_ranges_used": [], "capacity": 3}
        self.assertEqual(self.promote._allocate_port_base(host), 18000)

    def test_allocate_port_base_returns_next_free_slot(self):
        host = {"port_ranges_used": [18000, 18200], "capacity": 3}
        self.assertEqual(self.promote._allocate_port_base(host), 18100)

    def test_allocate_port_base_raises_when_full(self):
        host = {"port_ranges_used": [18000, 18100, 18200], "capacity": 3}
        with self.assertRaises(RuntimeError):
            self.promote._allocate_port_base(host)

    # ─── _next_host_id ──────────────────────────────────────────────

    def test_next_host_id_first(self):
        reg = {"launch_hosts": {}}
        self.assertEqual(self.promote._next_host_id(reg), "launch-host-1")

    def test_next_host_id_skips_existing(self):
        reg = {"launch_hosts": {
            "launch-host-1": {}, "launch-host-3": {},
        }}
        self.assertEqual(self.promote._next_host_id(reg), "launch-host-2")

    # ─── _pick_launch_host ──────────────────────────────────────────

    def test_pick_launch_host_reuses_active_with_capacity(self):
        # Seed a host that has room
        reg = {
            "schema_version": 1,
            "tenants": {},
            "launch_hosts": {
                "launch-host-1": {
                    "hetzner_server_id": 111, "ip": "10.0.0.1",
                    "location": "nbg1", "server_type": "cax41",
                    "tenant_slugs": ["launch-9"],
                    "capacity": 3, "status": "active",
                    "port_ranges_used": [18000],
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
            },
        }
        self.env.launch_reg.write_text(json.dumps(reg))

        log: list[str] = []
        host = self.promote._pick_launch_host({"hetzner_location": "nbg1"}, log)
        self.assertEqual(host["host_id"], "launch-host-1")
        self.assertEqual(host["ip"], "10.0.0.1")
        # No new host provisioned
        reg_after = json.loads(self.env.launch_reg.read_text())
        self.assertEqual(list(reg_after["launch_hosts"].keys()), ["launch-host-1"])

    def test_pick_launch_host_skips_full_and_cordoned(self):
        reg = {
            "schema_version": 1,
            "tenants": {},
            "launch_hosts": {
                "launch-host-1": {
                    "hetzner_server_id": 111, "ip": "10.0.0.1",
                    "location": "nbg1", "server_type": "cax41",
                    "tenant_slugs": ["a", "b", "c"],
                    "capacity": 3, "status": "full",
                    "port_ranges_used": [18000, 18100, 18200],
                    "created_at": "x",
                },
                "launch-host-2": {
                    "hetzner_server_id": 112, "ip": "10.0.0.2",
                    "location": "nbg1", "server_type": "cax41",
                    "tenant_slugs": [],
                    "capacity": 3, "status": "cordoned",
                    "port_ranges_used": [], "created_at": "x",
                },
                "launch-host-3": {
                    "hetzner_server_id": 113, "ip": "10.0.0.3",
                    "location": "nbg1", "server_type": "cax41",
                    "tenant_slugs": ["d"],
                    "capacity": 3, "status": "active",
                    "port_ranges_used": [18000], "created_at": "x",
                },
            },
        }
        self.env.launch_reg.write_text(json.dumps(reg))

        log: list[str] = []
        host = self.promote._pick_launch_host({"hetzner_location": "nbg1"}, log)
        self.assertEqual(host["host_id"], "launch-host-3")

    def test_pick_launch_host_provisions_when_no_capacity(self):
        # No hosts → must provision a new one. Mock hetzner + bootstrap.
        reg = {"schema_version": 1, "tenants": {}, "launch_hosts": {}}
        self.env.launch_reg.write_text(json.dumps(reg))

        fake_create_resp = {"server": {"id": 999}}
        fake_running = {"public_net": {"ipv4": {"ip": "10.0.0.99"}}}

        self.promote.hetzner_api = mock.MagicMock(
            create_server=mock.MagicMock(return_value=fake_create_resp),
            wait_for_running=mock.MagicMock(return_value=fake_running),
            map_region=mock.MagicMock(return_value="nbg1"),
        )
        with mock.patch.object(self.promote, "_run_host_bootstrap", return_value=True):
            log: list[str] = []
            host = self.promote._pick_launch_host(
                {"hetzner_location": "nbg1"}, log,
            )

        self.assertEqual(host["host_id"], "launch-host-1")
        self.assertEqual(host["hetzner_server_id"], 999)
        self.assertEqual(host["ip"], "10.0.0.99")
        # Persisted to registry
        reg_after = json.loads(self.env.launch_reg.read_text())
        self.assertIn("launch-host-1", reg_after["launch_hosts"])
        self.assertEqual(
            reg_after["launch_hosts"]["launch-host-1"]["status"], "active",
        )
        self.assertEqual(
            reg_after["launch_hosts"]["launch-host-1"]["capacity"], 3,
        )
        self.assertEqual(
            reg_after["launch_hosts"]["launch-host-1"]["tenant_slugs"], [],
        )

    # ─── _decommission_tenant_on_host ───────────────────────────────

    def test_decommission_tenant_on_host_frees_port_and_slug(self):
        # Seed a host with one tenant, capacity 1 → full
        reg = {
            "schema_version": 1,
            "tenants": {
                "launch-1": {
                    "shared": True, "host_id": "launch-host-1",
                    "port_base": 18000, "status": "live",
                    "ip": "10.0.0.1",
                },
            },
            "launch_hosts": {
                "launch-host-1": {
                    "hetzner_server_id": 111, "ip": "10.0.0.1",
                    "location": "nbg1", "server_type": "cax41",
                    "tenant_slugs": ["launch-1"],
                    "capacity": 1, "status": "full",
                    "port_ranges_used": [18000], "created_at": "x",
                },
            },
        }
        self.env.launch_reg.write_text(json.dumps(reg))

        # Mock subprocess.run inside promote (SSH calls)
        fake_run = mock.MagicMock(returncode=0, stdout="", stderr="")
        with mock.patch("subprocess.run", return_value=fake_run):
            log: list[str] = []
            ok = self.promote._decommission_tenant_on_host("launch-1", log)
        self.assertTrue(ok)

        reg_after = json.loads(self.env.launch_reg.read_text())
        host = reg_after["launch_hosts"]["launch-host-1"]
        self.assertEqual(host["tenant_slugs"], [])
        self.assertEqual(host["port_ranges_used"], [])
        self.assertEqual(host["status"], "active")  # demoted from 'full'
        self.assertEqual(
            reg_after["tenants"]["launch-1"]["status"], "decommissioned",
        )
        self.assertIn("decommissioned_at", reg_after["tenants"]["launch-1"])

    def test_decommission_tenant_on_host_is_idempotent(self):
        reg = {
            "schema_version": 1,
            "tenants": {
                "launch-1": {
                    "shared": True, "host_id": "launch-host-1",
                    "port_base": 18000, "status": "decommissioned",
                    "ip": "10.0.0.1",
                    "decommissioned_at": "2026-01-01T00:00:00+00:00",
                },
            },
            "launch_hosts": {
                "launch-host-1": {
                    "hetzner_server_id": 111, "ip": "10.0.0.1",
                    "tenant_slugs": [], "capacity": 1,
                    "status": "active", "port_ranges_used": [],
                    "location": "nbg1", "server_type": "cax41",
                    "created_at": "x",
                },
            },
        }
        self.env.launch_reg.write_text(json.dumps(reg))

        with mock.patch("subprocess.run") as run:
            log: list[str] = []
            ok = self.promote._decommission_tenant_on_host("launch-1", log)
            self.assertTrue(ok)
            # No SSH calls — already decommissioned
            run.assert_not_called()

    def test_decommission_tenant_unknown_slug_is_safe(self):
        reg = {"schema_version": 1, "tenants": {}, "launch_hosts": {}}
        self.env.launch_reg.write_text(json.dumps(reg))
        log: list[str] = []
        ok = self.promote._decommission_tenant_on_host("nope", log)
        self.assertTrue(ok)  # nothing to do = success
        self.assertTrue(any("nothing to do" in line for line in log))


class SharedHostExecuteTests(unittest.TestCase):
    """End-to-end of _execute_shared against fully-mocked I/O."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = _Env(Path(self.tmp.name))
        self.promote = self.env.promote

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_execute_shared_places_tenant_on_existing_host(self):
        reg = {
            "schema_version": 1,
            "tenants": {},
            "launch_hosts": {
                "launch-host-1": {
                    "hetzner_server_id": 111, "ip": "10.0.0.1",
                    "location": "nbg1", "server_type": "cax41",
                    "tenant_slugs": [],
                    "capacity": 3, "status": "active",
                    "port_ranges_used": [], "created_at": "x",
                },
            },
        }
        self.env.launch_reg.write_text(json.dumps(reg))

        plan = {
            "signup_id": 1,
            "customer_email": "alice@example.com",
            "first_name": "Alice",
            "product_name": "PrepSheet",
            "region": "eu-central",
            "hetzner_location": "nbg1",
            "customer_domain": "alice.dev",
            "sandbox_slug": "prepsheet",
            "github_username": "alicegit",
        }

        self.promote.hetzner_api = mock.MagicMock()
        self.promote.dns_api = mock.MagicMock()
        with mock.patch.object(self.promote, "_run_tenant_bootstrap_on_host", return_value=True), \
             mock.patch.object(self.promote, "_write_caddy_snippet_on_host", return_value=True), \
             mock.patch.object(self.promote, "_mark_sandbox_promoted"):
            log: list[str] = []
            result = self.promote._execute_shared(plan, log)

        self.assertTrue(result["ok"])
        self.assertEqual(result["host_id"], "launch-host-1")
        self.assertEqual(result["port_base"], 18000)
        self.assertEqual(result["status"], "live")
        self.assertTrue(result["shared"])
        self.assertEqual(result["live_url"], "https://alice.dev")

        reg_after = json.loads(self.env.launch_reg.read_text())
        # Tenant entry
        tenant = reg_after["tenants"]["launch-1"]
        self.assertEqual(tenant["status"], "live")
        self.assertEqual(tenant["host_id"], "launch-host-1")
        self.assertEqual(tenant["port_base"], 18000)
        self.assertTrue(tenant["shared"])
        # Host updated
        host = reg_after["launch_hosts"]["launch-host-1"]
        self.assertIn("launch-1", host["tenant_slugs"])
        self.assertIn(18000, host["port_ranges_used"])

    def test_execute_shared_resumes_on_existing_tenant(self):
        # Pre-place a tenant on host-1 in 'provisioning' — re-running
        # must reuse the same port_base instead of allocating a new one.
        reg = {
            "schema_version": 1,
            "tenants": {
                "launch-1": {
                    "signup_id": 1, "customer_email": "alice@example.com",
                    "customer_domain": "alice.dev",
                    "tier": "launch", "shared": True,
                    "host_id": "launch-host-1", "port_base": 18100,
                    "hetzner_server_id": 111, "ip": "10.0.0.1",
                    "status": "provisioning",
                },
            },
            "launch_hosts": {
                "launch-host-1": {
                    "hetzner_server_id": 111, "ip": "10.0.0.1",
                    "location": "nbg1", "server_type": "cax41",
                    "tenant_slugs": ["launch-1"],
                    "capacity": 3, "status": "active",
                    "port_ranges_used": [18100], "created_at": "x",
                },
            },
        }
        self.env.launch_reg.write_text(json.dumps(reg))

        plan = {
            "signup_id": 1,
            "customer_email": "alice@example.com",
            "first_name": "Alice",
            "product_name": "PrepSheet",
            "region": "eu-central",
            "hetzner_location": "nbg1",
            "customer_domain": "alice.dev",
            "sandbox_slug": "prepsheet",
            "github_username": "alicegit",
        }

        self.promote.hetzner_api = mock.MagicMock()
        self.promote.dns_api = mock.MagicMock()
        with mock.patch.object(self.promote, "_run_tenant_bootstrap_on_host", return_value=True), \
             mock.patch.object(self.promote, "_write_caddy_snippet_on_host", return_value=True), \
             mock.patch.object(self.promote, "_mark_sandbox_promoted"), \
             mock.patch.object(self.promote, "_pick_launch_host") as pick:
            log: list[str] = []
            result = self.promote._execute_shared(plan, log)
            # MUST NOT have called _pick_launch_host — resume path
            pick.assert_not_called()

        self.assertTrue(result["ok"])
        self.assertEqual(result["port_base"], 18100)
        # Host port list shouldn't have grown (still just [18100])
        reg_after = json.loads(self.env.launch_reg.read_text())
        self.assertEqual(
            reg_after["launch_hosts"]["launch-host-1"]["port_ranges_used"],
            [18100],
        )


if __name__ == "__main__":
    unittest.main()
