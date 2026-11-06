"""Restore commit points and ownership stay safe under synthetic interleavings."""

import os
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from test_backup import INSTALLATION, local_path, make_service, put

from brain_settings.errors import AppError
from brain_settings.providers import SaveRequest, Settings
from brain_settings.service import SettingsService


def test_settings_workflow_prevents_restore_between_keychain_read_and_save(tmp_path):
    service = make_service(tmp_path / "data")
    archive = local_path(service)
    service.installation_id = str(uuid4())
    preview = service.inspect(archive)
    started, finish = threading.Event(), threading.Event()

    class PausedVault:
        available = True

        def get(self, scope):
            started.set()
            assert finish.wait(10)
            return "synthetic-old-provider-value"

    settings = SettingsService(service.directory, vault=PausedVault())
    settings.store.save(Settings(model_id="synthetic/old-model"))
    errors = []

    def save():
        try:
            settings.save(
                SaveRequest(
                    provider="openrouter",
                    base_url="https://openrouter.ai/api/v1",
                    model_id="synthetic/old-model",
                )
            )
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=save)
    thread.start()
    try:
        assert started.wait(3)
        with pytest.raises(AppError) as caught:
            service.restore(preview["inspection_id"])
        assert caught.value.code == "memory_maintenance"
    finally:
        finish.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    service.restore(preview["inspection_id"])
    assert settings.store.load().requires_reauthentication is True


def test_crash_during_retired_snapshot_cleanup_keeps_committed_restore_readable(tmp_path):
    service = make_service(tmp_path / "data")
    record = put(service.store)
    archive = local_path(service)
    put(service.store, "Synthetic current text before restore.")
    script = """
import os, shutil, sys
from pathlib import Path
from brain_settings.backup import BackupService
root, archive = Path(sys.argv[1]), Path(sys.argv[2])
service = BackupService(root, installation_id=sys.argv[3], legacy_handles=lambda: ())
original = shutil.rmtree
def interrupted(path, *args, **kwargs):
    path = Path(path)
    database = path / 'memory.sqlite3'
    if path.name.startswith('.restore-') and database.is_file():
        database.unlink()
        os._exit(77)
    return original(path, *args, **kwargs)
shutil.rmtree = interrupted
service.restore(service.inspect(archive)['inspection_id'])
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(service.directory), str(archive), INSTALLATION],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 77, result.stderr.decode()
    from brain_settings.memory_store import MemoryStore

    assert (
        MemoryStore(service.directory).get_record(record["id"])["text"]
        == "Synthetic original evidence."
    )


def test_retention_preserves_raced_foreign_replacement(tmp_path, monkeypatch):
    import brain_settings.backup as module

    service = make_service(tmp_path / "data")
    row = service.create_local()
    path = service.local_path(row["id"])
    original_checksum, original_rename = module.checksum, os.rename
    replaced = False

    def replace():
        nonlocal replaced
        if not replaced:
            replaced = True
            path.unlink()
            path.write_bytes(b"synthetic unrelated replacement")

    def checksum(item):
        value = original_checksum(item)
        if Path(item) == path:
            replace()
        return value

    def rename(source, target, *args, **kwargs):
        if Path(source) == path:
            replace()
        return original_rename(source, target, *args, **kwargs)

    monkeypatch.setattr(module, "checksum", checksum)
    monkeypatch.setattr(os, "rename", rename)
    with pytest.raises(AppError):
        service.delete(row["id"])
    assert replaced
    assert path.read_bytes() == b"synthetic unrelated replacement"


def test_post_commit_wal_failure_reports_applied_restore_with_warning(tmp_path, monkeypatch):
    import sqlite3

    service = make_service(tmp_path / "data")
    record = put(service.store)
    archive = local_path(service)
    put(service.store, "Synthetic current version before restore.")
    preview = service.inspect(archive)
    original_connect = sqlite3.connect

    class FailingFinalizer(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.live_database = Path(str(args[0])) == service.store.path

        def execute(self, sql, *args, **kwargs):
            if self.live_database and sql == "PRAGMA journal_mode=WAL":
                raise sqlite3.OperationalError("Synthetic final WAL transition failure")
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        return original_connect(*args, **{**kwargs, "factory": FailingFinalizer})

    monkeypatch.setattr(sqlite3, "connect", connect)
    result = service.restore(preview["inspection_id"])
    assert result["restored"] is True and result["cleanup_warning"] is True
    assert service.store.get_record(record["id"])["text"] == "Synthetic original evidence."
    assert not (service.directory / ".restore-transaction").exists()
