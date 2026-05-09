"""Tests for the production-grade /api/health and /api/ready endpoints.

These cover the OK path, the degraded-write path (output dir is read-only or
unreachable so the writability check fails — must return 503), and the
ready probe.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slideatelier.config import validate_production_env
from slideatelier.web.app import app

client = TestClient(app)


def test_ready_probe_is_lightweight():
    r = client.get("/api/ready")
    assert r.status_code == 200
    assert r.json() == {"ready": True}


def test_health_ok_when_output_writable():
    r = client.get("/api/health")
    # Default dev config: ./output is writable, ./library/catalog.json may or
    # may not exist (we treat absent-catalog as OK on purpose).
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["output_writable"]["ok"] is True
    assert "library_catalog" in body["checks"]
    assert body["checks"]["library_catalog"]["ok"] is True


def test_health_degraded_when_output_dir_unwritable(monkeypatch, tmp_path):
    # Point output at a path that cannot be created/written to: a regular
    # file (not a directory). The writability check must fail and the
    # endpoint must return 503 with a structured error.
    blocking_file = tmp_path / "not-a-dir"
    blocking_file.write_text("i am a file, not a directory")
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(blocking_file))

    r = client.get("/api/health")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["output_writable"]["ok"] is False
    assert "error" in body["checks"]["output_writable"]


def test_health_degraded_when_catalog_corrupt(monkeypatch, tmp_path):
    # Drop a malformed catalog.json into a fake library dir under cwd.
    # Easiest reliable approach: chdir into tmp_path so the relative path
    # `./library/catalog.json` resolves there.
    library = tmp_path / "library"
    library.mkdir()
    (library / "catalog.json").write_text("{this is not json")

    output_dir = tmp_path / "output"
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(output_dir))
    monkeypatch.chdir(tmp_path)

    r = client.get("/api/health")
    assert r.status_code == 503
    body = r.json()
    assert body["checks"]["library_catalog"]["ok"] is False
    assert body["checks"]["library_catalog"]["present"] is True


# ---------- validate_production_env ----------

def test_validate_production_env_dev_mode_passes_with_warnings(monkeypatch):
    """Dev mode never raises, even when required prod vars are missing."""
    for k in ("ANTHROPIC_API_KEY", "DOMAIN", "SESSION_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SLIDEATELIER_ENV", "development")
    missing = validate_production_env()
    # Some vars are missing but no SystemExit raised.
    assert isinstance(missing, list)


def test_validate_production_env_prod_mode_exits_on_missing(monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_ENV", "production")
    # Wipe everything required.
    for k in (
        "ANTHROPIC_API_KEY",
        "SLIDEATELIER_OUTPUT_DIR",
        "SLIDEATELIER_TEMPLATES_DIR",
        "DOMAIN",
        "SESSION_SECRET",
    ):
        monkeypatch.delenv(k, raising=False)

    with pytest.raises(SystemExit) as ex:
        validate_production_env()
    assert ex.value.code == 2


def test_validate_production_env_prod_mode_passes_when_all_set(monkeypatch):
    monkeypatch.setenv("SLIDEATELIER_ENV", "production")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", "/tmp/out")
    monkeypatch.setenv("SLIDEATELIER_TEMPLATES_DIR", "/tmp/tpl")
    monkeypatch.setenv("DOMAIN", "slideatelier.example.com")
    monkeypatch.setenv("SESSION_SECRET", "x" * 32)

    missing = validate_production_env()
    assert missing == []
