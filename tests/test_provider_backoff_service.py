"""All provider calls are synthetic; holds never prevent import or local search."""

import threading

import pytest
from test_card_retry_service import await_idle
from test_llm_cards import cards_fixture, valid_card
from test_memory_service_api import add_text_source, memory_client, wait_job
from test_memory_service_guards import put_record
from test_provider_backoff import limited

from brain_settings.background import ScheduleConfig
from brain_settings.errors import AppError
from brain_settings.provider_backoff import ProviderBackoff

__all__ = ["memory_client", "cards_fixture"]


def prepare(memory_client, tmp_path, monkeypatch, count=4):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifiers = [put_record(service, source, str(index)) for index in range(count)]
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                          process_cards=True, card_concurrency=1))
    service._next_reconcile = float("inf")
    return client, llm, service, identifiers, clock


def test_429_stops_refill_retains_budget_and_blocks_manual_until_expiry(
    memory_client, tmp_path, monkeypatch,
):
    client, llm, service, identifiers, clock = prepare(memory_client, tmp_path, monkeypatch)
    llm.error = limited(retry_after=120)
    job = wait_job(client, service.start_cards(4)["job_id"])
    await_idle(service)
    assert len(llm.calls) == 1 and job["stats"]["errors"] == 1
    assert sorted(service.store.get_record(i)["card_attempts"] for i in identifiers) == [0, 0, 0, 1]
    assert service.status()["provider_backoff"]["blocked"]
    with pytest.raises(AppError) as caught:
        service.start_cards(4, retry_errors=True)
    assert caught.value.code == "provider_paused"
    service.tick(now=1100)
    await_idle(service)
    assert len(llm.calls) == 1
    clock[0] = 1120
    service.tick(now=1120)
    await_idle(service)
    assert len(llm.calls) == 2


def test_pre_post_guard_refunds_reservation_without_recording_error(
    memory_client, tmp_path, monkeypatch,
):
    client, llm, service, identifiers, _ = prepare(memory_client, tmp_path, monkeypatch, count=1)

    def admitted(record, before_request):
        ProviderBackoff(service.store).limit("injected", limited())
        before_request()
        raise AssertionError("A held provider must not receive a request")

    monkeypatch.setattr(llm, "generate_with_admission", admitted, raising=False)
    job = wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    record = service.store.get_record(identifiers[0])
    assert record["card_attempts"] == 0 and record["card_status"] == "pending"
    assert job["stats"]["errors"] == 0 and llm.calls == ()


def test_resume_only_clears_provider_hold_without_requests_or_attempt_reset(
    memory_client, tmp_path, monkeypatch,
):
    client, llm, service, identifiers, clock = prepare(
        memory_client, tmp_path, monkeypatch, count=1,
    )
    llm.error = limited(requires_user_action=True)
    wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    clock[0] = 10**10
    service.tick(now=clock[0])
    await_idle(service)
    assert len(llm.calls) == 1
    before = service.store.get_record(identifiers[0])["card_attempts"]
    response = service.resume_provider()
    assert not response["provider_backoff"]["blocked"]
    assert response["provider_backoff"]["effective_concurrency"] == 1
    assert len(llm.calls) == 1
    assert service.store.get_record(identifiers[0])["card_attempts"] == before


def test_import_and_search_continue_while_provider_held(memory_client, tmp_path, monkeypatch):
    client, llm, service, _, _ = prepare(memory_client, tmp_path, monkeypatch, count=0)
    ProviderBackoff(service.store).limit("injected", limited(requires_user_action=True))
    job = wait_job(client, service.start_import(process_cards=True)["job_id"])
    await_idle(service)
    assert job["status"] == "completed" and llm.calls == ()
    assert service.status()["records"] == 1
    assert len(service.search("")["results"]) == 1


def test_adaptive_concurrency_does_not_replace_configured_value(
    memory_client, tmp_path, monkeypatch,
):
    client, llm, service, _, clock = prepare(memory_client, tmp_path, monkeypatch)
    service.update_schedule(ScheduleConfig(card_concurrency=10))
    ProviderBackoff(service.store).limit("injected", limited(retry_after=1))
    clock[0] += 1
    job = wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    assert job["concurrency"] == 1 and service.schedule_store.load().card_concurrency == 10
    assert service.status()["provider_backoff"]["effective_concurrency"] == 2


def test_rate_limits_keep_exactly_three_automatic_attempts(memory_client, tmp_path, monkeypatch):
    client, llm, service, identifiers, clock = prepare(
        memory_client, tmp_path, monkeypatch, count=1,
    )
    llm.error = limited()
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                          process_cards=True, batch_limit=1, card_concurrency=1))
    service._next_reconcile = float("inf")
    wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    for instant in (1060, 1360, 2000, 99999):
        clock[0] = instant
        service.tick(now=instant)
        await_idle(service)
    assert len(llm.calls) == 3
    record = service.store.get_record(identifiers[0])
    assert record["card_attempts"] == 3 and record["card_waiting_for_user"]


def test_429_drains_already_admitted_calls_without_refilling(memory_client, tmp_path, monkeypatch):
    client, llm, service, identifiers, _ = prepare(memory_client, tmp_path, monkeypatch, count=6)
    service.update_schedule(ScheduleConfig(card_concurrency=2))
    barrier = threading.Barrier(2)
    release = threading.Event()
    failure_recorded = threading.Event()
    lock = threading.Lock()
    calls = []
    update = service._update

    def admitted(record, before_request):
        before_request()
        with lock:
            ordinal = len(calls)
            calls.append(record["id"])
        barrier.wait(timeout=3)
        if ordinal == 0:
            raise limited()
        assert release.wait(3)
        return {"card": valid_card(), "model": "synthetic"}

    def observe(job_id, **values):
        update(job_id, **values)
        if values.get("stats", {}).get("errors") == 1:
            failure_recorded.set()

    monkeypatch.setattr(llm, "generate_with_admission", admitted, raising=False)
    monkeypatch.setattr(service, "_update", observe)
    job_id = service.start_cards(6)["job_id"]
    try:
        assert failure_recorded.wait(3)
        assert service.status()["provider_backoff"]["blocked"]
        assert len(calls) == 2
    finally:
        release.set()
    job = wait_job(client, job_id)
    await_idle(service)
    assert len(calls) == 2
    assert job["stats"]["ready"] == job["stats"]["errors"] == 1
    attempts = [service.store.get_record(i)["card_attempts"] for i in identifiers]
    assert sorted(attempts) == [0, 0, 0, 0, 1, 1]
    assert service.status()["provider_backoff"]["effective_concurrency"] == 1


def test_real_generator_missing_token_makes_no_post_and_preserves_attempt_budget(
    memory_client, cards_fixture, tmp_path, monkeypatch,
):
    client, _, service, identifiers, _ = prepare(
        memory_client, tmp_path, monkeypatch, count=1,
    )
    settings, transport, generator, _ = cards_fixture
    service.generator = generator
    monkeypatch.setattr(settings.vault, "get", lambda scope: None)
    job = wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    record = service.store.get_record(identifiers[0])
    assert record["card_attempts"] == 0 and record["card_status"] == "pending"
    assert transport.calls == ()
    assert job["status"] == "error" and "API-токен" in job["error"]


@pytest.mark.parametrize("code", ["card_admission_failed", "memory_unavailable"])
def test_unsent_admission_error_refunds_and_reports_job_failure(
    memory_client, tmp_path, monkeypatch, code,
):
    client, llm, service, identifiers, _ = prepare(
        memory_client, tmp_path, monkeypatch, count=1,
    )

    def admitted(record, before_request):
        error = AppError(code, "Безопасная ошибка допуска запроса.", 503)
        error.request_sent = False
        raise error

    monkeypatch.setattr(llm, "generate_with_admission", admitted, raising=False)
    job = wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    record = service.store.get_record(identifiers[0])
    assert record["card_attempts"] == 0 and record["card_status"] == "pending"
    assert job["status"] == "error" and "Безопасная ошибка" in job["error"]


def test_scope_switch_before_post_refunds_and_never_calls_provider(
    memory_client, tmp_path, monkeypatch,
):
    client, llm, service, identifiers, _ = prepare(memory_client, tmp_path, monkeypatch, count=1)
    scope = ["first"]
    monkeypatch.setattr(llm, "provider_scope", lambda: scope[0], raising=False)

    def admitted(record, before_request):
        scope[0] = "second"
        before_request()
        raise AssertionError("A changed model must be re-admitted by a new job")

    monkeypatch.setattr(llm, "generate_with_admission", admitted, raising=False)
    wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    assert service.store.get_record(identifiers[0])["card_attempts"] == 0
    assert not service.status()["provider_backoff"]["blocked"]
