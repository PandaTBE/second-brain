"""Manual OpenAI-compatible connections do not depend on a provider catalog."""

import json

import pytest
from conftest import FAKE_TOKEN, MemoryVault
from test_llm_cards import EVIDENCE, valid_card

from brain_settings.errors import AppError
from brain_settings.llm_cards import LLMCardGenerator
from brain_settings.providers import SaveRequest, provider_metadata
from brain_settings.service import SettingsService


class NoCatalog:
    def request(self, *args, **kwargs):
        raise AssertionError("Saving a manual model must not query its catalog")


class ManualChat:
    def __init__(self):
        self.calls = ()

    def post_json(self, base_url, path, payload, token=None, timeout=45):
        self.calls = (*self.calls, (base_url, path, payload, token))
        return {"choices": [{"message": {"content": json.dumps(valid_card())}}]}


def test_custom_provider_explains_api_token_and_exact_model_id():
    metadata = next(row for row in provider_metadata() if row["id"] == "custom")
    assert metadata["label"] == "Свой API · OpenAI-совместимый"
    assert metadata["description"] == "Базовый HTTPS-адрес, API-токен и точный ID текстовой модели"
    assert metadata["requires_token"] is True


def test_manual_custom_model_saves_without_catalog_and_generates_using_exact_id(tmp_path):
    service = SettingsService(tmp_path, vault=MemoryVault(), transport=NoCatalog())
    address = "https://models.example.com/proxy/v1"
    model_id = "team/archive-card-experimental:2026-11"
    saved = service.save(SaveRequest(provider="custom", base_url=address,
                                     model_id=model_id, token=FAKE_TOKEN))
    assert saved["settings"]["model_id"] == model_id
    assert saved["settings"]["base_url"] == address
    assert saved["token_saved"] is True
    chat = ManualChat()
    result = LLMCardGenerator(service, transport=chat).generate({"text": EVIDENCE})
    assert result["card"] == valid_card() and len(chat.calls) == 1
    base_url, path, payload, token = chat.calls[0]
    assert base_url == address and path == "/chat/completions"
    assert payload["model"] == model_id and token == FAKE_TOKEN


def test_custom_credentials_are_isolated_by_api_address(tmp_path):
    service = SettingsService(tmp_path, vault=MemoryVault(), transport=NoCatalog())
    first = SaveRequest(provider="custom", base_url="https://first.example.com/v1",
                        model_id="manual/text-model", token=FAKE_TOKEN)
    service.save(first)
    second = first.model_copy(update={"base_url": "https://second.example.com/v1", "token": None})
    with pytest.raises(AppError) as caught:
        service.save(second)
    assert caught.value.code == "token_required"
    service.save(first.model_copy(update={"token": None}))
    chat = ManualChat()
    LLMCardGenerator(service, transport=chat).generate({"text": EVIDENCE})
    assert chat.calls[0][0] == first.base_url and chat.calls[0][3] == FAKE_TOKEN


@pytest.mark.parametrize("address", [
    "http://api.example.com/v1", "http://127.0.0.1:11434/v1", "https://192.168.1.20/v1",
])
def test_custom_manual_connection_does_not_allow_http_or_private_networks(tmp_path, address):
    service = SettingsService(tmp_path, vault=MemoryVault(), transport=NoCatalog())
    with pytest.raises(AppError) as caught:
        service.save(SaveRequest(provider="custom", base_url=address,
                                 model_id="manual/text-model", token=FAKE_TOKEN))
    assert caught.value.code == "invalid_endpoint"
