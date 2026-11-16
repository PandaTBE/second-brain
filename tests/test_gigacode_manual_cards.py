from test_memory_service_api import add_text_source, wait_job
from test_memory_service_api import memory_client as memory_client
from test_memory_service_automation import configure_codex, latest_job, write_session
from test_settings_api import assert_success

from brain_settings.errors import AppError


def select_cli(service, monkeypatch):
    settings = service.settings_service.store.load().model_copy(update={"provider": "gigacode_cli"})
    monkeypatch.setattr(service.settings_service.store, "load", lambda: settings)


def enable_automation(client, headers):
    assert_success(client.post("/api/memory/schedule", json={
        "enabled": True, "auto_import_enabled": True, "process_cards": True,
        "batch_limit": 2, "interval_minutes": 15,
    }, headers=headers))


def test_cli_auto_import_preserves_text_until_explicit_cards_job(
    memory_client, tmp_path, monkeypatch,
):
    client, headers, llm = memory_client
    directory = tmp_path / "sessions"
    configure_codex(client, headers, directory)
    write_session(directory / "session.jsonl", timestamp=1000, completed=True)
    service = client.app.state.memory_service
    select_cli(service, monkeypatch)
    enable_automation(client, headers)
    service.tick(now=1001)
    assert latest_job(client)["status"] == "completed"
    status = assert_success(client.get("/api/memory/status"))
    assert status["records"] == 1
    assert status["cards_ready"] == 0
    assert status["cards_pending"] == 1
    assert llm.calls == ()
    job = service.start_cards(limit=1)
    assert wait_job(client, job["job_id"])["status"] == "completed"
    assert len(llm.calls) == 1


def test_cli_automatic_start_is_rejected_before_creating_job(memory_client, monkeypatch):
    client, headers, llm = memory_client
    service = client.app.state.memory_service
    select_cli(service, monkeypatch)
    enable_automation(client, headers)
    try:
        service.start_cards(limit=1, automatic=True)
    except AppError as error:
        assert error.code == "cli_manual_only"
    else:
        raise AssertionError("Automatic CLI generation was admitted")
    assert llm.calls == ()
    assert service.status()["jobs"] == []


def test_cli_background_batch_cannot_bypass_manual_gate(memory_client, tmp_path, monkeypatch):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    wait_job(client, service.start_import(source_id=source["id"])["job_id"])
    select_cli(service, monkeypatch)
    enable_automation(client, headers)
    monkeypatch.setattr(service, "_update", lambda *args, **kwargs: None)
    service._cards("synthetic", 1, False, automatic_retries=True)
    assert llm.calls == ()
    assert service.status()["cards_pending"] == 1


def test_cli_background_record_is_not_claimed(memory_client, tmp_path, monkeypatch):
    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    wait_job(client, service.start_import(source_id=source["id"])["job_id"])
    select_cli(service, monkeypatch)
    record = service.store.pending(limit=1, allowed_source_ids=service._allowed())[0]
    result = service._process_card(record, expected_scope="injected", automatic=True)
    assert result["stale"] == 1
    assert llm.calls == ()
    assert service.status()["cards_pending"] == 1


def test_cli_retry_automation_remains_disabled(memory_client, monkeypatch):
    client, headers, _ = memory_client
    service = client.app.state.memory_service
    select_cli(service, monkeypatch)
    enable_automation(client, headers)
    assert service._retry_processing_state(service.schedule_store.load()) == "automation_disabled"
    assert service._processing_scope() == "model_external"


def test_cli_manual_generation_respects_local_only_source(memory_client, tmp_path, monkeypatch):
    from brain_settings.source_policy import SourcePolicy

    client, headers, llm = memory_client
    source = add_text_source(client, headers, tmp_path / "private-notes")
    service = client.app.state.memory_service
    service.store.set_source_policy(source["id"], SourcePolicy(
        local_model_enabled=True, external_model_enabled=False,
    ))
    wait_job(client, service.start_import(source_id=source["id"])["job_id"])
    select_cli(service, monkeypatch)
    job = wait_job(client, service.start_cards(limit=1)["job_id"])
    assert job["status"] == "completed"
    assert llm.calls == ()
    assert service.status()["records"] == 1


def test_cli_serial_execution_is_not_labelled_local(memory_client, monkeypatch):
    client, _, llm = memory_client
    service = client.app.state.memory_service
    select_cli(service, monkeypatch)
    monkeypatch.setattr(llm, "concurrency_limit", lambda: 1, raising=False)
    status = service._provider_status()
    assert status["effective_concurrency"] == 1
    assert status["execution_mode"] == "cloud"
