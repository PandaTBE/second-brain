"""Bounded credentials and exact repository scope for GitHub reads."""

import re

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt, field_validator

GitHubContent = tuple[str, ...]


def normalize_repository(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}", value
    ) or value.rsplit("/", 1)[-1] in (".", ".."):
        raise ValueError("Укажите репозиторий в формате owner/repository.")
    return value.lower()


def validate_github_token(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or any(
        ord(char) < 33 or ord(char) > 126 for char in value
    ):
        raise ValueError("Проверьте формат токена GitHub.")
    return value


class GitHubRepositoriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token: SecretStr = Field(min_length=1, max_length=4096)
    page: StrictInt = Field(default=1, ge=1, le=10000)

    @field_validator("token")
    @classmethod
    def safe_token(cls, value: SecretStr) -> SecretStr:
        validate_github_token(value.get_secret_value())
        return value
