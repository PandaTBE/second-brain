import time

import pytest
from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient
from test_llm_cards import EVIDENCE, valid_card
from test_settings_api import assert_success

from brain_settings.app import create_app


class FakeMemoryLLM:
    def __init__(self, error=None):
        self.error = error
        self.calls = ()

    def generate(self, record):
        self.calls = (*self.calls, record["id"])
        if self.error is not None:
            raise self.error
        return {"card": valid_card(), "model": "synthetic/cheap", "prompt_version": "1",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    def is_current(self, result):
        return True


def wait_job(client, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = assert_success(client.get(f"/api/memory/jobs/{job_id}"))
        job = result["job"]
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.01)
    raise AssertionError("Synthetic memory job did not finish")


@pytest.fixture
def memory_client(tmp_path):
    llm = FakeMemoryLLM()
    app = create_app(data_dir=tmp_path / "data", vault=MemoryVault(), transport=FakeTransport(),
                     memory_llm=llm)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = assert_success(client.get("/api/bootstrap"))["csrf_token"]
        headers = {"Origin": ORIGIN, "X-Brain-CSRF": csrf}
        yield client, headers, llm


def add_text_source(client, headers, directory):
    directory.mkdir()
    (directory / "decision.md").write_text(EVIDENCE)
    source = assert_success(client.post("/api/sources", json={
        "kind": "folder", "name": "Synthetic memory", "path": str(directory),
    }, headers=headers))["source"]
    return source


def test_memory_status_defaults_have_no_paid_jobs_or_fake_counts(memory_client):
    client, _, llm = memory_client
    data = assert_success(client.get("/api/memory/status"))
    assert data["records"] == 0
    assert data["cards_pending"] == data["cards_ready"] == data["cards_error"] == 0
    assert data["scheduler"]["process_cards"] is False
    assert data["scheduler"]["auto_import_enabled"] is True
    assert data["scheduler"]["quiet_seconds"] == 90
    assert llm.calls == ()


def test_import_search_detail_and_unchanged_retry_never_call_llm(memory_client, tmp_path):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    job_id = assert_success(client.post("/api/memory/import", json={
        "source_id": source["id"],
    }, headers=headers))["job_id"]
    job = wait_job(client, job_id)
    assert job["status"] == "completed"
    status = assert_success(client.get("/api/memory/status"))
    assert status["records"] == 1
    results = assert_success(client.get("/api/memory/search", params={"q": "SQLite", "limit": 8}))
    assert len(results["results"]) == 1
    record_id = results["results"][0]["id"]
    record = assert_success(client.get(f"/api/memory/records/{record_id}"))["record"]
    assert EVIDENCE in record["text"]
    again = assert_success(client.post("/api/memory/import", json={
        "source_id": source["id"],
    }, headers=headers))["job_id"]
    wait_job(client, again)
    assert assert_success(client.get("/api/memory/status"))["records"] == 1
    assert llm.calls == ()


def test_explicit_cards_job_processes_pending_once(memory_client, tmp_path):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    imported = assert_success(client.post("/api/memory/import", json={
        "source_id": source["id"],
    }, headers=headers))["job_id"]
    wait_job(client, imported)
    cards = assert_success(client.post("/api/memory/cards", json={"limit": 1}, headers=headers))
    job = wait_job(client, cards["job_id"])
    assert job["status"] == "completed"
    assert len(llm.calls) == 1
    assert assert_success(client.get("/api/memory/status"))["cards_ready"] == 1
    second = assert_success(client.post("/api/memory/cards", json={"limit": 1}, headers=headers))
    wait_job(client, second["job_id"])
    assert len(llm.calls) == 1


def test_disconnect_removes_memory_and_original_remains(memory_client, tmp_path):
    client, headers, _ = memory_client
    folder = tmp_path / "notes"
    source = add_text_source(client, headers, folder)
    job_id = assert_success(client.post("/api/memory/import", json={
        "source_id": source["id"],
    }, headers=headers))["job_id"]
    wait_job(client, job_id)
    assert_success(client.request("DELETE", f"/api/sources/{source['id']}",
                                  json={}, headers=headers))
    assert assert_success(client.get("/api/memory/search", params={"q": "SQLite"}))["results"] == []
    assert (folder / "decision.md").read_text() == EVIDENCE


@pytest.mark.parametrize("path,payload", [
    ("/api/memory/import", {}), ("/api/memory/cards", {"limit": 1}),
    ("/api/memory/schedule", {"enabled": True}),
])
def test_memory_actions_require_csrf(memory_client, path, payload):
    client, _, llm = memory_client
    response = client.post(path, json=payload)
    assert response.status_code == 403
    assert llm.calls == ()


@pytest.mark.parametrize("payload", [{"limit": 0}, {"limit": 51}, {"limit": True}])
def test_cards_batch_bound_is_enforced(memory_client, payload):
    client, headers, llm = memory_client
    response = client.post("/api/memory/cards", json=payload, headers=headers)
    assert response.status_code == 422
    assert llm.calls == ()


def test_schedule_optin_persists_but_never_immediately_generates(memory_client):
    client, headers, llm = memory_client
    result = assert_success(client.post("/api/memory/schedule", json={
        "enabled": True, "interval_minutes": 15, "process_cards": True,
        "batch_limit": 2, "auto_import_enabled": False,
    }, headers=headers))
    assert result["scheduler"]["process_cards"] is True
    assert result["scheduler"]["auto_import_enabled"] is False
    assert assert_success(client.get("/api/memory/status"))["scheduler"]["batch_limit"] == 2
    assert llm.calls == ()


def test_provider_resume_requires_csrf_and_empty_body(memory_client):
    client, headers, llm = memory_client
    assert client.post("/api/memory/provider-resume", json={}).status_code == 403
    response = client.post("/api/memory/provider-resume", json={"retry_errors": True},
                           headers=headers)
    assert response.status_code == 422
    assert llm.calls == ()


def test_provider_resume_clears_gate_without_starting_paid_work(memory_client):
    client, headers, llm = memory_client
    result = assert_success(client.post("/api/memory/provider-resume", json={}, headers=headers))
    assert result["provider_backoff"]["blocked"] is False
    assert llm.calls == ()
