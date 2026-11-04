import json
import socket
from unittest.mock import Mock

import pytest
from conftest import FAKE_TOKEN

from brain_settings.errors import AppError
from brain_settings.transport import (
    MAX_RESPONSE_BYTES,
    PinnedHTTPSConnection,
    ProviderTransport,
    validate_public_host,
)


def dns_result(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))


def test_public_dns_answers_are_validated_and_deduplicated(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        dns_result("93.184.216.34"), dns_result("93.184.216.34"),
    ])
    assert validate_public_host("api.example.com") == ["93.184.216.34"]


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "169.254.169.254", "192.168.1.1",
    "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1", "0.0.0.0",
])
def test_dns_resolving_to_nonpublic_address_is_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [dns_result(address)])
    with pytest.raises(AppError):
        validate_public_host("api.example.com")


def test_mixed_public_and_private_dns_answers_reject_whole_host(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        dns_result("93.184.216.34"), dns_result("10.0.0.5"),
    ])
    with pytest.raises(AppError):
        validate_public_host("api.example.com")


def test_dns_failure_has_safe_user_error(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("Synthetic resolution error " + FAKE_TOKEN)

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(AppError) as caught:
        validate_public_host("api.example.com")
    assert FAKE_TOKEN not in str(caught.value)


def test_connection_pins_ip_but_preserves_tls_server_hostname(monkeypatch):
    raw_socket, tls_socket = Mock(), Mock()
    connect = Mock(return_value=raw_socket)
    monkeypatch.setattr(socket, "create_connection", connect)
    context = Mock()
    context.wrap_socket.return_value = tls_socket
    connection = PinnedHTTPSConnection(
        "api.example.com", ["93.184.216.34"], context=context, timeout=8,
    )
    connection.connect()
    assert connect.call_args.args[0] == ("93.184.216.34", 443)
    context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="api.example.com")
    assert connection.sock is tls_socket
    assert connection.host == "api.example.com"


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = json.dumps({"data": []}).encode() if body is None else body
        self.read_sizes = ()

    def read(self, amount=None):
        self.read_sizes = (*self.read_sizes, amount)
        return self.body if amount is None else self.body[:amount]

    def getheader(self, name, default=None):
        return default


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.requests = ()
        self.closed = False

    def request(self, method, url, body=None, headers=None):
        self.requests = (*self.requests, (method, url, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


@pytest.fixture
def network(monkeypatch):
    import brain_settings.transport as module

    def make(response):
        connection = FakeConnection(response)
        monkeypatch.setattr(module, "validate_public_host", lambda hostname: ["93.184.216.34"])
        monkeypatch.setattr(module, "PinnedHTTPSConnection", lambda *args, **kwargs: connection)
        return connection

    return make


def test_provider_transport_reads_bounded_json_without_generation(network):
    response = FakeResponse()
    connection = network(response)
    assert ProviderTransport().request("https://api.example.com/v1", "/models", FAKE_TOKEN) == {
        "data": [],
    }
    assert len(connection.requests) == 1
    method, path, body, headers = connection.requests[0]
    assert method == "GET"
    assert path == "/v1/models"
    assert body is None
    assert headers["Authorization"] == "Bearer " + FAKE_TOKEN
    assert response.read_sizes == (MAX_RESPONSE_BYTES + 1,)
    assert connection.closed is True


def test_redirect_is_not_followed(network):
    connection = network(FakeResponse(status=302))
    with pytest.raises(AppError):
        ProviderTransport().request("https://api.example.com/v1", "/models", FAKE_TOKEN)
    assert len(connection.requests) == 1
    assert connection.closed is True


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_upstream_error_does_not_expose_body_or_key(network, status):
    network(FakeResponse(status=status, body=("provider echoed " + FAKE_TOKEN).encode()))
    with pytest.raises(AppError) as caught:
        ProviderTransport().request("https://api.example.com/v1", "/models", FAKE_TOKEN)
    assert FAKE_TOKEN not in str(caught.value)
    assert 400 <= caught.value.status_code < 600


@pytest.mark.parametrize("body", [b"not JSON", b"[]", b"null"])
def test_invalid_upstream_json_is_safe_failure(network, body):
    network(FakeResponse(body=body))
    with pytest.raises(AppError):
        ProviderTransport().request("https://api.example.com/v1", "/models", None)


def test_upstream_body_size_is_limited(network):
    network(FakeResponse(body=b"x" * (MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(AppError):
        ProviderTransport().request("https://api.example.com/v1", "/models", None)


def test_socket_failure_is_safe_and_connection_closed(network):
    connection = network(FakeResponse())

    def fail(*args, **kwargs):
        raise OSError("Synthetic connection error " + FAKE_TOKEN)

    connection.request = fail
    with pytest.raises(AppError) as caught:
        ProviderTransport().request("https://api.example.com/v1", "/models", FAKE_TOKEN)
    assert FAKE_TOKEN not in str(caught.value)
    assert connection.closed is True
