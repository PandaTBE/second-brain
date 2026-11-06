"""Future backup contracts use temporary databases and fake age transport only."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore
from brain_settings.source_storage import Source, SourcesStore

INSTALLATION = "00000000-0000-4000-8000-000000000061"
SOURCE = "00000000-0000-4000-8000-000000000062"


class FakeAge:
    """Opaque test transport, deliberately not an encryption implementation."""

    def __init__(self):
        self.payloads = {}
        self.calls = []

    def encrypt(self, source, destination, passphrase):
        marker = ("synthetic-age-" + str(uuid4())).encode()
        self.payloads[marker] = (passphrase, Path(source).read_bytes())
        self.calls.append("encrypt")
        Path(destination).write_bytes(marker)

    def decrypt(self, source, destination, passphrase):
        self.calls.append("decrypt")
        marker = Path(source).read_bytes()
        expected, payload = self.payloads[marker]
        if passphrase != expected:
            raise AppError("backup_password_invalid", "Неверный пароль или повреждённый архив.")
        Path(destination).write_bytes(payload)


def put(store, text="Synthetic original evidence.", **changes):
    return store.upsert_document(
        **{
            "source_id": SOURCE,
            "source_kind": "folder",
            "source_object_id": "note.md::chunk:000000",
            "title": "Synthetic backup note",
            "text": text,
            "source_uri": "file:///synthetic/origin/note.md#L1-L1",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            **changes,
        }
    )


def make_service(directory, *, installation_id=INSTALLATION, crypto=None, **kwargs):
    from brain_settings.backup import BackupService

    return BackupService(
        directory,
        store=MemoryStore(directory),
        crypto=crypto or FakeAge(),
        installation_id=installation_id,
        legacy_handles=lambda: (),
        **kwargs,
    )


@pytest.fixture
def seeded(tmp_path):
    directory = tmp_path / "data"
    service = make_service(directory)
    SourcesStore(directory).save(
        (Source(id=SOURCE, name="Synthetic", kind="folder", path="/synthetic/origin"),)
    )
    record = put(service.store)
    return service, record


def local_path(service):
    created = service.create_local()
    return service.local_path(created["id"])


def rewrite_zip(source, destination, transform):
    with zipfile.ZipFile(source) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, payload in transform(entries):
            archive.writestr(info, payload)


def replace_entry(entries, name, payload):
    updated = [(info, payload if info.filename == name else body) for info, body in entries]
    manifest_index = next(
        i for i, (info, _) in enumerate(updated) if info.filename == "manifest.json"
    )
    info, body = updated[manifest_index]
    manifest = json.loads(body)
    manifest["files"][name] = {
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    return [
        (item, json.dumps(manifest).encode() if index == manifest_index else data)
        for index, (item, data) in enumerate(updated)
    ]


def test_snapshot_uses_sqlite_backup_with_wal_and_manifest_allowlist(seeded):
    from brain_settings.source_policy import SourcePolicy

    service, record = seeded
    service.store.set_source_policy(SOURCE, SourcePolicy(mcp_enabled=False))
    (service.directory / "ignored-secret.json").write_text('{"token":"synthetic-never-export"}')
    (service.directory / "credentials.txt").write_text("synthetic-never-export")
    with service.store._connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    path = local_path(service)
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert names == {
            "manifest.json",
            "memory.sqlite3",
            "settings.json",
            "sources.json",
            "schedule.json",
        }
        assert b"synthetic-never-export" not in b"".join(archive.read(name) for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "second-brain-backup"
        assert manifest["format_version"] == 1
        from brain_settings import __version__

        assert manifest["app_version"] == __version__
        for name, expected in manifest["files"].items():
            payload = archive.read(name)
            assert expected == {
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        copied = service.directory / "synthetic-inspection.sqlite3"
        copied.write_bytes(archive.read("memory.sqlite3"))
    with sqlite3.connect(copied) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT id FROM documents").fetchone()[0] == record["id"]
        assert connection.execute("SELECT mcp_enabled FROM source_policies").fetchone() == (0,)


def test_encrypted_export_and_foreign_restore_preserve_allowed_records_and_policies(
    seeded, tmp_path
):
    from brain_settings.mcp_server import MemoryTools
    from brain_settings.source_policy import SourcePolicy

    origin, record = seeded
    crypto = origin.crypto
    origin.store.set_source_policy(SOURCE, SourcePolicy(external_model_enabled=False))
    archive = tmp_path / "portable.zip.age"
    result = origin.export(archive, "synthetic long passphrase")
    assert result["encrypted"] is True and crypto.calls == ["encrypt"]
    destination = make_service(tmp_path / "restored", installation_id=str(uuid4()), crypto=crypto)
    preview = destination.inspect(archive, "synthetic long passphrase")
    assert preview["foreign_installation"] is True
    assert preview["documents"] == 1
    assert preview["requires_relocation"][0]["source_id"] == SOURCE
    restored = destination.restore(preview["inspection_id"])
    assert restored["restored"] is True and restored["paused"] is True
    assert destination.store.get_record(record["id"])["text"] == "Synthetic original evidence."
    assert destination.store.source_policy(SOURCE).external_model_enabled is False
    assert destination.store.source_policy(SOURCE).import_enabled is False
    source = SourcesStore(destination.directory).load()[0]
    assert source.requires_relocation is True
    assert MemoryTools(destination.store, destination.directory).search_memory("Synthetic")[
        "results"
    ]


def test_wrong_password_and_tamper_never_change_working_memory(seeded, tmp_path):
    service, record = seeded
    archive = tmp_path / "portable.age"
    service.export(archive, "synthetic correct password")
    put(service.store, "Synthetic current evidence.")
    with pytest.raises(AppError):
        service.inspect(archive, "synthetic wrong password")
    assert service.store.get_record(record["id"])["text"] == "Synthetic current evidence."


@pytest.mark.parametrize(
    "attack", ["traversal", "absolute", "symlink", "duplicate", "extra", "directory"]
)
def test_hostile_zip_entries_are_rejected_before_working_database_changes(seeded, tmp_path, attack):
    service, record = seeded
    original = local_path(service)
    malicious = tmp_path / "malicious.zip"

    def mutate(entries):
        if attack == "duplicate":
            return [*entries, entries[0]]
        names = {
            "traversal": "../escape",
            "absolute": "/synthetic/escape",
            "extra": "token.json",
            "directory": "nested/",
            "symlink": "linked",
        }
        info = zipfile.ZipInfo(names[attack])
        if attack == "symlink":
            info.create_system = 3
            info.external_attr = 0o120777 << 16
        return [*entries, (info, b"synthetic")]

    rewrite_zip(original, malicious, mutate)
    with pytest.raises(AppError):
        service.inspect(malicious)
    assert service.store.get_record(record["id"])["text"] == "Synthetic original evidence."
    assert not (tmp_path / "escape").exists()


def test_checksum_failure_and_unknown_backup_schema_fail_closed(seeded, tmp_path):
    service, record = seeded
    original = local_path(service)
    corrupt = tmp_path / "bad-checksum.zip"
    rewrite_zip(
        original,
        corrupt,
        lambda entries: [
            (info, b"{}" if info.filename == "sources.json" else data) for info, data in entries
        ],
    )
    with pytest.raises(AppError):
        service.inspect(corrupt)
    unknown = tmp_path / "future.zip"

    def future(entries):
        return [
            (
                info,
                json.dumps({**json.loads(data), "format_version": 999}).encode()
                if info.filename == "manifest.json"
                else data,
            )
            for info, data in entries
        ]

    rewrite_zip(original, unknown, future)
    with pytest.raises(AppError):
        service.inspect(unknown)
    assert service.store.get_record(record["id"])["revision"] == 1


def test_unknown_sqlite_schema_is_rejected_even_with_correct_manifest_hash(seeded, tmp_path):
    service, record = seeded
    original = local_path(service)
    altered = tmp_path / "altered.sqlite3"
    with zipfile.ZipFile(original) as archive:
        altered.write_bytes(archive.read("memory.sqlite3"))
    with sqlite3.connect(altered) as connection:
        connection.execute("CREATE TABLE unexpected_schema(value TEXT)")
    malicious = tmp_path / "unknown-schema.zip"
    rewrite_zip(
        original,
        malicious,
        lambda entries: replace_entry(entries, "memory.sqlite3", altered.read_bytes()),
    )
    with pytest.raises(AppError):
        service.inspect(malicious)
    assert service.store.get_record(record["id"])["revision"] == 1


def test_inspection_restores_exact_reviewed_bytes_after_original_archive_changes(seeded):
    service, record = seeded
    archive = local_path(service)
    preview = service.inspect(archive)
    archive.write_bytes(b"synthetic changed after review")
    put(service.store, "Synthetic current version.")
    service.restore(preview["inspection_id"])
    assert service.store.get_record(record["id"])["text"] == "Synthetic original evidence."


def test_expired_inspection_requires_new_preflight(seeded):
    service, record = seeded
    now = [1_800_000_000.0]
    service.clock = lambda: now[0]
    preview = service.inspect(local_path(service))
    now[0] += 601
    with pytest.raises(AppError):
        service.restore(preview["inspection_id"])
    assert service.store.get_record(record["id"])["revision"] == 1


@pytest.mark.parametrize("phase", ["after_prepared", "after_database", "after_json"])
def test_interrupted_restore_recovers_original_state_before_next_store_read(
    seeded, phase, monkeypatch
):
    service, record = seeded
    archive = local_path(service)
    put(service.store, "Synthetic pre-restore current version.")
    rows = SourcesStore(service.directory).load()
    SourcesStore(service.directory).save((rows[0].model_copy(update={"name": "Current source"}),))
    script = """
import os, sys
from pathlib import Path
from brain_settings.backup import BackupService
from brain_settings.memory_store import MemoryStore
root, archive, phase = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
def crash(at):
    if at == phase:
        os._exit(77)
service = BackupService(root, store=MemoryStore(root), installation_id=sys.argv[4],
                        legacy_handles=lambda: (), fault=crash)
preview = service.inspect(archive)
service.restore(preview['inspection_id'])
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(service.directory), str(archive), phase, INSTALLATION],
        timeout=15,
        capture_output=True,
    )
    assert result.returncode == 77, result.stderr.decode()
    # Recovery uses the same synthetic empty handle inventory as the crashed child.
    monkeypatch.setattr("brain_settings.backup_restore.legacy_sqlite_handles", lambda _: ())
    reopened = MemoryStore(service.directory)
    assert reopened.get_record(record["id"])["text"] == "Synthetic pre-restore current version."
    assert reopened.source_policy(SOURCE).import_enabled is True
    assert SourcesStore(service.directory).load()[0].name == "Current source"


def test_restore_refuses_legacy_handles_and_preserves_inode(seeded):
    service, record = seeded
    preview = service.inspect(local_path(service))
    inode = service.store.path.stat().st_ino
    service.legacy_handles = lambda: (99999,)
    with pytest.raises(AppError) as caught:
        service.restore(preview["inspection_id"])
    assert caught.value.code == "backup_legacy_readers"
    assert service.store.path.stat().st_ino == inode
    assert service.store.get_record(record["id"])["revision"] == 1
    service.legacy_handles = lambda: ()
    service.restore(preview["inspection_id"])
    assert service.store.path.stat().st_ino == inode


def test_restore_safe_state_pauses_paid_jobs_and_embeddings(seeded):
    from brain_settings.background import ScheduleConfig, ScheduleStore

    service, _ = seeded
    ScheduleStore(service.directory).save(ScheduleConfig(enabled=True, process_cards=True))
    archive = local_path(service)
    service.restore(service.inspect(archive)["inspection_id"])
    config = ScheduleStore(service.directory).load()
    assert config.paused is True
    assert config.process_cards is False
    with service.store._connection() as connection:
        row = connection.execute(
            "SELECT enabled FROM embedding_settings WHERE singleton=1"
        ).fetchone()
        assert row is None or row[0] == 0


def test_retention_deletes_only_managed_local_copies_and_never_user_files(seeded):
    from brain_settings.backup import BackupConfig

    service, _ = seeded
    service.configure(BackupConfig(max_copies=2))
    first = service.create_local()
    first_path = service.local_path(first["id"])
    unrelated = first_path.parent / "user-important.zip"
    unrelated.write_bytes(b"synthetic unrelated file")
    second = service.create_local()
    third = service.create_local()
    inventory = service.status()["backups"]
    assert {item["id"] for item in inventory} == {second["id"], third["id"]}
    assert not first_path.exists()
    assert unrelated.read_bytes() == b"synthetic unrelated file"


def test_local_schedule_is_opt_in_and_retention_tick_never_generates_cards(seeded):
    from brain_settings.backup import BackupConfig

    service, _ = seeded
    assert service.status()["settings"]["enabled"] is False
    assert service.tick(now=1_800_000_000) is None
    assert service.status()["backups"] == []
    service.configure(BackupConfig(enabled=True, interval_hours=1))
    assert service.status()["backups"] == []
    service.tick(now=1_800_000_000)
    assert len(service.status()["backups"]) == 1
    assert service.tick(now=1_800_000_001) is None
    assert len(service.status()["backups"]) == 1
    assert service.crypto.calls == []


@pytest.mark.skipif(sys.platform != "darwin", reason="Legacy-reader detection targets macOS lsof")
def test_real_legacy_sqlite_handle_is_detected_without_killing_process(seeded):
    from brain_settings.maintenance import legacy_sqlite_handles

    service, _ = seeded
    connection = sqlite3.connect(service.store.path)
    try:
        connection.execute("SELECT count(*) FROM documents").fetchone()
        assert os.getpid() in legacy_sqlite_handles(service.store.path)
    finally:
        connection.close()


def test_backup_enospc_leaves_no_partial_published_copy(seeded, monkeypatch):
    import errno

    service, record = seeded
    original = zipfile.ZipFile.write

    def no_space(*args, **kwargs):
        raise OSError(errno.ENOSPC, "synthetic disk full")

    monkeypatch.setattr(zipfile.ZipFile, "write", no_space)
    with pytest.raises(AppError):
        service.create_local()
    monkeypatch.setattr(zipfile.ZipFile, "write", original)
    assert service.status()["backups"] == []
    assert service.store.get_record(record["id"])["revision"] == 1


def test_archive_size_limit_is_explicit_without_silent_truncation(seeded, monkeypatch):
    import brain_settings.backup as module

    service, record = seeded
    monkeypatch.setattr(module, "MAX_ARCHIVE_BYTES", 32)
    with pytest.raises(AppError) as caught:
        service.create_local()
    assert caught.value.code == "backup_too_large"
    assert service.store.get_record(record["id"])["text"] == "Synthetic original evidence."


def test_standard_age_header_work_factor_and_recipient_count_are_bounded():
    from brain_settings.backup_crypto import validate_age_header

    header = (
        b"age-encryption.org/v1\n-> scrypt AAAAAAAAAAAAAAAAAAAAAA 18\n"
        b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        b"--- AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
    )
    assert validate_age_header(header + b"synthetic-ciphertext") == 18
    for malicious in (
        header.replace(b" 18\n", b" 30\n"),
        header.replace(b"--- ", b"-> scrypt AAAAAAAAAAAAAAAAAAAAAA 18\n--- "),
        b"age-encryption.org/v1\n" + b"x" * 9000,
    ):
        with pytest.raises(AppError):
            validate_age_header(malicious)


def test_crypto_worker_command_is_frozen_safe_and_never_contains_passphrase(tmp_path):
    from brain_settings.backup_crypto import worker_command

    command = worker_command(
        "decrypt",
        tmp_path / "in.age",
        tmp_path / "out.zip",
        frozen=True,
        executable="/synthetic/SecondBrainHelper",
    )
    assert command[0] == "/synthetic/SecondBrainHelper"
    assert command[1] == "backup-crypto"
    assert "-c" not in command
    assert not any(
        "password" in str(argument) or "passphrase" in str(argument) for argument in command
    )


def test_foreign_connector_reauth_marker_blocks_even_matching_keychain_credentials(tmp_path):
    from brain_settings.source_storage import Source
    from brain_settings.sources import SourceService

    class ForbiddenVault:
        def get(self, scope):
            pytest.fail(
                "Restored connector consulted existing Keychain before explicit reauthentication"
            )

    source = Source(
        id=SOURCE,
        name="Synthetic remote",
        kind="github",
        github_repositories=("synthetic/project",),
        requires_reauthentication=True,
    )
    SourcesStore(tmp_path).save((source,))
    service = SourceService(tmp_path, ForbiddenVault())
    assert service.list_sources()["sources"][0]["credential_saved"] is False
    with pytest.raises(AppError) as caught:
        service.github_token(SOURCE)
    assert caught.value.code == "source_reauthentication_required"


def test_foreign_model_reauth_marker_blocks_reusing_matching_provider_keychain(tmp_path):
    from brain_settings.providers import SelectionRequest, Settings
    from brain_settings.service import SettingsService
    from brain_settings.storage import credential_scope

    class ForbiddenVault:
        def get(self, scope):
            pytest.fail("Restored provider consulted Keychain before explicit token save")

    service = SettingsService(tmp_path, vault=ForbiddenVault())
    settings = Settings(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model_id="synthetic/model",
        requires_reauthentication=True,
    )
    service.store.save(settings)
    with pytest.raises(AppError) as caught:
        request = SelectionRequest(
            provider=settings.provider, base_url=settings.base_url, model_id=settings.model_id
        )
        service._token(request, credential_scope(settings.provider, settings.base_url))
    assert caught.value.code == "model_reauthentication_required"


def test_relocation_preserves_identity_and_blocks_legacy_file_opening(tmp_path):
    from conftest import MemoryVault

    from brain_settings.source_relocation import SourceRelocationRequest
    from brain_settings.sources import SourceService

    directory = tmp_path / "data"
    store = MemoryStore(directory)
    record = put(store)
    SourcesStore(directory).save(
        (
            Source(
                id=SOURCE,
                name="Restored",
                kind="folder",
                path="/synthetic/origin",
                requires_relocation=True,
            ),
        )
    )
    service = SourceService(directory, MemoryVault())
    service.memory_store = store
    selected = tmp_path / "chosen"
    selected.mkdir()
    (selected / "note.md").write_text("Synthetic new local file.")
    result = service.relocate(SOURCE, SourceRelocationRequest(path=str(selected)))
    assert result["source"]["id"] == SOURCE
    assert result["source"]["requires_relocation"] is False
    assert store.get_record(record["id"])["source_uri"] == "file:///synthetic/origin/note.md#L1-L1"
    assert (
        service.open_target(store.get_record(record["id"]))
        != "file:///synthetic/origin/note.md#L1-L1"
    )


def test_crypto_worker_receives_secret_only_on_stdin(tmp_path, monkeypatch):
    from brain_settings.backup_crypto import AgeCrypto

    source, destination = tmp_path / "input.age", tmp_path / "output.zip"
    source.write_bytes(
        b"age-encryption.org/v1\n-> scrypt AAAAAAAAAAAAAAAAAAAAAA 18\n"
        b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        b"--- AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\nsynthetic"
    )
    calls = []
    pipe_payload = "synthetic secret sent through pipe only"

    def worker(command, **kwargs):
        calls.append((command, kwargs))
        Path(command[-1]).write_bytes(b"synthetic decrypted result")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", worker)
    crypto = AgeCrypto(executable="/synthetic/SecondBrainHelper", frozen=True)
    crypto.decrypt(source, destination, pipe_payload)
    command, options = calls[0]
    assert command[:2] == ["/synthetic/SecondBrainHelper", "backup-crypto"]
    assert pipe_payload not in str(command) and pipe_payload not in str(options.get("env", {}))
    payload = options["input"]
    assert pipe_payload in (payload.decode() if isinstance(payload, bytes) else payload)
    assert 0 < options["timeout"] <= 90


def test_crypto_worker_rejects_generated_archive_above_accepted_work_cap(tmp_path, monkeypatch):
    from brain_settings.backup_crypto import AgeCrypto

    source, destination = tmp_path / "plain.zip", tmp_path / "encrypted.age"
    source.write_bytes(b"synthetic plaintext")

    def worker(command, **kwargs):
        Path(command[-1]).write_bytes(
            b"age-encryption.org/v1\n-> scrypt AAAAAAAAAAAAAAAAAAAAAA 30\n"
            b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
            b"--- AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\nsynthetic"
        )
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", worker)
    with pytest.raises(AppError):
        AgeCrypto(executable="/synthetic/helper", frozen=True).encrypt(
            source, destination, "synthetic long password"
        )
    assert not destination.exists()


def test_crypto_resource_limits_preserve_macos_virtual_address_space(monkeypatch):
    import resource

    from brain_settings.backup_crypto import apply_worker_limits

    applied = {}
    monkeypatch.setattr(resource, "setrlimit", lambda kind, value: applied.update({kind: value}))
    apply_worker_limits()
    assert resource.RLIMIT_AS not in applied
    assert applied[resource.RLIMIT_CORE] == (0, 0)
    assert 0 < applied[resource.RLIMIT_CPU][0] <= 60
    assert applied[resource.RLIMIT_FSIZE][0] <= 130 * 1024 * 1024


def test_crypto_helper_subcommand_does_not_initialize_application_or_data_directory(
    tmp_path, monkeypatch
):
    from brain_settings import backup_crypto, helper

    called = []
    monkeypatch.setattr(helper, "data_directory", lambda *a, **k: pytest.fail("Created app data"))
    monkeypatch.setattr(backup_crypto, "worker_main", lambda *args: called.append(args))
    source, destination = tmp_path / "input.age", tmp_path / "output.zip"
    helper.main(["backup-crypto", "decrypt", str(source), str(destination)])
    assert called == [("decrypt", source, destination)]


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_helper_information_flags_do_not_initialize_services_or_data(tmp_path, monkeypatch, flag):
    from brain_settings import helper

    monkeypatch.setattr(helper, "data_directory", lambda *a, **k: pytest.fail("Created app data"))
    with pytest.raises(SystemExit) as caught:
        helper.main([flag])
    assert caught.value.code == 0


def test_active_writer_lease_prevents_restore_without_modifying_memory(seeded):
    from brain_settings.background import acquire_writer_lease, release_writer_lease

    service, record = seeded
    preview = service.inspect(local_path(service))
    lease = acquire_writer_lease(service.directory)
    try:
        with pytest.raises(AppError) as caught:
            service.restore(preview["inspection_id"])
        assert caught.value.code == "memory_busy"
        assert service.store.get_record(record["id"])["revision"] == 1
    finally:
        release_writer_lease(lease)


def test_normal_restore_failure_rolls_back_before_returning_to_reader(seeded):
    service, record = seeded
    archive = local_path(service)
    put(service.store, "Synthetic current version before failing restore.")
    rows = SourcesStore(service.directory).load()
    SourcesStore(service.directory).save((rows[0].model_copy(update={"name": "Current source"}),))

    def failure(phase):
        if phase == "after_json":
            raise OSError("Synthetic restore failure")

    service.fault = failure
    with pytest.raises(AppError):
        service.restore(service.inspect(archive)["inspection_id"])
    assert (
        service.store.get_record(record["id"])["text"]
        == "Synthetic current version before failing restore."
    )
    assert SourcesStore(service.directory).load()[0].name == "Current source"


def test_retention_failure_is_visible_and_preserves_existing_copy(seeded, monkeypatch):
    from brain_settings.backup import BackupConfig

    service, _ = seeded
    service.configure(BackupConfig(max_copies=1))
    first = service.create_local()
    first_path = service.local_path(first["id"])
    original = Path.unlink

    def denied(path, *args, **kwargs):
        if path.name == first_path.name:
            raise PermissionError("Synthetic retention refusal")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(AppError):
        service.create_local()
    assert first_path.exists()
    assert service.status()["last_error"]
