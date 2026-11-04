"""Provider metadata, endpoint validation, and normalized model catalogs."""

import ipaddress
import math
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .errors import AppError


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    description: str
    key_url: str


PROVIDERS = (
    Provider(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        "Много моделей, открытый каталог и цены",
        "https://openrouter.ai/settings/keys",
    ),
    Provider(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com",
        "Прямое подключение к DeepSeek",
        "https://platform.deepseek.com/api_keys",
    ),
    Provider(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        "Модели через API OpenAI",
        "https://platform.openai.com/api-keys",
    ),
    Provider(
        "simpleclaw",
        "SimpleClaw",
        "https://simple-claw.ru/v1",
        "Провайдер из видео, цены в рублях",
        "https://simple-claw.ru/login",
    ),
    Provider(
        "custom", "Свой провайдер", "", "Совместимый с OpenAI API, в том числе SimpleClaw", ""
    ),
)


def provider_metadata() -> list[dict]:
    return [asdict(provider) for provider in PROVIDERS]


def normalize_endpoint(provider: str, base_url: str) -> str:
    known = next((item for item in PROVIDERS if item.id == provider), None)
    if known is None:
        raise AppError("invalid_provider", "Выберите провайдера из списка.")
    candidate = base_url.strip() or known.base_url
    try:
        url = urlsplit(candidate)
        hostname = (url.hostname or "").encode("idna").decode("ascii").lower()
        if (
            url.scheme != "https"
            or not hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.port not in (None, 443)
        ):
            raise ValueError
        if not re.fullmatch(r"[a-z0-9.-]+", hostname) or ".." in hostname:
            raise ValueError
        if hostname == "localhost" or hostname.endswith(
            (".localhost", ".local", ".internal", ".lan")
        ):
            raise ValueError
        if not re.fullmatch(r"[a-zA-Z0-9_./~-]*", url.path):
            raise ValueError
        if any(part in (".", "..") for part in url.path.split("/")):
            raise ValueError
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
        normalized = urlunsplit(("https", hostname, url.path.rstrip("/"), "", ""))
    except (ValueError, UnicodeError) as exc:
        raise AppError(
            "invalid_endpoint",
            "Укажите публичный HTTPS-адрес API без логина, параметров и нестандартного порта.",
        ) from exc
    if provider != "custom" and normalized != known.base_url:
        raise AppError(
            "invalid_endpoint",
            "Адрес выбранного провайдера нельзя подменить. "
            "Для другого адреса выберите «Свой провайдер».",
        )
    return normalized


class EndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = Field(max_length=32)
    base_url: str = Field(max_length=2048)


class SelectionRequest(EndpointRequest):
    token: SecretStr | None = Field(default=None, max_length=8192)
    model_id: str = Field(default="", max_length=256)
    model_name: str = Field(default="", max_length=256)

    @field_validator("token")
    @classmethod
    def token_is_header_safe(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            token = value.get_secret_value()
            if not token or any(ord(char) < 33 or ord(char) > 126 for char in token):
                raise ValueError("Некорректный формат API-токена.")
        return value


class SaveRequest(SelectionRequest):
    model_id: str = Field(min_length=1, max_length=256)

    @field_validator("model_id")
    @classmethod
    def model_id_is_not_blank(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Введите ID модели.")
        return value.strip()


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = "openrouter"
    base_url: str = "https://openrouter.ai/api/v1"
    model_id: str = ""
    model_name: str = ""


def _price(value: object, multiplier: int = 1_000_000) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        result = Decimal(str(value)) * multiplier
        if not result.is_finite() or result < 0:
            return None
        output = float(result)
        return output if math.isfinite(output) else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def parse_models(payload: dict, provider: str = "openrouter") -> list[dict]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise AppError(
            "invalid_catalog",
            "Провайдер вернул неподдерживаемый каталог. ID модели можно указать вручную.",
            502,
        )
    models = []
    seen = frozenset()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        model_id = row["id"]
        if not model_id or len(model_id) > 256 or model_id in seen:
            continue
        protocols = row.get("protocols")
        if isinstance(protocols, list) and "chat_completions" not in protocols:
            continue
        if provider == "simpleclaw" and not isinstance(protocols, list):
            continue
        if provider == "openai" and (
            ("codex" in model_id.lower() and "chat" not in model_id.lower())
            or "deep-research" in model_id.lower()
        ):
            continue
        if provider == "openai" and any(
            part in model_id.lower()
            for part in (
                "embedding",
                "whisper",
                "tts",
                "dall-e",
                "gpt-image",
                "transcribe",
                "moderation",
                "sora",
                "realtime",
                "audio",
                "video",
            )
        ):
            continue
        architecture = row.get("architecture")
        architecture = architecture if isinstance(architecture, dict) else {}
        modalities = row.get("output_modalities", architecture.get("output_modalities"))
        if isinstance(modalities, list) and "text" not in modalities:
            continue
        pricing = row.get("pricing")
        pricing = pricing if isinstance(pricing, dict) else {}
        context = row.get("context_length", row.get("context_window"))
        name = row.get("display_name", row.get("name", model_id))
        rubles = provider == "simpleclaw"
        models = [
            *models,
            {
                "id": model_id,
                "name": name if isinstance(name, str) else model_id,
                "input_price": _price(pricing.get("inputRublesPerMillion"), 1)
                if rubles
                else _price(pricing.get("prompt")),
                "output_price": _price(pricing.get("outputRublesPerMillion"), 1)
                if rubles
                else _price(pricing.get("completion")),
                "context_length": context
                if isinstance(context, int) and not isinstance(context, bool) and context > 0
                else None,
            },
        ]
        seen = seen | {model_id}
    return models
