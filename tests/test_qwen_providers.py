"""Qwen cloud and the narrowly scoped local provider require different credentials."""

import pytest
from conftest import FAKE_TOKEN, FakeTransport, MemoryVault

from brain_settings.errors import AppError
from brain_settings.providers import (
    EndpointRequest,
    SaveRequest,
    SelectionRequest,
    normalize_endpoint,
    provider_metadata,
)
from brain_settings.service import SettingsService

QWEN = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
OLLAMA = "http://127.0.0.1:11434/v1"


class NoVault:
    available = False

    def get(self, *args):
        raise AssertionError("Local models must never access Keychain")

    set = delete = get


class LocalModels:
    def __init__(self):
        self.calls = ()
        self.models = [{"id": "qwen3:8b", "name": "qwen3:8b", "input_price": 0,
                        "output_price": 0, "context_length": None}]

    def catalog(self):
        self.calls = (*self.calls, "catalog")
        return self.models


def local_service(tmp_path):
    client = LocalModels()
    service = SettingsService(tmp_path, vault=NoVault(), transport=FakeTransport(),
                              local_client=client)
    return service, client


def test_provider_metadata_explains_cloud_and_local_credentials():
    metadata = {row["id"]: row for row in provider_metadata()}
    assert metadata["qwen"]["base_url"] == QWEN
    assert metadata["qwen"]["requires_token"] is True
    assert metadata["ollama"]["base_url"] == OLLAMA
    assert metadata["ollama"]["requires_token"] is False
    assert normalize_endpoint("qwen", QWEN + "/") == QWEN
    assert normalize_endpoint("ollama", OLLAMA + "/") == OLLAMA


@pytest.mark.parametrize("provider,address", [
    ("custom", OLLAMA), ("qwen", OLLAMA),
    ("ollama", "http://localhost:11434/v1"),
    ("ollama", "http://127.0.0.1:11435/v1"),
    ("ollama", "http://127.0.0.2:11434/v1"),
    ("ollama", "http://127.0.0.1:11434/v1?key=secret"),
    ("ollama", "http://127.0.0.1:11434/v1#fragment"),
    ("ollama", "http://user@127.0.0.1:11434/v1"),
    ("ollama", "http://127.0.0.1:11434/v1/../api"),
    ("ollama", "https://example.com/v1"),
])
def test_local_provider_is_not_a_general_local_network_exception(provider, address):
    with pytest.raises(AppError) as caught:
        normalize_endpoint(provider, address)
    assert caught.value.code == "invalid_endpoint"


def test_local_save_bootstrap_credentials_and_delete_never_open_keychain(tmp_path):
    service, client = local_service(tmp_path)
    response = service.save(SaveRequest(provider="ollama", base_url=OLLAMA, model_id="qwen3:8b"))
    assert response["settings"]["provider"] == "ollama" and response["token_saved"] is False
    assert service.bootstrap("synthetic-csrf")["token_saved"] is False
    scope = EndpointRequest(provider="ollama", base_url=OLLAMA)
    assert service.credential_status(scope) == {"token_saved": False}
    assert service.delete_token(scope) == {"token_saved": False}
    assert client.calls == () and service.transport.calls == ()


@pytest.mark.parametrize("action", ["save", "models", "test_connection"])
def test_local_operations_reject_supplied_credentials_without_reading_vault(tmp_path, action):
    service, _ = local_service(tmp_path)
    request_type = SaveRequest if action == "save" else SelectionRequest
    request = request_type(provider="ollama", base_url=OLLAMA,
                           model_id="qwen3:8b", token=FAKE_TOKEN)
    with pytest.raises(AppError) as caught:
        getattr(service, action)(request)
    assert caught.value.code == "local_token_forbidden"
    assert FAKE_TOKEN not in str(caught.value)


def test_local_catalog_and_check_do_not_start_generation_or_use_public_transport(tmp_path):
    service, client = local_service(tmp_path)
    request = SelectionRequest(provider="ollama", base_url=OLLAMA, model_id="qwen3:8b")
    result = service.models(request)
    assert result["models"] == client.models
    checked = service.test_connection(request)
    assert checked["status"] == "ok" and checked["model_available"] is True
    assert "Генерация не запускалась" in checked["message"]
    missing = service.test_connection(request.model_copy(update={"model_id": "missing:8b"}))
    assert missing["status"] == "warning" and missing["model_available"] is False
    assert service.transport.calls == ()


def test_qwen_cloud_still_requires_keychain_token_and_public_https(tmp_path):
    transport = FakeTransport(models={"output": {"models": []}})
    service = SettingsService(tmp_path, vault=MemoryVault(), transport=transport)
    with pytest.raises(AppError) as caught:
        service.save(SaveRequest(provider="qwen", base_url=QWEN, model_id="qwen-plus"))
    assert caught.value.code == "token_required"
    saved = service.save(SaveRequest(provider="qwen", base_url=QWEN,
                                     model_id="qwen-plus", token=FAKE_TOKEN))
    assert saved["token_saved"] is True
    service.models(SelectionRequest(provider="qwen", base_url=QWEN))
    assert transport.calls[-1] == (
        "https://dashscope-intl.aliyuncs.com/api/v1",
        "/models?providers=qwen&capabilities=TG&page_size=100&page_no=1", FAKE_TOKEN,
    )


def test_local_bootstrap_does_not_even_query_vault_availability(tmp_path):
    class UnavailableVault:
        @property
        def available(self):
            raise AssertionError("Local bootstrap must not query Keychain availability")

    service = SettingsService(tmp_path, vault=UnavailableVault(), local_client=LocalModels())
    service.save(SaveRequest(provider="ollama", base_url=OLLAMA, model_id="qwen3:8b"))
    result = service.bootstrap("synthetic-csrf")
    assert result["storage"]["available"] is False and result["warning"] is None


def test_local_catalog_obeys_existing_bounded_check_rate(tmp_path):
    service, client = local_service(tmp_path)
    request = SelectionRequest(provider="ollama", base_url=OLLAMA)
    for _ in range(30):
        service.models(request)
    with pytest.raises(AppError) as caught:
        service.models(request)
    assert caught.value.code == "local_rate_limited" and len(client.calls) == 30


def test_http_local_configuration_round_trip_has_no_keychain_or_public_http(tmp_path):
    from conftest import ORIGIN
    from fastapi.testclient import TestClient
    from test_settings_api import assert_success

    from brain_settings.app import create_app

    public = FakeTransport()
    application = create_app(data_dir=tmp_path, vault=NoVault(), transport=public)
    settings = application.state.memory_service.generator.settings_service
    local = LocalModels()
    settings._local_client = local
    with TestClient(application, base_url=ORIGIN) as client:
        csrf = assert_success(client.get("/api/bootstrap"))["csrf_token"]
        headers = {"Origin": ORIGIN, "X-Brain-CSRF": csrf}
        request = {"provider": "ollama", "base_url": OLLAMA, "model_id": "qwen3:8b"}
        assert client.post("/api/settings", json=request).status_code == 403
        saved = assert_success(client.post("/api/settings", json=request, headers=headers))
        assert saved["token_saved"] is False
        bootstrap = assert_success(client.get("/api/bootstrap"))
        assert bootstrap["settings"]["provider"] == "ollama"
        assert bootstrap["warning"] is None
        assert assert_success(client.post("/api/models", json=request,
                                          headers=headers))["models"] == local.models
        assert assert_success(client.post("/api/test-connection", json=request,
                                          headers=headers))["model_available"] is True
        wrong = {**request, "provider": "custom"}
        assert client.post("/api/models", json=wrong, headers=headers).status_code == 400
    assert public.calls == ()
