"""Notion source lifecycle, native HTTP contract and shared memory; synthetic only."""

import json

import pytest
from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient
from test_memory_service_api import wait_job
from test_settings_api import assert_success

from brain_settings.app import create_app
from brain_settings.background import ScheduleConfig
from brain_settings.errors import AppError
from brain_settings.mcp_server import MemoryTools
from brain_settings.memory_text import redact_text

PAGE = "12345678-1234-1234-1234-123456789abc"
TOKEN = "ntn_" + "synthetic_not_real_" * 3


class FakeNotion:
    def __init__(self):
        self.calls = ()
        self.error = None
        self.text = "Synthetic Notion SQLite decision for shared memory."

    def search_pages(self, token, query="", cursor=None):
        self.calls = (*self.calls, ("search", token, query, cursor))
        if self.error:
            raise self.error
        return {"pages": [{"id": PAGE, "title": "Synthetic", "url":
                          "https://www.notion.so/" + PAGE.replace("-", "")}],
                "next_cursor": None}

    def retrieve_metadata(self, token, page_id, check_stop=lambda: None):
        return {key: value for key, value in self.retrieve_page(
            token, page_id, check_stop=check_stop
        ).items() if key != "text"}

    def retrieve_page(self, token, page_id, check_stop=lambda: None):
        check_stop()
        self.calls = (*self.calls, ("retrieve", token, page_id))
        if self.error:
            raise self.error
        return {"id": page_id, "title": "Synthetic Notion", "url":
                "https://www.notion.so/" + page_id.replace("-", ""),
                "created_at": "2026-11-04T10:00:00Z", "updated_at": "2026-11-04T11:00:00Z",
                "text": self.text}


@pytest.fixture
def notion(tmp_path):
    vault, remote = MemoryVault(), FakeNotion()
    app = create_app(data_dir=tmp_path, vault=vault, transport=FakeTransport(),
                     notion_client=remote)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = assert_success(client.get("/api/bootstrap"))["csrf_token"]
        yield client, {"Origin": ORIGIN, "X-Brain-CSRF": csrf}, vault, remote, app


def add(notion, **changes):
    client, headers, *_ = notion
    return client.post("/api/sources", headers=headers, json={
        "kind": "notion", "name": "Synthetic Notion", "token": TOKEN,
        "notion_page_ids": [PAGE], **changes,
    })


def test_notion_secret_lifecycle_and_discovery(notion, tmp_path):
    client, headers, vault, remote, _ = notion
    data = assert_success(client.post("/api/sources/notion/pages", headers=headers,
                                     json={"token": TOKEN, "query": "Synthetic"}))
    assert data["pages"][0]["id"] == PAGE
    assert not vault.entries
    source = assert_success(add(notion))["source"]
    assert source["notion_page_ids"] == [PAGE]
    assert source["credential_saved"] is True
    assert len(remote.calls) == 1  # Saving is not a content import.
    assert list(vault.entries.values()) == [TOKEN]
    assert TOKEN not in json.dumps(assert_success(client.get("/api/sources")))
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes()
    checked = assert_success(client.post(f"/api/sources/{source['id']}/test",
                                        json={}, headers=headers))["source"]
    assert checked["status"] == "ok" and checked["item_count"] == 1
    deleted = client.request("DELETE", f"/api/sources/{source['id']}", json={}, headers=headers)
    assert assert_success(deleted)["deleted"] is True
    assert not vault.entries


@pytest.mark.parametrize("changes", [
    {"token": None}, {"token": ""}, {"token": "bad\r\ntoken"},
    {"notion_page_ids": []}, {"notion_page_ids": ["../../private"]},
    {"notion_page_ids": [PAGE] * 101},
])
def test_notion_invalid_selection_never_connects(notion, changes):
    response = add(notion, **changes)
    assert response.status_code in (400, 422)
    assert not notion[2].entries and not notion[3].calls
    assert TOKEN not in response.text


def test_notion_import_updates_search_mcp_and_disconnect(notion, tmp_path):
    client, headers, _, remote, app = notion
    source = assert_success(add(notion))["source"]
    for expected in ("imported", "unchanged", "updated"):
        if expected == "updated":
            remote.text += " Updated decision."
        job_id = assert_success(client.post("/api/memory/import", headers=headers,
                                            json={"source_id": source["id"]}))["job_id"]
        job = wait_job(client, job_id)
        assert job["status"] == "completed", job
        assert job["stats"][expected] == 1
    hits = assert_success(client.get("/api/memory/search", params={"q": "SQLite"}))["results"]
    assert len(hits) == 1 and hits[0]["source_kind"] == "notion"
    tools = MemoryTools(app.state.memory_service.store, tmp_path)
    assert len(tools.search_memory("SQLite")["results"]) == 1
    assert_success(client.request("DELETE", f"/api/sources/{source['id']}",
                                  json={}, headers=headers))
    assert tools.search_memory("SQLite")["results"] == []


def test_notion_periodic_sync_obeys_pause_and_manual_import_is_allowed(notion):
    client, headers, _, remote, app = notion
    source = assert_success(add(notion))["source"]
    service = app.state.memory_service
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                           process_cards=False, paused=True))
    service.tick(now=10**12)
    assert not remote.calls
    service.set_sync_paused(False)
    service.tick(now=10**12)
    job = service.status()["jobs"][0]
    assert wait_job(client, job["id"])["status"] == "completed"
    assert remote.calls
    service.set_sync_paused(True)
    job_id = assert_success(client.post("/api/memory/import", headers=headers,
                                        json={"source_id": source["id"]}))["job_id"]
    assert wait_job(client, job_id)["status"] == "completed"


def test_notion_error_is_safe_and_persisted(notion):
    client, headers, _, remote, _ = notion
    source = assert_success(add(notion))["source"]
    remote.error = AppError("notion_access", "Откройте интеграции доступ к странице.", 403)
    response = client.post(f"/api/sources/{source['id']}/test", json={}, headers=headers)
    assert response.status_code == 403
    assert TOKEN not in response.text
    saved = assert_success(client.get("/api/sources"))["sources"][0]
    assert saved["status"] == "error" and saved["last_checked_at"]


@pytest.mark.parametrize("prefix", ["ntn_", "secret_"])
def test_bare_notion_tokens_are_redacted(prefix):
    secret = prefix + "A" * 40
    assert secret not in redact_text("Shared accidentally: " + secret)
