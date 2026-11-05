"""macOS-only credential storage and atomic nonsecret preferences."""

import hashlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from .errors import AppError
from .providers import Settings, normalize_endpoint

logger = logging.getLogger(__name__)


class Vault(Protocol):
    available: bool

    def get(self, scope: str) -> str | None: ...
    def set(self, scope: str, token: str) -> None: ...
    def delete(self, scope: str) -> None: ...


def credential_scope(provider: str, base_url: str) -> str:
    return provider + ":" + hashlib.sha256(base_url.encode("utf-8")).hexdigest()


class MacKeychain:
    service = "codex-second-brain-settings"

    def __init__(self):
        self._backend = None
        if sys.platform == "darwin":
            try:
                from keyring.backends.macOS import Keyring

                self._backend = Keyring()
            except ImportError:
                self._backend = None
        self.available = self._backend is not None

    def _require(self):
        if self._backend is None:
            raise AppError("keychain_unavailable", "Связка ключей macOS недоступна. "
                           "API-токены не сохраняются в файл.", 503)
        return self._backend

    def get(self, scope: str) -> str | None:
        return self._require().get_password(self.service, scope)

    def set(self, scope: str, token: str) -> None:
        self._require().set_password(self.service, scope, token)

    def delete(self, scope: str) -> None:
        backend = self._require()
        if backend.get_password(self.service, scope) is not None:
            backend.delete_password(self.service, scope)


def vault_operation(vault: Vault, operation: str, *args):
    if not vault.available:
        raise AppError("keychain_unavailable", "Связка ключей macOS недоступна. "
                       "API-токен не будет сохранён в файл.", 503)
    try:
        return getattr(vault, operation)(*args)
    except Exception as exc:
        raise AppError("keychain_unavailable", "Не удалось открыть Связку ключей macOS. "
                       "Проверьте, что она разблокирована, и повторите операцию.", 503) from exc


class SettingsStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "settings.json"

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            if self.path.stat().st_size > 16_384:
                raise ValueError
            settings = Settings.model_validate(json.loads(self.path.read_text("utf-8")))
            normalized = normalize_endpoint(settings.provider, settings.base_url,
                                            allow_private_network=settings.allow_private_network)
            return settings.model_copy(update={"base_url": normalized})
        except (OSError, ValueError, ValidationError, AppError) as exc:
            raise AppError("settings_unreadable", "Не удалось прочитать настройки. "
                           "Выберите подключение заново и сохраните его.", 503) from exc

    def save(self, settings: Settings) -> None:
        temporary = None
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            descriptor, filename = tempfile.mkstemp(prefix=".settings-", dir=self.directory)
            temporary = Path(filename)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                excluded = ({"allow_private_network"}
                            if not settings.allow_private_network else set())
                json.dump(settings.model_dump(exclude=excluded), output,
                          ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
        except OSError as exc:
            raise AppError("settings_write_failed", "Не удалось сохранить настройки. "
                           "Проверьте доступ к папке приложения.", 503) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove an incomplete nonsecret settings file")
