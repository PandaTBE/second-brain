"""Read-only GitHub REST API with fixed pinned HTTPS, explicit scope and bounded bodies.

https://docs.github.com/en/rest/about-the-rest-api/api-versions
Only selected repositories are read; returned API URLs and attachments are never fetched.
"""

import base64
import binascii
import hashlib
import http.client
import json
import re
import ssl
import threading
import time
from urllib.parse import quote, urlencode

import certifi

from .errors import AppError
from .github_models import GitHubRepositoriesRequest, normalize_repository, validate_github_token
from .transport import PinnedHTTPSConnection, validate_public_host

API_VERSION = "2026-03-10"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_BYTES = 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}")


def _noop() -> None:
    pass


def _invalid() -> AppError:
    return AppError("github_response", "GitHub вернул неполный или некорректный ответ.", 502)


def _metadata(value: dict) -> dict:
    try:
        name = normalize_repository(value["full_name"])
        identifier, branch = value["id"], value["default_branch"]
        if (not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1
                or not isinstance(branch, str) or not branch or len(branch) > 255
                or not isinstance(value.get("private"), bool)):
            raise ValueError
        created, updated = value["created_at"], value["updated_at"]
        if not isinstance(created, str) or not isinstance(updated, str):
            raise ValueError
        description = value.get("description")
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise ValueError
        # Do not return temp_clone_token or any future credentials in repository API metadata.
        return {"id": identifier, "full_name": name, "description": description[:1000],
                "private": value["private"], "default_branch": branch,
                "url": "https://github.com/" + name,
                "created_at": created[:64], "updated_at": updated[:64]}
    except (ValueError, TypeError, KeyError) as exc:
        raise _invalid() from exc


def _page(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10000:
        raise ValueError("Проверьте номер страницы GitHub.")
    return value


def _next_page(rows, headers, page):
    # Never follow a Link URL. The only allowed continuation is the next local page number.
    more = bool(re.search(r'rel="next"', headers.get("link", headers.get("Link", ""))))
    if more or len(rows) == 100:
        if page >= 10000:
            raise AppError("github_pagination_limit", "Архив GitHub превышает лимит обхода.", 502)
        return page + 1
    return None


def _safe_path(path: str) -> bool:
    return (isinstance(path, str) and 0 < len(path) <= 4096 and not path.startswith("/")
            and all(part not in ("", ".", "..") for part in path.split("/"))
            and not any(ord(char) < 32 for char in path))


class GitHubClient:
    def __init__(self, *, sleep=time.sleep, monotonic=time.monotonic, wall_time=time.time):
        self._sleep, self._monotonic, self._wall_time = sleep, monotonic, wall_time
        self._lock = threading.Lock()
        self._last_request = float("-inf")
        self._blocked_until = float("-inf")

    def _request(self, token: str, path: str, check_stop=_noop):
        validate_github_token(token)
        if (not path.startswith("/") or "#" in path or not re.fullmatch(
            r"/(?:user/repos|repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
            r"(?:/(?:issues|pulls)|/branches/[^/?#]+|/git/(?:trees|blobs)/[^/?#]+)?)"
            r"(?:\?[^#]*)?", path
        )):
            raise AppError("github_endpoint", "Операция GitHub недоступна.")
        check_stop()
        acquired = self._lock.acquire(timeout=1.0)
        try:
            check_stop()
            if not acquired:
                raise AppError("github_busy", "GitHub уже синхронизируется. "
                               "Повторите действие немного позже.", 503)
            if self._monotonic() < self._blocked_until:
                raise self._rate_error()
            remaining = max(0, .25 - (self._monotonic() - self._last_request))
            while remaining > 0:
                check_stop()
                interval = min(remaining, .1)
                self._sleep(interval)
                remaining -= interval
            check_stop()
            status, headers, raw = self._send(token, path)
            self._last_request = self._monotonic()
            check_stop()
            headers = {key.lower(): str(value) for key, value in headers.items()}
            if status == 429 or (status == 403 and (
                "retry-after" in headers or headers.get("x-ratelimit-remaining") == "0"
            )):
                delay = 60.0
                try:
                    if "retry-after" in headers:
                        delay = float(headers["retry-after"])
                    elif "x-ratelimit-reset" in headers:
                        delay = float(headers["x-ratelimit-reset"]) - self._wall_time()
                except ValueError:
                    pass
                self._blocked_until = self._monotonic() + max(1, min(delay, 86400))
                raise self._rate_error()
            if status == 403:
                # GitHub secondary throttles may omit all rate-limit headers. Cool down
                # conservatively while preserving the useful permission error for this call.
                self._blocked_until = self._monotonic() + 60
            return self._decode(status, raw), headers
        finally:
            if acquired:
                self._lock.release()

    @staticmethod
    def _rate_error():
        return AppError("github_rate_limited", "GitHub ограничил частоту запросов. "
                        "Повторите синхронизацию позже; текущая память сохранена.", 429)

    @staticmethod
    def _send(token, path):
        addresses = validate_public_host("api.github.com")
        context = ssl.create_default_context(cafile=certifi.where())
        context.set_alpn_protocols(["http/1.1"])
        connection = PinnedHTTPSConnection("api.github.com", addresses, context, timeout=20)
        headers = {"Accept": "application/vnd.github+json", "Accept-Encoding": "identity",
                   "X-GitHub-Api-Version": API_VERSION, "Authorization": "Bearer " + token,
                   "User-Agent": "SecondBrainLocal/0.1"}
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            # Error bodies can contain credentials/private source text; never retain or expose them.
            raw = response.read(MAX_RESPONSE_BYTES + 1) if response.status == 200 else b""
            if len(raw) > MAX_RESPONSE_BYTES:
                raise AppError("github_response_large", "Ответ GitHub превышает размер импорта.",
                               502)
            safe_headers = {key: response.getheader(key) for key in
                            ("Link", "Retry-After", "X-RateLimit-Remaining", "X-RateLimit-Reset")
                            if response.getheader(key) is not None}
            return response.status, safe_headers, raw
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise AppError("github_network", "Не удалось подключиться к GitHub. "
                           "Проверьте интернет и повторите синхронизацию.", 502) from exc
        finally:
            connection.close()

    @staticmethod
    def _decode(status, raw):
        failures = {
            401: ("github_auth", "GitHub отклонил токен. Проверьте токен подключения.", 401),
            403: ("github_access", "Нет доступа к GitHub. Проверьте выбранные репозитории "
                  "и права токена Contents, Issues и Pull requests: Read-only.", 403),
            404: ("github_not_found", "Репозиторий или материал GitHub недоступен. "
                  "Проверьте доступ токена к выбранному репозиторию.", 404),
        }
        if status in failures:
            raise AppError(*failures[status])
        if 300 <= status < 400:
            raise AppError("github_redirect", "GitHub перенаправил запрос. "
                           "Перенаправления не поддерживаются.", 502)
        if status != 200:
            raise AppError("github_upstream", "GitHub временно не может выполнить запрос. "
                           "Повторите синхронизацию позже.", 502)
        try:
            value = json.loads(raw)
            if not isinstance(value, (list, dict)):
                raise ValueError
            return value
        except (ValueError, UnicodeError) as exc:
            raise _invalid() from exc

    def list_repositories(self, token: str, page: int = 1) -> dict:
        request = GitHubRepositoriesRequest(token=token, page=page)
        query = urlencode({"per_page": 100, "page": request.page, "sort": "updated",
                           "direction": "desc",
                           "affiliation": "owner,collaborator,organization_member"})
        rows, headers = self._request(token, "/user/repos?" + query)
        if not isinstance(rows, list) or len(rows) > 100:
            raise _invalid()
        return {"repositories": [_metadata(row) for row in rows],
                "next_page": _next_page(rows, headers, page)}

    def retrieve_repository(self, token: str, full_name: str, check_stop=_noop) -> dict:
        repo = normalize_repository(full_name)
        value, _ = self._request(token, "/repos/" + repo, check_stop)
        if not isinstance(value, dict):
            raise _invalid()
        result = _metadata(value)
        if result["full_name"] != repo:
            raise _invalid()
        return result

    def list_document_files(self, token: str, repo: str, branch: str, check_stop=_noop) -> dict:
        repo = normalize_repository(repo)
        if not isinstance(branch, str) or not 1 <= len(branch) <= 255:
            raise ValueError("Проверьте ветку GitHub.")
        branch_data, _ = self._request(token, "/repos/" + repo + "/branches/" +
                                       quote(branch, safe=""), check_stop)
        try:
            commit_sha = branch_data["commit"]["sha"]
            tree_sha = branch_data["commit"]["commit"]["tree"]["sha"]
            if not SHA.fullmatch(commit_sha) or not SHA.fullmatch(tree_sha):
                raise ValueError
        except (ValueError, TypeError, KeyError) as exc:
            raise _invalid() from exc
        value, _ = self._request(token, "/repos/" + repo + "/git/trees/" +
                                 tree_sha + "?recursive=1", check_stop)
        if (not isinstance(value, dict) or value.get("truncated") is not False
                or not isinstance(value.get("tree"), list)):
            raise AppError("github_incomplete", "GitHub вернул неполное дерево файлов. "
                           "Предыдущие материалы сохранены.", 502)
        if value.get("sha") != tree_sha:
            raise _invalid()
        files = []
        for row in value["tree"]:
            if not isinstance(row, dict):
                raise _invalid()
            path = row.get("path", "")
            if row.get("type") != "blob" or row.get("mode") not in ("100644", "100755"):
                continue
            if not _safe_path(path):
                raise _invalid()
            name = path.rsplit("/", 1)[-1].lower()
            if not (name.endswith((".md", ".markdown", ".mdown"))
                    or name in ("readme", "readme.txt")):
                continue
            size, sha = row.get("size"), row.get("sha")
            if (not isinstance(size, int) or isinstance(size, bool) or size < 0
                    or not isinstance(sha, str) or not SHA.fullmatch(sha)):
                raise _invalid()
            files = [*files, {"path": path, "sha": sha, "size": size}]
        return {"files": sorted(files, key=lambda row: row["path"]), "snapshot_sha": commit_sha}

    def retrieve_document(self, token: str, repo: str, file: dict, check_stop=_noop) -> str:
        repo = normalize_repository(repo)
        sha, size = file.get("sha"), file.get("size")
        if (not isinstance(sha, str) or not SHA.fullmatch(sha) or not isinstance(size, int)
                or isinstance(size, bool) or not 0 <= size <= MAX_DOCUMENT_BYTES):
            raise AppError("github_document_large", "Файл GitHub превышает размер импорта.", 502)
        value, _ = self._request(token, "/repos/" + repo + "/git/blobs/" + sha, check_stop)
        try:
            if (not isinstance(value, dict) or value.get("encoding") != "base64"
                    or value.get("sha") != sha or value.get("size") != size
                    or not isinstance(value.get("content"), str)
                    or len(value["content"]) > 2 * MAX_DOCUMENT_BYTES):
                raise ValueError
            raw = base64.b64decode("".join(value["content"].split()), validate=True)
            actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if len(raw) != size or actual != sha or b"\0" in raw:
                raise ValueError
            return raw.decode("utf-8-sig")
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise AppError("github_incomplete", "Не удалось прочитать полный текст файла GitHub. "
                           "Предыдущая версия сохранена.", 502) from exc

    def _list_items(self, token, repo, page, pulls, check_stop):
        repo, page = normalize_repository(repo), _page(page)
        query = urlencode({"state": "all", "sort": "created", "direction": "asc",
                           "per_page": 100, "page": page})
        path = "/repos/" + repo + ("/pulls?" if pulls else "/issues?") + query
        rows, headers = self._request(token, path, check_stop)
        if not isinstance(rows, list) or len(rows) > 100:
            raise _invalid()
        items = []
        for row in rows:
            try:
                number, title = row["number"], row["title"]
                body = row.get("body")
                if body is None:
                    body = ""
                state = row["state"]
                created, updated = row["created_at"], row["updated_at"]
                if (not isinstance(number, int) or isinstance(number, bool) or number < 1
                        or not isinstance(title, str) or not isinstance(body, str)
                        or state not in ("open", "closed") or not isinstance(created, str)
                        or not isinstance(updated, str) or not isinstance(row.get("labels"), list)):
                    raise ValueError
                labels = [item["name"][:100] for item in row["labels"]
                          if isinstance(item, dict) and isinstance(item.get("name"), str)]
                is_pull = pulls or "pull_request" in row
                items = [*items, {"number": number, "title": title, "body": body,
                                 "state": state, "labels": labels, "is_pull_request": is_pull,
                                 "created_at": created[:64], "updated_at": updated[:64],
                                 "url": "https://github.com/" + repo +
                                 ("/pull/" if is_pull else "/issues/") + str(number)}]
            except (KeyError, TypeError, ValueError) as exc:
                raise _invalid() from exc
        return {"items": items, "next_page": _next_page(rows, headers, page)}

    def list_issues(self, token, repo, page=1, check_stop=_noop):
        return self._list_items(token, repo, page, False, check_stop)

    def list_pull_requests(self, token, repo, page=1, check_stop=_noop):
        return self._list_items(token, repo, page, True, check_stop)
