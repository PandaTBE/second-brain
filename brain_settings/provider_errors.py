"""Safe provider diagnostics; upstream text is never a user-facing error."""

import json
import math
import time
from datetime import UTC
from email.utils import parsedate_to_datetime

from .errors import AppError

MAX_ERROR_BYTES = 16 * 1024
DEFAULT_RETRY_AFTER = 60.0
MAX_RETRY_AFTER = 86400.0
_QUOTA_CODES = frozenset({"RUBLE_QUOTA_EXCEEDED", "TOKEN_QUOTA_EXCEEDED"})
_MANUAL_CODES = frozenset({
    "API_KEY_RUBLE_BUDGET_EXCEEDED", "PREPAID_RUBLE_BALANCE_EXCEEDED", "insufficient_quota",
})
_SAFE_CODES = _QUOTA_CODES | _MANUAL_CODES


def _retry_after_seconds(value: str | None, now: float | None) -> float:
    if not isinstance(value, str):
        return DEFAULT_RETRY_AFTER
    value = value.strip()
    if value.isascii() and value.isdecimal():
        # Avoid converting arbitrarily large integers supplied in response headers.
        significant = value.lstrip("0") or "0"
        return max(1.0, float(significant) if len(significant) <= 5 else MAX_RETRY_AFTER)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        seconds = date.timestamp() - (time.time() if now is None else now)
    except (TypeError, ValueError, OverflowError, IndexError):
        return DEFAULT_RETRY_AFTER
    if not math.isfinite(seconds):
        return DEFAULT_RETRY_AFTER
    return min(MAX_RETRY_AFTER, max(1.0, seconds))


def _provider_code(body: bytes) -> str | None:
    if not isinstance(body, bytes) or len(body) > MAX_ERROR_BYTES:
        return None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code in _SAFE_CODES else None


class ProviderHTTPError(AppError):
    """An HTTP429 with bounded retry metadata and an allowlisted provider reason."""

    def __init__(self, retry_after: float, provider_code: str | None = None):
        safe_code = provider_code if provider_code in _SAFE_CODES else None
        if safe_code in _MANUAL_CODES:
            message = ("Исчерпан бюджет или баланс API провайдера. Проверьте оплату и лимит "
                       "ключа, затем возобновите генерацию вручную.")
        elif safe_code in _QUOTA_CODES:
            message = ("Исчерпана временная квота провайдера на токены или расходы. "
                       "Генерация продолжится после паузы.")
        else:
            message = ("Провайдер временно ограничил запросы. "
                       "Генерация продолжится после паузы.")
        super().__init__("rate_limited", message, 429)
        self.retry_after = min(MAX_RETRY_AFTER, max(1.0, retry_after))
        self.provider_code = safe_code
        self.requires_user_action = safe_code in _MANUAL_CODES
        self.request_sent = True


def rate_limit_error(retry_after: str | None = None, error_body: bytes = b"",
                     *, now: float | None = None) -> ProviderHTTPError:
    """Extract only known codes; discard upstream messages, ids, and raw headers."""
    return ProviderHTTPError(_retry_after_seconds(retry_after, now), _provider_code(error_body))
