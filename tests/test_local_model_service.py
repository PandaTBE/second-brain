"""Local card generation retains evidence, admission and bounded retry semantics."""

import json

import pytest
from conftest import FakeTransport
from test_llm_cards import EVIDENCE, cards_fixture, valid_card
from test_memory_service_api import memory_client
from test_qwen_providers import OLLAMA, NoVault

from brain_settings.errors import AppError
from brain_settings.llm_cards import CardGenerationError, LLMCardGenerator
from brain_settings.providers import SaveRequest
from brain_settings.service import SettingsService

__all__ = ["memory_client", "cards_fixture"]


class LocalChat:
    def __init__(self):
        self.checked = ()
        self.calls = ()
        self.error = None
        self.card = valid_card()

    def ensure_local_model(self, model_id):
        self.checked = (*self.checked, model_id)
        if self.error:
            raise self.error

    def generate(self, payload, timeout, *, before_request=None):
        if before_request is not None:
            before_request()
        self.calls = (*self.calls, (payload, timeout))
        return {"choices": [{"message": {"content": json.dumps(self.card)},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9}}


def local_generator(tmp_path):
    local = LocalChat()
    service = SettingsService(tmp_path, vault=NoVault(), transport=FakeTransport(),
                              local_client=local)
    service.save(SaveRequest(provider="ollama", base_url=OLLAMA, model_id="qwen3:8b"))
    cloud = FakeTransport()
    generator = LLMCardGenerator(service, transport=cloud)
    return service, local, cloud, generator


def test_local_generation_never_reads_cloud_credentials_or_uses_public_transport(tmp_path):
    service, local, cloud, generator = local_generator(tmp_path)
    result = generator.generate({"text": EVIDENCE})
    assert result["card"] == valid_card() and result["usage"]["total_tokens"] == 9
    assert local.checked == ("qwen3:8b",) and len(local.calls) == 1
    payload, timeout = local.calls[0]
    assert payload["model"] == "qwen3:8b" and payload["max_tokens"] == 1024
    assert timeout == 180 and cloud.calls == () and service.transport.calls == ()
    assert generator.concurrency_limit() == 1


def test_local_missing_model_is_an_unsent_failure_with_no_generation(tmp_path):
    _, local, cloud, generator = local_generator(tmp_path)
    local.error = AppError("local_model_required", "Загрузите локальную модель.", 400)
    with pytest.raises(CardGenerationError) as caught:
        generator.generate({"text": EVIDENCE})
    assert caught.value.code == "local_model_required" and caught.value.request_sent is False
    assert local.calls == () and cloud.calls == ()


def test_local_admission_checks_after_catalog_before_chat(tmp_path):
    _, local, _, generator = local_generator(tmp_path)

    def refuse():
        assert local.checked == ("qwen3:8b",)
        raise AppError("provider_paused", "На паузе.", 429)

    with pytest.raises(CardGenerationError) as caught:
        generator.generate({"text": EVIDENCE}, before_request=refuse)
    assert caught.value.request_sent is False and local.calls == ()


def test_local_generated_evidence_keeps_strict_source_validation(tmp_path):
    _, local, _, generator = local_generator(tmp_path)
    local.card = {**valid_card(), "atoms": [
        {"kind": "fact", "text": "Invented", "evidence": "Invented"},
    ]}
    with pytest.raises(CardGenerationError) as caught:
        generator.generate({"text": EVIDENCE})
    assert caught.value.code == "card_evidence_missing" and caught.value.request_sent is True
    assert caught.value.usage["total_tokens"] == 9 and len(local.calls) == 1


def test_local_concurrency_cap_is_effective_without_rewriting_saved_target(
    memory_client, monkeypatch,
):
    client, _, llm = memory_client
    service = client.app.state.memory_service
    config = service.schedule_store.load().model_copy(update={"card_concurrency": 10})
    service.update_schedule(config)
    monkeypatch.setattr(llm, "concurrency_limit", lambda: 1, raising=False)
    status = service.status()["provider_backoff"]
    assert status["effective_concurrency"] == 1
    assert status["configured_concurrency"] == 10 and status["execution_mode"] == "local"
    assert service.schedule_store.load().card_concurrency == 10


def test_cloud_settings_keep_unbounded_requested_concurrency(cards_fixture):
    _, _, generator, _ = cards_fixture
    assert generator.concurrency_limit() is None


def test_real_local_preflight_failure_refunds_record_budget(memory_client, tmp_path):
    from test_memory_service_api import add_text_source, wait_job
    from test_memory_service_guards import put_record

    client, headers, _ = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "local-unavailable")
    _, local, _, generator = local_generator(tmp_path / "settings")
    local.error = AppError("local_model_required", "Загрузите локальную модель.", 400)
    service.generator = generator
    job = wait_job(client, service.start_cards(1)["job_id"])
    record = service.store.get_record(identifier)
    assert job["status"] == "error"
    assert record["card_attempts"] == 0 and record["card_status"] == "pending"
    assert local.calls == ()


def test_actual_local_failures_keep_three_attempts_and_single_worker(
    memory_client, tmp_path, monkeypatch,
):
    from test_card_retry_service import await_idle
    from test_memory_service_api import add_text_source, wait_job
    from test_memory_service_guards import put_record

    from brain_settings.background import ScheduleConfig

    client, headers, _ = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "local-failure")
    _, local, _, generator = local_generator(tmp_path / "settings")
    service.generator = generator
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    service.update_schedule(ScheduleConfig(enabled=True, process_cards=True,
                                          auto_import_enabled=False, batch_limit=1,
                                          card_concurrency=10))
    service._next_reconcile = float("inf")

    def fail_chat(payload, timeout, *, before_request=None):
        local.calls = (*local.calls, (payload, timeout))
        raise AppError("network_error", "Не удалось выполнить локальную генерацию.", 502)

    local.generate = fail_chat
    job = wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    assert job["concurrency"] == 1
    for instant in (1060, 1360, 2000):
        clock[0] = instant
        service.tick(now=instant)
        await_idle(service)
    record = service.store.get_record(identifier)
    assert record["card_attempts"] == 3 and record["card_waiting_for_user"]
    assert len(local.calls) == 3
    assert service.schedule_store.load().card_concurrency == 10


@pytest.mark.parametrize("model_id,uses_parameter", [
    ("qwen-plus", True), ("qwen3.7-max", True),
    ("qwen3-coder-plus", False), ("qwq-32b", False),
])
def test_qwen_cloud_only_disables_thinking_for_documented_hybrid_models(
    tmp_path, model_id, uses_parameter,
):
    from conftest import FAKE_TOKEN, MemoryVault
    from test_llm_cards import FakeChat
    from test_qwen_providers import QWEN

    service = SettingsService(tmp_path, vault=MemoryVault())
    service.save(SaveRequest(provider="qwen", base_url=QWEN, model_id=model_id, token=FAKE_TOKEN))
    chat = FakeChat()
    LLMCardGenerator(service, transport=chat).generate({"text": EVIDENCE})
    payload = chat.calls[0][2]
    assert ("enable_thinking" in payload) is uses_parameter
    if uses_parameter:
        assert payload["enable_thinking"] is False
    assert payload["max_tokens"] == 1024 and "response_format" not in payload


def test_provider_switch_does_not_migrate_an_existing_cloud_window_to_local(
    memory_client, tmp_path, monkeypatch,
):
    import threading

    from test_card_retry_service import await_idle
    from test_memory_service_api import add_text_source, wait_job
    from test_memory_service_guards import put_record

    from brain_settings.background import ScheduleConfig

    client, headers, fake = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifiers = [put_record(service, source, str(index)) for index in range(20)]
    service.update_schedule(ScheduleConfig(enabled=False, process_cards=False,
                                          auto_import_enabled=False, card_concurrency=10))
    mode = {"scope": "cloud"}
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    calls = []

    def generate(record, before_request):
        before_request()
        scope = mode["scope"]
        with lock:
            calls.append(scope)
            if calls.count("cloud") == 10:
                entered.set()
        if scope == "cloud":
            assert release.wait(5)
        return {"card": valid_card(), "model": scope}

    monkeypatch.setattr(fake, "provider_scope", lambda: mode["scope"], raising=False)
    monkeypatch.setattr(fake, "concurrency_limit",
                        lambda: 1 if mode["scope"] == "local" else None, raising=False)
    monkeypatch.setattr(fake, "generate_with_admission", generate, raising=False)
    monkeypatch.setattr(fake, "is_current", lambda result: result["model"] == mode["scope"])
    job_id = service.start_cards(20)["job_id"]
    try:
        assert entered.wait(5)
        mode["scope"] = "local"
    finally:
        release.set()
    job = wait_job(client, job_id)
    await_idle(service)
    assert job["concurrency"] == 10 and calls == ["cloud"] * 10
    attempts = [service.store.get_record(identifier)["card_attempts"] for identifier in identifiers]
    assert sorted(attempts) == [0] * 10 + [1] * 10
    next_job = wait_job(client, service.start_cards(10)["job_id"])
    assert next_job["concurrency"] == 1 and calls.count("local") == 10


def test_local_chat_receives_final_admission_callback_after_early_guard(tmp_path):
    from brain_settings.provider_backoff import paused_error

    _, local, _, generator = local_generator(tmp_path)
    admissions = []

    def before_request():
        admissions.append("checked")
        if len(admissions) == 2:
            raise paused_error()

    with pytest.raises(CardGenerationError) as caught:
        generator.generate({"text": EVIDENCE}, before_request=before_request)
    assert admissions == ["checked", "checked"]
    assert caught.value.request_sent is False and local.calls == ()


@pytest.mark.parametrize("original_scope", [None, "cloud"])
def test_batch_captures_scope_before_sampling_current_concurrency(
    memory_client, tmp_path, monkeypatch, original_scope,
):
    from test_card_retry_service import await_idle
    from test_memory_service_api import add_text_source, wait_job
    from test_memory_service_guards import put_record

    client, headers, fake = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    identifier = put_record(service, source, "scope-race")
    mode = {"scope": original_scope}
    monkeypatch.setattr(fake, "provider_scope", lambda: mode["scope"], raising=False)
    original_status = service._provider_status

    def switching_status(config=None):
        state = original_status(config)
        mode["scope"] = "local"
        return state

    monkeypatch.setattr(service, "_provider_status", switching_status)
    job = wait_job(client, service.start_cards(1)["job_id"])
    await_idle(service)
    assert fake.calls == () and job["stats"]["ready"] == 0
    assert service.store.get_record(identifier)["card_attempts"] == 0
