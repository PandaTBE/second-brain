"""The local connector never performs real network or model operations in tests."""

import http.client
import json
import socket
from unittest.mock import Mock

import pytest

from brain_settings.errors import AppError
from brain_settings.ollama import BASE_URL, MAX_RESPONSE_BYTES, OllamaClient, _LoopbackConnection


def local_model(**changes):
    return {"name": "qwen3:8b", "model": "qwen3:8b", "size": 5_200_000_000, **changes}


class Response:
    def __init__(self, payload=None, status=200, raw=None):
        self.status = status
        self.raw = raw if raw is not None else json.dumps(payload).encode()
        self.read_sizes = []

    def read(self, amount):
        self.read_sizes.append(amount)
        return self.raw[:amount]

    def getheader(self, name, default=None):
        return default


class Connection:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, *arguments, **keywords):
        self.requests.append((arguments, keywords))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def client_for(*responses):
    connections = [Connection(response) for response in responses]
    calls = []

    def factory(*arguments, **keywords):
        calls.append((arguments, keywords))
        return connections[len(calls) - 1]

    return OllamaClient(connection_factory=factory), connections, calls


def payload(**changes):
    return {"model": "qwen3:8b", "messages": [{"role": "user", "content": "Synthetic note"}],
            "max_tokens": 1024, **changes}


def generated(**changes):
    return {"message": {"role": "assistant", "content": '{"title":"Synthetic"}'},
            "done": True, "done_reason": "stop", "prompt_eval_count": 100,
            "eval_count": 25, **changes}


def test_catalog_is_only_a_fixed_loopback_read_without_auth_or_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.invalid:8080")
    response = Response({"models": [local_model()]})
    client, connections, calls = client_for(response)
    assert BASE_URL == "http://127.0.0.1:11434/v1"
    assert client.catalog() == [{"id": "qwen3:8b", "name": "qwen3:8b", "input_price": 0,
                                 "output_price": 0, "context_length": None}]
    assert calls == [(("127.0.0.1", 11434), {"timeout": 5})]
    arguments, keywords = connections[0].requests[0]
    assert arguments[:2] == ("GET", "/api/tags")
    assert "Authorization" not in keywords["headers"]
    assert response.read_sizes == [MAX_RESPONSE_BYTES + 1]
    assert connections[0].closed


@pytest.mark.parametrize("row", [
    local_model(remote_host="https://cloud.example.invalid"),
    local_model(remote_model="qwen3:8b"), local_model(cloud=True),
    local_model(is_cloud=True), local_model(remote=True),
    local_model(name="Qwen:ClOuD"), local_model(name="qwen3:8b-ClOuD"),
    local_model(name="qwen3:8b:cloud:local"), local_model(size=0), local_model(size=-1),
    local_model(size=True), local_model(size="123"), local_model(name=""),
    local_model(name="bad\nmodel"), local_model(name="x" * 257), None, [],
])
def test_catalog_excludes_cloud_missing_downloads_and_invalid_rows(row):
    client, _, _ = client_for(Response({"models": [row]}))
    assert client.catalog() == []


def test_catalog_deduplicates_and_supports_the_model_field_fallback():
    client, _, _ = client_for(Response({"models": [
        local_model(), local_model(), {"model": "qwen3:4b", "size": 123},
    ]}))
    assert [model["id"] for model in client.catalog()] == ["qwen3:8b", "qwen3:4b"]


@pytest.mark.parametrize("value", [{}, {"models": None}, {"models": {}}, [], "private error"])
def test_bad_catalog_is_a_safe_unsent_error(value):
    client, connections, _ = client_for(Response(value))
    with pytest.raises(AppError) as caught:
        client.catalog()
    assert caught.value.code == "invalid_local_catalog"
    assert caught.value.request_sent is False
    assert "private error" not in str(caught.value)
    assert connections[0].closed


def test_ensure_requires_the_exact_installed_local_id():
    client, _, _ = client_for(Response({"models": [local_model()]}))
    client.ensure_local_model("qwen3:8b")
    client, _, _ = client_for(Response({"models": [local_model()]}))
    with pytest.raises(AppError) as caught:
        client.ensure_local_model("qwen3")
    assert caught.value.code == "local_model_required" and not caught.value.request_sent


@pytest.mark.parametrize("model", [None, "", "  ", True, "bad\rmodel", "qwen:cloud"])
def test_invalid_or_cloud_manual_id_never_connects(model):
    factory = Mock(side_effect=AssertionError("No connection is allowed"))
    with pytest.raises(AppError) as caught:
        OllamaClient(connection_factory=factory).ensure_local_model(model)
    assert caught.value.request_sent is False
    factory.assert_not_called()


def test_generate_rechecks_local_model_and_maps_native_chat_usage():
    client, connections, calls = client_for(
        Response({"models": [local_model()]}), Response(generated()),
    )
    result = client.generate(payload(options={"num_ctx": 1}, stream=True, think=True,
                                     token="-".join(("synthetic", "value"))), timeout=999)
    assert result["choices"] == [{"message": {"role": "assistant", "content":
                                             '{"title":"Synthetic"}'},
                                  "finish_reason": "stop"}]
    assert result["usage"] == {"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125}
    arguments, keywords = connections[1].requests[0]
    assert arguments[:2] == ("POST", "/api/chat")
    request = json.loads(keywords["body"])
    assert request == {"model": "qwen3:8b:local", "messages": payload()["messages"],
                       "stream": False, "think": False, "format": "json", "truncate": False,
                       "options": {"num_ctx": 16384, "num_predict": 1024}}
    assert "Authorization" not in keywords["headers"]
    assert calls[1] == (("127.0.0.1", 11434), {"timeout": 180})
    assert all(connection.closed for connection in connections)


def test_cloud_alias_cannot_receive_a_chat_request():
    client, connections, calls = client_for(Response({"models": [
        local_model(remote_host="https://cloud.example.invalid"),
    ]}))
    with pytest.raises(AppError) as caught:
        client.generate(payload())
    assert caught.value.code == "local_model_required" and not caught.value.request_sent
    assert len(calls) == 1 and connections[0].requests[0][0][0] == "GET"


@pytest.mark.parametrize("changes", [
    {"messages": []}, {"messages": "not messages"},
    {"messages": [{"role": "tool", "content": "unsupported"}]},
    {"messages": [{"role": "user", "content": []}]},
    {"max_tokens": True}, {"max_tokens": 0}, {"max_tokens": -1},
    {"messages": [{"role": "user", "content": "x" * 140_000}]},
])
def test_invalid_chat_input_is_rejected_before_network(changes):
    factory = Mock(side_effect=AssertionError("Invalid input must not connect"))
    with pytest.raises(AppError) as caught:
        OllamaClient(connection_factory=factory).generate(payload(**changes))
    assert not caught.value.request_sent
    factory.assert_not_called()


def test_length_and_invalid_native_counters_are_adapted_safely():
    client, _, _ = client_for(Response({"models": [local_model()]}), Response(generated(
        done_reason="length", prompt_eval_count=True, eval_count=-1,
    )))
    result = client.generate(payload(max_tokens=999_999))
    assert result["choices"][0]["finish_reason"] == "length"
    assert result["usage"]["total_tokens"] == 0


@pytest.mark.parametrize("native", [
    {"error": "PRIVATE-ERROR-DATA"}, {"message": None},
    {"message": {"content": []}}, generated(done=False),
    generated(remote_host="https://cloud.example.invalid"),
    generated(remote_model="qwen3:8b"),
])
def test_invalid_generation_response_is_safe_and_counts_the_sent_request(native):
    client, _, _ = client_for(Response({"models": [local_model()]}), Response(native))
    with pytest.raises(AppError) as caught:
        client.generate(payload())
    assert caught.value.request_sent is True
    assert "PRIVATE-ERROR-DATA" not in str(caught.value)


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 404, 429, 500, 503])
def test_http_errors_never_follow_redirects_or_echo_server_data(status):
    client, connections, calls = client_for(Response(raw=b"PRIVATE-ERROR-DATA", status=status))
    with pytest.raises(AppError) as caught:
        client.catalog()
    assert not caught.value.request_sent
    assert "PRIVATE-ERROR-DATA" not in str(caught.value)
    assert len(calls) == 1 and connections[0].closed


@pytest.mark.parametrize("body", [b"not-json", b"\xff", b"[]", b"x" * (MAX_RESPONSE_BYTES + 1)])
def test_bad_or_oversized_json_is_bounded_and_never_leaked(body):
    client, connections, _ = client_for(Response(raw=body))
    with pytest.raises(AppError) as caught:
        client.catalog()
    assert not caught.value.request_sent and connections[0].closed


def test_socket_failure_is_safe_and_closed():
    client, connections, _ = client_for(Response({"models": []}))
    connections[0].request = Mock(side_effect=OSError("PRIVATE-ERROR-DATA"))
    with pytest.raises(AppError) as caught:
        client.catalog()
    assert caught.value.code == "local_model_unavailable"
    assert not caught.value.request_sent and connections[0].closed
    assert "PRIVATE-ERROR-DATA" not in str(caught.value)


def test_numeric_loopback_connect_bypasses_dns_and_closes_failed_socket(monkeypatch):
    raw = Mock()
    monkeypatch.setattr(socket, "getaddrinfo", Mock(side_effect=AssertionError("No DNS")))
    constructor = Mock(return_value=raw)
    monkeypatch.setattr(socket, "socket", constructor)
    connection = _LoopbackConnection("127.0.0.1", 11434, timeout=3)
    connection.connect()
    constructor.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
    raw.connect.assert_called_once_with(("127.0.0.1", 11434))
    raw.settimeout.assert_called_once_with(3)
    raw.connect.side_effect = OSError("unreachable")
    with pytest.raises(OSError):
        connection.connect()
    raw.close.assert_called_once()


def test_chat_http_failure_is_a_sent_attempt():
    client, connections, _ = client_for(Response({"models": [local_model()]}),
                                       Response(status=503, raw=b"PRIVATE-ERROR-DATA"))
    with pytest.raises(AppError) as caught:
        client.generate(payload())
    assert caught.value.request_sent is True and all(c.closed for c in connections)


def test_response_read_failure_is_safe_and_closed():
    response = Response({"models": []})
    response.read = Mock(side_effect=http.client.IncompleteRead(b"private"))
    client, connections, _ = client_for(response)
    with pytest.raises(AppError) as caught:
        client.catalog()
    assert not caught.value.request_sent and connections[0].closed


def test_elapsed_absolute_deadline_is_rejected_even_with_a_readable_response(monkeypatch):
    clock = iter([100.0, 106.0])
    monkeypatch.setattr("brain_settings.ollama.time.monotonic", lambda: next(clock))
    client, connections, _ = client_for(Response({"models": []}))
    with pytest.raises(AppError) as caught:
        client.catalog()
    assert caught.value.code == "local_model_unavailable"
    assert not caught.value.request_sent and connections[0].closed


def test_deadline_abort_interrupts_the_underlying_socket(monkeypatch):
    raw = Mock()
    monkeypatch.setattr(socket, "socket", Mock(return_value=raw))
    connection = _LoopbackConnection("127.0.0.1", 11434, timeout=3)
    connection.connect()
    connection.abort()
    raw.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    raw.close.assert_called_once()


def test_disallowed_http_operation_is_rejected_without_network():
    factory = Mock(side_effect=AssertionError("Unexpected operation"))
    with pytest.raises(AppError) as caught:
        OllamaClient(connection_factory=factory)._request("GET", "/api/delete")
    assert caught.value.code == "invalid_local_request" and not caught.value.request_sent
    factory.assert_not_called()


def test_connection_constructor_failure_is_safe_and_unsent():
    factory = Mock(side_effect=OSError("PRIVATE-CONSTRUCTOR-DATA"))
    with pytest.raises(AppError) as caught:
        OllamaClient(connection_factory=factory).catalog()
    assert not caught.value.request_sent
    assert "PRIVATE-CONSTRUCTOR-DATA" not in str(caught.value)


def test_deadline_during_connect_cannot_leave_a_usable_late_socket(monkeypatch):
    raw = Mock()
    monkeypatch.setattr(socket, "socket", Mock(return_value=raw))
    connection = _LoopbackConnection("127.0.0.1", 11434, timeout=3)
    raw.connect.side_effect = lambda _: connection.abort()
    with pytest.raises(TimeoutError):
        connection.connect()
    raw.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    assert raw.close.called and connection.sock is None


def test_deadline_before_connect_does_not_allocate_a_socket(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(socket, "socket", factory)
    connection = _LoopbackConnection("127.0.0.1", 11434, timeout=3)
    connection.abort()
    with pytest.raises(TimeoutError):
        connection.connect()
    factory.assert_not_called()


def test_admission_runs_after_final_catalog_and_prevents_chat_after_pause():
    client, connections, calls = client_for(Response({"models": [local_model()]}))

    def admission():
        assert len(calls) == 1
        assert connections[0].requests[0][0][:2] == ("GET", "/api/tags")
        raise AppError("provider_paused", "Пауза синхронизации.", 429)

    with pytest.raises(AppError) as caught:
        client.generate(payload(), before_request=admission)
    assert caught.value.code == "provider_paused"
    assert caught.value.request_sent is False
    assert len(calls) == 1 and connections[0].closed


def test_unexpected_admission_failure_is_safe_unsent_without_post():
    client, _, calls = client_for(Response({"models": [local_model()]}))
    admission = Mock(side_effect=RuntimeError("PRIVATE-ADMISSION-DATA"))
    with pytest.raises(AppError) as caught:
        client.generate(payload(), before_request=admission)
    assert caught.value.code == "local_admission_failed"
    assert not caught.value.request_sent and len(calls) == 1
    assert "PRIVATE-ADMISSION-DATA" not in str(caught.value)


def test_successful_admission_is_between_the_last_catalog_and_chat():
    client, connections, calls = client_for(Response({"models": [local_model()]}),
                                           Response(generated()))

    def admission():
        assert len(calls) == 1 and not connections[1].requests

    result = client.generate(payload(), before_request=admission)
    assert result["choices"][0]["finish_reason"] == "stop"
    assert len(calls) == 2
