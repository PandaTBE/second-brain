"""Connection operations separated from HTTP and browser state."""

import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from .errors import AppError
from .providers import (
    EndpointRequest,
    SaveRequest,
    SelectionRequest,
    Settings,
    normalize_endpoint,
    parse_models,
    provider_metadata,
)
from .storage import MacKeychain, SettingsStore, credential_scope, vault_operation
from .transport import ProviderTransport

logger = logging.getLogger(__name__)


class SettingsService:
    def __init__(self, data_dir: Path, vault=None, transport=None, local_client=None):
        self.store = SettingsStore(data_dir)
        self.vault = vault if vault is not None else MacKeychain()
        self.transport = transport if transport is not None else ProviderTransport()
        self._lock = threading.RLock()
        self._request_times = ()
        self._local_client = local_client

    def bootstrap(self, csrf_token: str) -> dict:
        warning = None
        try:
            settings = self.store.load()
        except AppError as error:
            settings = Settings()
            warning = error.message
        token_saved = False
        if settings.provider != "ollama" and self.vault.available:
            try:
                token_saved = bool(vault_operation(self.vault, "get", credential_scope(
                    settings.provider, settings.base_url)))
            except AppError as error:
                warning = error.message
        return {
            "csrf_token": csrf_token, "providers": provider_metadata(),
            "settings": settings.model_dump(), "token_saved": token_saved,
            "storage": {"available": bool(self.vault.available)
                        if settings.provider != "ollama" else False,
                        "label": "Связка ключей macOS"
                        if settings.provider != "ollama" else "Локальная модель без токена"},
            "warning": warning,
        }

    def _endpoint(self, request: EndpointRequest) -> tuple[str, str]:
        base_url = normalize_endpoint(request.provider, request.base_url,
                                      allow_private_network=request.allow_private_network)
        if request.provider == "ollama" and getattr(request, "token", None) is not None:
            raise AppError("local_token_forbidden", "Для локального Ollama API-токен не нужен. "
                           "Удалите токен из запроса.")
        return base_url, credential_scope(request.provider, base_url)

    def _token(self, request: SelectionRequest, scope: str) -> str:
        if request.token is not None:
            return request.token.get_secret_value()
        token = vault_operation(self.vault, "get", scope)
        if not token:
            raise AppError("token_required", "Вставьте API-токен выбранного провайдера.")
        return token

    def local_client(self):
        with self._lock:
            if self._local_client is None:
                from .ollama import OllamaClient

                self._local_client = OllamaClient()
            return self._local_client

    def _request(self, base_url: str, path: str, token: str | None = None, *, local=False,
                 allow_private_network=False):
        with self._lock:
            now = time.monotonic()
            recent = tuple(stamp for stamp in self._request_times if now - stamp < 60)
            if len(recent) >= 30:
                raise AppError("local_rate_limited", "Слишком много проверок. "
                               "Подождите минуту и повторите.", 429)
            self._request_times = (*recent, now)
        try:
            if local:
                return self.local_client().catalog()
            if allow_private_network:
                return self.transport.request(base_url, path, token, allow_private_network=True)
            return self.transport.request(base_url, path, token)
        except AppError:
            raise
        except Exception as exc:
            logger.error("Provider operation failed (%s)", type(exc).__name__)
            raise AppError("network_error", "Не удалось получить ответ провайдера. "
                           "Проверьте подключение и повторите.", 502) from exc

    def credential_status(self, request: EndpointRequest) -> dict:
        _, scope = self._endpoint(request)
        if request.provider == "ollama":
            return {"token_saved": False}
        return {"token_saved": bool(vault_operation(self.vault, "get", scope))}

    def models(self, request: SelectionRequest) -> dict:
        base_url, scope = self._endpoint(request)
        if request.provider == "ollama":
            return {"models": self._request(base_url, "/api/tags", local=True),
                    "fetched_at": datetime.now(UTC).isoformat(), "currency": "USD",
                    "price_unit": "Локально · без API-тарифа"}
        if request.provider == "qwen":
            from .qwen_catalog import fetch_catalog

            result = fetch_catalog(self._request, self._token(request, scope))
            return {**result, "fetched_at": datetime.now(UTC).isoformat(), "currency": None,
                    "price_unit": "Цена и валюта не указаны в каталоге Qwen"}
        public = request.provider in ("openrouter", "simpleclaw")
        token = None if public else self._token(request, scope)
        payload = self._request(base_url, "/models", token,
                                allow_private_network=request.allow_private_network)
        currency = "RUB" if request.provider == "simpleclaw" else "USD"
        return {"models": parse_models(payload, request.provider),
                "fetched_at": datetime.now(UTC).isoformat(), "currency": currency,
                "price_unit": currency + " / 1M tokens"}

    def test_connection(self, request: SelectionRequest) -> dict:
        base_url, scope = self._endpoint(request)
        if request.provider == "ollama":
            models = self._request(base_url, "/api/tags", local=True)
            available = any(model["id"] == request.model_id for model in models)
            available = available if request.model_id else None
            message = "Локальный Ollama доступен. Генерация не запускалась."
            if available is False:
                message += " Выбранная модель не загружена локально."
            elif not models:
                message += " Загрузите локальную модель в Ollama."
            return {"status": "warning" if available is False or not models else "ok",
                    "message": message, "checked_at": datetime.now(UTC).isoformat(),
                    "model_available": available}
        token = self._token(request, scope)
        if request.provider == "openrouter":
            verified = self._request(base_url, "/key", token)
            if not isinstance(verified.get("data"), dict):
                raise AppError("invalid_response", "Провайдер не подтвердил API-токен.", 502)
            payload = self._request(base_url, "/models")
        elif request.provider == "qwen":
            catalog = self.models(request)
            available = any(model["id"] == request.model_id for model in catalog["models"])
            available = available if request.model_id else None
            message = "Каталог Qwen доступен с этим токеном. Генерация не запускалась."
            if available is False:
                message += " Модель не найдена в загруженной части каталога; проверьте ID."
            if catalog["truncated"]:
                message += " Каталог показан частично."
            return {"status": "warning" if available is False or catalog["truncated"] else "ok",
                    "message": message, "checked_at": datetime.now(UTC).isoformat(),
                    "model_available": available}
        elif request.provider == "simpleclaw":
            payload = self._request(base_url, "/models")
        else:
            payload = self._request(base_url, "/models", token,
                                allow_private_network=request.allow_private_network)
        models = parse_models(payload, request.provider)
        available = any(model["id"] == request.model_id for model in models)
        available = available if request.model_id else None
        unverified_auth = request.provider in ("custom", "simpleclaw")
        if unverified_auth:
            message = "Каталог доступен. API-токен и генерация не проверены: "
            message += "у этого провайдера нет отдельной подтверждённой проверки ключа."
        else:
            message = "API-токен принят провайдером. Генерация не запускалась."
        if available is False:
            message += " Выбранная модель отсутствует в каталоге; проверьте её ID."
        return {"status": "warning" if unverified_auth or available is False else "ok",
                "message": message, "checked_at": datetime.now(UTC).isoformat(),
                "model_available": available}

    def save(self, request: SaveRequest) -> dict:
        base_url, scope = self._endpoint(request)
        if request.provider == "ollama":
            settings = Settings(provider=request.provider, base_url=base_url,
                                model_id=request.model_id,
                                model_name=request.model_name.strip() or request.model_id,
                                allow_private_network=request.allow_private_network)
            with self._lock:
                self.store.save(settings)
            return {"settings": settings.model_dump(), "token_saved": False}
        with self._lock:
            previous = vault_operation(self.vault, "get", scope)
            token = request.token.get_secret_value() if request.token is not None else previous
            if not token:
                raise AppError("token_required", "Вставьте API-токен выбранного провайдера.")
            settings = Settings(provider=request.provider, base_url=base_url,
                                model_id=request.model_id,
                                model_name=request.model_name.strip() or request.model_id,
                                allow_private_network=request.allow_private_network)
            replaced = request.token is not None
            if replaced:
                vault_operation(self.vault, "set", scope, token)
            try:
                self.store.save(settings)
            except AppError:
                if replaced:
                    try:
                        if previous is None:
                            vault_operation(self.vault, "delete", scope)
                        else:
                            vault_operation(self.vault, "set", scope, previous)
                    except AppError:
                        logger.error("Credential rollback failed; credential stays in Keychain")
                raise
        return {"settings": settings.model_dump(), "token_saved": True}

    def delete_token(self, request: EndpointRequest) -> dict:
        _, scope = self._endpoint(request)
        if request.provider == "ollama":
            return {"token_saved": False}
        with self._lock:
            vault_operation(self.vault, "delete", scope)
        return {"token_saved": False}
