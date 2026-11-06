"""Explicit recovery actions; restore itself never consults the Keychain."""

from fastapi import APIRouter

from .source_relocation import SourceReauthenticationRequest, SourceRelocationRequest


def create_source_recovery_router(service):
    router = APIRouter(prefix="/api/sources")

    @router.post("/{source_id}/relocate")
    def relocate(source_id: str, request: SourceRelocationRequest):
        return {"success": True, "data": service.relocate(source_id, request)}

    @router.post("/{source_id}/reauthenticate")
    def reauthenticate(source_id: str, request: SourceReauthenticationRequest):
        return {"success": True, "data": service.reauthenticate(source_id, request)}

    return router
