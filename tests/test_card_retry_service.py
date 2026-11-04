"""Retry scheduling uses fake clocks/providers and never credentials or real archives."""

import time

import pytest
from test_memory_service_api import add_text_source, memory_client, wait_job
from test_memory_service_guards import put_record

from brain_settings.background import ScheduleConfig

__all__ = ["memory_client"]


def await_idle(service):
    for _ in range(300):
        with service._lock:
            idle = service._active_job is None
        if idle:
            return
        time.sleep(0.005)
    raise AssertionError("Synthetic worker did not finish")


def test_due_retries_run_without_new_files_and_never_exceed_three(memory_client, tmp_path,
                                                                monkeypatch):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "retry")
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    llm.error = RuntimeError("Synthetic failure")
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                          process_cards=True, batch_limit=1))
    service._next_reconcile = float("inf")
    wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    assert len(llm.calls) == 1
    for instant, expected in ((1059, 1), (1060, 2), (1359, 2), (1360, 3), (99999, 3)):
        clock[0] = instant
        service.tick(now=instant)
        await_idle(service)
        assert len(llm.calls) == expected
    record = service.record(identifier)["record"]
    assert record["card_attempts"] == 3 and record["card_waiting_for_user"]
    assert service.status()["cards_waiting_user"] == 1
    assert service.status()["jobs"][0]["phase"] == "cards"


@pytest.mark.parametrize("enabled,auto_import,process_cards", [
    (False, False, True), (True, False, False), (False, True, False),
])
def test_retry_scheduler_respects_pause_and_paid_optin(memory_client, tmp_path, monkeypatch,
                                                    enabled, auto_import, process_cards):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "retry")
    service.store.record_card_error(identifier, 1, "Synthetic error", now=0)
    empty_batch = type("Batch", (), {"paths": ()})()
    monkeypatch.setattr(service.watcher, "collect", lambda *a, **k: empty_batch)
    service.update_schedule(ScheduleConfig(enabled=enabled, auto_import_enabled=auto_import,
                                          process_cards=process_cards))
    service._next_reconcile = float("inf")
    service.tick(now=1000)
    await_idle(service)
    assert llm.calls == ()


def test_manual_retry_is_one_call_without_replenishing_budget(memory_client, tmp_path,
                                                           monkeypatch):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "retry")
    llm.error = RuntimeError("Synthetic error")
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    for instant in (1000, 1060, 1360):
        clock[0] = instant
        wait_job(client, service.start_cards(1, retry_errors=True)["job_id"])
        await_idle(service)
    assert len(llm.calls) == 3
    clock[0] = 2000
    wait_job(client, service.start_cards(1, retry_errors=True)["job_id"])
    await_idle(service)
    assert len(llm.calls) == 4
    assert service.store.get_record(identifier)["card_waiting_for_user"]
    assert service.store.has_due_card_retries(now=99999) is False


@pytest.mark.parametrize("succeed_on", [2, 3])
def test_success_on_retry_stops_future_automatic_calls(memory_client, tmp_path, monkeypatch,
                                                     succeed_on):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "retry")
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                          process_cards=True, batch_limit=1))
    service._next_reconcile = float("inf")
    llm.error = RuntimeError("Synthetic failure")
    wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    for attempt, instant in ((2, 1060), (3, 1360)):
        clock[0] = instant
        if attempt == succeed_on:
            llm.error = None
        service.tick(now=instant)
        await_idle(service)
    service.tick(now=99999)
    await_idle(service)
    assert len(llm.calls) == succeed_on
    assert service.store.get_record(identifier)["card_status"] == "ready"


def test_stale_model_response_exhaustion_remains_visible_and_manually_retryable(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "retry")
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    monkeypatch.setattr(llm, "is_current", lambda result: False)
    for instant in (1000, 1060, 1360):
        clock[0] = instant
        wait_job(client, service.start_cards(1)["job_id"])
        await_idle(service)
    record = service.store.get_record(identifier)
    assert record["card_status"] == "error" and record["card_waiting_for_user"]
    assert service.store.pending(automatic_retries=True, now=99999) == []
    assert service.store.pending(retry_errors=True, now=99999)[0]["id"] == identifier


def test_pause_between_reservation_and_provider_refunds_unused_attempt(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "retry")
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                          process_cards=True))
    original = service.store.claim_card_attempt

    def pause_after_claim(*args, **kwargs):
        token = original(*args, **kwargs)
        service.update_schedule(ScheduleConfig(enabled=False, auto_import_enabled=False,
                                              process_cards=True))
        return token

    monkeypatch.setattr(service.store, "claim_card_attempt", pause_after_claim)
    wait_job(client, service.start_cards(1, automatic=True)["job_id"])
    await_idle(service)
    assert llm.calls == ()
    assert service.store.get_record(identifier)["card_attempts"] == 0


def test_due_errors_share_the_existing_total_batch_limit(memory_client, tmp_path, monkeypatch):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    for index in range(4):
        identifier = put_record(service, source, str(index))
        service.store.record_card_error(identifier, 1, "Synthetic failure", now=0)
    monkeypatch.setattr("time.time", lambda: 1000)
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                          process_cards=True, batch_limit=2))
    service._next_reconcile = float("inf")
    service.tick(now=1000)
    await_idle(service)
    assert len(llm.calls) == 2
    assert len(set(llm.calls)) == 2
    assert service.status()["cards_ready"] == 2
