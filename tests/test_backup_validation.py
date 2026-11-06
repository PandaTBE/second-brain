"""Malformed manifest metadata and unsupported configuration never reach restoration."""

import json

import pytest
from test_backup import local_path, make_service, put, replace_entry, rewrite_zip

from brain_settings.errors import AppError


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
