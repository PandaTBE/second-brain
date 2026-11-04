"""Bounded input for a read-only Notion internal connection."""

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def normalize_page_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9-]{32,36}", value):
        raise ValueError("Укажите ID страницы Notion.")
    return str(UUID(value))


def validate_notion_token(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or any(
        ord(char) < 33 or ord(char) > 126 for char in value
    ):
        raise ValueError("Проверьте формат токена Notion.")
    return value


class NotionSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token: SecretStr = Field(min_length=1, max_length=4096)
    query: str = Field(default="", max_length=200)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("token")
    @classmethod
    def safe_token(cls, value: SecretStr) -> SecretStr:
        validate_notion_token(value.get_secret_value())
        return value

    @field_validator("query", "cursor")
    @classmethod
    def safe_text(cls, value: str | None) -> str | None:
        if value is not None and any(ord(char) < 32 for char in value):
            raise ValueError("Проверьте запрос к Notion.")
        return value
