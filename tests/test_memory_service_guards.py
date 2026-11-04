import threading

from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient
from test_llm_cards import EVIDENCE
from test_memory_service_api import (
    FakeMemoryLLM,
    add_text_source,
    wait_job,
)
from test_memory_service_api import memory_client as memory_client
from test_settings_api import assert_success

from brain_settings.app import create_app
from brain_settings.errors import AppError


def put_record(service, source, object_id, date="2026-01-01T00:00:00+00:00"):
    return service.store.upsert_document(
        source_id=source["id"], source_kind="folder", source_object_id=object_id,
        title="Synthetic decision", text=EVIDENCE, source_uri="synthetic://" + object_id,
        created_at=date, updated_at=date,
    )["id"]


def disable_source(service, source_id):
    store = service.source_service.store
    store.save(tuple(source.model_copy(update={"enabled": False})
                     if source.id == source_id else source for source in store.load()))


def test_recent_search_filters_disabled_sources_before_limiting(memory_client, tmp_path):
    client, headers, _ = memory_client
    active = add_text_source(client, headers, tmp_path / "active")
    disabled = add_text_source(client, headers, tmp_path / "disabled")
    service = client.app.state.memory_service
    wanted = put_record(service, active, "wanted", "2020-01-01T00:00:00+00:00")
    for index in range(8):
        put_record(service, disabled, f"hidden-{index}")
    disable_source(service, disabled["id"])
    results = assert_success(client.get("/api/memory/search", params={"q": "", "limit": 8}))
    assert [record["id"] for record in results["results"]] == [wanted]


def test_pending_batch_filters_disabled_sources_before_limiting(memory_client, tmp_path):
    client, headers, llm = memory_client
    active = add_text_source(client, headers, tmp_path / "active")
    disabled = add_text_source(client, headers, tmp_path / "disabled")
    service = client.app.state.memory_service
    put_record(service, disabled, "hidden", "2020-01-01T00:00:00+00:00")
    wanted = put_record(service, active, "wanted")
    disable_source(service, disabled["id"])
    data = assert_success(client.post("/api/memory/cards", json={"limit": 1}, headers=headers))
    job_id = data["job_id"]
    wait_job(client, job_id)
    assert llm.calls == (wanted,)
    assert service.store.get_record(wanted)["card_status"] == "ready"


def test_disconnected_source_is_rechecked_before_every_paid_call(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    put_record(service, source, "first")
    put_record(service, source, "second")
    service.update_schedule(service.schedule_store.load().model_copy(
        update={"card_concurrency": 1}
    ))
    original = llm.generate

    def remove_during_first_call(record):
        result = original(record)
        if len(llm.calls) == 1:
            service.disconnect_source(source["id"])
        return result

    monkeypatch.setattr(llm, "generate", remove_during_first_call)
    data = assert_success(client.post("/api/memory/cards", json={"limit": 2}, headers=headers))
    job_id = data["job_id"]
    wait_job(client, job_id)
    assert len(llm.calls) == 1
    assert service.store.status()["documents"] == 0


def test_two_app_instances_cannot_generate_same_pending_record_twice(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    put_record(service, source, "pending")
    entered, release = threading.Event(), threading.Event()
    original = llm.generate

    def blocked(record):
        entered.set()
        assert release.wait(3)
        return original(record)

    monkeypatch.setattr(llm, "generate", blocked)
    data = assert_success(client.post("/api/memory/cards", json={"limit": 1}, headers=headers))
    job_id = data["job_id"]
    assert entered.wait(2)
    second_llm = FakeMemoryLLM()
    second_app = create_app(data_dir=service.schedule_store.path.parent, vault=MemoryVault(),
                            transport=FakeTransport(), memory_llm=second_llm)
    try:
        with TestClient(second_app, base_url=ORIGIN) as other:
            csrf = assert_success(other.get("/api/bootstrap"))["csrf_token"]
            response = other.post("/api/memory/cards", json={"limit": 1}, headers={
                "Origin": ORIGIN, "X-Brain-CSRF": csrf,
            })
            assert response.status_code == 409
            assert second_llm.calls == ()
    finally:
        release.set()
    wait_job(client, job_id)


def test_stale_paid_response_keeps_usage_but_never_activates_card(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    record_id = put_record(service, source, "pending")
    monkeypatch.setattr(llm, "is_current", lambda result: False)
    data = assert_success(client.post("/api/memory/cards", json={"limit": 1}, headers=headers))
    job_id = data["job_id"]
    job = wait_job(client, job_id)
    assert service.store.get_record(record_id)["card_status"] == "pending"
    assert job["stats"]["stale"] == 1
    assert job["stats"]["usage"]["total_tokens"] == 15


def test_restored_schedule_uses_saved_interval_for_first_reconciliation(
    memory_client, tmp_path, monkeypatch,
):
    import brain_settings.memory_service as module

    client, headers, _ = memory_client
    add_text_source(client, headers, tmp_path / "notes")
    assert_success(client.post("/api/memory/schedule", json={
        "enabled": True, "interval_minutes": 60, "auto_import_enabled": False,
        "process_cards": False, "batch_limit": 1,
    }, headers=headers))
    directory = client.app.state.memory_service.schedule_store.path.parent
    monkeypatch.setattr(module.time, "time", lambda: 1000)
    app = create_app(data_dir=directory, vault=MemoryVault(), transport=FakeTransport(),
                     memory_llm=FakeMemoryLLM())
    with TestClient(app, base_url=ORIGIN) as restarted:
        service = app.state.memory_service
        service.tick(now=1901)
        assert assert_success(restarted.get("/api/memory/status"))["jobs"] == []


def test_partial_disconnect_never_rewatches_or_reimports_revoked_source(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, _ = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    put_record(service, source, "pending")

    def fail(source_id):
        raise AppError("settings_write_failed", "Synthetic config write failure", 503)

    monkeypatch.setattr(service.source_service, "delete", fail)
    response = client.request("DELETE", f"/api/sources/{source['id']}", json={}, headers=headers)
    assert response.status_code == 503

    def forbidden(*args, **kwargs):
        raise AssertionError("Revoked source must never be scanned again")

    monkeypatch.setattr(service.watcher, "collect", forbidden)
    service.tick(now=10**12)
    response = client.post("/api/memory/import", json={"source_id": source["id"]}, headers=headers)
    assert response.status_code == 404
