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
    def __init__(self, data_dir: Path, vault=None, transport=None):
        self.store = SettingsStore(data_dir)
        self.vault = vault if vault is not None else MacKeychain()
        self.transport = transport if transport is not None else ProviderTransport()
        self._lock = threading.RLock()
        self._request_times = ()

    def bootstrap(self, csrf_token: str) -> dict:
        warning = None
        try:
            settings = self.store.load()
        except AppError as error:
            settings = Settings()
            warning = error.message
        token_saved = False
        if self.vault.available:
            try:
                token_saved = bool(vault_operation(self.vault, "get", credential_scope(
                    settings.provider, settings.base_url)))
            except AppError as error:
                warning = error.message
        return {
            "csrf_token": csrf_token, "providers": provider_metadata(),
            "settings": settings.model_dump(), "token_saved": token_saved,
            "storage": {"available": bool(self.vault.available),
                        "label": "Связка ключей macOS"},
            "warning": warning,
        }

    def _endpoint(self, request: EndpointRequest) -> tuple[str, str]:
        base_url = normalize_endpoint(request.provider, request.base_url)
        return base_url, credential_scope(request.provider, base_url)

    def _token(self, request: SelectionRequest, scope: str) -> str:
        if request.token is not None:
            return request.token.get_secret_value()
        token = vault_operation(self.vault, "get", scope)
        if not token:
            raise AppError("token_required", "Вставьте API-токен выбранного провайдера.")
        return token

    def _request(self, base_url: str, path: str, token: str | None = None) -> dict:
        with self._lock:
            now = time.monotonic()
            recent = tuple(stamp for stamp in self._request_times if now - stamp < 60)
            if len(recent) >= 30:
                raise AppError("local_rate_limited", "Слишком много проверок. "
                               "Подождите минуту и повторите.", 429)
            self._request_times = (*recent, now)
        try:
            return self.transport.request(base_url, path, token)
        except AppError:
            raise
        except Exception as exc:
            logger.error("Provider operation failed (%s)", type(exc).__name__)
            raise AppError("network_error", "Не удалось получить ответ провайдера. "
                           "Проверьте подключение и повторите.", 502) from exc

    def credential_status(self, request: EndpointRequest) -> dict:
        _, scope = self._endpoint(request)
        return {"token_saved": bool(vault_operation(self.vault, "get", scope))}

    def models(self, request: SelectionRequest) -> dict:
        base_url, scope = self._endpoint(request)
        public = request.provider in ("openrouter", "simpleclaw")
        token = None if public else self._token(request, scope)
        payload = self._request(base_url, "/models", token)
        currency = "RUB" if request.provider == "simpleclaw" else "USD"
        return {"models": parse_models(payload, request.provider),
                "fetched_at": datetime.now(UTC).isoformat(), "currency": currency,
                "price_unit": currency + " / 1M tokens"}

    def test_connection(self, request: SelectionRequest) -> dict:
        base_url, scope = self._endpoint(request)
        token = self._token(request, scope)
        if request.provider == "openrouter":
            verified = self._request(base_url, "/key", token)
            if not isinstance(verified.get("data"), dict):
                raise AppError("invalid_response", "Провайдер не подтвердил API-токен.", 502)
            payload = self._request(base_url, "/models")
        elif request.provider == "simpleclaw":
            payload = self._request(base_url, "/models")
        else:
            payload = self._request(base_url, "/models", token)
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
        with self._lock:
            previous = vault_operation(self.vault, "get", scope)
            token = request.token.get_secret_value() if request.token is not None else previous
            if not token:
                raise AppError("token_required", "Вставьте API-токен выбранного провайдера.")
            settings = Settings(provider=request.provider, base_url=base_url,
                                model_id=request.model_id,
                                model_name=request.model_name.strip() or request.model_id)
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
        with self._lock:
            vault_operation(self.vault, "delete", scope)
        return {"token_saved": False}
