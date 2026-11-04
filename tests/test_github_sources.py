"""Synthetic GitHub source lifecycle, API boundaries and shared-memory integration."""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient
from test_memory_service_api import wait_job
from test_settings_api import assert_success

from brain_settings.app import create_app
from brain_settings.background import ScheduleConfig
from brain_settings.errors import AppError
from brain_settings.mcp_server import MemoryTools

REPOSITORY = "synthetic-owner/second-brain"
TOKEN = "github_pat_" + "SYNTHETIC_NOT_REAL_" * 3


class FakeGitHub:
    def __init__(self):
        self.calls = ()
        self.error = None
        self.metadata_started = None
        self.metadata_release = None
        self.text = "Synthetic GitHub SQLite documentation."
        self.on_document = None

    @staticmethod
    def repository(full_name=REPOSITORY):
        return {"id": 12345, "full_name": full_name, "description": "Synthetic repository",
                "private": True, "default_branch": "main",
                "url": "https://github.com/" + full_name,
                "created_at": "2026-11-04T10:00:00Z",
                "updated_at": "2026-11-04T11:00:00Z"}

    def list_repositories(self, token, page=1):
        self.calls = (*self.calls, ("list", token, page))
        if self.error is not None:
            raise self.error
        return {"repositories": [self.repository()], "next_page": 2 if page == 1 else None}

    def retrieve_repository(self, token, full_name, check_stop=lambda: None):
        check_stop()
        self.calls = (*self.calls, ("repository", token, full_name))
        if self.metadata_started is not None:
            self.metadata_started.set()
            if not self.metadata_release.wait(3):
                raise AssertionError("Synthetic metadata request was not released")
        if self.error is not None:
            raise self.error
        return self.repository(full_name)

    def list_document_files(self, token, repository, branch, check_stop=lambda: None):
        check_stop()
        self.calls = (*self.calls, ("documents", token, repository, branch))
        digest = hashlib.sha256(self.text.encode()).hexdigest()[:40]
        return {"files": [{"path": "README.md", "sha": digest, "size": len(self.text)}],
                "snapshot_sha": digest}

    def retrieve_document(self, token, repository, file, check_stop=lambda: None):
        check_stop()
        self.calls = (*self.calls, ("document", token, repository, file["path"]))
        if self.on_document is not None:
            self.on_document()
        check_stop()
        return self.text

    def list_issues(self, token, repository, page=1, check_stop=lambda: None):
        check_stop()
        self.calls = (*self.calls, ("issues", token, repository, page))
        return {"items": [self.issue(1)], "next_page": None}

    def list_pull_requests(self, token, repository, page=1, check_stop=lambda: None):
        check_stop()
        self.calls = (*self.calls, ("pulls", token, repository, page))
        return {"items": [self.issue(2, pull=True)], "next_page": None}

    @staticmethod
    def issue(number, pull=False):
        return {"number": number, "title": "Synthetic SQLite decision",
                "body": "Use SQLite for the local shared memory.", "state": "closed",
                "labels": ["decision"], "is_pull_request": pull,
                "created_at": "2026-11-04T10:00:00Z", "updated_at": "2026-11-04T11:00:00Z",
                "url": "https://github.com/" + REPOSITORY
                + ("/pull/" if pull else "/issues/") + str(number)}


@dataclass(frozen=True)
class GitHubHarness:
    client: TestClient
    headers: dict
    vault: MemoryVault
    remote: FakeGitHub
    app: object

    @property
    def service(self):
        return self.app.state.memory_service

    def add(self, **changes):
        return self.client.post("/api/sources", headers=self.headers, json={
            "kind": "github", "name": "Synthetic GitHub", "token": TOKEN,
            "github_repositories": [REPOSITORY],
            "github_content": ["docs", "issues", "pull_requests"], **changes,
        })

    def delete(self, source_id):
        return self.client.request("DELETE", f"/api/sources/{source_id}", json={},
                                   headers=self.headers)


@pytest.fixture
def github(tmp_path):
    vault, remote = MemoryVault(), FakeGitHub()
    app = create_app(data_dir=tmp_path, vault=vault, transport=FakeTransport(),
                     github_client=remote)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = assert_success(client.get("/api/bootstrap"))["csrf_token"]
        yield GitHubHarness(client, {"Origin": ORIGIN, "X-Brain-CSRF": csrf},
                            vault, remote, app)


def test_github_discovery_is_paginated_ephemeral_and_does_not_save_secrets(github):
    for page, expected in ((1, 2), (2, None)):
        response = github.client.post("/api/sources/github/repositories", headers=github.headers,
                                      json={"token": TOKEN, "page": page})
        data = assert_success(response)
        assert data["repositories"][0]["full_name"] == REPOSITORY
        assert data["next_page"] == expected
        assert TOKEN not in response.text
    assert not github.vault.entries
    assert github.remote.calls == (("list", TOKEN, 1), ("list", TOKEN, 2))
    assert assert_success(github.client.get("/api/sources"))["sources"] == []


def test_github_secret_lifecycle_saves_only_keychain_and_check_is_explicit(github, tmp_path):
    response = github.add(github_repositories=[REPOSITORY.upper(), REPOSITORY])
    source = assert_success(response)["source"]
    assert source["kind"] == "github" and source["status"] == "not_checked"
    assert source["github_repositories"] == [REPOSITORY]
    assert source["github_content"] == ["docs", "issues", "pull_requests"]
    assert source["credential_saved"] is True
    assert github.remote.calls == ()
    assert list(github.vault.entries.values()) == [TOKEN]
    assert TOKEN not in response.text
    assert TOKEN not in json.dumps(assert_success(github.client.get("/api/sources")))
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes()
    checked = assert_success(github.client.post(f"/api/sources/{source['id']}/test",
                                                headers=github.headers, json={}))["source"]
    assert checked["status"] == "ok" and checked["item_count"] == 1
    assert checked["last_checked_at"]
    assert github.remote.calls == (("repository", TOKEN, REPOSITORY),)
    assert assert_success(github.delete(source["id"]))["deleted"] is True
    assert not github.vault.entries


@pytest.mark.parametrize("changes", [
    {"token": None}, {"token": ""}, {"token": "bad\r\ntoken"},
    {"github_repositories": []}, {"github_repositories": [REPOSITORY] * 21},
    {"github_repositories": ["../../private"]},
    {"github_repositories": ["https://github.com/owner/repository"]},
    {"github_repositories": ["owner/space name"]},
    {"github_repositories": [None]}, {"github_repositories": None},
    {"github_content": []}, {"github_content": ["delete"]},
    {"github_content": None}, {"github_content": [None]},
])
def test_github_invalid_selection_never_connects_or_writes_secret(github, changes):
    response = github.add(**changes)
    assert response.status_code in (400, 422)
    assert response.json()["success"] is False
    assert TOKEN not in response.text
    assert not github.remote.calls and not github.vault.entries


@pytest.mark.parametrize("payload", [
    {"token": TOKEN, "page": True}, {"token": TOKEN, "page": 0},
    {"token": TOKEN, "page": 10001}, {"token": "bad\nkey", "page": 1},
])
def test_github_discovery_validation_never_sends_malformed_request(github, payload):
    response = github.client.post("/api/sources/github/repositories", headers=github.headers,
                                  json=payload)
    assert response.status_code == 422
    assert not github.remote.calls
    assert TOKEN not in response.text


def test_github_discovery_requires_csrf(github):
    response = github.client.post("/api/sources/github/repositories",
                                  headers={"Origin": ORIGIN}, json={"token": TOKEN})
    assert response.status_code == 403
    assert not github.remote.calls and not github.vault.entries


def test_github_check_error_is_safe_and_persisted(github):
    source = assert_success(github.add())["source"]
    github.remote.error = AppError("github_access", "Разрешите чтение репозитория.", 403)
    response = github.client.post(f"/api/sources/{source['id']}/test",
                                  headers=github.headers, json={})
    assert response.status_code == 403
    assert TOKEN not in response.text
    saved = assert_success(github.client.get("/api/sources"))["sources"][0]
    assert saved["status"] == "error" and saved["last_checked_at"]
    assert saved["message"] == "Разрешите чтение репозитория."


def test_github_unexpected_check_error_does_not_echo_token(github):
    source = assert_success(github.add())["source"]
    github.remote.error = RuntimeError("Synthetic server echoed " + TOKEN)
    response = github.client.post(f"/api/sources/{source['id']}/test",
                                  headers=github.headers, json={})
    assert response.status_code == 502
    assert TOKEN not in response.text
    saved = assert_success(github.client.get("/api/sources"))["sources"][0]
    assert saved["status"] == "error" and TOKEN not in saved["message"]


def test_github_failed_configuration_write_rolls_back_keychain(github, monkeypatch):
    def fail_save(_sources):
        raise AppError("sources_write_failed", "Synthetic source write failure.", 503)

    monkeypatch.setattr(github.service.source_service.store, "save", fail_save)
    response = github.add()
    assert response.status_code == 503
    assert not github.vault.entries
    assert assert_success(github.client.get("/api/sources"))["sources"] == []

def test_github_missing_saved_token_is_visible_without_network_or_file_fallback(github):
    source = assert_success(github.add())["source"]
    github.vault.entries = {}
    saved = assert_success(github.client.get("/api/sources"))["sources"][0]
    assert saved["credential_saved"] is False
    response = github.client.post(f"/api/sources/{source['id']}/test",
                                  headers=github.headers, json={})
    assert response.status_code in (400, 503)
    assert response.json()["success"] is False
    assert not github.remote.calls
    assert TOKEN not in response.text


def test_github_keychain_failure_does_not_create_source_or_echo_token(github, monkeypatch):
    def fail_set(_scope, _token):
        raise RuntimeError("Synthetic vault failure " + TOKEN)

    monkeypatch.setattr(github.vault, "set", fail_set)
    response = github.add()
    assert response.status_code == 503
    assert TOKEN not in response.text
    assert not github.vault.entries and not github.remote.calls
    assert assert_success(github.client.get("/api/sources"))["sources"] == []


def test_github_credentials_remain_scoped_to_independent_sources(github):
    first = assert_success(github.add())["source"]
    second_token = "github_pat_" + "SECOND_SYNTHETIC_NOT_REAL_" * 3
    second = assert_success(github.add(token=second_token))["source"]
    assert first["id"] != second["id"]
    assert len(github.vault.entries) == 2
    assert_success(github.delete(first["id"]))
    assert list(github.vault.entries.values()) == [second_token]
    remaining = assert_success(github.client.get("/api/sources"))["sources"]
    assert [source["id"] for source in remaining] == [second["id"]]


def test_github_twenty_repositories_check_only_first_before_import(github):
    selected = [f"synthetic-owner/repository-{index}" for index in range(20)]
    source = assert_success(github.add(github_repositories=selected))["source"]
    assert source["github_repositories"] == selected
    checked = assert_success(github.client.post(f"/api/sources/{source['id']}/test",
                                                headers=github.headers, json={}))["source"]
    assert checked["status"] == "ok" and checked["item_count"] == 20
    assert github.remote.calls == (("repository", TOKEN, selected[0]),)


def test_github_delete_during_metadata_check_does_not_resurrect_source(github):
    source = assert_success(github.add())["source"]
    remote = github.remote
    remote.metadata_started, remote.metadata_release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as executor:
        check = executor.submit(github.client.post, f"/api/sources/{source['id']}/test",
                                headers=github.headers, json={})
        assert remote.metadata_started.wait(2)
        delete = executor.submit(github.delete, source["id"])
        try:
            assert assert_success(delete.result(timeout=2))["deleted"] is True
        finally:
            remote.metadata_release.set()
        response = check.result(timeout=2)
    assert response.status_code == 404
    assert not github.vault.entries
    assert assert_success(github.client.get("/api/sources"))["sources"] == []

def import_job(github, source_id=None):
    response = github.client.post("/api/memory/import", headers=github.headers,
                                  json={"source_id": source_id})
    job_id = assert_success(response)["job_id"]
    return wait_job(github.client, job_id)


def test_github_import_search_mcp_idempotence_and_document_update(github, tmp_path):
    source = assert_success(github.add())["source"]
    assert import_job(github, source["id"])["status"] == "completed"
    tools = MemoryTools(github.service.store, tmp_path)
    hits = tools.search_memory("SQLite", limit=20)["results"]
    assert len(hits) == 3
    assert {hit["source_kind"] for hit in hits} == {"github"}
    revisions = {hit["id"]: hit["revision"] for hit in hits}
    assert all(revision == 1 for revision in revisions.values())
    assert import_job(github, source["id"])["status"] == "completed"
    again = tools.search_memory("SQLite", limit=20)["results"]
    assert {hit["id"]: hit["revision"] for hit in again} == revisions
    github.remote.text += " Updated default-branch documentation."
    assert import_job(github, source["id"])["status"] == "completed"
    updated = tools.search_memory("SQLite", limit=20)["results"]
    assert len(updated) == 3
    assert sorted(hit["revision"] for hit in updated) == [1, 1, 2]
    assert_success(github.delete(source["id"]))
    assert tools.search_memory("SQLite")["results"] == []
    assert not github.vault.entries


def test_github_selected_content_does_not_expand_to_unselected_types(github):
    source = assert_success(github.add(github_content=["pull_requests"]))["source"]
    assert import_job(github, source["id"])["status"] == "completed"
    operations = [call[0] for call in github.remote.calls]
    assert "pulls" in operations
    assert not {"issues", "documents", "document"}.intersection(operations)
    hits = assert_success(github.client.get("/api/memory/search",
                                           params={"q": "SQLite"}))["results"]
    assert len(hits) == 1
    assert "/pull/" in hits[0]["source_uri"]


def test_github_periodic_sync_obeys_pause_and_manual_import_while_paused(github):
    source = assert_success(github.add())["source"]
    service = github.service
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                           process_cards=False, paused=True))
    service.tick(now=10**12)
    assert not github.remote.calls
    service.set_sync_paused(False)
    service.tick(now=10**12)
    job = service.status()["jobs"][0]
    assert wait_job(github.client, job["id"])["status"] == "completed"
    assert github.remote.calls
    service.set_sync_paused(True)
    assert import_job(github, source["id"])["status"] == "completed"


def test_github_pause_during_automatic_retrieval_preserves_uncommitted_content(github):
    source = assert_success(github.add(github_content=["docs"]))["source"]
    service = github.service
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                           process_cards=False))
    github.remote.on_document = lambda: service.set_sync_paused(True)
    service.tick(now=10**12)
    job = wait_job(github.client, service.status()["jobs"][0]["id"])
    assert job["status"] == "error"
    assert service.status()["scheduler"]["paused"] is True
    assert service.status()["records"] == 0
    github.remote.on_document = None
    assert import_job(github, source["id"])["status"] == "completed"
    assert service.status()["records"] == 1


def test_github_deferred_remote_work_is_visible_after_other_sources(github, monkeypatch, tmp_path):
    import brain_settings.memory_service as memory_module

    assert_success(github.add())
    directory = tmp_path / "notes"
    directory.mkdir()
    (directory / "note.md").write_text("Synthetic local note.")
    assert_success(github.client.post("/api/sources", headers=github.headers, json={
        "kind": "folder", "name": "Synthetic notes", "path": str(directory),
    }))

    def incomplete_remote(*_args, **_kwargs):
        return {"imported": 0, "updated": 0, "unchanged": 0, "skipped": 0,
                "errors": [], "warnings": ["Synthetic remote continuation remains."],
                "files_processed": 0, "files_unchanged": 0, "files_skipped": 0,
                "bytes_read": 0, "has_more": True, "files_deferred": 7, "deferred_paths": []}

    monkeypatch.setattr(memory_module, "import_github", incomplete_remote)
    job = import_job(github)
    assert job["status"] == "completed"
    assert job["stats"]["has_more"] is True
    assert job["stats"]["files_deferred"] == 7
    assert job["stats"]["imported"] == 1
    assert "Synthetic remote continuation remains." in job["stats"]["warnings"]
