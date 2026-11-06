"""Explicit repair of imported source scopes and credentials after a transfer."""

import json
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .errors import AppError
from .local_scan import DIRECTORY_FLAGS, _open_directory, validate_local_path
from .maintenance import operation
from .source_storage import SourceCreate
from .storage import vault_operation

LOCAL_KINDS = ("folder", "codex", "claude", "qwen")


class SourceRelocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str | None = Field(default=None, max_length=4096)
    notes_folder_ids: tuple[str, ...] = Field(default=(), max_length=50)

    @field_validator("notes_folder_ids")
    @classmethod
    def safe_folders(cls, value):
        return SourceCreate.safe_notes_folders(value)


class SourceReauthenticationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token: SecretStr | None = Field(default=None, max_length=8192)
    app_password: SecretStr | None = Field(default=None, max_length=8192)
    outlook_auth_id: str | None = Field(default=None, min_length=20, max_length=128)

    @field_validator("token", "app_password")
    @classmethod
    def safe_credential(cls, value):
        if value is not None:
            text = value.get_secret_value()
            if not text.strip() or any(ord(char) < 32 or ord(char) > 126 for char in text):
                raise ValueError("Invalid credential format")
        return value


def relocate(owner, source_id, request):
    from .background import acquire_writer_lease, release_writer_lease

    lease = acquire_writer_lease(owner.store.directory)
    try:
        return _relocate_locked(owner, source_id, request)
    finally:
        release_writer_lease(lease)


def _clear_progress(owner, source_id):
    store = getattr(owner, "memory_store", None)
    if store is None:
        if not (owner.store.directory / "memory.sqlite3").exists():
            return
        from .memory_store import MemoryStore

        store = MemoryStore(owner.store.directory)
    with store._connection(write=True) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for name in ("import_checkpoints", "apple_notes_sync_progress"):
            if name in tables:
                connection.execute(f"DELETE FROM {name} WHERE source_id=?", (source_id,))


def _relocate_locked(owner, source_id, request):
    request = SourceRelocationRequest.model_validate(request)
    with owner._lock, operation(owner.store.directory):
        current = owner.store.load()
        source = owner._find(current, source_id)
        if source.kind in LOCAL_KINDS:
            if request.notes_folder_ids:
                raise AppError(
                    "invalid_source_relocation", "Для папки выберите новый локальный путь."
                )
            path = validate_local_path(request.path)
            descriptor = _open_directory(path)
            os.close(descriptor)
            updates = {"path": path}
        elif source.kind == "apple_notes":
            if request.path is not None or not request.notes_folder_ids:
                raise AppError("invalid_source_relocation", "Выберите папки Apple Заметок заново.")
            available = {
                folder["id"] for folder in owner.apple_notes_client.list_folders()["folders"]
            }
            if not set(request.notes_folder_ids) <= available:
                raise AppError(
                    "notes_folder_missing", "Выбранная папка Apple Заметок недоступна.", 404
                )
            updates = {"notes_folder_ids": request.notes_folder_ids}
        else:
            raise AppError("invalid_source_relocation", "Для этого источника требуется новый вход.")
        _clear_progress(owner, source_id)
        updated = source.model_copy(
            update={
                **updates,
                "requires_relocation": False,
                "status": "not_checked",
                "message": None,
            }
        )
        owner.store.save(tuple(updated if item.id == source_id else item for item in current))
        return {"source": owner._public(updated)}


def _outlook_credentials(owner, source, request):
    if request.token is not None or request.app_password is not None or not request.outlook_auth_id:
        raise AppError("outlook_auth_required", "Выполните новый вход Microsoft.")
    credentials = owner.outlook_auth.take(request.outlook_auth_id)
    try:
        owner.connected._decode_credentials(json.dumps(credentials), source)
        owner.connected._claim_active(request.outlook_auth_id)
        return credentials
    except Exception:
        owner.outlook_auth.restore(request.outlook_auth_id, credentials)
        raise


def reauthenticate(owner, source_id, request):
    from .sources import source_credential_scope

    request = SourceReauthenticationRequest.model_validate(request)
    with owner._lock, operation(owner.store.directory):
        current = owner.store.load()
        source = owner._find(current, source_id)
        credentials = None
        if source.kind == "outlook":
            credentials = _outlook_credentials(owner, source, request)
            payload = json.dumps(credentials, separators=(",", ":"))
        elif source.kind in ("github", "notion", "imap"):
            selected = request.app_password if source.kind == "imap" else request.token
            wrong = request.token if source.kind == "imap" else request.app_password
            if selected is None or wrong is not None or request.outlook_auth_id is not None:
                raise AppError(
                    "source_credentials_required", "Введите новые учётные данные источника."
                )
            payload = selected.get_secret_value()
        else:
            raise AppError(
                "invalid_source_reauthentication", "Для локального источника выберите папку."
            )
        updated = source.model_copy(
            update={
                "requires_reauthentication": False,
                "credential_saved": True,
                "status": "not_checked",
                "message": None,
            }
        )
        try:
            vault_operation(owner.vault, "set", source_credential_scope(source), payload)
            if credentials is not None:
                owner.connected._claim_active(request.outlook_auth_id)
            owner.store.save(tuple(updated if item.id == source_id else item for item in current))
            if (
                credentials is not None
                and owner.outlook_auth.finish(request.outlook_auth_id) is False
            ):
                owner.store.save(current)
                raise AppError("outlook_auth_expired", "Вход Microsoft отменён или истёк.", 409)
        except Exception:
            # Failed saves retain the marker and never read/delete a coincident old key.
            if credentials is not None:
                owner.outlook_auth.restore(request.outlook_auth_id, credentials)
            raise
        return {"source": owner._public(updated)}


def _relative_key(value):
    if not isinstance(value, str) or len(value) > 4096 or "::file:" in value:
        return None
    key, separator, position = value.rpartition("::chunk:")
    key = key if separator and position.isdigit() else value
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or any(part in ("", ".", "..") for part in key.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in key)
    ):
        return None
    return key


def _local_target(source, record):
    key = _relative_key(record.get("source_object_id"))
    if key is None or not source.path:
        return None
    root = Path(source.path)
    if not root.is_absolute() or root.resolve() != root:
        return None
    descriptor = None
    try:
        descriptor = _open_directory(str(root))
        components = Path(key).parts
        for component in components[:-1]:
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_fd = os.open(
            components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
        )
        try:
            return (root / key).as_uri() if stat.S_ISREG(os.fstat(file_fd).st_mode) else None
        finally:
            os.close(file_fd)
    except OSError:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def open_target(owner, record):
    with owner._lock, operation(owner.store.directory):
        source = owner._find(owner.store.load(), record.get("source_id"))
        if (
            not source.enabled
            or source.requires_relocation
            or record.get("source_kind") != source.kind
        ):
            return None
        if source.kind in LOCAL_KINDS:
            return _local_target(source, record)
        allowed_hosts = {
            "github": {"github.com"},
            "notion": {"notion.so", "www.notion.so"},
            "outlook": {"outlook.office.com", "outlook.live.com"},
        }
        uri = record.get("source_uri", "")
        try:
            parsed = urlsplit(uri)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname in allowed_hosts.get(source.kind, set())
                and parsed.username is None
                and parsed.password is None
                and parsed.port in (None, 443)
                and len(uri) <= 2048
            )
            return uri if valid else None
        except (ValueError, TypeError):
            return None
