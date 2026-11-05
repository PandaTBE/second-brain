"""Explicit corporate HTTPS policy with synthetic DNS and no live credentials."""

import json
import socket
import ssl
from unittest.mock import Mock

import pytest
from conftest import FAKE_TOKEN, MemoryVault
from pydantic import ValidationError
from test_llm_cards import EVIDENCE, valid_card
from test_transport_security import FakeConnection, FakeResponse, dns_result

from brain_settings.errors import AppError
from brain_settings.llm_cards import LLMCardGenerator
from brain_settings.providers import EndpointRequest, SaveRequest, SelectionRequest, Settings
from brain_settings.service import SettingsService
from brain_settings.storage import SettingsStore
from brain_settings.transport import ProviderTransport, validate_public_host

CORPORATE_URL = "https://cards.example.internal/api/v2"


@pytest.mark.parametrize("address", ["10.12.3.4", "172.20.1.2", "192.168.2.3",
                                      "100.64.1.2", "fd12:3456::1"])
def test_private_dns_requires_explicit_opt_in(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [dns_result(address)])
    with pytest.raises(AppError, match="внутренн"):
        validate_public_host("cards.example.internal")
    assert validate_public_host("cards.example.internal", allow_private_network=True) == [address]


@pytest.mark.parametrize("address", [
    "127.0.0.1", "::1", "169.254.169.254", "169.254.170.2", "fe80::1",
    "100.100.100.200", "fd00:ec2::254", "fd20:ce::254", "0.0.0.0", "::",
    "224.0.0.1", "ff02::1", "198.18.0.1", "192.0.2.1", "240.0.0.1",
    "::ffff:127.0.0.1", "::ffff:169.254.169.254", "::ffff:10.0.0.1",
    "4000::1", "fec0::1", "64:ff9b::7f00:1", "64:ff9b:1::7f00:1",
])
def test_opt_in_never_admits_special_or_metadata_destinations(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [dns_result(address)])
    with pytest.raises(AppError) as error:
        validate_public_host("cards.example.internal", allow_private_network=True)
    assert error.value.code == "unsafe_endpoint"


def test_one_forbidden_dns_answer_rejects_the_entire_corporate_host(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        dns_result("10.2.3.4"), dns_result("93.184.216.34"), dns_result("169.254.169.254")])
    connection = Mock(side_effect=AssertionError("No connection before policy validation"))
    monkeypatch.setattr("brain_settings.transport.PinnedHTTPSConnection", connection)
    with pytest.raises(AppError):
        ProviderTransport().post_json(CORPORATE_URL, "/chat/completions", {},
                                      token=FAKE_TOKEN, allow_private_network=True)
    connection.assert_not_called()


@pytest.mark.parametrize("model", [EndpointRequest, SelectionRequest, SaveRequest, Settings])
def test_policy_is_strict_and_custom_only(model):
    values = {"provider": "custom", "base_url": CORPORATE_URL, "model_id": "demo"}
    if model is EndpointRequest:
        values.pop("model_id")
    assert model(**values).allow_private_network is False
    assert model(**values, allow_private_network=True).allow_private_network is True
    for invalid in ("true", 1, None):
        with pytest.raises(ValidationError):
            model(**values, allow_private_network=invalid)
    with pytest.raises(ValidationError):
        model(**{**values, "provider": "openai"}, allow_private_network=True)


@pytest.mark.parametrize("base_url", ["http://cards.example.internal/api/v2",
                                      "https://localhost/api/v2", "https://api.localhost/api/v2",
                                      "https://127.0.0.1/api/v2", "https://169.254.169.254/api/v2",
                                      "https://cards.example.internal:8443/api/v2"])
def test_opt_in_retains_https_port_and_loopback_boundaries(tmp_path, base_url):
    service = SettingsService(tmp_path, vault=MemoryVault())
    with pytest.raises(AppError):
        service.save(SaveRequest(provider="custom", base_url=base_url,
                                 model_id="demo", token=FAKE_TOKEN, allow_private_network=True))


def test_old_settings_load_with_public_policy_and_internal_settings_round_trip(tmp_path):
    store = SettingsStore(tmp_path)
    tmp_path.mkdir(exist_ok=True)
    store.path.write_text(json.dumps({"provider": "custom", "base_url": "https://api.example.com/v2",
                                     "model_id": "demo", "model_name": "Demo"}))
    assert store.load().allow_private_network is False
    service = SettingsService(tmp_path, vault=MemoryVault())
    saved = service.save(SaveRequest(provider="custom", base_url=CORPORATE_URL, token=FAKE_TOKEN,
                                     model_id="demo", allow_private_network=True))
    assert saved["settings"]["allow_private_network"] is True
    assert service.store.load().allow_private_network is True
    assert FAKE_TOKEN not in store.path.read_text()


class CorporateTransport:
    def __init__(self):
        self.calls = ()

    def request(self, base_url, path, token=None, *, allow_private_network=False):
        self.calls = (*self.calls, (base_url, path, token, allow_private_network))
        return {"data": [{"id": "corporate-demo"}]}

    def post_json(self, base_url, path, payload, token=None, timeout=45,
                  *, allow_private_network=False):
        self.calls = (*self.calls, (base_url, path, token, allow_private_network))
        return {"choices": [{"message": {"content": json.dumps(valid_card())}}]}


def test_corporate_policy_reaches_catalog_connection_and_generation(tmp_path):
    transport = CorporateTransport()
    service = SettingsService(tmp_path, vault=MemoryVault(), transport=transport)
    values = {"provider": "custom", "base_url": CORPORATE_URL, "token": FAKE_TOKEN,
              "model_id": "corporate-demo", "allow_private_network": True}
    service.models(SelectionRequest(**values))
    service.test_connection(SelectionRequest(**values))
    service.save(SaveRequest(**values))
    generator = LLMCardGenerator(service, transport=transport)
    result = generator.generate({"text": EVIDENCE})
    assert result["card"] == valid_card() and generator.is_current(result)
    assert [call[1] for call in transport.calls] == ["/models", "/models", "/chat/completions"]
    assert all(call[0] == CORPORATE_URL and call[2] == FAKE_TOKEN and call[3]
               for call in transport.calls)


def test_policy_change_invalidates_scope_and_late_generation_result(tmp_path):
    transport = CorporateTransport()
    service = SettingsService(tmp_path, vault=MemoryVault(), transport=transport)
    values = {"provider": "custom", "base_url": "https://api.example.com/api/v2",
              "token": FAKE_TOKEN, "model_id": "corporate-demo"}
    service.save(SaveRequest(**values))
    generator = LLMCardGenerator(service, transport=transport)
    old_scope = generator.provider_scope()
    result = generator.generate({"text": EVIDENCE})
    service.save(SaveRequest(**values, allow_private_network=True))
    assert generator.provider_scope() != old_scope
    assert not generator.is_current(result)
    result = generator.generate({"text": EVIDENCE})
    service.save(SaveRequest(**values))
    assert generator.provider_scope() == old_scope and not generator.is_current(result)


@pytest.mark.parametrize("operation", ["GET", "POST"])
def test_actual_transport_keeps_pinning_tls_and_versioned_paths(monkeypatch, operation):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [dns_result("10.23.4.5")])
    connection = FakeConnection(FakeResponse())
    captured = ()

    def connect(host, addresses, context, **kwargs):
        nonlocal captured
        captured = (host, addresses, context)
        return connection

    monkeypatch.setattr("brain_settings.transport.PinnedHTTPSConnection", connect)
    transport = ProviderTransport()
    if operation == "GET":
        transport.request(CORPORATE_URL, "/models", FAKE_TOKEN, allow_private_network=True)
    else:
        transport.post_json(CORPORATE_URL, "/chat/completions", {}, token=FAKE_TOKEN,
                            allow_private_network=True)
    assert captured[0:2] == ("cards.example.internal", ["10.23.4.5"])
    assert captured[2].verify_mode == ssl.CERT_REQUIRED and captured[2].check_hostname
    assert connection.requests[0][0:2] == (
        operation, "/api/v2/models" if operation == "GET" else "/api/v2/chat/completions")
    assert connection.closed


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("invalid", ["false", "true", 1, None])
def test_transport_never_promotes_non_boolean_policy(monkeypatch, method, invalid):
    connect = Mock(side_effect=AssertionError("Invalid policy must fail before a socket"))
    monkeypatch.setattr("brain_settings.transport.PinnedHTTPSConnection", connect)
    transport = ProviderTransport()
    with pytest.raises(AppError) as error:
        if method == "GET":
            transport.request(CORPORATE_URL, "/models", allow_private_network=invalid)
        else:
            transport.post_json(CORPORATE_URL, "/chat/completions", {},
                                allow_private_network=invalid)
    assert error.value.code == "invalid_network_policy"
    connect.assert_not_called()


def test_corporate_opt_in_round_trips_through_http_and_keeps_csrf(harness):
    values = {"provider": "custom", "base_url": CORPORATE_URL, "model_id": "corporate-demo",
              "token": FAKE_TOKEN, "allow_private_network": True}
    denied = harness.client.post("/api/settings", json=values)
    assert denied.status_code == 403
    saved = harness.post("/api/settings", values)
    assert saved.status_code == 200
    assert saved.json()["data"]["settings"]["allow_private_network"] is True
    bootstrap = harness.client.get("/api/bootstrap").json()["data"]
    assert bootstrap["settings"]["base_url"] == CORPORATE_URL
    assert bootstrap["settings"]["allow_private_network"] is True
    assert FAKE_TOKEN not in json.dumps(bootstrap)
    invalid = harness.post("/api/settings", {**values, "allow_private_network": "true"})
    assert invalid.status_code == 422
    invalid = harness.post("/api/settings", {**values, "provider": "openai"})
    assert invalid.status_code == 422
    assert not harness.transport.calls


def test_disabled_policy_keeps_legacy_settings_file_format(tmp_path):
    store = SettingsStore(tmp_path)
    settings = Settings(provider="custom", base_url="https://api.example.com/v2", model_id="demo")
    store.save(settings)
    assert "allow_private_network" not in json.loads(store.path.read_text())
    assert set(json.loads(store.path.read_text())) == {
        "provider", "base_url", "model_id", "model_name"}
    store.save(settings.model_copy(update={"allow_private_network": True}))
    assert json.loads(store.path.read_text())["allow_private_network"] is True
    assert store.load().allow_private_network is True
    store.save(settings)
    assert "allow_private_network" not in json.loads(store.path.read_text())
    assert store.load().allow_private_network is False
