"""Persistent synchronization pause leaves local reads and manual work available."""

import json
import os
import threading

import pytest
from test_card_retry_service import await_idle
from test_llm_cards import EVIDENCE
from test_memory_service_api import add_text_source, memory_client, wait_job
from test_memory_service_automation import configure_codex, latest_job, write_session
from test_memory_service_guards import put_record

from brain_settings.background import ScheduleConfig, ScheduleStore, SessionWatcher
from brain_settings.mcp_server import MemoryTools

__all__ = ["memory_client"]


def set_pause(client, headers, value):
    response = client.post("/api/memory/sync", json={"paused": value}, headers=headers)
    assert response.status_code == 200
    return response.json()["data"]["scheduler"]


def test_pause_persists_without_replacing_preferences_and_legacy_updates(memory_client):
    client, headers, llm = memory_client
    service = client.app.state.memory_service
    config = ScheduleConfig(enabled=False, auto_import_enabled=True, process_cards=True,
                            batch_limit=7, interval_minutes=21)
    service.update_schedule(config)
    paused = set_pause(client, headers, True)
    assert paused["paused"] is True
    assert {key: paused[key] for key in config.model_dump()} == {
        **config.model_dump(), "paused": True,
    }
    assert ScheduleStore(service.schedule_store.path.parent).load().paused
    legacy = config.model_dump(exclude={"paused"}) | {"interval_minutes": 22}
    result = client.post("/api/memory/schedule", json=legacy, headers=headers)
    assert result.status_code == 200
    assert result.json()["data"]["scheduler"]["paused"] is True
    resumed = set_pause(client, headers, False)
    assert resumed["paused"] is False and resumed["interval_minutes"] == 22
    assert resumed["process_cards"] is True and resumed["auto_import_enabled"] is True
    assert resumed["enabled"] is False and resumed["batch_limit"] == 7
    assert llm.calls == ()


@pytest.mark.parametrize("payload", [{}, {"paused": "true"}, {"paused": 1},
                                      {"paused": None}, {"paused": True, "extra": 1}])
def test_pause_input_is_strict_and_requires_csrf(memory_client, payload):
    client, headers, _ = memory_client
    assert client.post("/api/memory/sync", json={"paused": True}).status_code == 403
    assert client.post("/api/memory/sync", json=payload, headers=headers).status_code == 422


def test_paused_tick_skips_watcher_reconcile_and_due_retry(memory_client, tmp_path, monkeypatch):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    record_id = put_record(service, source, "paused-retry")
    service.store.record_card_error(record_id, 1, "Synthetic failure")
    before = service.record(record_id)["record"]
    service.update_schedule(ScheduleConfig(enabled=True, process_cards=True))
    service._next_reconcile = 0
    set_pause(client, headers, True)
    monkeypatch.setattr(service.watcher, "collect", lambda *a, **k: pytest.fail("watched"))
    monkeypatch.setattr(service.store, "has_due_card_retries",
                        lambda *a, **k: pytest.fail("checked retries"))
    service.tick(now=99999)
    assert service.status()["jobs"] == [] and llm.calls == ()
    after = service.record(record_id)["record"]
    assert after["card_attempts"] == before["card_attempts"]
    assert after["card_retry_at"] == before["card_retry_at"]


def test_pause_keeps_mcp_search_atlas_and_manual_actions_available(memory_client, tmp_path):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    set_pause(client, headers, True)
    imported = service.start_import(source["id"])["job_id"]
    assert wait_job(client, imported)["status"] == "completed"
    await_idle(service)
    query = EVIDENCE.split()[0]
    assert service.search(query)["results"]
    tools = MemoryTools(service.store, service.schedule_store.path.parent)
    assert tools.search_memory(query)["results"]
    assert service.atlas()["total_records"] == 1
    cards = service.start_cards(limit=1)["job_id"]
    assert wait_job(client, cards)["status"] == "completed"
    assert len(llm.calls) == 1
    assert service.status()["scheduler"]["paused"] is True


def test_pause_mid_batch_finishes_current_request_but_stops_next(memory_client, tmp_path):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    for index in range(2):
        put_record(service, source, str(index))
    service.update_schedule(ScheduleConfig(enabled=True, process_cards=True, card_concurrency=1))
    entered, release = threading.Event(), threading.Event()
    generate = llm.generate

    def blocked(record):
        entered.set()
        assert release.wait(timeout=3)
        return generate(record)

    llm.generate = blocked
    job_id = service.start_cards(limit=2, automatic=True)["job_id"]
    try:
        assert entered.wait(timeout=3)
        set_pause(client, headers, True)
    finally:
        release.set()
    assert wait_job(client, job_id)["status"] == "completed"
    await_idle(service)
    assert len(llm.calls) == 1
    assert service.status()["cards_pending"] == 1
    assert service.store.pending()[0]["card_attempts"] == 0


def test_resume_uses_latest_activity_quiet_deadline_not_resume_time(memory_client, tmp_path):
    client, headers, llm = memory_client
    directory = tmp_path / "sessions"
    configure_codex(client, headers, directory)
    path = directory / "live.jsonl"
    service = client.app.state.memory_service
    write_session(path, timestamp=1000)
    service.tick(now=1050)
    set_pause(client, headers, True)
    write_session(path, timestamp=1060)
    service.tick(now=1080)
    write_session(path, timestamp=1120)
    service.tick(now=1209)
    assert service.status()["jobs"] == []
    set_pause(client, headers, False)
    service.tick(now=1209)
    assert service.status()["jobs"] == []
    service.tick(now=1210)
    assert latest_job(client)["status"] == "completed"
    assert service.status()["records"] == 1 and llm.calls == ()
    service.tick(now=1211)
    assert len(service.status()["jobs"]) == 1
    set_pause(client, headers, True)
    write_session(path, text=EVIDENCE + " Updated.", timestamp=1220)
    set_pause(client, headers, False)
    service.tick(now=1400)
    assert latest_job(client)["status"] == "completed"
    assert service.status()["records"] == 1


@pytest.mark.parametrize("kind", ["codex", "claude"])
@pytest.mark.parametrize("partial", [True, False])
def test_new_activity_after_completed_turn_waits_for_quiet(tmp_path, partial, kind):
    directory = tmp_path / "sessions"
    directory.mkdir()
    path = directory / "live.jsonl"
    if kind == "codex":
        write_session(path, timestamp=1000, completed=True)
    else:
        path.write_text('{"type":"assistant","message":{"stop_reason":"end_turn"}}\n')
        os.utime(path, (1000, 1000))
    source = {"id": "synthetic", "kind": kind, "enabled": True, "path": str(directory)}
    watcher = SessionWatcher()
    assert watcher.collect(source, now=1001).paths
    with path.open("ab") as handle:
        handle.write(b'{"type":"response_item","payload":' if partial else b'{}\n')
    os.utime(path, (1120, 1120))
    assert not watcher.collect(source, now=1121).paths
    assert not watcher.collect(source, now=1209).paths
    assert watcher.collect(source, now=1210).paths


def test_legacy_schedule_defaults_unpaused_and_explicit_update_can_resume(tmp_path, memory_client):
    store = ScheduleStore(tmp_path / "legacy")
    store.path.parent.mkdir()
    legacy = ScheduleConfig(enabled=True, process_cards=True).model_dump(exclude={"paused"})
    store.path.write_text(json.dumps(legacy))
    loaded = store.load()
    assert loaded.paused is False
    assert loaded.enabled and loaded.process_cards
    client, headers, _ = memory_client
    set_pause(client, headers, True)
    response = client.post("/api/memory/schedule", json={**legacy, "paused": False},
                           headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["scheduler"]["paused"] is False


def test_resuming_due_retry_preserves_delay_and_spent_budget(memory_client, tmp_path, monkeypatch):
    client, headers, llm = memory_client
    service = client.app.state.memory_service
    source = add_text_source(client, headers, tmp_path / "notes")
    identifier = put_record(service, source, "retry")
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    llm.error = RuntimeError("Synthetic failure")
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                          process_cards=True, batch_limit=1))
    service._next_reconcile = float("inf")
    assert wait_job(client, service.start_cards(1)["job_id"])["status"] == "completed"
    await_idle(service)
    before = service.record(identifier)["record"]
    set_pause(client, headers, True)
    clock[0] = 9999
    service.tick(now=clock[0])
    assert len(llm.calls) == 1
    assert service.record(identifier)["record"]["card_retry_at"] == before["card_retry_at"]
    set_pause(client, headers, False)
    service.tick(now=clock[0])
    await_idle(service)
    assert len(llm.calls) == 2
    assert service.record(identifier)["record"]["card_attempts"] == 2


def test_pause_during_watch_scan_prevents_late_enqueue_and_does_not_ack(memory_client, tmp_path,
                                                                    monkeypatch):
    client, headers, llm = memory_client
    directory = tmp_path / "sessions"
    configure_codex(client, headers, directory)
    write_session(directory / "live.jsonl", timestamp=1000)
    service = client.app.state.memory_service
    collect = service.watcher.collect

    def pause_during_scan(source, now=None, **kwargs):
        batch = collect(source, now=now, **kwargs)
        assert batch.paths
        service.set_sync_paused(True)
        return batch

    monkeypatch.setattr(service.watcher, "collect", pause_during_scan)
    service.tick(now=2000)
    assert service.status()["jobs"] == []
    assert service.status()["scheduler"]["paused"] is True
    monkeypatch.setattr(service.watcher, "collect", collect)
    set_pause(client, headers, False)
    service.tick(now=2001)
    assert latest_job(client)["status"] == "completed"
    assert service.status()["records"] == 1 and llm.calls == ()
