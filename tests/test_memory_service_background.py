import json
import os

import pytest

from brain_settings.background import SessionWatcher
from brain_settings.errors import AppError


def source_for(directory):
    return {"id": "synthetic-source", "kind": "codex", "path": str(directory), "enabled": True}


def test_recent_dirty_file_waits_until_quiet_then_is_not_reimported(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text('{"type":"message","text":"Synthetic"}\n')
    os.utime(path, (1000, 1000))
    watcher = SessionWatcher(quiet_seconds=90)
    source = source_for(tmp_path)
    assert watcher.collect(source, now=1050).paths == frozenset()
    batch = watcher.collect(source, now=1091)
    assert batch.paths == frozenset({str(path)})
    watcher.mark_imported(batch)
    assert watcher.collect(source, now=1200).paths == frozenset()
    path.write_text('{"type":"message","text":"Changed"}\n')
    os.utime(path, (1200, 1200))
    assert watcher.collect(source, now=1220).paths == frozenset()
    assert watcher.collect(source, now=1291).paths == frozenset({str(path)})


def test_recognized_completed_turn_marker_can_import_without_waiting(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
    os.utime(path, (1000, 1000))
    batch = SessionWatcher(quiet_seconds=90).collect(source_for(tmp_path), now=1001)
    assert batch.paths == frozenset({str(path)})


def test_watcher_never_follows_symlinks_or_generic_folder_sources(tmp_path):
    selected = tmp_path / "selected"
    selected.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("Synthetic\n")
    (selected / "linked.jsonl").symlink_to(outside)
    watcher = SessionWatcher()
    assert watcher.collect(source_for(selected), now=10**12).paths == frozenset()
    (selected / "real.jsonl").write_text("Synthetic\n")
    generic = {**source_for(selected), "kind": "folder"}
    assert watcher.collect(generic, now=10**12).paths == frozenset()


def test_watcher_rejects_ancestor_replaced_by_symlink(tmp_path):
    container = tmp_path / "container"
    selected = container / "approved"
    selected.mkdir(parents=True)
    source = source_for(selected)
    container.rename(tmp_path / "old-container")
    outside = tmp_path / "outside"
    (outside / "approved").mkdir(parents=True)
    (outside / "approved" / "private.jsonl").write_text("Synthetic outside content\n")
    container.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AppError):
        SessionWatcher().collect(source, now=10**12)


def test_watcher_bounds_empty_directories_as_well_as_files(tmp_path):
    for index in range(4):
        (tmp_path / f"directory-{index}").mkdir()
    with pytest.raises(AppError):
        SessionWatcher(max_entries=3).collect(source_for(tmp_path), now=10**12)
