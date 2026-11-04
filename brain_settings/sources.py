"""Source connections and explicit access checks; no content import."""

import hashlib
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .errors import AppError
from .github_client import GitHubClient
from .github_models import GitHubRepositoriesRequest
from .local_scan import scan_local, validate_local_path
from .mail import MailTester
from .mail_client import MailClient
from .mail_presets import mail_access_error, mail_connection
from .notion_client import NotionClient
from .notion_models import NotionSearchRequest
from .source_storage import Source, SourceCreate, SourcesStore
from .storage import vault_operation

logger = logging.getLogger(__name__)


def source_credential_scope(source: Source) -> str:
    identity = (source.host or "") + "\0" + (source.username or "")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return "source:" + source.id + ":" + digest


class SourceService:
    def __init__(self, data_dir: Path, vault, mail_tester=None, *, notion_client=None,
                 github_client=None, mail_client=None):
        self.store = SourcesStore(data_dir)
        self.mail_client = mail_client if mail_client is not None else MailClient()
        self.github_client = github_client if github_client is not None else GitHubClient()
        self.notion_client = notion_client if notion_client is not None else NotionClient()
        self.vault = vault
        self.mail_tester = mail_tester if mail_tester is not None else MailTester()
        self._lock = threading.RLock()

    def _public(self, source: Source) -> dict:
        if source.kind not in ("imap", "notion", "github"):
            return source.model_dump()
        try:
            present = bool(vault_operation(self.vault, "get", source_credential_scope(source)))
            return source.model_copy(update={"credential_saved": present}).model_dump()
        except AppError as error:
            return source.model_copy(update={"credential_saved": False, "status": "error",
                                             "message": error.message}).model_dump()

    def list_sources(self) -> dict:
        with self._lock:
            return {"sources": [self._public(source) for source in self.store.load()]}

    def notion_pages(self, request: NotionSearchRequest) -> dict:
        return self.notion_client.search_pages(
            request.token.get_secret_value(), query=request.query, cursor=request.cursor
        )

    def github_repositories(self, request: GitHubRepositoriesRequest) -> dict:
        return self.github_client.list_repositories(request.token.get_secret_value(),
                                                    page=request.page)

    def github_token(self, source_id: str) -> str:
        with self._lock:
            source = self._find(self.store.load(), source_id)
            if source.kind != "github" or not source.enabled:
                raise AppError("github_source_unavailable", "Источник GitHub отключён.", 404)
            token = vault_operation(self.vault, "get", source_credential_scope(source))
        if not token:
            raise AppError("github_token_required", "Токен GitHub отсутствует в Связке ключей. "
                           "Удалите подключение и добавьте его заново.")
        return token

    def _test_github(self, source: Source) -> dict:
        checked_at = datetime.now(UTC).isoformat()
        error = None
        try:
            token = self.github_token(source.id)
            self.github_client.retrieve_repository(token, source.github_repositories[0])
            updates = {"status": "ok", "message":
                       "Токен и первый выбранный репозиторий доступны. "
                       "Доступ к материалам проверяется при импорте.",
                       "item_count": len(source.github_repositories)}
        except AppError as caught:
            error = caught
            updates = {"status": "error", "message": caught.message, "item_count": None}
        except Exception as caught:
            logger.warning("GitHub check failed (%s)", type(caught).__name__)
            error = AppError("github_check_failed", "Не удалось проверить GitHub. "
                             "Проверьте токен и доступ к репозиториям.", 502)
            updates = {"status": "error", "message": error.message, "item_count": None}
        with self._lock:
            current = self.store.load()
            existing = self._find(current, source.id)
            checked = existing.model_copy(update={**updates, "last_checked_at": checked_at})
            self.store.save(tuple(checked if item.id == source.id else item for item in current))
        if error is not None:
            raise error
        return {"source": self._public(checked)}

    def notion_token(self, source_id: str) -> str:
        with self._lock:
            source = self._find(self.store.load(), source_id)
            if source.kind != "notion" or not source.enabled:
                raise AppError("notion_source_unavailable", "Источник Notion отключён.", 404)
            token = vault_operation(self.vault, "get", source_credential_scope(source))
        if not token:
            raise AppError("notion_token_required", "Токен Notion отсутствует в Связке ключей. "
                           "Удалите подключение и добавьте его заново.")
        return token

    def _test_notion(self, source: Source) -> dict:
        checked_at = datetime.now(UTC).isoformat()
        error = None
        try:
            token = self.notion_token(source.id)
            self.notion_client.retrieve_metadata(token, source.notion_page_ids[0])
            updates = {"status": "ok", "message":
                       "Токен и первая выбранная страница доступны. "
                       "Остальные страницы проверяются при импорте.",
                       "item_count": len(source.notion_page_ids)}
        except AppError as caught:
            error = caught
            updates = {"status": "error", "message": caught.message, "item_count": None}
        except Exception as caught:
            logger.warning("Notion check failed (%s)", type(caught).__name__)
            error = AppError("notion_check_failed", "Не удалось проверить Notion. "
                             "Проверьте токен и доступ к выбранным страницам.", 502)
            updates = {"status": "error", "message": error.message, "item_count": None}
        with self._lock:
            current = self.store.load()
            existing = self._find(current, source.id)
            checked = existing.model_copy(update={**updates, "last_checked_at": checked_at})
            self.store.save(tuple(checked if item.id == source.id else item for item in current))
        if error is not None:
            raise error
        return {"source": self._public(checked)}

    def add(self, request: SourceCreate) -> dict:
        if request.kind == "imap":
            if request.password is None:
                raise AppError("mail_password_required", "Введите пароль приложения для почты.")
            host, username, password = mail_connection(
                request.mail_provider, request.host, request.username,
                request.password.get_secret_value(),
            )
            source = Source(id=str(uuid4()), kind=request.kind, name=request.name,
                            host=host, username=username, credential_saved=True,
                            mail_provider=request.mail_provider,
                            mail_import_enabled=request.mail_import_enabled,
                            mail_lookback_days=request.mail_lookback_days)
        elif request.kind == "notion":
            if request.token is None:
                raise AppError("notion_token_required", "Введите токен интеграции Notion.")
            if not request.notion_page_ids:
                raise AppError("notion_pages_required", "Выберите хотя бы одну страницу Notion.")
            source = Source(id=str(uuid4()), kind=request.kind, name=request.name,
                            notion_page_ids=request.notion_page_ids, credential_saved=True)
        elif request.kind == "github":
            if request.token is None:
                raise AppError("github_token_required", "Введите токен GitHub.")
            if not request.github_repositories:
                raise AppError("github_repositories_required", "Выберите репозитории GitHub.")
            source = Source(id=str(uuid4()), kind=request.kind, name=request.name,
                            github_repositories=request.github_repositories,
                            github_content=request.github_content, credential_saved=True)
        else:
            source = Source(id=str(uuid4()), kind=request.kind, name=request.name,
                            path=validate_local_path(request.path))
        with self._lock:
            current = self.store.load()
            if len(current) >= 100:
                raise AppError("source_limit", "Можно подключить не более 100 источников.")
            credential_written = False
            if source.kind in ("imap", "notion", "github"):
                secret = password if source.kind == "imap" else request.token.get_secret_value()
                vault_operation(self.vault, "set", source_credential_scope(source), secret)
                credential_written = True
            try:
                self.store.save((*current, source))
            except AppError:
                if credential_written:
                    self._rollback_credential(source, None)
                raise
        return {"source": self._public(source)}

    @staticmethod
    def _find(sources: tuple[Source, ...], source_id: str) -> Source:
        source = next((item for item in sources if item.id == source_id), None)
        if source is None:
            raise AppError("source_not_found", "Источник не найден.", 404)
        return source

    def _mail_check(self, source: Source) -> int:
        password = self.mail_password(source.id)
        try:
            count = self.mail_tester.test(host=source.host, username=source.username,
                                          password=password, port=source.port,
                                          mailbox=source.mailbox)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError
            return count
        except AppError:
            raise
        except Exception as exc:
            logger.error("Mail check failed (%s)", type(exc).__name__)
            raise AppError("mail_check_failed", "Не удалось проверить почту. "
                           "Проверьте подключение и настройки сервера.", 502) from exc

    def mail_password(self, source_id: str) -> str:
        with self._lock:
            source = self._find(self.store.load(), source_id)
            if source.kind != "imap" or not source.enabled:
                raise AppError("mail_source_unavailable", "Почтовый источник отключён.", 404)
            password = vault_operation(self.vault, "get", source_credential_scope(source))
        if not password:
            raise AppError("mail_password_required", "Пароль почты отсутствует в Связке ключей. "
                           "Удалите подключение и добавьте его заново.")
        return password

    def set_mail_import(self, source_id: str, enabled: bool) -> dict:
        if not isinstance(enabled, bool):
            raise AppError("invalid_mail_import", "Укажите состояние импорта писем.", 422)
        with self._lock:
            current = self.store.load()
            source = self._find(current, source_id)
            if source.kind != "imap":
                raise AppError("source_not_mail", "Выберите почтовый источник.", 400)
            updated = source.model_copy(update={"mail_import_enabled": enabled})
            self.store.save(tuple(updated if item.id == source_id else item for item in current))
        return {"source": self._public(updated)}

    def _test_mail(self, source: Source) -> dict:
        checked_at = datetime.now(UTC).isoformat()
        error = None
        try:
            count = self._mail_check(source)
            updates = {"status": "ok", "message":
                       f"Во входящих писем: {count}. Проверка не загружает содержимое в память.",
                       "item_count": count}
        except AppError as caught:
            error = mail_access_error(source.mail_provider, caught)
            updates = {"status": "error", "message": error.message, "item_count": None}
        with self._lock:
            current = self.store.load()
            existing = self._find(current, source.id)
            checked = existing.model_copy(update={**updates, "last_checked_at": checked_at})
            self.store.save(tuple(checked if item.id == source.id else item for item in current))
        if error is not None:
            raise error
        return {"source": self._public(checked)}

    def test(self, source_id: str) -> dict:
        with self._lock:
            source = self._find(self.store.load(), source_id)
        if source.kind == "notion":
            return self._test_notion(source)
        if source.kind == "github":
            return self._test_github(source)
        if source.kind == "imap":
            return self._test_mail(source)
        with self._lock:
            sources = self.store.load()
            source = self._find(sources, source_id)
            checked_at = datetime.now(UTC).isoformat()
            try:
                count = scan_local(source.path, source.kind)
                message = f"Найдено файлов: {count}. Проверен доступ к папке."
                checked = source.model_copy(update={"status": "ok", "message": message,
                                                    "item_count": count,
                                                    "last_checked_at": checked_at})
            except AppError as error:
                checked = source.model_copy(update={"status": "error", "message": error.message,
                                                    "item_count": None,
                                                    "last_checked_at": checked_at})
                self.store.save(tuple(checked if item.id == source_id else item
                                      for item in sources))
                raise
            self.store.save(tuple(checked if item.id == source_id else item for item in sources))
            return {"source": self._public(checked)}

    def _rollback_credential(self, source: Source, previous: str | None) -> None:
        try:
            if previous is None:
                vault_operation(self.vault, "delete", source_credential_scope(source))
            else:
                vault_operation(self.vault, "set", source_credential_scope(source), previous)
        except AppError:
            logger.error("Source credential rollback failed; credential stays in Keychain")

    def delete(self, source_id: str) -> dict:
        with self._lock:
            current = self.store.load()
            source = self._find(current, source_id)
            previous = None
            if source.kind in ("imap", "notion", "github"):
                scope = source_credential_scope(source)
                previous = vault_operation(self.vault, "get", scope)
                vault_operation(self.vault, "delete", scope)
            try:
                self.store.save(tuple(item for item in current if item.id != source_id))
            except AppError:
                if source.kind in ("imap", "notion", "github"):
                    self._rollback_credential(source, previous)
                raise
        return {"deleted": True}
