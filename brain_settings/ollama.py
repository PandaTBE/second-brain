"""A credential-free Ollama connector confined to one numeric loopback origin."""

import http.client
import json
import math
import re
import socket
import threading
import time

from .errors import AppError
from .provider_errors import rate_limit_error

BASE_URL = "http://127.0.0.1:11434/v1"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 128 * 1024
_HOST = "127.0.0.1"
_PORT = 11434
_REMOTE_FIELDS = ("remote_host", "remote_model", "cloud", "is_cloud", "remote")


def _error(code, message, *, sent=False, status=503):
    error = AppError(code, message, status)
    error.request_sent = sent
    return error


def _model_id(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", value)
        or "cloud" in value.lower()
        or value.lower().endswith(":local")
    ):
        raise _error(
            "local_model_required",
            "Выберите загруженную локальную модель из каталога "
            "Ollama. Облачные модели не поддерживаются.",
            status=400,
        )
    return value


def _timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 180
    return min(180, max(1, value))


class _LoopbackConnection(http.client.HTTPConnection):
    def __init__(self, *arguments, **keywords):
        super().__init__(*arguments, **keywords)
        self._deadline_lock = threading.Lock()
        self._deadline_expired = False
        self._deadline_socket = None

    def connect(self):
        # Publish the socket before connect so the deadline can interrupt that phase too.
        with self._deadline_lock:
            if self._deadline_expired:
                raise TimeoutError
            raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._deadline_socket = raw
        try:
            raw.settimeout(self.timeout)
            raw.connect((_HOST, _PORT))
            with self._deadline_lock:
                if self._deadline_expired:
                    raise TimeoutError
                self.sock = raw
        except OSError:
            raw.close()
            raise

    def abort(self):
        # shutdown interrupts a blocked read even when its file object owns the fd.
        with self._deadline_lock:
            self._deadline_expired = True
            raw = self._deadline_socket
            self._deadline_socket = None
        if raw is not None:
            try:
                raw.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # The other thread may already have closed this socket.
            if self.sock is not raw:
                raw.close()
        self.close()


def _abort(connection):
    abort = getattr(connection, "abort", None)
    if callable(abort):
        abort()
    else:
        connection.close()


def _request_payload(payload):
    if not isinstance(payload, dict):
        raise _error(
            "invalid_local_request", "Не удалось подготовить локальный запрос.", status=400
        )
    model = _model_id(payload.get("model"))
    messages = payload.get("messages")
    if (
        not isinstance(messages, list)
        or not 1 <= len(messages) <= 64
        or any(
            not isinstance(row, dict)
            or row.get("role")
            not in (
                "system",
                "user",
                "assistant",
            )
            or not isinstance(row.get("content"), str)
            for row in messages
        )
    ):
        raise _error(
            "invalid_local_request", "Неподдерживаемый текст локального запроса.", status=400
        )
    predict = payload.get("max_tokens", 1024)
    if isinstance(predict, bool) or not isinstance(predict, int) or predict <= 0:
        raise _error(
            "invalid_local_request", "Неподдерживаемый лимит локальной модели.", status=400
        )
    result = {
        # Current Ollama enforces this source qualifier server-side, including aliases.
        # Older versions must fail; never fall back to an unqualified/cloud-capable request.
        "model": model + ":local",
        "messages": [{"role": row["role"], "content": row["content"]} for row in messages],
        "stream": False,
        "think": False,
        "format": "json",
        "truncate": False,
        "options": {"num_ctx": 16384, "num_predict": min(predict, 4096)},
    }
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_REQUEST_BYTES:
        raise _error(
            "request_too_large", "Текст для локальной карточки слишком большой.", status=400
        )
    return model, result


def _usage(payload):
    def count(name):
        value = payload.get(name)
        return (
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            and (0 <= value <= 1_000_000_000)
            else 0
        )

    prompt = count("prompt_eval_count")
    completion = count("eval_count")
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _response_payload(response, sent, deadline):
    if 300 <= response.status < 400:
        raise _error(
            "redirect_blocked",
            "Ollama перенаправляет запрос. Перенаправления локальной модели запрещены.",
            sent=sent,
            status=502,
        )
    if response.status == 429:
        error = rate_limit_error(response.getheader("Retry-After"))
        error.request_sent = sent
        raise error
    if not 200 <= response.status < 300:
        raise _error(
            "local_model_unavailable",
            "Ollama не выполнил запрос. Проверьте локальную модель и обновите Ollama.",
            sent=sent,
        )
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _error(
            "response_too_large", "Ответ локальной модели слишком большой.", sent=sent, status=502
        )
    if time.monotonic() >= deadline:
        raise TimeoutError
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError
    if result.get("error"):
        raise _error(
            "local_generation_failed",
            "Ollama не смог подготовить ответ. Исходный текст сохранён.",
            sent=sent,
            status=502,
        )
    return result


class OllamaClient:
    def __init__(self, connection_factory=None):
        self._connection_factory = connection_factory or _LoopbackConnection

    def _request(self, method, path, payload=None, *, timeout=5, deadline=None):
        if (method, path) not in {
            ("GET", "/api/tags"),
            ("POST", "/api/chat"),
            ("POST", "/api/embed"),
        }:
            raise _error("invalid_local_request", "Неподдерживаемый локальный запрос.", status=400)
        seconds = _timeout(timeout)
        connection, timer, response = None, None, None
        sent = False
        if deadline is None:
            deadline = time.monotonic() + seconds
        else:
            seconds = min(seconds, max(0, deadline - time.monotonic()))
        try:
            if seconds <= 0:
                raise TimeoutError
            connection = self._connection_factory(_HOST, _PORT, timeout=seconds)
            timer = threading.Timer(seconds, _abort, args=(connection,))
            timer.daemon = True
            body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
            if body is not None and len(body) > MAX_REQUEST_BYTES:
                raise _error(
                    "request_too_large", "Текст для локальной карточки слишком большой.", status=400
                )
            timer.start()
            headers = {
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Content-Type": "application/json",
                "User-Agent": "SecondBrainLocal/0.1",
            }
            sent = method == "POST"
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return _response_payload(response, sent, deadline)
        except (OSError, http.client.HTTPException, RuntimeError):
            raise _error(
                "local_model_unavailable",
                "Не удалось подключиться к Ollama. "
                "Запустите его на этом Mac и загрузите локальную модель.",
                sent=sent,
            ) from None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise _error(
                "invalid_local_response",
                "Ollama вернул неподдерживаемый ответ. Исходный текст сохранён.",
                sent=sent,
                status=502,
            ) from None
        finally:
            if timer is not None:
                timer.cancel()
            close_response = getattr(response, "close", None)
            if callable(close_response):
                close_response()
            if connection is not None:
                connection.close()

    def catalog(self):
        try:
            payload = self._request("GET", "/api/tags")
        except AppError as error:
            if error.code != "invalid_local_response":
                raise
            raise _error(
                "invalid_local_catalog", "Ollama вернул неподдерживаемый каталог.", status=502
            ) from None
        rows = payload.get("models")
        if not isinstance(rows, list):
            raise _error(
                "invalid_local_catalog", "Ollama вернул неподдерживаемый каталог.", status=502
            )
        models = []
        seen = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or any(row.get(name) for name in _REMOTE_FIELDS)
                or not isinstance(row.get("size"), int)
                or isinstance(row["size"], bool)
                or row["size"] <= 0
            ):
                continue
            try:
                model = _model_id(row.get("name", row.get("model")))
            except AppError:
                continue
            if model not in seen:
                models = [
                    *models,
                    {
                        "id": model,
                        "name": model,
                        "input_price": 0,
                        "output_price": 0,
                        "context_length": None,
                    },
                ]
                seen = seen | {model}
        return models

    def ensure_local_model(self, model_id):
        selected = _model_id(model_id)
        if not any(model["id"] == selected for model in self.catalog()):
            raise _error(
                "local_model_required",
                "Модель не найдена среди локально загруженных. "
                "Загрузите её в Ollama и обновите каталог.",
                status=400,
            )

    def embedding_catalog(self, *, timeout=5, deadline=None):
        """Only installed local models with an immutable digest can back an index."""
        payload = self._request("GET", "/api/tags", timeout=timeout, deadline=deadline)
        rows = payload.get("models")
        if not isinstance(rows, list):
            raise _error(
                "invalid_local_catalog", "Не удалось прочитать локальные модели.", status=502
            )
        models = []
        seen = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or any(row.get(key) for key in _REMOTE_FIELDS)
                or isinstance(row.get("size"), bool)
                or not isinstance(row.get("size"), int)
                or row["size"] <= 0
            ):
                continue
            digest = row.get("digest")
            if not isinstance(digest, str) or not re.fullmatch(
                r"(?:sha256:)?[a-fA-F0-9]{64}", digest
            ):
                continue
            try:
                model = _model_id(row.get("name", row.get("model")))
            except AppError:
                continue
            if model not in seen:
                models.append({"id": model, "name": model, "digest": digest.lower()})
                seen = seen | {model}
        return models

    def embed(
        self,
        model_id,
        texts,
        *,
        expected_digest=None,
        dimension=None,
        timeout=5,
        before_request=None,
    ):
        """Embed a bounded batch locally; never truncate, download or use cloud fallback."""
        from .embedding_store import MAX_DIMENSIONS, normalized

        model = _model_id(model_id)
        if (
            not isinstance(texts, list)
            or not 1 <= len(texts) <= 16
            or any(
                not isinstance(text, str) or not text.strip() or len(text) > 12000 for text in texts
            )
            or (
                dimension is not None
                and (
                    isinstance(dimension, bool)
                    or not isinstance(dimension, int)
                    or not 1 <= dimension <= MAX_DIMENSIONS
                )
            )
        ):
            raise _error(
                "invalid_embedding_input", "Сократите текст для локального индекса.", status=400
            )
        native = {"model": model + ":local", "input": texts, "truncate": False, "keep_alive": "5m"}
        if len(json.dumps(native, ensure_ascii=False).encode()) > MAX_REQUEST_BYTES:
            raise _error(
                "embedding_input_too_long", "Пакет embeddings превышает лимит.", status=400
            )
        deadline = time.monotonic() + _timeout(timeout)
        candidates = self.embedding_catalog(timeout=min(timeout, 1), deadline=deadline)
        selected = next((row for row in candidates if row["id"] == model), None)
        if selected is None or (
            expected_digest is not None and selected["digest"] != expected_digest
        ):
            raise _error(
                "embedding_model_changed",
                "Локальная embedding-модель изменена. Выберите её повторно в настройках.",
                status=409,
            )
        if before_request is not None:
            before_request()
        result = self._request("POST", "/api/embed", native, timeout=timeout, deadline=deadline)
        vectors = result.get("embeddings")
        if (
            any(result.get(key) for key in _REMOTE_FIELDS)
            or result.get("model") not in (model, model + ":local")
            or not isinstance(vectors, list)
            or len(vectors) != len(texts)
        ):
            raise _error(
                "invalid_embedding", "Не удалось проверить локальный вектор.", sent=True, status=502
            )
        actual_dimension = dimension
        for vector in vectors:
            normalized(vector, actual_dimension)
            actual_dimension = len(vector)
        after = self.embedding_catalog(timeout=min(timeout, 1), deadline=deadline)
        if not any(row["id"] == model and row["digest"] == selected["digest"] for row in after):
            raise _error(
                "embedding_model_changed",
                "Embedding-модель изменилась во время запроса.",
                sent=True,
                status=409,
            )
        return {
            "embeddings": vectors,
            "model": model,
            "digest": selected["digest"],
            "total_duration": result.get("total_duration"),
            "load_duration": result.get("load_duration"),
        }

    def generate(self, payload, timeout=180, *, before_request=None):
        model_id, native = _request_payload(payload)
        self.ensure_local_model(model_id)
        if before_request is not None:
            try:
                before_request()
            except AppError as error:
                raise _error(error.code, error.message, status=error.status_code) from None
            except Exception:
                raise _error(
                    "local_admission_failed",
                    "Локальный запрос не отправлен. "
                    "Проверьте состояние синхронизации и повторите позже.",
                ) from None
        result = self._request("POST", "/api/chat", native, timeout=timeout)
        message = result.get("message")
        if (
            any(result.get(name) for name in _REMOTE_FIELDS)
            or result.get("done") is not True
            or not isinstance(message, dict)
            or not isinstance(message.get("content"), str)
        ):
            raise _error(
                "invalid_local_response",
                "Не удалось подтвердить полный локальный ответ "
                "Ollama. Проверьте модель и обновите Ollama.",
                sent=True,
                status=502,
            )
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": message["content"]},
                    "finish_reason": "length" if result.get("done_reason") == "length" else "stop",
                }
            ],
            "usage": _usage(result),
        }
