"""Durable provider-wide admission holds; never stores credentials or payloads."""

import math
import time
from datetime import UTC, datetime

from .errors import AppError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_backoff (
    scope TEXT PRIMARY KEY,
    pause_until REAL,
    requires_user_action INTEGER NOT NULL DEFAULT 0,
    effective_concurrency INTEGER NOT NULL DEFAULT 1,
    consecutive_limits INTEGER NOT NULL DEFAULT 0
)
"""
_COOLDOWN = "Провайдер ограничил запросы. Генерация карточек временно приостановлена."
_ACTION = ("Провайдер сообщил о лимите бюджета или баланса. "
           "Проверьте аккаунт и возобновите генерацию.")


def _blocked(row, now):
    return bool(row and (row["requires_user_action"] or (row["pause_until"] or 0) > now))


def _retry_delay(error):
    value = getattr(error, "retry_after", 60)
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return 60
    if not math.isfinite(value) or value <= 0:
        return 60
    return min(86400, max(1, value))


def paused_error():
    error = AppError("provider_paused",
                     "Провайдер временно приостановлен; запрос не отправлен.", 429)
    error.request_sent = False
    return error


class ProviderBackoff:
    def __init__(self, store):
        self.store = store
        with store._connection(write=True) as connection:
            connection.execute(_SCHEMA)

    def _row(self, connection, scope):
        return connection.execute(
            "SELECT * FROM provider_backoff WHERE scope=?", (scope,),
        ).fetchone()

    def status(self, scope, configured_concurrency):
        with self.store._connection() as connection:
            row = self._row(connection, scope) if scope else None
        blocked = _blocked(row, time.time())
        action = bool(row and row["requires_user_action"])
        until = row["pause_until"] if row and blocked and not action else None
        return {
            "blocked": blocked,
            "retry_at": datetime.fromtimestamp(until, UTC).isoformat() if until else None,
            "requires_user_action": action,
            "message": (_ACTION if action else _COOLDOWN) if blocked else None,
            "effective_concurrency": min(configured_concurrency, row["effective_concurrency"])
            if row else configured_concurrency,
            "configured_concurrency": configured_concurrency,
        }

    def ensure_ready(self, scope):
        if scope is None:
            return
        with self.store._connection() as connection:
            row = self._row(connection, scope)
        if _blocked(row, time.time()):
            raise paused_error()

    def limit(self, scope, error):
        if scope is None:
            return
        now = time.time()
        until = now + _retry_delay(error)
        with self.store._connection(write=True) as connection:
            row = self._row(connection, scope)
            action = bool(getattr(error, "requires_user_action", False)) or bool(
                row and row["requires_user_action"]
            )
            until = None if action else max(until, (row["pause_until"] or 0) if row else 0)
            connection.execute(
                "INSERT INTO provider_backoff VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(scope) DO UPDATE SET pause_until=excluded.pause_until, "
                "requires_user_action=excluded.requires_user_action, effective_concurrency=1, "
                "consecutive_limits=excluded.consecutive_limits",
                (scope, until, int(action), 1 + (row["consecutive_limits"] if row else 0)),
            )

    def success(self, scope, configured_concurrency):
        if scope is None:
            return
        with self.store._connection(write=True) as connection:
            row = self._row(connection, scope)
            if row and not _blocked(row, time.time()):
                connection.execute(
                    "UPDATE provider_backoff SET effective_concurrency=?, consecutive_limits=0 "
                    "WHERE scope=?",
                    (min(configured_concurrency, row["effective_concurrency"] + 1), scope),
                )

    def resume(self, scope):
        with self.store._connection(write=True) as connection:
            connection.execute(
                "UPDATE provider_backoff SET pause_until=NULL, requires_user_action=0, "
                "effective_concurrency=1 WHERE scope=?", (scope,),
            )
