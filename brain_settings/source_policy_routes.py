"""Local HTTP operations for independent source policy and exclusion previews."""

from fastapi import APIRouter

from .errors import AppError
from .maintenance import operation
from .source_policy import LOCAL_KINDS, SourcePolicy


def create_source_policy_router(memory_service):
    router = APIRouter()
    sources, store = memory_service.source_service, memory_service.store

    def checked(source_id, candidate=None):
        source = sources._find(sources.store.load(), source_id)
        if candidate is not None:
            if candidate.import_enabled and source.requires_relocation:
                raise AppError(
                    "source_relocation_required",
                    "После переноса выберите локальную папку заново.",
                    409,
                )
            if candidate.import_enabled and source.requires_reauthentication:
                raise AppError(
                    "source_reauthentication_required",
                    "После переноса выполните новый вход в источник.",
                    409,
                )
            if candidate.excluded_paths and source.kind not in LOCAL_KINDS:
                raise AppError(
                    "invalid_source_policy", "Исключения путей доступны для локальных папок."
                )
            if candidate.excluded_notes_folder_ids and source.kind != "apple_notes":
                raise AppError(
                    "invalid_source_policy", "Исключения папок доступны для Apple Заметок."
                )
            historical = set(store.source_policy(source_id).excluded_notes_folder_ids)
            allowed_folders = set(source.notes_folder_ids) | historical
            if not set(candidate.excluded_notes_folder_ids) <= allowed_folders:
                raise AppError(
                    "invalid_source_policy", "Выберите папки из настроек источника Заметок."
                )
        return source

    @router.get("/api/sources/{source_id}/policy")
    def get_policy(source_id: str):
        with operation(sources.store.directory), sources._lock:
            checked(source_id)
            return {"success": True, "data": {"policy": store.source_policy(source_id)}}

    @router.put("/api/sources/{source_id}/policy")
    def set_policy(source_id: str, request: SourcePolicy):
        with operation(sources.store.directory), sources._lock:
            checked(source_id, request)
            result = store.set_source_policy(source_id, request)
            return {"success": True, "data": {"policy": result}}

    @router.post("/api/sources/{source_id}/policy/preview")
    def preview_policy(source_id: str, request: SourcePolicy):
        with operation(sources.store.directory), sources._lock:
            checked(source_id, request)
            return {"success": True, "data": store.preview_source_policy(source_id, request)}

    return router
