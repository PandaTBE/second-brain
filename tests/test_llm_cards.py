import json

import pytest
from conftest import FAKE_TOKEN, FakeTransport, MemoryVault

from brain_settings.errors import AppError
from brain_settings.llm_cards import LLMCardGenerator, validate_card
from brain_settings.providers import SaveRequest
from brain_settings.service import SettingsService

EVIDENCE = "Решили использовать SQLite для локальной памяти."


def valid_card():
    return {
        "title": "Локальная память", "summary": "Выбран SQLite.",
        "keywords": ["SQLite", "память"], "questions": ["Как хранить память?"],
        "atoms": [{"kind": "decision", "text": EVIDENCE, "evidence": EVIDENCE}],
    }


class FakeChat:
    def __init__(self, card=None, error=None):
        self.card = valid_card() if card is None else card
        self.error = error
        self.calls = ()

    def post_json(self, base_url, path, payload, token=None, timeout=45):
        self.calls = (*self.calls, (base_url, path, payload, token, timeout))
        if self.error:
            raise self.error
        return {"choices": [{"message": {"content": json.dumps(self.card)}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}}


@pytest.fixture
def cards_fixture(tmp_path):
    settings = SettingsService(tmp_path, vault=MemoryVault(), transport=FakeTransport())
    settings.save(SaveRequest(
        provider="openrouter", base_url="https://openrouter.ai/api/v1",
        model_id="synthetic/cheap", model_name="Synthetic", token=FAKE_TOKEN,
    ))
    chat = FakeChat()
    generator = LLMCardGenerator(settings, transport=chat)
    record = {"id": "synthetic-record", "revision": 1, "title": "Решение", "text": EVIDENCE}
    return settings, chat, generator, record


def test_card_call_is_single_bounded_request_and_preserves_actual_usage(cards_fixture):
    _, chat, generator, record = cards_fixture
    result = generator.generate(record)
    assert result["card"] == valid_card()
    assert result["model"] == "synthetic/cheap"
    assert result["usage"] == {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}
    assert len(chat.calls) == 1
    _, path, payload, token, timeout = chat.calls[0]
    assert path == "/chat/completions"
    assert payload["model"] == "synthetic/cheap"
    assert payload["max_tokens"] == 1024
    assert token == FAKE_TOKEN
    assert timeout <= 60
    assert "response_format" not in payload
    assert FAKE_TOKEN not in json.dumps(payload)


def test_generation_redacts_secret_like_source_text_before_outbound(cards_fixture):
    _, chat, generator, record = cards_fixture
    raw_secret = "sk-proj-" + "SyntheticSecret" * 5
    generator.generate({**record, "text": EVIDENCE + "\nOPENAI_API_KEY=" + raw_secret})
    payload = chat.calls[0][2]
    assert raw_secret not in json.dumps(payload)


@pytest.mark.parametrize("card", [
    {**valid_card(), "unexpected": "field"},
    {**valid_card(), "summary": ""},
    {**valid_card(), "atoms": [{"kind": "decision", "text": "Invented", "evidence": "Absent"}]},
    {**valid_card(), "atoms": [{"kind": "decision", "text": "Invented", "evidence": EVIDENCE}]},
])
def test_card_schema_and_extractive_evidence_reject_unsupported_atoms(card):
    with pytest.raises(AppError):
        validate_card(card, EVIDENCE)


def test_generation_failure_never_retries_or_echoes_provider_secret(cards_fixture):
    _, chat, generator, record = cards_fixture
    chat.error = RuntimeError("Synthetic provider echoed " + FAKE_TOKEN)
    with pytest.raises(AppError) as caught:
        generator.generate(record)
    assert len(chat.calls) == 1
    assert FAKE_TOKEN not in str(caught.value)


def test_invalid_card_preserves_actual_reported_usage(cards_fixture):
    _, chat, generator, record = cards_fixture
    chat.card = {**valid_card(), "summary": ""}
    with pytest.raises(AppError) as caught:
        generator.generate(record)
    assert caught.value.usage == {
        "prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160,
    }
    assert len(chat.calls) == 1


def test_changed_model_invalidates_late_result(cards_fixture):
    settings, _, generator, record = cards_fixture
    result = generator.generate(record)
    settings.save(SaveRequest(
        provider="openrouter", base_url="https://openrouter.ai/api/v1",
        model_id="synthetic/new-model", model_name="New", token=None,
    ))
    assert generator.is_current(result) is False


def test_missing_configuration_fails_before_any_paid_request(tmp_path):
    chat = FakeChat()
    generator = LLMCardGenerator(SettingsService(tmp_path, vault=MemoryVault()), transport=chat)
    with pytest.raises(AppError):
        generator.generate({"id": "record", "revision": 1, "title": "Title", "text": EVIDENCE})
    assert chat.calls == ()
