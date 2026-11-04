"""Read-only Notion API with pinned TLS, bounded responses and explicit page scope.

API contract: https://developers.notion.com/reference/retrieve-page-markdown
No attachment, link, redirect, or user-supplied endpoint is fetched.
"""

import http.client
import json
import re
import ssl
import threading
import time

import certifi

from .errors import AppError
from .notion_models import NotionSearchRequest, normalize_page_id, validate_notion_token
from .transport import PinnedHTTPSConnection, validate_public_host

API_VERSION = "2026-03-11"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_MARKDOWN_CHARS = 1_000_000
REQUEST_SPACING = 0.35


def _noop() -> None:
    pass


def _invalid() -> AppError:
    return AppError("notion_response", "Notion вернул неполный или некорректный ответ.", 502)


def _page_metadata(value: dict) -> dict:
    try:
        if value.get("object") != "page":
            raise ValueError
        identifier = normalize_page_id(value["id"])
        if value.get("archived") or value.get("in_trash"):
            raise AppError("notion_not_found", "Страница Notion удалена или недоступна.", 404)
        properties = value.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError
        title_parts = next((prop.get("title", []) for prop in properties.values()
                            if isinstance(prop, dict) and prop.get("type") == "title"), [])
        if not isinstance(title_parts, list):
            raise ValueError
        title = "".join(item.get("plain_text", item.get("text", {}).get("content", ""))
                        for item in title_parts if isinstance(item, dict))[:256]
        created, updated = value.get("created_time"), value.get("last_edited_time")
        if not isinstance(created, str) or not isinstance(updated, str):
            raise ValueError
        return {"id": identifier, "title": title or "Без названия",
                "url": "https://www.notion.so/" + identifier.replace("-", ""),
                "created_at": created[:64], "updated_at": updated[:64]}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _invalid() from exc


class NotionClient:
    def __init__(self, *, sleep=time.sleep, monotonic=time.monotonic):
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request = float("-inf")
        self._lock = threading.Lock()

    def _wait(self, seconds: float, check_stop) -> None:
        # Short waits let pause/quit/disconnection interrupt a Retry-After wait.
        remaining = seconds
        while remaining > 0:
            check_stop()
            interval = min(remaining, 0.25)
            self._sleep(interval)
            remaining -= interval
        check_stop()

    def _request(self, token: str, method: str, path: str, payload=None,
                 check_stop=_noop) -> dict:
        validate_notion_token(token)
        if not ((method == "POST" and path == "/v1/search") or
                (method == "GET" and re.fullmatch(
                    r"/v1/pages/[a-f0-9-]{36}(?:/markdown\?markdown_version=v2)?", path))):
            raise AppError("notion_endpoint", "Операция Notion недоступна.")
        check_stop()
        acquired = self._lock.acquire(timeout=1.0)
        try:
            check_stop()
            if not acquired:
                raise AppError("notion_busy", "Notion уже синхронизируется. "
                               "Повторите действие немного позже.", 503)
            for attempt in range(3):
                check_stop()
                self._wait(max(0, REQUEST_SPACING -
                               (self._monotonic() - self._last_request)), check_stop)
                status, retry_after, result = self._send(token, method, path, payload)
                self._last_request = self._monotonic()
                check_stop()
                if status != 429:
                    return self._decode(status, result)
                try:
                    delay = float(retry_after)
                except (ValueError, TypeError):
                    delay = 1.0
                if not 0 <= delay <= 60 or attempt == 2:
                    raise AppError("notion_rate_limited", "Notion ограничил частоту запросов. "
                                   "Синхронизация повторится при следующем запуске.", 429)
                self._wait(max(delay, REQUEST_SPACING), check_stop)
        finally:
            if acquired:
                self._lock.release()
        raise _invalid()  # pragma: no cover  # pragma: no cover

    @staticmethod
    def _send(token, method, path, payload):
        addresses = validate_public_host("api.notion.com")
        context = ssl.create_default_context(cafile=certifi.where())
        context.set_alpn_protocols(["http/1.1"])
        connection = PinnedHTTPSConnection("api.notion.com", addresses, context, timeout=20)
        headers = {"Accept": "application/json", "Accept-Encoding": "identity",
                   "Content-Type": "application/json", "Notion-Version": API_VERSION,
                   "Authorization": "Bearer " + token, "User-Agent": "SecondBrainLocal/0.1"}
        try:
            body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            # Never parse or expose remote error bodies: they can echo secrets and private text.
            raw = response.read(MAX_RESPONSE_BYTES + 1) if response.status == 200 else b""
            if len(raw) > MAX_RESPONSE_BYTES:
                raise AppError("notion_response_large", "Страница Notion превышает размер импорта.",
                               502)
            return response.status, response.getheader("Retry-After"), raw
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise AppError("notion_network", "Не удалось подключиться к Notion. "
                           "Проверьте интернет и повторите синхронизацию.", 502) from exc
        finally:
            connection.close()

    @staticmethod
    def _decode(status, raw):
        failures = {
            401: ("notion_auth", "Notion отклонил токен. Проверьте токен подключения.", 401),
            403: ("notion_access", "Нет доступа к Notion. Разрешите чтение содержимого "
                  "и подключите нужные страницы к интеграции.", 403),
            404: ("notion_not_found", "Страница Notion недоступна. Проверьте её доступ "
                  "через меню Connections в Notion.", 404),
        }
        if status in failures:
            raise AppError(*failures[status])
        if 300 <= status < 400:
            raise AppError("notion_redirect", "Notion перенаправил запрос. "
                           "Перенаправления не поддерживаются.", 502)
        if status != 200:
            raise AppError("notion_upstream", "Notion временно не может выполнить запрос. "
                           "Повторите синхронизацию позже.", 502)
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeError) as exc:
            raise _invalid() from exc

    def search_pages(self, token: str, query: str = "", cursor: str | None = None) -> dict:
        request = NotionSearchRequest(token=token, query=query, cursor=cursor)
        payload = {"filter": {"property": "object", "value": "page"}, "page_size": 100,
                   "sort": {"direction": "descending", "timestamp": "last_edited_time"},
                   "query": request.query}
        if request.cursor:
            payload = {**payload, "start_cursor": request.cursor}
        value = self._request(token, "POST", "/v1/search", payload)
        rows, has_more = value.get("results"), value.get("has_more")
        cursor = value.get("next_cursor") if has_more else None
        if (not isinstance(rows, list) or len(rows) > 100 or not isinstance(has_more, bool)
                or (has_more and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 512))):
            raise _invalid()
        pages = []
        for row in rows:
            if not isinstance(row, dict):
                raise _invalid()
            if row.get("archived") or row.get("in_trash"):
                continue
            metadata = _page_metadata(row)
            pages = [*pages, {key: metadata[key] for key in ("id", "title", "url")}]
        return {"pages": pages, "next_cursor": cursor}

    def retrieve_metadata(self, token: str, page_id: str, check_stop=_noop) -> dict:
        identifier = normalize_page_id(page_id)
        metadata = _page_metadata(self._request(
            token, "GET", "/v1/pages/" + identifier, check_stop=check_stop))
        if metadata["id"] != identifier:
            raise _invalid()
        return metadata

    def retrieve_page(self, token: str, page_id: str, check_stop=_noop) -> dict:
        identifier = normalize_page_id(page_id)
        path = "/v1/pages/" + identifier
        metadata = _page_metadata(self._request(token, "GET", path, check_stop=check_stop))
        if metadata["id"] != identifier:
            raise _invalid()
        content = self._request(token, "GET", path + "/markdown?markdown_version=v2",
                                check_stop=check_stop)
        try:
            if (content.get("object") != "page_markdown"
                    or normalize_page_id(content.get("id")) != identifier
                    or not isinstance(content.get("markdown"), str)
                    or content.get("truncated") is not False
                    or content.get("unknown_block_ids") != []):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise AppError("notion_incomplete", "Notion вернул неполную страницу. "
                           "Предыдущая версия в памяти сохранена. Проверьте доступ к блокам.",
                           502) from exc
        if len(content["markdown"]) > MAX_MARKDOWN_CHARS:
            raise AppError("notion_response_large",
                           "Страница Notion превышает размер импорта.", 502)
        after = _page_metadata(self._request(token, "GET", path, check_stop=check_stop))
        if after["id"] != identifier or after["updated_at"] != metadata["updated_at"]:
            raise AppError("notion_changed", "Страница Notion изменилась во время чтения. "
                           "Она будет обработана при следующей синхронизации.", 409)
        return {**metadata, "text": content["markdown"]}
