"""Future cross-process maintenance guards operate only on synthetic temporary state."""

import json
import subprocess
import sys

import pytest

from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore
from brain_settings.source_storage import Source, SourcesStore


def test_nested_shared_json_and_sqlite_operations_are_reentrant(tmp_path):
    from brain_settings.maintenance import operation

    store = MemoryStore(tmp_path)
    with operation(tmp_path):
        with operation(tmp_path):
            assert store.status()["documents"] == 0
            assert SourcesStore(tmp_path).load() == ()


def test_shared_to_exclusive_upgrade_is_rejected_promptly(tmp_path):
    from brain_settings.maintenance import exclusive, operation

    with operation(tmp_path), pytest.raises(AppError) as caught:
        with exclusive(tmp_path):
            pytest.fail("Shared-to-exclusive lock upgrade was incorrectly allowed")
    assert caught.value.code == "maintenance_lock_upgrade"


def test_existing_process_pause_and_resume_never_terminates_reader(tmp_path):
    from brain_settings.maintenance import exclusive

    MemoryStore(tmp_path)
    script = """
import json, sys
from pathlib import Path
from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore
store = MemoryStore(Path(sys.argv[1]))
print('ready', flush=True)
for command in sys.stdin:
    try:
        print(json.dumps({'documents': store.status()['documents']}), flush=True)
    except AppError as error:
        print(json.dumps({'error': error.code}), flush=True)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        with exclusive(tmp_path):
            process.stdin.write("read\n")
            process.stdin.flush()
            assert json.loads(process.stdout.readline())["error"] == "memory_maintenance"
            assert process.poll() is None
        process.stdin.write("read\n")
        process.stdin.flush()
        assert json.loads(process.stdout.readline()) == {"documents": 0}
        assert process.poll() is None
    finally:
        process.stdin.close()
        process.wait(timeout=10)
        assert process.returncode == 0, process.stderr.read()


def test_maintenance_blocks_json_load_and_save_not_only_database(tmp_path):
    from brain_settings.background import ScheduleStore
    from brain_settings.maintenance import exclusive
    from brain_settings.storage import SettingsStore

    MemoryStore(tmp_path)
    script = """
import json, sys
from pathlib import Path
from brain_settings.errors import AppError
from brain_settings.background import ScheduleStore
from brain_settings.storage import SettingsStore
from brain_settings.source_storage import SourcesStore
root = Path(sys.argv[1])
print('ready', flush=True)
for command in sys.stdin:
    results = []
    for factory in (SettingsStore, SourcesStore, ScheduleStore):
        try:
            factory(root).load()
            results.append('allowed')
        except AppError as error:
            results.append(error.code)
    print(json.dumps(results), flush=True)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        with exclusive(tmp_path):
            process.stdin.write("read\n")
            process.stdin.flush()
            assert json.loads(process.stdout.readline()) == ["memory_maintenance"] * 3
        assert SettingsStore(tmp_path).load().model_id == ""
        assert ScheduleStore(tmp_path).load().enabled is False
    finally:
        process.stdin.close()
        process.wait(timeout=10)


def test_lockfile_symlink_is_rejected_without_modifying_target(tmp_path):
    from brain_settings.maintenance import operation

    target = tmp_path / "synthetic-target"
    target.write_text("unchanged")
    (tmp_path / "memory-maintenance.lock").symlink_to(target)
    with pytest.raises(AppError):
        with operation(tmp_path):
            pytest.fail("Followed a maintenance-lock symlink")
    assert target.read_text() == "unchanged"


def test_unreadable_pending_recovery_journal_fails_closed_before_schema_or_sources(tmp_path):
    from brain_settings.maintenance import RECOVERY_DIRECTORY

    root = tmp_path / "data"
    root.mkdir()
    pending = root / RECOVERY_DIRECTORY
    pending.mkdir()
    (pending / "journal.json").write_text('{"phase":"prepared","unsafe":"../escape"}')
    with pytest.raises(AppError) as caught:
        MemoryStore(root)
    assert caught.value.code == "restore_recovery_required"
    with pytest.raises(AppError):
        SourcesStore(root).load()
    assert not (root / "memory.sqlite3").exists()


def test_two_live_mcp_readers_pause_during_restore_and_resume_same_citations(tmp_path):
    from brain_settings.backup import BackupService
    from brain_settings.source_policy import SourcePolicy

    sid = "00000000-0000-4000-8000-000000000063"
    directory = tmp_path / "data"
    store = MemoryStore(directory)
    SourcesStore(directory).save(
        (Source(id=sid, name="Synthetic", kind="folder", path="/synthetic"),)
    )
    store.upsert_document(
        source_id=sid,
        source_kind="folder",
        source_object_id="note.md::chunk:0",
        title="Synthetic",
        text="Synthetic citation proof.",
        source_uri="file:///synthetic/note.md",
    )
    store.set_source_policy(sid, SourcePolicy())
    script = """
import json, sys
from pathlib import Path
from brain_settings.memory_store import MemoryStore
from brain_settings.mcp_server import MemoryTools
root = Path(sys.argv[1]); tools = MemoryTools(MemoryStore(root), root)
print('ready', flush=True)
for command in sys.stdin:
    print(json.dumps(tools.get_evidence_bundle('Synthetic')), flush=True)
"""
    readers = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(directory)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]

    def ask(reader):
        reader.stdin.write("evidence\n")
        reader.stdin.flush()
        return json.loads(reader.stdout.readline())

    try:
        assert all(reader.stdout.readline().strip() == "ready" for reader in readers)
        before = [ask(reader) for reader in readers]
        assert before[0] == before[1] and before[0]["citations"]

        def while_exclusive(phase):
            if phase == "after_prepared":
                for reader in readers:
                    assert "error" in ask(reader)
                    assert reader.poll() is None

        service = BackupService(
            directory, store=store, legacy_handles=lambda: (), fault=while_exclusive
        )
        backup = service.create_local()
        preview = service.inspect(service.local_path(backup["id"]))
        service.restore(preview["inspection_id"])
        after = [ask(reader) for reader in readers]
        assert after[0] == after[1]
        assert [item["citation_id"] for item in before[0]["citations"]] == [
            item["citation_id"] for item in after[0]["citations"]
        ]
    finally:
        for reader in readers:
            reader.stdin.close()
        for reader in readers:
            reader.wait(timeout=10)


def test_missing_lsof_fails_closed_instead_of_assuming_no_legacy_readers(tmp_path, monkeypatch):
    from brain_settings.maintenance import legacy_sqlite_handles

    store = MemoryStore(tmp_path)

    def missing(*args, **kwargs):
        raise FileNotFoundError("Synthetic missing lsof")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(AppError) as caught:
        legacy_sqlite_handles(store.path)
    assert caught.value.code == "backup_isolation_unavailable"
