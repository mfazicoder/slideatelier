"""Sprint N — Named Version Cards + branching history.

Tests cover:
  - snapshot with label persists meta sidecar
  - snapshot with prompt auto-populates ai_summary
  - restore-and-fork creates a new snapshot pointing to restored as parent
  - list_snapshots returns label + ai_summary + parent_timestamp
  - compare endpoint returns 200 with both timestamps' content visible
  - rename endpoint updates label
  - existing F-sprint history tests still pass — backwards-compat preserved
    (the existing test_history.py tests assert this; we cross-verify here).
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from slideatelier.web.app import app
from slideatelier.web.workflow_history import (
    list_snapshots,
    rename_snapshot,
    restore_snapshot,
    snapshot,
)

client = TestClient(app)


def _write_deck(job_dir: Path, title: str, slides: int = 1) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "title": title,
        "subtitle": "",
        "core_message": "Test core message stating an answer in one sentence.",
        "narrative_arc": "Open. Defend. Close.",
        "slides": [
            {
                "layout": "content",
                "title": f"Slide {i}",
                "strap": "",
                "body": [f"bullet {i}-1", f"bullet {i}-2"],
                "body_left": [],
                "body_right": [],
                "speaker_notes": "",
                "rationale": "",
                "asset_ref": None,
                "extras": [],
                "block_bbox": {},
                "block_style": {},
            }
            for i in range(slides)
        ],
    }
    (job_dir / "deck.json").write_text(json.dumps(payload, indent=2))


def test_snapshot_with_label_persists_meta_sidecar(tmp_path: Path):
    """snapshot(label=...) writes a sidecar meta JSON next to the snapshot."""
    job_dir = tmp_path / "wf-label"
    _write_deck(job_dir, "v1")
    ts = snapshot(job_dir, "deck", label="My first take")
    assert ts is not None
    meta_path = job_dir / "history" / f"deck-{ts}.meta.json"
    assert meta_path.exists(), "sidecar meta should be written eagerly"
    meta = json.loads(meta_path.read_text())
    assert meta["label"] == "My first take"
    assert meta["prompt"] is None
    assert meta["ai_summary"] is None
    assert meta["parent_timestamp"] is None


def test_snapshot_with_prompt_truncates_to_ai_summary(tmp_path: Path):
    """When `prompt` is passed, ai_summary becomes the first 60 chars (no LLM call)."""
    job_dir = tmp_path / "wf-prompt"
    _write_deck(job_dir, "v1")
    long_prompt = "Make it pop with brand colors and rewrite slide 2 to emphasise growth metrics"
    ts = snapshot(job_dir, "deck", prompt=long_prompt)
    assert ts is not None
    meta = json.loads((job_dir / "history" / f"deck-{ts}.meta.json").read_text())
    # First 60 chars + ellipsis (since prompt > 60 chars)
    assert meta["ai_summary"] is not None
    assert meta["ai_summary"].startswith(long_prompt[:60])
    assert meta["ai_summary"].endswith("…")
    assert meta["prompt"] == long_prompt
    # Default label still applied (no label passed)
    assert meta["label"].startswith("Manual deck edit")


def test_snapshot_default_label_when_omitted(tmp_path: Path):
    """No label arg → default `Manual <kind> edit · HH:MM`."""
    job_dir = tmp_path / "wf-default"
    _write_deck(job_dir, "v1")
    ts = snapshot(job_dir, "deck")
    assert ts is not None
    meta = json.loads((job_dir / "history" / f"deck-{ts}.meta.json").read_text())
    assert meta["label"].startswith("Manual deck edit · ")


def test_backwards_compat_no_kwargs(tmp_path: Path):
    """The old call signature `snapshot(job_dir, kind)` still works."""
    job_dir = tmp_path / "wf-compat"
    _write_deck(job_dir, "v1")
    # The call site in three_stage_routes.py uses this positional form.
    ts = snapshot(job_dir, "deck")
    assert ts is not None
    # And the snapshot file is created at the same path the old code expected.
    assert (job_dir / "history" / f"deck-{ts}.json").exists()


def test_restore_and_fork_writes_parent_pointer(tmp_path: Path):
    """Restoring T1 should produce a NEW snapshot whose parent_timestamp == T1."""
    job_dir = tmp_path / "wf-fork"
    _write_deck(job_dir, "v1")
    ts1 = snapshot(job_dir, "deck", label="Origin")
    _write_deck(job_dir, "v2")
    snapshot(job_dir, "deck", label="Edit two")
    _write_deck(job_dir, "v3")
    snapshot(job_dir, "deck", label="Edit three")

    assert ts1 is not None
    assert restore_snapshot(job_dir, "deck", ts1) is True

    # Live deck.json should now match v1 (restored)
    live = json.loads((job_dir / "deck.json").read_text())
    assert live["title"] == "v1"

    # The forked snapshot is the new head and points back at ts1
    snaps = list_snapshots(job_dir)
    head = snaps[0]  # newest first
    assert head["kind"] == "deck"
    assert head["parent_timestamp"] == ts1
    assert head["label"].startswith("Forked from ")
    assert head["is_current"] is True


def test_list_snapshots_returns_label_and_ai_summary(tmp_path: Path):
    """list_snapshots should surface label, ai_summary, prompt, parent_timestamp."""
    job_dir = tmp_path / "wf-list"
    _write_deck(job_dir, "v1")
    snapshot(job_dir, "deck", label="My label", prompt="Polish copy and reorder slides for impact")
    snaps = list_snapshots(job_dir)
    assert len(snaps) == 1
    s = snaps[0]
    assert s["label"] == "My label"
    assert s["ai_summary"] is not None
    assert s["ai_summary"].startswith("Polish copy and reorder slides")
    assert s["prompt"] == "Polish copy and reorder slides for impact"
    assert s["parent_timestamp"] is None


def test_rename_endpoint_updates_label(tmp_path: Path, monkeypatch):
    """POST /version-cards/label updates the sidecar meta's label."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_id = "wf-rename"
    job_dir = tmp_path / "workflow" / job_id
    _write_deck(job_dir, "v1")
    ts = snapshot(job_dir, "deck", label="Original")
    assert ts is not None

    r = client.post(
        f"/workflow/wireframe/{job_id}/version-cards/label",
        data={"kind": "deck", "timestamp": ts, "label": "Renamed!"},
    )
    assert r.status_code == 200, r.text
    assert "Renamed!" in r.text

    # Verify on disk
    meta = json.loads((job_dir / "history" / f"deck-{ts}.meta.json").read_text())
    assert meta["label"] == "Renamed!"


def test_rename_function_directly(tmp_path: Path):
    """rename_snapshot is the underlying primitive for the endpoint."""
    job_dir = tmp_path / "wf-rn"
    _write_deck(job_dir, "v1")
    ts = snapshot(job_dir, "deck", label="A")
    assert ts is not None
    assert rename_snapshot(job_dir, "deck", ts, "B") is True
    snaps = list_snapshots(job_dir)
    assert snaps[0]["label"] == "B"
    # Empty string falls back to default label
    assert rename_snapshot(job_dir, "deck", ts, "") is True
    snaps = list_snapshots(job_dir)
    assert snaps[0]["label"].startswith("Manual deck edit")
    # Unknown timestamp → False
    assert rename_snapshot(job_dir, "deck", "20991231T000000000000", "x") is False


def test_compare_endpoint_returns_both_timestamps(tmp_path: Path, monkeypatch):
    """GET /compare?a=&b= renders a side-by-side page showing both snapshots' content."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_id = "wf-compare"
    job_dir = tmp_path / "workflow" / job_id
    _write_deck(job_dir, "alpha-deck")
    ts_a = snapshot(job_dir, "deck", label="Alpha")
    _write_deck(job_dir, "beta-deck")
    ts_b = snapshot(job_dir, "deck", label="Beta")
    assert ts_a is not None and ts_b is not None

    r = client.get(
        f"/workflow/wireframe/{job_id}/compare",
        params={"a": ts_a, "b": ts_b, "kind": "deck"},
    )
    assert r.status_code == 200
    body = r.text
    # Both timestamps should appear in the page (in iframe URLs and headers)
    assert ts_a in body
    assert ts_b in body
    # Both labels surface in the pane headers
    assert "Alpha" in body
    assert "Beta" in body

    # The iframe URLs hit the snapshot-view endpoint — verify it renders both
    r_a = client.get(
        f"/workflow/wireframe/{job_id}/version-cards/snapshot/{ts_a}",
        params={"kind": "deck"},
    )
    assert r_a.status_code == 200
    assert "alpha-deck" in r_a.text

    r_b = client.get(
        f"/workflow/wireframe/{job_id}/version-cards/snapshot/{ts_b}",
        params={"kind": "deck"},
    )
    assert r_b.status_code == 200
    assert "beta-deck" in r_b.text


def test_compare_rejects_missing_params(tmp_path: Path, monkeypatch):
    """Missing `a` or `b` is a 400."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_id = "wf-compare-bad"
    job_dir = tmp_path / "workflow" / job_id
    _write_deck(job_dir, "v1")
    snapshot(job_dir, "deck")

    r = client.get(f"/workflow/wireframe/{job_id}/compare", params={"kind": "deck"})
    assert r.status_code == 400


def test_restore_endpoint_creates_fork_via_http(tmp_path: Path, monkeypatch):
    """POST /version-cards/restore/<ts> writes the snapshot back AND records the fork."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_id = "wf-restore-http"
    job_dir = tmp_path / "workflow" / job_id
    _write_deck(job_dir, "first-state")
    ts1 = snapshot(job_dir, "deck", label="Origin")
    _write_deck(job_dir, "second-state")
    snapshot(job_dir, "deck", label="Second")

    r = client.post(
        f"/workflow/wireframe/{job_id}/version-cards/restore/{ts1}",
        data={"kind": "deck"},
    )
    assert r.status_code == 200
    # Live file restored
    live = json.loads((job_dir / "deck.json").read_text())
    assert live["title"] == "first-state"
    # Newest snapshot is the fork pointing at ts1
    snaps = list_snapshots(job_dir)
    assert snaps[0]["parent_timestamp"] == ts1


def test_version_cards_rail_endpoint_renders(tmp_path: Path, monkeypatch):
    """GET /version-cards returns 200 with at least one card visible."""
    monkeypatch.setenv("SLIDEATELIER_OUTPUT_DIR", str(tmp_path))
    job_id = "wf-rail"
    job_dir = tmp_path / "workflow" / job_id
    _write_deck(job_dir, "v1")
    snapshot(job_dir, "deck", label="My version")

    r = client.get(f"/workflow/wireframe/{job_id}/version-cards")
    assert r.status_code == 200
    assert "My version" in r.text
    # Single current card: no Restore button surfaces; the "current" badge does.
    assert "current" in r.text
