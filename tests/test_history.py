"""Tests for the cross-stage history machinery and workflow management
(rename via title_override, archive, list_workflows include_archived flag)."""
from pathlib import Path

from slideatelier.web.workflow_history import (
    can_redo,
    can_undo,
    describe_workflow,
    list_snapshots,
    list_workflows,
    redo,
    restore_snapshot,
    snapshot,
    undo,
)


def _write_storyboard(job_dir: Path, title: str, slides: int = 1) -> None:
    """Write a minimal-but-valid storyboard.json into job_dir."""
    import json
    payload = {
        "title": title,
        "subtitle": "",
        "core_message": "Core message.",
        "narrative_arc": "Open. Defend. Close.",
        "slides": [
            {
                "layout": "title",
                "title": f"Slide {i}",
                "purpose": "test",
                "rationale": "",
            }
            for i in range(slides)
        ],
    }
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "storyboard.json").write_text(json.dumps(payload, indent=2))


def test_storyboard_snapshot_undo_redo(tmp_path: Path):
    """Storyboard snapshots support undo/redo with the same machinery as deck."""
    job_dir = tmp_path / "wf-1"

    # Initial state — write storyboard then snapshot (mirrors the storyboard worker)
    _write_storyboard(job_dir, "v1")
    snapshot(job_dir, "storyboard")
    assert not can_undo(job_dir, "storyboard"), "no undo at first snapshot"

    # Edit 1
    _write_storyboard(job_dir, "v2")
    snapshot(job_dir, "storyboard")
    assert can_undo(job_dir, "storyboard")
    assert not can_redo(job_dir, "storyboard")

    # Edit 2
    _write_storyboard(job_dir, "v3")
    snapshot(job_dir, "storyboard")

    # Undo back to v2
    assert undo(job_dir, "storyboard") is True
    import json
    sb = json.loads((job_dir / "storyboard.json").read_text())
    assert sb["title"] == "v2"
    assert can_redo(job_dir, "storyboard")

    # Undo back to v1
    assert undo(job_dir, "storyboard") is True
    sb = json.loads((job_dir / "storyboard.json").read_text())
    assert sb["title"] == "v1"
    assert not can_undo(job_dir, "storyboard")

    # Redo forward to v2
    assert redo(job_dir, "storyboard") is True
    sb = json.loads((job_dir / "storyboard.json").read_text())
    assert sb["title"] == "v2"

    # Independence: deck snapshots have their own history and aren't affected
    assert not can_undo(job_dir, "deck")


def test_describe_workflow_uses_title_override(tmp_path: Path):
    """title_override.txt should win over deck.json's title field."""
    job_dir = tmp_path / "wf-2"
    job_dir.mkdir()
    import json
    (job_dir / "deck.json").write_text(json.dumps({
        "title": "Generated Title",
        "subtitle": "sub",
        "core_message": "msg",
        "narrative_arc": "arc",
        "slides": [],
    }))

    # Without override, title should be the deck's title
    info = describe_workflow(job_dir)
    assert info is not None
    assert info["title"] == "Generated Title"

    # With override, title should switch
    (job_dir / "title_override.txt").write_text("My Custom Name  ")
    info2 = describe_workflow(job_dir)
    assert info2 is not None
    assert info2["title"] == "My Custom Name"

    # Empty override should NOT clobber the real title (graceful fallback)
    (job_dir / "title_override.txt").write_text("   \n")
    info3 = describe_workflow(job_dir)
    assert info3 is not None
    assert info3["title"] == "Generated Title"


def test_archive_and_unarchive(tmp_path: Path):
    """Archived workflows are excluded by default and included when requested."""
    workflows_root = tmp_path / "workflow"
    workflows_root.mkdir()

    # One active workflow
    active = workflows_root / "active-1"
    _write_storyboard(active, "Active deck")

    # One archived workflow under .archive/
    archive_root = workflows_root / ".archive"
    archive_root.mkdir()
    archived = archive_root / "archived-1"
    _write_storyboard(archived, "Archived deck")

    # Default: only active
    listed = list_workflows(workflows_root)
    assert len(listed) == 1
    assert listed[0]["title"] == "Active deck"
    assert listed[0]["archived"] is False

    # With include_archived=True: both, archived flagged
    listed_all = list_workflows(workflows_root, include_archived=True)
    titles = {w["title"]: w["archived"] for w in listed_all}
    assert titles == {"Active deck": False, "Archived deck": True}


def test_list_snapshots_and_restore(tmp_path: Path):
    """list_snapshots reports both kinds; restore_snapshot returns to a prior state."""
    job_dir = tmp_path / "wf-3"
    _write_storyboard(job_dir, "first")
    snapshot(job_dir, "storyboard")
    first_ts_pointer = list_snapshots(job_dir)
    assert len(first_ts_pointer) == 1
    assert first_ts_pointer[0]["kind"] == "storyboard"
    assert first_ts_pointer[0]["is_current"] is True
    target_ts = first_ts_pointer[0]["timestamp"]

    # Edit
    _write_storyboard(job_dir, "second")
    snapshot(job_dir, "storyboard")
    snaps = list_snapshots(job_dir)
    assert len(snaps) == 2

    # Restore to original
    assert restore_snapshot(job_dir, "storyboard", target_ts) is True
    import json
    sb = json.loads((job_dir / "storyboard.json").read_text())
    assert sb["title"] == "first"

    # Restoring an unknown timestamp returns False
    assert restore_snapshot(job_dir, "storyboard", "20991231T000000000000") is False
