"""Malformed manifest metadata and unsupported configuration never reach restoration."""

import json
import sqlite3

import pytest
from test_backup import (
    PRE_TRACKER_SCHEMA,
    legacy_archive,
    local_path,
    make_service,
    put,
    replace_entry,
    rewrite_zip,
    seed_task,
)
from test_backup import seeded as seeded

from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore


@pytest.mark.parametrize(
    "field,value",
    [
        ("installation_id", "not-a-uuid"),
        ("installation_id", {"untrusted": True}),
        ("app_version", ["0.1.0"]),
        ("app_version", "x" * 1000),
        ("created_at", "2026-11-06"),
        ("created_at", "2026-11-06T12:00:00"),
        ("created_at", float("nan")),
        ("created_at", {}),
    ],
)
def test_invalid_manifest_metadata_is_rejected(tmp_path, field, value):
    service = make_service(tmp_path / "data")
    record = put(service.store)
    source, destination = local_path(service), tmp_path / "invalid.zip"

    def mutate(entries):
        return [
            (
                info,
                json.dumps({**json.loads(payload), field: value}).encode()
                if info.filename == "manifest.json"
                else payload,
            )
            for info, payload in entries
        ]

    rewrite_zip(source, destination, mutate)
    with pytest.raises(AppError):
        service.inspect(destination)
    assert service.store.get_record(record["id"])["revision"] == 1


@pytest.mark.parametrize(
    "name,payload",
    [
        ("settings.json", {"token": "synthetic-forbidden-extra-field"}),
        ("sources.json", {"sources": [], "oauth_tokens": "synthetic-forbidden-extra-field"}),
        ("schedule.json", {"unexpected": True}),
    ],
)
def test_unknown_configuration_fields_fail_even_when_manifest_hashes_match(tmp_path, name, payload):
    service = make_service(tmp_path / "data")
    source, destination = local_path(service), tmp_path / "invalid-config.zip"
    rewrite_zip(
        source,
        destination,
        lambda entries: replace_entry(entries, name, json.dumps(payload).encode()),
    )
    with pytest.raises(AppError):
        service.inspect(destination)


def test_snapshot_budget_includes_manifest_before_publication(tmp_path, monkeypatch):
    import zipfile

    import brain_settings.backup as module

    service = make_service(tmp_path / "data")
    archive = local_path(service)
    with zipfile.ZipFile(archive) as source:
        payload_only = sum(
            entry.file_size for entry in source.infolist() if entry.filename != "manifest.json"
        )
    monkeypatch.setattr(module, "MAX_ARCHIVE_BYTES", payload_only)
    with pytest.raises(AppError) as caught:
        service.create_local()
    assert caught.value.code == "backup_too_large"


def test_repeated_snapshot_restore_closes_sqlite_handles_with_real_lsof(tmp_path):
    import sys

    from brain_settings.maintenance import legacy_sqlite_handles

    if sys.platform != "darwin":
        pytest.skip("The application restore isolation targets macOS")
    service = make_service(tmp_path / "data")
    put(service.store)
    service.legacy_handles = lambda: legacy_sqlite_handles(service.store.path)
    for _ in range(3):
        archive = local_path(service)
        assert not service.legacy_handles()
        result = service.restore(service.inspect(archive)["inspection_id"])
        assert result["restored"] is True
        assert not service.legacy_handles()


def test_default_source_recovery_markers_keep_legacy_disk_format(tmp_path):
    from test_backup import SOURCE

    from brain_settings.source_storage import Source, SourcesStore

    store = SourcesStore(tmp_path)
    store.save((Source(id=SOURCE, name="Synthetic", kind="folder", path="/synthetic"),))
    payload = json.loads(store.path.read_text())["sources"][0]
    assert "requires_relocation" not in payload
    assert "requires_reauthentication" not in payload
    restored = Source(
        id=SOURCE, name="Synthetic", kind="folder", path="/synthetic", requires_relocation=True
    )
    store.save((restored,))
    assert json.loads(store.path.read_text())["sources"][0]["requires_relocation"] is True


def test_legacy_backup_migrates_only_staging_and_restores_empty_tasks(seeded, tmp_path):
    from brain_settings.backup_archive import schema_fingerprint

    service, record = seeded
    seed_task(service.store)
    assert service.legacy_schema == PRE_TRACKER_SCHEMA
    archive = legacy_archive(service, tmp_path)
    original_bytes = archive.read_bytes()
    preview = service.inspect(archive)
    assert archive.read_bytes() == original_bytes
    with service.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM tracker_tasks").fetchone()[0] == 1
    staged = service._inspections[preview["inspection_id"]]["staged"]
    with sqlite3.connect(staged / "memory.sqlite3") as connection:
        assert schema_fingerprint(connection) == service.expected_schema
        assert connection.execute("SELECT count(*) FROM tracker_tasks").fetchone()[0] == 0
    assert not list(staged.glob("memory.sqlite3-*"))
    assert service.restore(preview["inspection_id"])["restored"]
    reopened = MemoryStore(service.directory)
    with reopened._connection() as connection:
        assert connection.execute("SELECT count(*) FROM tracker_tasks").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM tracker_progress").fetchone()[0] == 0
    assert reopened.get_record(record["id"])["revision"] == 1


@pytest.mark.parametrize("extra_sql", [
    "CREATE TABLE unexpected_schema(value TEXT)",
    "CREATE TABLE tracker_unexpected(value TEXT)",
    "CREATE TRIGGER tracker_unexpected AFTER INSERT ON documents BEGIN SELECT 1; END",
])
def test_legacy_backup_rejects_unknown_schema_without_changing_live_data(
    seeded, tmp_path, extra_sql,
):
    service, _ = seeded
    seed_task(service.store)
    archive = legacy_archive(service, tmp_path, extra_sql=extra_sql)
    with pytest.raises(AppError) as error:
        service.inspect(archive)
    assert error.value.code == "backup_invalid"
    with service.store._connection() as connection:
        assert connection.execute("SELECT sum(xp) FROM tracker_progress").fetchone()[0] == 25


def test_backup_rejects_mismatched_manifest_and_actual_schema(seeded, tmp_path):
    service, _ = seeded
    archive = legacy_archive(service, tmp_path, manifest_schema=service.expected_schema)
    with pytest.raises(AppError):
        service.inspect(archive)


def test_legacy_migration_failure_leaves_live_tasks_untouched(seeded, tmp_path, monkeypatch):
    from brain_settings import tasks

    service, _ = seeded
    seed_task(service.store)
    archive = legacy_archive(service, tmp_path)

    def fail(connection):
        connection.execute("CREATE TABLE tracker_partial(value TEXT)")
        raise sqlite3.OperationalError("synthetic migration failure")

    monkeypatch.setattr(tasks, "migrate", fail)
    with pytest.raises(AppError):
        service.inspect(archive)
    with service.store._connection() as connection:
        assert connection.execute("SELECT sum(xp) FROM tracker_progress").fetchone()[0] == 25
    assert not list(service.directory.glob(".brain-inspection-*"))


def test_legacy_migration_result_must_match_exact_current_schema(seeded, tmp_path, monkeypatch):
    from brain_settings import tasks

    service, _ = seeded
    seed_task(service.store)
    archive = legacy_archive(service, tmp_path)
    migrate = tasks.migrate

    def unexpected(connection):
        migrate(connection)
        connection.execute("CREATE TABLE tracker_unexpected(value TEXT)")

    monkeypatch.setattr(tasks, "migrate", unexpected)
    with pytest.raises(AppError):
        service.inspect(archive)
    with service.store._connection() as connection:
        assert connection.execute("SELECT sum(xp) FROM tracker_progress").fetchone()[0] == 25
