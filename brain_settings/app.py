"""Same-origin local HTTP application; all secret operations stay server-side."""

import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from .errors import AppError, error_content
from .github_models import GitHubRepositoriesRequest
from .memory_routes import create_memory_router
from .memory_service import MemoryService
from .notion_models import NotionSearchRequest
from .providers import EndpointRequest, SaveRequest, SelectionRequest
from .runtime import asset_directory, data_directory
from .security import LocalSecurityMiddleware
from .service import SettingsService
from .source_storage import MailImportRequest, SourceCreate
from .sources import SourceService

logger = logging.getLogger(__name__)


def create_app(data_dir: Path | None = None, vault=None, transport=None,
               mail_tester=None, memory_llm=None, notion_client=None,
               github_client=None, mail_client=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_application):
        memory_service.start_background()
        try:
            yield
        finally:
            await memory_service.close()

    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    csrf_token = secrets.token_urlsafe(32)
    service = SettingsService(data_directory(data_dir),
                              vault=vault, transport=transport)
    source_service = SourceService(service.store.directory, service.vault, mail_tester,
                                   notion_client=notion_client, github_client=github_client,
                                   mail_client=mail_client)
    memory_service = MemoryService(service.store.directory, source_service, service,
                                   generator=memory_llm)
    application.state.memory_service = memory_service
    application.include_router(create_memory_router(memory_service))
    instance_id = os.environ.get("SECOND_BRAIN_INSTANCE_ID")
    try:
        UUID(instance_id or "")
    except ValueError:
        instance_id = None
    application.add_middleware(LocalSecurityMiddleware, csrf_token=csrf_token)

    @application.exception_handler(AppError)
    async def app_error(_request: Request, error: AppError):
        return JSONResponse(error_content(error), status_code=error.status_code)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _error: RequestValidationError):
        message = "Проверьте провайдера, адрес API, ID модели и формат токена."
        if request.url.path.startswith("/api/sources"):
            message = ("Проверьте название источника, путь, настройки почты "
                       "или токен и выбранные материалы Notion/GitHub.")
        elif request.url.path.startswith("/api/memory"):
            message = "Проверьте источник, размер пакета и параметры расписания памяти."
        error = AppError("validation_error", message, 422)
        return JSONResponse(error_content(error), status_code=422)

    @application.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException):
        safe = AppError("http_error", "Запрошенная операция недоступна.", error.status_code)
        return JSONResponse(error_content(safe), status_code=error.status_code)

    @application.exception_handler(Exception)
    async def unexpected_error(_request: Request, error: Exception):
        logger.error("Application operation failed (%s)", type(error).__name__)
        safe = AppError("internal_error", "Не удалось выполнить операцию. "
                        "Обновите страницу и повторите.", 500)
        return JSONResponse(error_content(safe), status_code=500,
                            headers={"Cache-Control": "no-store"})

    @application.get("/api/health")
    def health():
        return {"success": True, "data": {"app_id": "second-brain-settings",
                                          "instance_id": instance_id}}

    @application.get("/api/bootstrap")
    def bootstrap():
        return {"success": True, "data": service.bootstrap(csrf_token)}

    @application.post("/api/credential-status")
    def credential_status(request: EndpointRequest):
        return {"success": True, "data": service.credential_status(request)}

    @application.post("/api/models")
    def models(request: SelectionRequest):
        return {"success": True, "data": service.models(request)}

    @application.post("/api/test-connection")
    def test_connection(request: SelectionRequest):
        return {"success": True, "data": service.test_connection(request)}

    @application.post("/api/settings")
    def save(request: SaveRequest):
        return {"success": True, "data": service.save(request)}

    @application.delete("/api/token")
    def delete_token(request: EndpointRequest):
        return {"success": True, "data": service.delete_token(request)}

    @application.get("/api/sources")
    def list_sources():
        return {"success": True, "data": source_service.list_sources()}

    @application.post("/api/sources")
    def add_source(request: SourceCreate):
        return {"success": True, "data": source_service.add(request)}

    @application.post("/api/sources/github/repositories")
    def github_repositories(request: GitHubRepositoriesRequest):
        return {"success": True, "data": source_service.github_repositories(request)}

    @application.post("/api/sources/notion/pages")
    def notion_pages(request: NotionSearchRequest):
        return {"success": True, "data": source_service.notion_pages(request)}

    @application.post("/api/sources/{source_id}/mail-import")
    def set_mail_import(source_id: str, request: MailImportRequest):
        return {"success": True, "data": source_service.set_mail_import(source_id, request.enabled)}

    @application.post("/api/sources/{source_id}/test")
    def test_source(source_id: str):
        return {"success": True, "data": source_service.test(source_id)}

    @application.delete("/api/sources/{source_id}")
    def delete_source(source_id: str):
        return {"success": True, "data": memory_service.disconnect_source(source_id)}

    @application.get("/")
    def index():
        path = asset_directory() / "static" / "index.html"
        if not path.exists():
            raise AppError("interface_unavailable", "Интерфейс приложения ещё не готов.", 503)
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @application.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    application.mount("/static", StaticFiles(directory=asset_directory() / "static"), name="static")
    return application
