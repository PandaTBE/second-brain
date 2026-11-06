"""Bounded local snapshots, age exports, reviewed restores, and managed retention."""

import json
import os
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from .backup_archive import pack, schema_fingerprint, snapshot, unpack
from .backup_crypto import AgeCrypto
from .backup_files import (
    atomic_bytes,
    bounded_read,
    checksum,
    copy_bounded,
    fsync_directory,
    json_bytes,
)
from .backup_restore import restore as restore_database
from .errors import AppError
from .maintenance import legacy_sqlite_handles, operation
from .memory_store import MemoryStore

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
WARNING = "Локальные копии не зашифрованы и могут содержать материалы, позднее удалённые из памяти."


class BackupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: StrictBool = False
    directory: str | None = Field(default=None, max_length=4096)
    interval_hours: StrictInt = Field(default=24, ge=1, le=168)
    max_copies: StrictInt = Field(default=10, ge=1, le=50)
    max_total_bytes: StrictInt = Field(default=1024**3, ge=1024**2, le=10 * 1024**3)

    @field_validator("directory")
    @classmethod
    def absolute_directory(cls, value):
        if value is not None and (not Path(value).is_absolute() or "\x00" in value):
            raise ValueError("Select an absolute directory")
        return value


def _complete_schema(store):
    """Initialize the application's known lazy progress tables without reading any source."""
    from .apple_notes_import import AppleNotesProgress
    from .github_progress import GitHubProgress
    from .import_progress import ImportProgress
    from .mail_progress import MailProgress
    from .outlook_progress import OutlookProgress
    from .provider_backoff import ProviderBackoff

    ProviderBackoff(store)
    for progress in (
        AppleNotesProgress,
        GitHubProgress,
        ImportProgress,
        MailProgress,
        OutlookProgress,
    ):
        progress(store, "backup-schema-only")


class BackupService:
    def __init__(
        self,
        directory,
        *,
        store=None,
        crypto=None,
        legacy_handles=None,
        installation_id=None,
        clock=time.time,
        fault=lambda _: None,
    ):
        self.directory = Path(directory).resolve()
        self.store = store if store is not None else MemoryStore(self.directory)
        _complete_schema(self.store)
        self.crypto = crypto if crypto is not None else AgeCrypto()
        self.legacy_handles = (
            legacy_handles
            if legacy_handles is not None
            else lambda: legacy_sqlite_handles(self.store.path)
        )
        self.clock, self.fault = clock, fault
        self._lock = threading.RLock()
        self._inspections = {}
        self._path = self.directory / "backup-settings.json"
        with operation(self.directory):
            state = self._load()
            if installation_id is not None:
                state = {**state, "installation_id": str(UUID(installation_id))}
            self.installation_id = state["installation_id"]
            self._save(state)
        with tempfile.TemporaryDirectory(prefix="brain-backup-schema-") as temporary:
            template = MemoryStore(Path(temporary))
            _complete_schema(template)
            with template._connection() as connection:
                self.expected_schema = schema_fingerprint(connection)

    def _load(self):
        if not self._path.exists():
            return {
                "installation_id": str(uuid4()),
                "settings": BackupConfig().model_dump(),
                "backups": [],
                "last_run_at": None,
                "last_error": None,
            }
        try:
            value = json.loads(bounded_read(self._path, 1_048_576))
            UUID(value["installation_id"])
            BackupConfig.model_validate(value["settings"])
            if not isinstance(value["backups"], list) or len(value["backups"]) > 101:
                raise ValueError("Invalid backup inventory")
            return value
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise AppError(
                "backup_settings_unreadable", "Не удалось прочитать настройки копий.", 503
            ) from error

    def _save(self, value):
        atomic_bytes(self._path, json_bytes(value))

    def _cleanup(self):
        for key, entry in tuple(self._inspections.items()):
            if entry["expires"] <= self.clock():
                entry["temporary"].cleanup()
                self._inspections.pop(key, None)

    def _discard_staging(self, temporary):
        import logging

        try:
            temporary.cleanup()
            return True
        except OSError:
            logging.getLogger(__name__).warning("Backup staging cleanup deferred")
            return False

    def status(self):
        with self._lock, operation(self.directory):
            self._cleanup()
            state = self._load()
            public = [
                {key: row[key] for key in ("id", "name", "created_at", "size_bytes", "encrypted")}
                for row in state["backups"]
                if Path(row["path"]).is_file()
            ]
            return {
                "settings": state["settings"],
                "backups": public,
                "last_error": state["last_error"],
                "limits": {"max_archive_bytes": MAX_ARCHIVE_BYTES},
                "warning": WARNING,
            }

    def configure(self, settings):
        settings = BackupConfig.model_validate(settings)
        with self._lock, operation(self.directory):
            state = self._load()
            self._save({**state, "settings": settings.model_dump(mode="json")})
        return {"settings": settings.model_dump(mode="json")}

    def _archive(self, directory, target):
        manifest = snapshot(
            self.directory, directory, self.installation_id, self.expected_schema, MAX_ARCHIVE_BYTES
        )
        pack(directory, target, MAX_ARCHIVE_BYTES)
        return manifest

    def _error(self, error):
        with operation(self.directory):
            state = self._load()
            self._save(
                {
                    **state,
                    "last_error": getattr(error, "message", "Не удалось создать копию памяти."),
                }
            )

    def create_local(self):
        with self._lock:
            try:
                return self._create_local()
            except (OSError, ValueError, AppError) as error:
                self._error(error)
                if isinstance(error, AppError):
                    raise
                raise AppError(
                    "backup_failed", "Не удалось создать копию. Проверьте свободное место."
                ) from error

    def _create_local(self):
        with operation(self.directory):
            state = self._load()
        settings = BackupConfig.model_validate(state["settings"])
        folder = Path(settings.directory) if settings.directory else self.directory / "backups"
        if folder.resolve() != folder.absolute():
            raise AppError("backup_invalid_path", "Выберите папку без символических ссылок.")
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        identifier = str(uuid4())
        filename = "snapshot-" + identifier + ".zip"
        with tempfile.TemporaryDirectory(prefix=".brain-backup-", dir=folder) as temporary:
            staging = Path(temporary)
            archive = staging / "snapshot.zip"
            manifest = self._archive(staging, archive)
            size = archive.stat().st_size
            if size > settings.max_total_bytes:
                raise AppError("backup_too_large", "Копия превышает выбранный предел хранения.")
            destination = folder / filename
            os.link(archive, destination)
            fsync_directory(folder)
            row = {
                "id": identifier,
                "name": filename,
                "path": str(destination),
                "created_at": manifest["created_at"],
                "size_bytes": size,
                "encrypted": False,
                "sha256": checksum(destination)["sha256"],
            }
        with operation(self.directory):
            state = self._load()
            state = {
                **state,
                "backups": [*state["backups"], row],
                "last_run_at": self.clock(),
                "last_error": None,
            }
            self._save(state)
            self._prune(state, settings)
        return {key: row[key] for key in ("id", "name", "created_at", "size_bytes", "encrypted")}

    def _prune(self, state, settings):
        rows = list(state["backups"])
        while (
            len(rows) > settings.max_copies
            or sum(row["size_bytes"] for row in rows) > settings.max_total_bytes
        ):
            row = rows[0]
            self._delete_file(row)
            rows = rows[1:]
            self._save({**state, "backups": rows})

    def _delete_file(self, row):
        path = Path(row["path"])
        if path.is_symlink() or path.name != "snapshot-" + str(UUID(row["id"])) + ".zip":
            raise AppError("backup_retention_failed", "Копия изменена; удалите её вручную.")
        if not path.exists():
            return
        expected = {"size_bytes": row["size_bytes"], "sha256": row["sha256"]}
        with bounded_file(path) as opened:
            identity = os.fstat(opened.fileno())
            if checksum(path) != expected:
                raise AppError("backup_retention_failed", "Копия изменена; удалите её вручную.")
            quarantine = Path(tempfile.mkdtemp(prefix="Preserved-", dir=path.parent))
            moved = quarantine / path.name
            os.rename(path, moved)
            current = moved.lstat()
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino) or checksum(
                moved
            ) != expected:
                try:
                    os.link(moved, path)
                except FileExistsError:
                    pass
                raise AppError(
                    "backup_retention_failed",
                    "Файл изменён и сохранён без удаления. Проверьте папку копий.",
                )
            try:
                moved.unlink()
            except OSError:
                try:
                    os.link(moved, path)
                except FileExistsError:
                    pass
                raise
            quarantine.rmdir()
            fsync_directory(path.parent)

    def local_path(self, backup_id):
        with self._lock, operation(self.directory):
            identifier = str(UUID(backup_id))
            row = next((row for row in self._load()["backups"] if row["id"] == identifier), None)
            if row is None:
                raise AppError("backup_not_found", "Копия не найдена.", 404)
            return Path(row["path"])

    def delete(self, backup_id):
        with self._lock, operation(self.directory):
            path = self.local_path(backup_id)
            state = self._load()
            row = next(item for item in state["backups"] if item["path"] == str(path))
            self._delete_file(row)
            self._save({**state, "backups": [item for item in state["backups"] if item != row]})
        return {"deleted": True}

    def export(self, path, passphrase):
        path = Path(path)
        if (
            not path.is_absolute()
            or path.exists()
            or path.is_symlink()
            or path.parent.resolve() != path.parent
        ):
            raise AppError(
                "backup_invalid_path", "Выберите новый файл архива без символических ссылок."
            )
        with (
            self._lock,
            tempfile.TemporaryDirectory(prefix=".brain-export-", dir=path.parent) as temporary,
        ):
            staging = Path(temporary)
            archive, encrypted = staging / "snapshot.zip", staging / "snapshot.age"
            self._archive(staging, archive)
            self.crypto.encrypt(archive, encrypted, passphrase)
            os.link(encrypted, path)
            path.chmod(0o600)
            fsync_directory(path.parent)
        return {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "encrypted": True,
            "format": "age",
        }

    def inspect(self, path, passphrase=None):
        with self._lock:
            self._cleanup()
            temporary = tempfile.TemporaryDirectory(prefix=".brain-inspection-", dir=self.directory)
            staging = Path(temporary.name)
            try:
                source = staging / "input"
                with bounded_file(path) as stream:
                    copy_bounded(stream, source, MAX_ARCHIVE_BYTES + 1_048_576)
                archive = staging / "archive.zip"
                if passphrase is not None:
                    self.crypto.decrypt(source, archive, passphrase)
                else:
                    source.replace(archive)
                extracted = staging / "contents"
                extracted.mkdir(mode=0o700)
                manifest, counts = unpack(
                    archive, extracted, self.expected_schema, MAX_ARCHIVE_BYTES
                )
                foreign = manifest["installation_id"] != self.installation_id
                preview = self._preview(manifest, counts, extracted, foreign)
                identifier = str(uuid4())
                expires = self.clock() + 600
                self._inspections[identifier] = {
                    "temporary": temporary,
                    "staged": extracted,
                    "foreign": foreign,
                    "expires": expires,
                    "preview": preview,
                }
                return {
                    **preview,
                    "inspection_id": identifier,
                    "expires_at": datetime.fromtimestamp(expires, UTC).isoformat(),
                }
            except Exception as error:
                temporary.cleanup()
                if isinstance(error, AppError):
                    raise
                raise AppError(
                    "backup_invalid", "Архив повреждён или не удалось проверить пароль."
                ) from error

    def _preview(self, manifest, counts, extracted, foreign):
        from .source_storage import SourcesStore

        sources = SourcesStore(extracted).load()
        items = [
            {"source_id": source.id, "name": source.name[:200], "kind": source.kind}
            for source in sources
        ]
        return {
            **counts,
            "created_at": manifest["created_at"],
            "app_version": manifest["app_version"],
            "foreign_installation": foreign,
            "requires_relocation": [
                item
                for item in items
                if foreign and item["kind"] in ("folder", "codex", "claude", "qwen", "apple_notes")
            ],
            "requires_reauthentication": [
                item
                for item in items
                if foreign and item["kind"] in ("imap", "notion", "github", "outlook")
            ],
            "warnings": [
                WARNING,
                "После восстановления автоматическая обработка будет приостановлена.",
            ],
        }

    def restore(self, inspection_id):
        with self._lock:
            self._cleanup()
            entry = self._inspections.get(inspection_id)
            if entry is None:
                raise AppError(
                    "backup_inspection_expired",
                    "Проверьте архив заново перед восстановлением.",
                    409,
                )
            outcome = restore_database(
                self.directory,
                entry["staged"],
                foreign=entry["foreign"],
                handles=self.legacy_handles,
                fault=self.fault,
            )
            self._inspections.pop(inspection_id)
            staging_cleaned = self._discard_staging(entry["temporary"])
            cleanup_warning = not outcome["cleanup_complete"] or not staging_cleaned
            if cleanup_warning:
                try:
                    self._error(
                        AppError(
                            "backup_cleanup_deferred",
                            "Память восстановлена. Часть завершающих операций отложена.",
                        )
                    )
                except (AppError, OSError):
                    import logging

                    logging.getLogger(__name__).warning("Could not persist restore cleanup warning")
            return {
                key: entry["preview"][key]
                for key in ("documents", "sources", "foreign_installation")
            } | {
                "restored": True,
                "paused": True,
                "restart_recommended": False,
                "cleanup_warning": cleanup_warning,
            }

    def tick(self, now=None):
        now = self.clock() if now is None else now
        with self._lock, operation(self.directory):
            state = self._load()
            config = BackupConfig.model_validate(state["settings"])
            if not config.enabled or (
                state["last_run_at"] is not None
                and now - state["last_run_at"] < config.interval_hours * 3600
            ):
                return None
        result = self.create_local()
        with operation(self.directory):
            self._save({**self._load(), "last_run_at": now})
        return result


def bounded_file(path):
    from .backup_files import regular_file

    return regular_file(Path(path), MAX_ARCHIVE_BYTES + 1_048_576)
