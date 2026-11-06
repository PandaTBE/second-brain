"""Explicit same-origin backup operations; passphrases are transient request secrets."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .background import SessionWatcher
from .backup import BackupConfig
from .errors import AppError
from .maintenance import operation


class EmptyBackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    passphrase: SecretStr | None = None


class ExportRequest(ArchiveRequest):
    passphrase: SecretStr


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inspection_id: UUID


def create_backup_router(backups, memory, sources):
    router = APIRouter()

    @router.get("/api/backups")
    def status():
        return {"success": True, "data": backups.status()}

    @router.put("/api/backups/settings")
    def settings(request: BackupConfig):
        backups.configure(request)
        return {"success": True, "data": backups.status()}

    @router.post("/api/backups/local")
    def local(_request: EmptyBackupRequest):
        return {"success": True, "data": {"backup": backups.create_local()}}

    @router.post("/api/backups/export")
    def export(request: ExportRequest):
        result = backups.export(Path(request.path), request.passphrase.get_secret_value())
        return {"success": True, "data": result}

    @router.post("/api/backups/inspect")
    def inspect(request: ArchiveRequest):
        phrase = request.passphrase.get_secret_value() if request.passphrase is not None else None
        return {"success": True, "data": backups.inspect(Path(request.path), phrase)}

    @router.post("/api/backups/restore")
    def restore(request: RestoreRequest):
        result = backups.restore(str(request.inspection_id))
        sources.outlook_auth.close()
        with memory._lock:
            memory.watcher = SessionWatcher(quiet_seconds=90)
            if memory._active_job is None:
                memory._jobs = {}
        return {"success": True, "data": result}

    @router.delete("/api/backups/{backup_id}")
    def delete(backup_id: UUID):
        return {"success": True, "data": backups.delete(str(backup_id))}

    @router.get("/api/memory/records/{record_id}/open-target")
    def open_target(record_id: str):
        keys = ("id", "revision", "source_id", "source_kind", "source_object_id", "source_uri")
        with operation(memory.store.directory), sources._lock:
            captured = memory.record(record_id)["record"]
            # Serialize the final authorization and mapping against all policy/revision writers.
            with memory.store._connection(write=True):
                current = memory.store.get_record(
                    record_id, allowed_source_ids=memory._allowed(), visibility="local"
                )
                if any(current[key] != captured[key] for key in keys):
                    raise AppError(
                        "record_changed", "Материал изменился. Откройте его заново.", 409
                    )
                uri = sources.open_target(current)
                reference = {key: current[key] for key in keys}
        return {
            "success": True,
            "data": {
                "uri": uri,
                "available": uri is not None,
                "reference": reference,
                "message": None
                if uri
                else "Выберите локальный источник заново или скопируйте его ID.",
            },
        }

    return router
