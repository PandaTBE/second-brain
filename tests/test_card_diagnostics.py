import json

import pytest
from conftest import FAKE_TOKEN, FakeTransport, MemoryVault
from test_llm_cards import EVIDENCE, valid_card

from brain_settings.errors import AppError
from brain_settings.llm_cards import CardGenerationError, LLMCardGenerator, validate_card
from brain_settings.providers import SaveRequest
from brain_settings.service import SettingsService

USAGE = {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}


class ResponseChat:
    def __init__(self):
        self.response = {
            "choices": [{"message": {"content": json.dumps(valid_card())}}], "usage": USAGE,
        }
        self.calls = []

    def post_json(self, base_url, path, payload, token=None, timeout=45):
        self.calls.append(payload)
        return self.response


@pytest.fixture
def diagnostic_fixture(tmp_path):
    settings = SettingsService(tmp_path, vault=MemoryVault(), transport=FakeTransport())
    settings.save(SaveRequest(
        provider="openrouter", base_url="https://openrouter.ai/api/v1",
        model_id="synthetic/cheap", model_name="Synthetic", token=FAKE_TOKEN,
    ))
    chat = ResponseChat()
    generator = LLMCardGenerator(settings, transport=chat)
    return settings, chat, generator, {"title": "Решение", "text": EVIDENCE}


@pytest.mark.parametrize(("change", "code"), [
    ({"summary": ""}, "card_schema_invalid"),
    ({"summary": " "}, "card_schema_invalid"),
    ({"atoms": [{"kind": "fact", "text": " ", "evidence": EVIDENCE}]},
     "card_schema_invalid"),
    ({"unexpected": "synthetic-private-value"}, "card_schema_invalid"),
    ({"atoms": [{"kind": "fact", "text": "Absent", "evidence": "Absent"}]},
     "card_evidence_missing"),
    ({"atoms": [{"kind": "fact", "text": "Absent", "evidence": EVIDENCE}]},
     "card_text_outside_evidence"),
])
def test_schema_and_literal_evidence_failures_have_safe_distinct_codes(change, code):
    with pytest.raises(AppError) as caught:
        validate_card({**valid_card(), **change}, EVIDENCE)
    assert caught.value.code == code
    assert "synthetic-private-value" not in str(caught.value)
    assert EVIDENCE not in str(caught.value)


def test_no_evidence_can_be_reported_with_empty_atoms():
    card = {**valid_card(), "atoms": []}
    assert validate_card(card, EVIDENCE) == card


@pytest.mark.parametrize(("choice", "code"), [
    ({"finish_reason": "length", "message": {"content": "{"}}, "card_output_truncated"),
    ({"finish_reason": "content_filter", "message": {"content": None}}, "card_model_refused"),
    ({"message": {"content": None, "refusal": "synthetic-private-value"}},
     "card_model_refused"),
    ({"message": {"content": "{"}}, "card_json_invalid"),
    ({"message": {"content": "9" * 5000}}, "card_json_invalid"),
    ({"message": {"content": []}}, "card_response_invalid"),
    ({"message": {"content": "x" * 32_001}}, "card_response_invalid"),
    ({"message": {}}, "card_response_invalid"),
    ({}, "card_response_invalid"),
    (None, "card_response_invalid"),
])
def test_response_diagnostics_preserve_usage_without_echo_or_paid_retry(
    diagnostic_fixture, choice, code,
):
    _, chat, generator, record = diagnostic_fixture
    chat.response = {"choices": [choice], "usage": USAGE}
    with pytest.raises(CardGenerationError) as caught:
        generator.generate(record)
    assert caught.value.code == code
    assert caught.value.usage == USAGE
    assert caught.value.request_sent is True
    assert "synthetic-private-value" not in str(caught.value)
    assert len(chat.calls) == 1


@pytest.mark.parametrize("response", [None, [], {}, {"choices": []}, {"choices": {}}])
def test_invalid_response_envelopes_are_classified(diagnostic_fixture, response):
    _, chat, generator, record = diagnostic_fixture
    chat.response = response
    with pytest.raises(CardGenerationError) as caught:
        generator.generate(record)
    assert caught.value.code == "card_response_invalid"
    assert caught.value.usage == {}


def test_explicit_stop_and_legacy_missing_finish_reason_are_compatible(diagnostic_fixture):
    _, chat, generator, record = diagnostic_fixture
    assert generator.generate(record)["card"] == valid_card()
    chat.response["choices"][0]["finish_reason"] = "stop"
    assert generator.generate(record)["prompt_version"] == "3"


def test_prompt_targets_small_cards_and_keeps_existing_budget(diagnostic_fixture):
    _, chat, generator, record = diagnostic_fixture
    generator.generate(record)
    payload = chat.calls[0]
    prompt = payload["messages"][0]["content"]
    assert payload["max_tokens"] == 1024
    assert "response_format" not in payload
    assert "temperature" not in payload
    for target in ("1–3", "5–8", "2–3", "1500", "2000", "100–300"):
        assert target in prompt


def test_provider_scope_uses_only_normalized_configuration(diagnostic_fixture):
    settings, _, generator, _ = diagnostic_fixture
    scope = generator.provider_scope()
    assert len(scope) == 64
    original_get = settings.vault.get
    settings.vault.get = lambda *_: pytest.fail("Scope must not read credentials")
    assert generator.provider_scope() == scope
    settings.vault.get = original_get
    settings.save(SaveRequest(
        provider="openrouter", base_url="https://openrouter.ai/api/v1",
        model_id="synthetic/different", model_name="Different", token=None,
    ))
    assert generator.provider_scope() != scope


def test_unconfigured_generator_has_no_provider_scope(tmp_path):
    generator = LLMCardGenerator(SettingsService(tmp_path, vault=MemoryVault()))
    assert generator.provider_scope() is None


def test_admission_runs_once_after_credential_read_before_post(diagnostic_fixture):
    settings, chat, generator, record = diagnostic_fixture
    original_get = settings.vault.get
    events = []

    def get(scope):
        events.append("credential")
        return original_get(scope)

    def admit():
        assert not chat.calls
        events.append("admission")

    settings.vault.get = get
    generator.generate_with_admission(record, admit)
    assert events == ["credential", "admission"]
    assert len(chat.calls) == 1


def test_admission_denial_refunds_request_and_preserves_safe_metadata(diagnostic_fixture):
    _, chat, generator, record = diagnostic_fixture
    error = CardGenerationError(
        "rate_limited", "Подождите перед следующим запросом.", 429, retry_after=15,
        provider_code="insufficient_quota", requires_user_action=True,
    )

    def deny():
        raise error

    with pytest.raises(CardGenerationError) as caught:
        generator.generate_with_admission(record, deny)
    assert caught.value.request_sent is False
    assert caught.value.retry_after == 15
    assert caught.value.provider_code == "insufficient_quota"
    assert caught.value.requires_user_action is True
    assert caught.value.usage == {}
    assert chat.calls == []


def test_unexpected_admission_failure_is_safe_and_never_sends(diagnostic_fixture):
    _, chat, generator, record = diagnostic_fixture

    def deny():
        raise RuntimeError("synthetic-private-value")

    with pytest.raises(CardGenerationError) as caught:
        generator.generate_with_admission(record, deny)
    assert caught.value.request_sent is False
    assert "synthetic-private-value" not in str(caught.value)
    assert chat.calls == []


def test_generation_error_positional_contract_is_preserved():
    error = CardGenerationError("synthetic", "Safe message", 429, USAGE)
    assert error.usage == USAGE
    assert error.request_sent is True
    assert error.retry_after is None
    assert error.provider_code is None
    assert error.requires_user_action is False


def test_provider_failure_preserves_backoff_metadata(diagnostic_fixture):
    _, chat, generator, record = diagnostic_fixture
    error = AppError("rate_limited", "Провайдер временно ограничил запросы.", 429)
    error.retry_after = 15
    error.provider_code = "insufficient_quota"
    error.requires_user_action = True
    error.request_sent = True

    def fail(*args, **kwargs):
        raise error

    chat.post_json = fail
    with pytest.raises(CardGenerationError) as caught:
        generator.generate(record)
    assert caught.value.code == "rate_limited"
    assert caught.value.retry_after == 15
    assert caught.value.provider_code == "insufficient_quota"
    assert caught.value.requires_user_action is True
    assert caught.value.request_sent is True
    assert caught.value.usage == {}


def test_service_admission_pause_is_refundable(diagnostic_fixture):
    _, chat, generator, record = diagnostic_fixture

    def pause():
        raise AppError("provider_paused", "Ожидание провайдера.", 429)

    with pytest.raises(CardGenerationError) as caught:
        generator.generate_with_admission(record, pause)
    assert caught.value.code == "provider_paused"
    assert caught.value.request_sent is False
    assert chat.calls == []


@pytest.mark.parametrize(("stage", "code"), [
    ("configuration", "model_required"),
    ("credential", "keychain_unavailable"),
    ("token", "token_required"),
    ("payload", "card_preparation_failed"),
])
def test_all_pre_request_failures_are_refundable(diagnostic_fixture, stage, code):
    settings, chat, generator, record = diagnostic_fixture

    def fail_configuration():
        raise AppError("model_required", "Сначала выберите модель.")

    def fail_credential(*args):
        raise RuntimeError("synthetic-private-value")

    class InvalidText:
        def __str__(self):
            raise RuntimeError("synthetic-private-value")

    if stage == "configuration":
        generator._configuration = fail_configuration
    elif stage == "credential":
        settings.vault.get = fail_credential
    elif stage == "token":
        settings.vault.get = lambda *_: None
    else:
        record = {**record, "text": InvalidText()}

    with pytest.raises(CardGenerationError) as caught:
        generator.generate_with_admission(record, lambda: pytest.fail("Admission was too early"))
    assert caught.value.code == code
    assert caught.value.request_sent is False
    assert caught.value.usage == {}
    assert "synthetic-private-value" not in str(caught.value)
    assert chat.calls == []
