"""Synthetic Notion API responses; no account or real tokens are used."""

import json

import pytest
from pydantic import ValidationError

from brain_settings.errors import AppError
from brain_settings.notion_client import API_VERSION, MAX_RESPONSE_BYTES, NotionClient
from brain_settings.notion_models import NotionSearchRequest, normalize_page_id

PAGE = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
TOKEN = "ntn_" + "synthetic_not_a_real_token"


def page(**updates):
    return {"object": "page", "id": PAGE, "archived": False, "in_trash": False,
            "created_time": "2026-01-01T00:00:00Z", "last_edited_time": "2026-01-02T00:00:00Z",
            "properties": {"Name": {"type": "title", "title": [{"plain_text": "Project"}]}},
            **updates}


class Response:
    def __init__(self, value, status=200, headers=None, raw=None):
        self.status = status
        self.headers = headers or {}
        self.raw = json.dumps(value).encode() if raw is None else raw

    def read(self, size):
        return self.raw[:size]

    def getheader(self, key, default=None):
        return self.headers.get(key, default)


class Connection:
    def __init__(self, responses, calls):
        self.responses, self.calls = responses, calls

    def request(self, method, path, body, headers):
        self.calls.append((method, path, body, headers))

    def getresponse(self):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


def client(monkeypatch, responses):
    import brain_settings.notion_client as module
    calls, sleeps = [], []
    monkeypatch.setattr(module, "validate_public_host", lambda host: ["1.1.1.1"])
    monkeypatch.setattr(module, "PinnedHTTPSConnection",
                        lambda *args, **kwargs: Connection(responses, calls))
    return NotionClient(sleep=sleeps.append), calls, sleeps


def test_search_is_paginated_read_only_and_pinned(monkeypatch):
    api, calls, _ = client(monkeypatch, [Response({"results": [page()], "has_more": True,
                                                 "next_cursor": "cursor-2"})])
    result = api.search_pages(TOKEN, "Project")
    assert result["pages"] == [{"id": PAGE, "title": "Project",
                                "url": "https://www.notion.so/" + PAGE.replace("-", "")}]
    assert result["next_cursor"] == "cursor-2"
    method, path, body, headers = calls[0]
    assert (method, path) == ("POST", "/v1/search")
    assert json.loads(body)["filter"] == {"property": "object", "value": "page"}
    assert json.loads(body)["page_size"] == 100
    assert headers["Notion-Version"] == API_VERSION == "2026-03-11"
    assert headers["Authorization"] == "Bearer " + TOKEN


def test_search_cursor_is_forwarded_and_archived_pages_hidden(monkeypatch):
    api, calls, _ = client(monkeypatch, [Response({"results": [page(archived=True)],
                                                 "has_more": False, "next_cursor": None})])
    assert api.search_pages(TOKEN, cursor="opaque-cursor")["pages"] == []
    assert json.loads(calls[0][2])["start_cursor"] == "opaque-cursor"


@pytest.mark.parametrize("value", ["", "bad\ntoken", "a b", "é"])
def test_token_is_validated_without_echo(value):
    with pytest.raises(ValidationError):
        NotionSearchRequest(token=value)


def test_page_id_and_search_limits():
    assert normalize_page_id(PAGE.replace("-", "")) == PAGE
    for value in ["https://evil.test/x", "../" + PAGE, "invalid"]:
        with pytest.raises(ValueError):
            normalize_page_id(value)
    with pytest.raises(ValidationError):
        NotionSearchRequest(token=TOKEN, cursor="x" * 513)
    with pytest.raises(ValidationError):
        NotionSearchRequest(token=TOKEN, query="x" * 201)


@pytest.mark.parametrize("status,code", [(301, "notion_redirect"), (401, "notion_auth"),
                                        (403, "notion_access"), (404, "notion_not_found"),
                                        (500, "notion_upstream")])
def test_http_errors_are_safe(monkeypatch, status, code):
    api, calls, _ = client(monkeypatch, [Response({"message": TOKEN}, status=status)])
    with pytest.raises(AppError) as caught:
        api.search_pages(TOKEN)
    assert caught.value.code == code
    assert TOKEN not in str(caught.value)
    assert len(calls) == 1


def test_rate_limit_retries_after_server_delay(monkeypatch):
    api, calls, sleeps = client(monkeypatch, [Response({}, 429, {"Retry-After": "1"}),
                                             Response({"results": [], "has_more": False})])
    assert api.search_pages(TOKEN)["pages"] == []
    assert len(calls) == 2
    assert sum(sleeps) >= 1


def test_excessive_retry_after_returns_without_shortened_retry(monkeypatch):
    api, calls, _ = client(monkeypatch, [Response({}, 429, {"Retry-After": "120"})])
    with pytest.raises(AppError, match="Notion"):
        api.search_pages(TOKEN)
    assert len(calls) == 1


@pytest.mark.parametrize("response", [Response({}, raw=b"not-json"), Response([]),
                                      Response({}, raw=b"x" * (MAX_RESPONSE_BYTES + 1))])
def test_bad_or_large_response_rejected(monkeypatch, response):
    api, _, _ = client(monkeypatch, [response])
    with pytest.raises(AppError):
        api.search_pages(TOKEN)


def test_markdown_requires_complete_content_and_stable_revision(monkeypatch):
    api, calls, _ = client(monkeypatch, [Response(page()),
        Response({"object": "page_markdown", "id": PAGE, "markdown": "Notes",
                  "truncated": False, "unknown_block_ids": []}), Response(page())])
    result = api.retrieve_page(TOKEN, PAGE)
    assert result["text"] == "Notes"
    assert result["title"] == "Project"
    assert [item[1] for item in calls] == [f"/v1/pages/{PAGE}",
        f"/v1/pages/{PAGE}/markdown?markdown_version=v2", f"/v1/pages/{PAGE}"]


@pytest.mark.parametrize("changes", [{"truncated": True}, {"unknown_block_ids": [PAGE]},
                                    {"id": "11111111-1111-4111-8111-111111111111"}])
def test_incomplete_or_mismatched_markdown_is_not_returned(monkeypatch, changes):
    api, _, _ = client(monkeypatch, [Response(page()), Response({"object": "page_markdown",
        "id": PAGE, "markdown": "partial", "truncated": False, "unknown_block_ids": [],
        **changes})])
    with pytest.raises(AppError):
        api.retrieve_page(TOKEN, PAGE)


def test_changed_page_during_read_is_not_returned(monkeypatch):
    api, _, _ = client(monkeypatch, [Response(page()), Response({"object": "page_markdown",
        "id": PAGE, "markdown": "Notes", "truncated": False, "unknown_block_ids": []}),
        Response(page(last_edited_time="2026-01-03T00:00:00Z"))])
    with pytest.raises(AppError) as caught:
        api.retrieve_page(TOKEN, PAGE)
    assert caught.value.code == "notion_changed"


def test_stop_callback_prevents_network(monkeypatch):
    api, calls, _ = client(monkeypatch, [])
    def stopped():
        raise AppError("memory_cancelled", "Остановлено")
    with pytest.raises(AppError):
        api.retrieve_page(TOKEN, PAGE, check_stop=stopped)
    assert calls == []


def test_busy_client_returns_promptly_and_recovers_after_release(monkeypatch):
    api, calls, _ = client(monkeypatch, [Response({"results": [], "has_more": False})])
    class BusyLock:
        available = False
        released = False
        timeouts = []

        def acquire(self, *, timeout):
            self.timeouts.append(timeout)
            return self.available

        def release(self):
            self.released = True

    lock = BusyLock()
    api._lock = lock
    with pytest.raises(AppError) as caught:
        api.search_pages(TOKEN)
    assert caught.value.code == "notion_busy"
    assert caught.value.status_code == 503
    assert lock.timeouts == [1.0]
    assert calls == [] and not lock.released
    lock.available = True
    assert api.search_pages(TOKEN)["pages"] == []
    assert lock.released and len(calls) == 1


def test_cancel_after_lock_acquisition_releases_lock(monkeypatch):
    api, calls, _ = client(monkeypatch, [])
    checks = []
    def stopped():
        checks.append(1)
        if len(checks) == 2:
            raise AppError("memory_cancelled", "Остановлено")
    with pytest.raises(AppError):
        api.retrieve_metadata(TOKEN, PAGE, check_stop=stopped)
    assert api._lock.acquire(blocking=False)
    api._lock.release()
    assert calls == []


def test_retrieve_metadata_is_one_bounded_request(monkeypatch):
    api, calls, _ = client(monkeypatch, [Response(page())])
    assert api.retrieve_metadata(TOKEN, PAGE)["title"] == "Project"
    assert len(calls) == 1


def test_no_write_or_arbitrary_read_endpoints(monkeypatch):
    api, calls, _ = client(monkeypatch, [])
    for method, path in [("PATCH", "/v1/pages/" + PAGE),
                         ("GET", "https://evil.test"), ("GET", "/v1/users/me")]:
        with pytest.raises(AppError):
            api._request(TOKEN, method, path)
    assert calls == []


def test_rate_limit_has_exact_retry_ceiling(monkeypatch):
    api, calls, _ = client(monkeypatch, [Response({}, 429)] * 3)
    with pytest.raises(AppError) as caught:
        api.search_pages(TOKEN)
    assert caught.value.code == "notion_rate_limited"
    assert len(calls) == 3


@pytest.mark.parametrize("response", [
    {"results": {}, "has_more": False},
    {"results": [], "has_more": True, "next_cursor": ""},
    {"results": [1], "has_more": False},
    {"results": [{"object": "user"}], "has_more": False},
    {"results": [page(properties=[])], "has_more": False},
    {"results": [page(properties={"Name": {"type": "title", "title": 3}})], "has_more": False},
    {"results": [page(created_time=None)], "has_more": False},
])
def test_malformed_search_response_is_rejected(monkeypatch, response):
    api, _, _ = client(monkeypatch, [Response(response)])
    with pytest.raises(AppError):
        api.search_pages(TOKEN)


def test_network_failure_does_not_echo_exception(monkeypatch):
    api, _, _ = client(monkeypatch, [OSError(TOKEN)])
    with pytest.raises(AppError) as caught:
        api.search_pages(TOKEN)
    assert TOKEN not in str(caught.value)
