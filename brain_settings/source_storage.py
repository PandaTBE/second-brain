"""Source schemas and atomic, nonsecret source configuration."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    ValidationError,
    field_validator,
)

from .errors import AppError
from .github_models import normalize_repository
from .notion_models import normalize_page_id, validate_notion_token

logger = logging.getLogger(__name__)
SourceKind = Literal["codex", "claude", "qwen", "folder", "imap", "notion", "github"]
GitHubContent = Literal["docs", "issues", "pull_requests"]


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: SourceKind
    name: str = Field(min_length=1, max_length=100)
    path: str | None = Field(default=None, max_length=4096)
    host: str | None = Field(default=None, max_length=253)
    username: str | None = Field(default=None, max_length=320)
    port: Literal[993] = 993
    mailbox: Literal["INBOX"] = "INBOX"
    mail_provider: Literal["custom", "gmail", "yandex"] = "custom"
    mail_import_enabled: StrictBool = False
    mail_lookback_days: int = Field(default=30, strict=True, ge=0, le=365)
    password: SecretStr | None = Field(default=None, max_length=4096)
    token: SecretStr | None = Field(default=None, max_length=4096)
    notion_page_ids: tuple[str, ...] = Field(default=(), max_length=100)
    github_repositories: tuple[str, ...] = Field(default=(), max_length=20)
    github_content: tuple[GitHubContent, ...] = Field(
        default=("docs", "issues", "pull_requests"), min_length=1, max_length=3
    )

    @field_validator("token")
    @classmethod
    def safe_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            validate_notion_token(value.get_secret_value())
        return value

    @field_validator("notion_page_ids")
    @classmethod
    def safe_page_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(normalize_page_id(item) for item in value))

    @field_validator("github_repositories")
    @classmethod
    def safe_repositories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(normalize_repository(item) for item in value))

    @field_validator("github_content")
    @classmethod
    def unique_github_content(cls, value: tuple[GitHubContent, ...]) -> tuple[GitHubContent, ...]:
        return tuple(dict.fromkeys(value))

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Введите название источника.")
        return value.strip()

    @field_validator("password")
    @classmethod
    def safe_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            text = value.get_secret_value()
            if (not text.strip() or not text.isascii()
                    or any(ord(char) < 32 or ord(char) > 126 for char in text)):
                raise ValueError("Проверьте формат пароля приложения.")
        return value

    @field_validator("mail_lookback_days")
    @classmethod
    def valid_mail_window(cls, value: int) -> int:
        if value not in (0, 30, 90, 365):
            raise ValueError("Выберите период: 30, 90, 365 дней или вся история.")
        return value


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    kind: SourceKind
    path: str | None = None
    host: str | None = None
    username: str | None = None
    port: Literal[993] = 993
    mailbox: Literal["INBOX"] = "INBOX"
    mail_provider: Literal["custom", "gmail", "yandex"] = "custom"
    mail_import_enabled: StrictBool = False
    mail_lookback_days: int = Field(default=30, strict=True, ge=0, le=365)
    enabled: bool = True
    credential_saved: bool = False
    notion_page_ids: tuple[str, ...] = Field(default=(), max_length=100)
    github_repositories: tuple[str, ...] = Field(default=(), max_length=20)
    github_content: tuple[GitHubContent, ...] = Field(
        default=("docs", "issues", "pull_requests"), min_length=1, max_length=3
    )
    last_checked_at: str | None = None
    status: Literal["not_checked", "ok", "error"] = "not_checked"
    message: str | None = None
    item_count: int | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        UUID(value)
        return value

    @field_validator("mail_lookback_days")
    @classmethod
    def valid_mail_window(cls, value: int) -> int:
        return SourceCreate.valid_mail_window(value)


class MailImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: StrictBool


class SourcesStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "sources.json"

    def load(self) -> tuple[Source, ...]:
        if not self.path.exists():
            return ()
        try:
            if self.path.stat().st_size > 1_048_576:
                raise ValueError
            data = json.loads(self.path.read_text("utf-8"))
            rows = data["sources"]
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError
            result = tuple(Source.model_validate(row) for row in rows)
            if len({source.id for source in result}) != len(result):
                raise ValueError
            return result
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            raise AppError("sources_unreadable", "Не удалось прочитать настройки источников. "
                           "Проверьте файл sources.json в папке данных приложения.", 503) from exc

    def save(self, sources: tuple[Source, ...]) -> None:
        temporary = None
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            descriptor, filename = tempfile.mkstemp(prefix=".sources-", dir=self.directory)
            temporary = Path(filename)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({"sources": [source.model_dump() for source in sources]}, output,
                          ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
        except OSError as exc:
            raise AppError("sources_write_failed", "Не удалось сохранить источники. "
                           "Проверьте доступ к папке данных приложения.", 503) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove an incomplete nonsecret sources file")
