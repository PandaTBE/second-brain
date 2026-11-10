import json

import pytest
from pydantic import ValidationError
from test_card_retry_service import await_idle
from test_gigacode_manual_cards import enable_automation
from test_gigacode_provider import selection
from test_gigacode_provider import service as settings_service
from test_memory_service_api import add_text_source, wait_job
from test_memory_service_api import memory_client as memory_client
from test_memory_service_automation import configure_codex, latest_job, write_session
from test_memory_service_guards import put_record

from brain_settings.background import ScheduleConfig
from brain_settings.providers import EndpointRequest, SaveRequest, Settings
from brain_settings.source_policy import SourcePolicy


def select_cli(service, enabled=True):
    service.settings_service.store.save(Settings(
        provider="gigacode_cli", base_url="gigacode://local", model_id="default",
        gigacode_auto_cards=enabled,
    ))


@pytest.mark.parametrize("model", [EndpointRequest, SaveRequest, Settings])
def test_optin_is_strict_and_cli_only(model):
    values = dict(provider="gigacode_cli", base_url="gigacode://local", model_id="default")
    if model is EndpointRequest:
        values.pop("model_id")
    assert model(**values, gigacode_auto_cards=True).gigacode_auto_cards is True
    assert model(**values).gigacode_auto_cards is False
    with pytest.raises(ValidationError):
        model(**values, gigacode_auto_cards="true")
    with pytest.raises(ValidationError):
        model(**{**values, "provider": "openrouter"}, gigacode_auto_cards=True)


def test_save_optin_and_omitted_consent_resets_legacy_format(tmp_path):
    service = settings_service(tmp_path)
    result = service.save(selection(gigacode_auto_cards=True))
    assert result["settings"]["gigacode_auto_cards"] is True
    assert service.bootstrap("synthetic-csrf")["settings"]["gigacode_auto_cards"] is True
    service.save(selection(cli_path="/synthetic/changed/gigacode"))
    assert service.store.load().gigacode_auto_cards is False
    assert "gigacode_auto_cards" not in json.loads(service.store.path.read_text())


def test_cli_optin_generates_cards_after_auto_import(memory_client, tmp_path):
    client, headers, llm = memory_client
    directory = tmp_path / "sessions"
    configure_codex(client, headers, directory)
    write_session(directory / "session.jsonl", timestamp=1000, completed=True)
    service = client.app.state.memory_service
    select_cli(service)
    enable_automation(client, headers)
    service.tick(now=1001)
    assert latest_job(client)["status"] == "completed"
    assert len(llm.calls) == 1
    assert service.status()["cards_ready"] == 1
    assert service._processing_scope() == "model_external"


@pytest.mark.parametrize("config", [
    ScheduleConfig(enabled=False, auto_import_enabled=False, process_cards=True),
    ScheduleConfig(enabled=True, process_cards=False),
    ScheduleConfig(enabled=True, process_cards=True, paused=True),
])
def test_cli_optin_requires_general_automation(memory_client, tmp_path, config):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    put_record(service, source, "pending")
    select_cli(service)
    service.update_schedule(config)
    wait_job(client, service.start_cards(1, automatic=True)["job_id"])
    assert llm.calls == ()


def test_disabling_cli_optin_after_claim_refunds_attempt(memory_client, tmp_path, monkeypatch):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "pending")
    select_cli(service)
    enable_automation(client, headers)
    original = service.store.claim_card_attempt

    def disable_after_claim(*args, **kwargs):
        result = original(*args, **kwargs)
        select_cli(service, enabled=False)
        return result

    monkeypatch.setattr(service.store, "claim_card_attempt", disable_after_claim)
    wait_job(client, service.start_cards(1, automatic=True)["job_id"])
    assert llm.calls == ()
    assert service.store.get_record(identifier)["card_attempts"] == 0


def test_cli_optin_due_retries_preserve_attempt_budget(memory_client, tmp_path, monkeypatch):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "retry")
    select_cli(service)
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    llm.error = RuntimeError("Synthetic failure")
    service.update_schedule(ScheduleConfig(enabled=True, process_cards=True, batch_limit=1))
    service._next_reconcile = float("inf")
    wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    for instant, expected in ((1059, 1), (1060, 2), (1360, 3), (99999, 3)):
        clock[0] = instant
        service.tick(now=instant)
        await_idle(service)
        assert len(llm.calls) == expected
    assert service.record(identifier)["record"]["card_waiting_for_user"]


def test_disabling_cli_optin_at_final_admission_never_sends_request(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "pending")
    select_cli(service)
    enable_automation(client, headers)

    def queued_generation(record, before_request):
        select_cli(service, enabled=False)
        before_request()
        return llm.generate(record)

    monkeypatch.setattr(llm, "generate_with_admission", queued_generation, raising=False)
    wait_job(client, service.start_cards(1, automatic=True)["job_id"])
    assert llm.calls == ()
    assert service.store.get_record(identifier)["card_attempts"] == 0


def test_disabling_cli_optin_between_records_stops_next_launch(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    for index in range(2):
        put_record(service, source, str(index))
    select_cli(service)
    enable_automation(client, headers)
    original = llm.generate

    def generate_and_disable(record):
        result = original(record)
        select_cli(service, enabled=False)
        return result

    monkeypatch.setattr(llm, "generate", generate_and_disable)
    wait_job(client, service.start_cards(2, automatic=True)["job_id"])
    assert len(llm.calls) == 1
    assert service.status()["cards_ready"] == 1
    assert service.status()["cards_pending"] == 1


def test_cli_optin_excludes_local_only_sources(memory_client, tmp_path):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "private-notes")
    service = client.app.state.memory_service
    service.store.set_source_policy(source["id"], SourcePolicy(
        local_model_enabled=True, external_model_enabled=False,
    ))
    wait_job(client, service.start_import(source_id=source["id"])["job_id"])
    select_cli(service)
    enable_automation(client, headers)
    wait_job(client, service.start_cards(1, automatic=True)["job_id"])
    assert llm.calls == ()


@pytest.mark.parametrize("foreign", [False, True])
def test_restore_always_requires_fresh_cli_optin(tmp_path, monkeypatch, foreign):
    from brain_settings import backup_restore

    original = Settings(provider="gigacode_cli", base_url="gigacode://local",
                        model_id="default", gigacode_auto_cards=True)
    monkeypatch.setattr(backup_restore, "validate_configs", lambda _: (
        original, (), ScheduleConfig(enabled=True, process_cards=True),
    ))
    _, payloads = backup_restore._restored_configs(tmp_path, foreign=foreign)
    assert json.loads(payloads["settings.json"])["gigacode_auto_cards"] is False
