"""CSRF-protected, explicit discovery and Microsoft authorization operations."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MicrosoftStartRequest(EmptyRequest):
    client_id: str = Field(min_length=36, max_length=36)

    @field_validator("client_id")
    @classmethod
    def normalize_client_id(cls, value):
        return str(UUID(value))


class MicrosoftFlowRequest(EmptyRequest):
    flow_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def create_connected_source_router(service):
    router = APIRouter(prefix="/api/sources")

    @router.post("/apple-notes/folders")
    def notes_folders(_request: EmptyRequest):
        return {"success": True, "data": service.connected.notes_folders()}

    @router.post("/outlook/auth/start")
    def outlook_start(request: MicrosoftStartRequest):
        return {"success": True, "data": service.outlook_auth.start(request.client_id)}

    @router.post("/outlook/auth/poll")
    def outlook_poll(request: MicrosoftFlowRequest):
        return {"success": True, "data": service.outlook_auth.poll(request.flow_id)}

    @router.post("/outlook/auth/cancel")
    def outlook_cancel(request: MicrosoftFlowRequest):
        service.outlook_auth.cancel(request.flow_id)
        return {"success": True, "data": {}}

    return router
