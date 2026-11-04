"""Explicit whole-queue retries use synthetic sources and never real providers."""

import threading

import pytest
from test_card_retry_service import await_idle
from test_llm_cards import EVIDENCE
from test_memory_service_api import add_text_source, memory_client, wait_job
from test_memory_service_guards import disable_source, put_record
from test_provider_backoff import limited
from test_settings_api import assert_success

from brain_settings.background import ScheduleConfig, acquire_writer_lease, release_writer_lease
from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore

__all__ = ["memory_client"]
ENDPOINT = "/api/memory/cards/retry-errors"


def failed(store, source="allowed", number="error"):
    record = store.upsert_document(
        source_id=source, source_kind="folder", source_object_id=number,
        title="Synthetic decision", text=EVIDENCE,
        source_uri="synthetic://" + number,
    )
    store.record_card_error(record["id"], record["revision"], "Synthetic provider failure", now=0)
    with store._connection(write=True) as connection:
        connection.execute("UPDATE documents SET card_attempts=3 WHERE id=?", (record["id"],))
    return record["id"]


def setup_service(memory_client, tmp_path):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "failed")
    service.store.record_card_error(identifier, 1, "Synthetic failure", now=0)
    with service.store._connection(write=True) as connection:
        connection.execute("UPDATE documents SET card_attempts=3 WHERE id=?", (identifier,))
    return client, headers, llm, service, source, identifier


def test_store_requeues_more_than_one_batch_without_changing_content_or_index(tmp_path):
    store = MemoryStore(tmp_path / "data")
    identifiers = [failed(store, number=str(index)) for index in range(65)]
    before = store.get_record(identifiers[0])
    hits = [row["id"] for row in store.search("SQLite", limit=8)]
    assert store.requeue_card_errors(allowed_source_ids={"allowed"}) == 65
    assert store.requeue_card_errors(allowed_source_ids={"allowed"}) == 0
    assert [row["id"] for row in store.search("SQLite", limit=8)] == hits
    for identifier in identifiers:
        row = store.get_record(identifier)
        assert row["card_status"] == "pending" and row["card_attempts"] == 0
        assert row["card_retry_at"] is None and row["card_error"] is None
    after = store.get_record(identifiers[0])
    for field in ("text", "title", "revision", "source_uri", "updated_at", "card"):
        assert after.get(field) == before.get(field)


def test_store_requeue_skips_claims_ready_pending_disallowed_and_deleted(tmp_path):
    store = MemoryStore(tmp_path / "data")
    wanted = failed(store)
    hidden = failed(store, "hidden", "hidden")
    deleted = failed(store, "deleted", "deleted")
    claimed = failed(store, number="claimed")
    pending = failed(store, number="pending")
    ready = failed(store, number="ready")
    with store._connection(write=True) as connection:
        connection.execute("INSERT INTO deleted_sources VALUES (?)", ("deleted",))
        connection.execute("UPDATE documents SET card_claim_token='synthetic-claim',"
                           "card_claim_until=500 WHERE id=?", (claimed,))
        connection.execute("UPDATE documents SET card_status='pending' WHERE id=?", (pending,))
        connection.execute("UPDATE documents SET card_status='ready' WHERE id=?", (ready,))
    with store._connection() as connection:
        before = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM documents")}
    assert store.requeue_card_errors(allowed_source_ids={"allowed", "deleted"}) == 1
    with store._connection() as connection:
        after = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM documents")}
    for identifier in (hidden, deleted, claimed, pending, ready):
        assert after[identifier] == before[identifier]
    assert after[wanted]["card_status"] == "pending"
    assert store.requeue_card_errors(allowed_source_ids=set()) == 0


@pytest.mark.parametrize("flags,state", [
    ({"enabled": True, "process_cards": True, "paused": True}, "paused"),
    ({"enabled": True, "process_cards": False}, "automation_disabled"),
    ({"enabled": False, "auto_import_enabled": False, "process_cards": True},
     "automation_disabled"),
])
def test_retry_queues_without_paid_calls_when_automation_cannot_run(
    memory_client, tmp_path, flags, state,
):
    client, headers, llm, service, _, identifier = setup_service(memory_client, tmp_path)
    service.update_schedule(ScheduleConfig(**flags))
    previous = service.schedule_store.load()
    result = assert_success(client.post(ENDPOINT, json={}, headers=headers))
    assert result == {"requeued": 1, "processing_state": state, "job_id": None}
    assert service.schedule_store.load() == previous and llm.calls == ()
    assert service.store.get_record(identifier)["card_attempts"] == 0


def test_retry_starts_configured_batch_then_auto_drains_all(memory_client, tmp_path):
    client, headers, llm, service, source, identifier = setup_service(memory_client, tmp_path)
    identifiers = [identifier]
    for index in range(4):
        identifiers.append(failed(service.store, source["id"], str(index)))
    service.update_schedule(ScheduleConfig(enabled=True, process_cards=True,
                                          auto_import_enabled=False, batch_limit=2))
    service._next_reconcile = float("inf")
    result = assert_success(client.post(ENDPOINT, json={}, headers=headers))
    assert result["requeued"] == 5 and result["processing_state"] == "started"
    wait_job(client, result["job_id"])
    await_idle(service)
    assert len(llm.calls) == 2
    for _ in range(2):
        service.tick()
        await_idle(service)
    assert len(llm.calls) == 5 and len(set(llm.calls)) == 5
    assert all(service.store.get_record(i)["card_status"] == "ready" for i in identifiers)
    result = assert_success(client.post(ENDPOINT, json={}, headers=headers))
    assert result == {"requeued": 0, "processing_state": "empty", "job_id": None}
    assert len(llm.calls) == 5


def test_retry_preserves_provider_hold_and_returns_waiting_state(memory_client, tmp_path):
    client, headers, llm, service, _, _ = setup_service(memory_client, tmp_path)
    service.update_schedule(ScheduleConfig(enabled=True, process_cards=True))
    service.provider_backoff.limit("injected", limited(requires_user_action=True))
    before = service._provider_status()
    result = assert_success(client.post(ENDPOINT, json={}, headers=headers))
    assert result == {"requeued": 1, "processing_state": "waiting_provider", "job_id": None}
    assert service._provider_status() == before and llm.calls == ()


def test_retry_without_model_only_queues(memory_client, tmp_path, monkeypatch):
    client, headers, llm, service, _, _ = setup_service(memory_client, tmp_path)
    service.update_schedule(ScheduleConfig(enabled=True, process_cards=True))
    monkeypatch.setattr(llm, "provider_scope", lambda: None, raising=False)
    result = assert_success(client.post(ENDPOINT, json={}, headers=headers))
    assert result == {"requeued": 1, "processing_state": "model_required", "job_id": None}
    assert llm.calls == ()


def test_retry_renews_exactly_three_automatic_attempts(memory_client, tmp_path, monkeypatch):
    client, headers, llm, service, _, identifier = setup_service(memory_client, tmp_path)
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    llm.error = RuntimeError("Synthetic failure")
    service.update_schedule(ScheduleConfig(enabled=True, process_cards=True,
                                          auto_import_enabled=False, batch_limit=1))
    service._next_reconcile = float("inf")
    result = assert_success(client.post(ENDPOINT, json={}, headers=headers))
    wait_job(client, result["job_id"])
    await_idle(service)
    for instant in (1060, 1360, 9999):
        clock[0] = instant
        service.tick(now=instant)
        await_idle(service)
    assert len(llm.calls) == 3
    record = service.store.get_record(identifier)
    assert record["card_attempts"] == 3 and record["card_waiting_for_user"]


def test_retry_requires_csrf_empty_body_and_respects_source_scope(memory_client, tmp_path):
    client, headers, llm, service, source, identifier = setup_service(memory_client, tmp_path)
    assert client.post(ENDPOINT, json={}).status_code == 403
    assert client.post(ENDPOINT, json={"retry_errors": True}, headers=headers).status_code == 422
    assert client.post(ENDPOINT, headers=headers).status_code == 415
    assert client.post(ENDPOINT, headers={**headers, "Content-Type": "application/json"}
                       ).status_code == 422
    disable_source(service, source["id"])
    result = assert_success(client.post(ENDPOINT, json={}, headers=headers))
    assert result["requeued"] == 0 and result["processing_state"] == "empty"
    assert service.store.get_record(identifier)["card_attempts"] == 3 and llm.calls == ()


def test_retry_rejects_foreign_writer_before_mutating(memory_client, tmp_path):
    client, headers, llm, service, _, identifier = setup_service(memory_client, tmp_path)
    lease = acquire_writer_lease(service.schedule_store.path.parent)
    try:
        response = client.post(ENDPOINT, json={}, headers=headers)
    finally:
        release_writer_lease(lease)
    assert response.status_code == 409 and response.json()["error"]["code"] == "memory_busy"
    assert service.store.get_record(identifier)["card_attempts"] == 3 and llm.calls == ()


def test_retry_rejects_active_job_before_mutating(memory_client, tmp_path, monkeypatch):
    client, headers, llm, service, source, identifier = setup_service(memory_client, tmp_path)
    put_record(service, source, "pending")
    entered, release = threading.Event(), threading.Event()
    original = llm.generate

    def blocked(record):
        entered.set()
        assert release.wait(3)
        return original(record)

    monkeypatch.setattr(llm, "generate", blocked)
    job_id = service.start_cards(1)["job_id"]
    try:
        assert entered.wait(2)
        assert client.post(ENDPOINT, json={}, headers=headers).status_code == 409
        assert service.store.get_record(identifier)["card_attempts"] == 3
    finally:
        release.set()
    wait_job(client, job_id)
    await_idle(service)


def test_retry_preserves_requeue_if_another_writer_wins_start_race(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, llm, service, _, identifier = setup_service(memory_client, tmp_path)
    service.update_schedule(ScheduleConfig(enabled=True, process_cards=True))

    def competing_writer(*args, **kwargs):
        raise AppError("memory_busy", "Synthetic competing writer", 409)

    monkeypatch.setattr(service, "start_cards", competing_writer)
    result = assert_success(client.post(ENDPOINT, json={}, headers=headers))
    assert result == {"requeued": 1, "processing_state": "waiting_worker", "job_id": None}
    assert service.store.get_record(identifier)["card_attempts"] == 0 and llm.calls == ()


def test_api_requeues_all_errors_beyond_manual_batch_limit(memory_client, tmp_path):
    client, headers, llm, service, source, _ = setup_service(memory_client, tmp_path)
    for index in range(64):
        failed(service.store, source["id"], "many-" + str(index))
    result = assert_success(client.post(ENDPOINT, json={}, headers=headers))
    assert result == {"requeued": 65, "processing_state": "automation_disabled", "job_id": None}
    assert service.status()["cards_error"] == 0 and service.status()["cards_pending"] == 65
    assert llm.calls == ()


def test_retry_does_not_mutate_if_preflight_settings_are_unavailable(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, llm, service, _, identifier = setup_service(memory_client, tmp_path)

    def unavailable():
        raise AppError("schedule_unavailable", "Synthetic unavailable settings", 503)

    monkeypatch.setattr(service.schedule_store, "load", unavailable)
    assert client.post(ENDPOINT, json={}, headers=headers).status_code == 503
    assert service.store.get_record(identifier)["card_attempts"] == 3 and llm.calls == ()


def test_retry_reports_provider_pause_that_arrives_before_optional_start(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, llm, service, _, identifier = setup_service(memory_client, tmp_path)
    service.update_schedule(ScheduleConfig(enabled=True, process_cards=True))

    def newly_paused(*args, **kwargs):
        raise AppError("provider_paused", "Synthetic provider pause", 429)

    monkeypatch.setattr(service, "start_cards", newly_paused)
    result = assert_success(client.post(ENDPOINT, json={}, headers=headers))
    assert result == {"requeued": 1, "processing_state": "waiting_provider", "job_id": None}
    assert service.store.get_record(identifier)["card_attempts"] == 0 and llm.calls == ()


def test_stopping_service_rejects_retry_without_mutating(memory_client, tmp_path):
    client, headers, llm, service, _, identifier = setup_service(memory_client, tmp_path)
    service._stop.set()
    assert client.post(ENDPOINT, json={}, headers=headers).status_code == 503
    assert service.store.get_record(identifier)["card_attempts"] == 3 and llm.calls == ()
