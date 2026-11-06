"""Version-one allowlisted ZIP format, schema validation, and consistent snapshots."""

import hashlib
import json
import sqlite3
import stat
import time
import zipfile
from contextlib import closing
from datetime import UTC, datetime

from . import __version__
from .background import ScheduleConfig, ScheduleStore, acquire_writer_lease, release_writer_lease
from .backup_files import (
    atomic_bytes,
    bounded_read,
    checksum,
    copy_bounded,
    json_bytes,
    regular_file,
)
from .errors import AppError
from .maintenance import exclusive
from .providers import Settings
from .source_storage import Source, SourcesStore
from .storage import SettingsStore

CONFIG_FILES = ("settings.json", "sources.json", "schedule.json")
LIVE_CONFIG_NAMES = {"schedule.json": "memory_schedule.json"}
ARCHIVE_FILES = ("memory.sqlite3", *CONFIG_FILES)
MAX_CONFIG_BYTES = 1_048_576


def schema_fingerprint(connection):
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
    ).fetchall()
    normalized = [list(row[:3]) + [" ".join((row[3] or "").split())] for row in rows]
    return hashlib.sha256(json_bytes(normalized)).hexdigest()


def copy_database(source, destination, *, timeout=15):
    deadline = time.monotonic() + timeout

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise AppError("backup_timeout", "Копирование памяти превысило допустимое время.", 503)

    source.backup(destination, pages=128, progress=progress, sleep=0.02)


def config_payloads(directory):
    for name in CONFIG_FILES:
        path = directory / LIVE_CONFIG_NAMES.get(name, name)
        if path.exists():
            bounded_read(path, MAX_CONFIG_BYTES)
    return {
        "settings.json": json_bytes(SettingsStore(directory).load().model_dump(mode="json")),
        "sources.json": json_bytes(
            {
                "sources": [
                    source.model_dump(mode="json") for source in SourcesStore(directory).load()
                ]
            }
        ),
        "schedule.json": json_bytes(ScheduleStore(directory).load().model_dump(mode="json")),
    }


def validate_configs(directory):
    settings = Settings.model_validate(json.loads(bounded_read(directory / "settings.json", 16384)))
    sources_value = json.loads(bounded_read(directory / "sources.json", MAX_CONFIG_BYTES))
    if set(sources_value) != {"sources"} or not isinstance(sources_value["sources"], list):
        raise ValueError("Invalid sources configuration")
    sources = tuple(Source.model_validate(item) for item in sources_value["sources"])
    if len(sources) > 100 or len({source.id for source in sources}) != len(sources):
        raise ValueError("Invalid sources configuration")
    schedule = ScheduleConfig.model_validate(
        json.loads(bounded_read(directory / "schedule.json", 16384))
    )
    return settings, sources, schedule


def snapshot(directory, destination, installation_id, expected_schema, maximum):
    lease = acquire_writer_lease(directory)
    try:
        with exclusive(directory):
            with closing(sqlite3.connect(directory / "memory.sqlite3")) as source:
                with closing(sqlite3.connect(destination / "memory.sqlite3")) as target:
                    copy_database(source, target)
                    target.execute("PRAGMA journal_mode=DELETE")
            for name, payload in config_payloads(directory).items():
                atomic_bytes(destination / name, payload)
    finally:
        release_writer_lease(lease)
    database = destination / "memory.sqlite3"
    database.chmod(0o600)
    with closing(sqlite3.connect(database)) as connection:
        if schema_fingerprint(connection) != expected_schema:
            raise AppError(
                "backup_schema_unsupported", "Схема базы не поддерживается форматом копии."
            )
    files = {name: checksum(destination / name) for name in ARCHIVE_FILES}
    if sum(item["size_bytes"] for item in files.values()) > maximum:
        raise AppError("backup_too_large", "Размер данных копии превышает 128 МиБ.")
    manifest = {
        "format": "second-brain-backup",
        "format_version": 1,
        "app_version": __version__,
        "created_at": datetime.now(UTC).isoformat(),
        "installation_id": installation_id,
        "schema_fingerprint": expected_schema,
        "files": files,
    }
    manifest_bytes = json_bytes(manifest)
    if sum(item["size_bytes"] for item in files.values()) + len(manifest_bytes) > maximum:
        raise AppError("backup_too_large", "Данные и manifest превышают допустимый размер.")
    atomic_bytes(destination / "manifest.json", manifest_bytes)
    return manifest


def pack(directory, destination, maximum):
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("manifest.json", *ARCHIVE_FILES):
            archive.write(directory / name, name)
    destination.chmod(0o600)
    if destination.stat().st_size > maximum:
        raise AppError("backup_too_large", "Размер архива превышает 128 МиБ.")


def _validate_manifest(manifest, expected_schema, maximum):
    from uuid import UUID

    expected_keys = {
        "format",
        "format_version",
        "app_version",
        "created_at",
        "installation_id",
        "schema_fingerprint",
        "files",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != expected_keys
        or manifest["format"] != "second-brain-backup"
        or type(manifest["format_version"]) is not int
        or manifest["format_version"] != 1
        or manifest["schema_fingerprint"] != expected_schema
        or not isinstance(manifest["files"], dict)
        or set(manifest["files"]) != set(ARCHIVE_FILES)
    ):
        raise ValueError("Unknown backup format")
    if (
        not isinstance(manifest["app_version"], str)
        or not 1 <= len(manifest["app_version"]) <= 64
        or not isinstance(manifest["created_at"], str)
        or len(manifest["created_at"]) > 64
        or not isinstance(manifest["installation_id"], str)
    ):
        raise ValueError("Invalid manifest metadata")
    UUID(manifest["installation_id"])
    timestamp = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Missing manifest timezone")
    for metadata in manifest["files"].values():
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"size_bytes", "sha256"}
            or type(metadata["size_bytes"]) is not int
            or not 0 <= metadata["size_bytes"] <= maximum
            or not isinstance(metadata["sha256"], str)
            or len(metadata["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in metadata["sha256"])
        ):
            raise ValueError("Invalid manifest file metadata")


def unpack(source, destination, expected_schema, maximum):
    try:
        with regular_file(source, maximum) as stream, zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or set(names) != {"manifest.json", *ARCHIVE_FILES}:
                raise ValueError("Unexpected archive entries")
            if sum(entry.file_size for entry in entries) > maximum:
                raise AppError("backup_too_large", "Распакованные данные превышают 128 МиБ.")
            for entry in entries:
                mode = entry.external_attr >> 16
                if (
                    entry.is_dir()
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                    or entry.flag_bits & 1
                    or entry.file_size < 0
                ):
                    raise ValueError("Unsafe archive entry")
                bound = min(maximum, 16384 if entry.filename == "manifest.json" else maximum)
                with archive.open(entry) as input_file:
                    copy_bounded(input_file, destination / entry.filename, bound)
        manifest = json.loads(bounded_read(destination / "manifest.json", 16384))
        _validate_manifest(manifest, expected_schema, maximum)
        for name in ARCHIVE_FILES:
            if manifest["files"][name] != checksum(destination / name):
                raise ValueError("Archive checksum mismatch")
        validate_configs(destination)
        database = destination / "memory.sqlite3"
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as connection:
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA query_only=ON")
            if schema_fingerprint(connection) != expected_schema or connection.execute(
                "PRAGMA integrity_check"
            ).fetchall() != [("ok",)]:
                raise ValueError("Unsupported or damaged SQLite database")
            counts = {
                "documents": connection.execute("SELECT count(*) FROM documents").fetchone()[0],
                "sources": len(SourcesStore(destination).load()),
            }
        return manifest, counts
    except AppError:
        raise
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, zipfile.BadZipFile) as error:
        raise AppError(
            "backup_invalid", "Архив повреждён или имеет неподдерживаемую схему."
        ) from error
