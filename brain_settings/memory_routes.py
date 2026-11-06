"""REST management API for the local memory worker; paid work is explicit."""

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .background import ScheduleConfig
from .memory_browse import CardStatus


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str | None = Field(default=None, max_length=100)


class CardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    limit: StrictInt = Field(default=10, ge=1, le=50)
    retry_errors: StrictBool = False


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    paused: StrictBool


class RetryCardErrorsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)



class EmbeddingSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: StrictBool
    model_id: str | None = Field(default=None, max_length=256)


class EmbeddingIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    limit: StrictInt = Field(default=8, ge=1, le=16)

def create_memory_router(service) -> APIRouter:
    router = APIRouter(prefix="/api/memory")

    @router.get("/status")
    def status():
        return {"success": True, "data": service.status()}

    @router.get("/search")
    def search(q: str = Query(default="", max_length=512),
               limit: int = Query(default=8, ge=1, le=20), mode: str = "lexical"):
        return {"success": True, "data": service.search(q, limit, mode)}

    @router.get("/embeddings")
    def embedding_settings():
        return {"success": True, "data": service.embedding_settings()}

    @router.get("/embeddings/models")
    def embedding_models():
        return {"success": True, "data": service.embedding_models()}

    @router.post("/embeddings")
    def configure_embeddings(request: EmbeddingSettingsRequest):
        return {"success": True, "data": service.configure_embeddings(
            request.enabled, request.model_id)}

    @router.post("/embeddings/index")
    def index_embeddings(request: EmbeddingIndexRequest):
        return {"success": True, "data": service.start_embedding_index(request.limit)}

    @router.get("/evidence")
    def evidence(q: str = Query(min_length=1, max_length=512),
                 source_id: str | None = Query(default=None, max_length=128),
                 project: str | None = Query(default=None, max_length=256),
                 date_from: str | None = Query(default=None, max_length=64),
                 date_to: str | None = Query(default=None, max_length=64),
                 budget_chars: int = Query(default=12000, ge=2000, le=32000),
                 mode: str = "hybrid"):
        return {"success": True, "data": service.evidence(
            q, source_id=source_id, project=project, date_from=date_from, date_to=date_to,
            budget_chars=budget_chars, mode=mode,
        )}

    @router.get("/browse")
    def browse(
        q: str = Query(default="", max_length=512),
        card_status: CardStatus = "all",
        limit: int = Query(default=30, ge=1, le=50),
        cursor: str | None = None,
    ):
        return {"success": True, "data": service.browse(q, card_status, limit, cursor)}

    @router.get("/atlas")
    def atlas():
        return {"success": True, "data": service.atlas()}

    @router.get("/records/{record_id}")
    def record(record_id: str):
        return {"success": True, "data": service.record(record_id)}

    @router.post("/import")
    def import_memory(request: ImportRequest):
        return {"success": True, "data": service.start_import(request.source_id)}

    @router.post("/cards")
    def cards(request: CardsRequest):
        return {"success": True, "data": service.start_cards(request.limit, request.retry_errors)}

    @router.post("/cards/retry-errors")
    def retry_card_errors(_request: RetryCardErrorsRequest):
        return {"success": True, "data": service.retry_all_card_errors()}

    @router.post("/sync")
    def sync(request: SyncRequest):
        return {"success": True, "data": service.set_sync_paused(request.paused)}

    @router.post("/provider-resume")
    def resume_provider(_request: ProviderResumeRequest):
        return {"success": True, "data": service.resume_provider()}

    @router.post("/schedule")
    def schedule(request: ScheduleConfig):
        return {"success": True, "data": service.update_schedule(request)}

    @router.get("/jobs/{job_id}")
    def job(job_id: str):
        return {"success": True, "data": service.job(job_id)}

    @router.get("/mcp")
    def mcp():
        from .mcp_config import connection_info

        return {"success": True, "data": connection_info(service.schedule_store.path.parent)}

    return router
