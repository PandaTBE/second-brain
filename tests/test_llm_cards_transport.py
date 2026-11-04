import json
from unittest.mock import Mock

import pytest

from brain_settings.errors import AppError
from brain_settings.transport import MAX_RESPONSE_BYTES, ProviderTransport


@pytest.fixture
def outbound(monkeypatch):
    import brain_settings.transport as module

    response = Mock(status=200)
    response.read.return_value = b'{"choices": []}'
    connection = Mock()
    connection.getresponse.return_value = response
    factory = Mock(return_value=connection)
    monkeypatch.setattr(module, "PinnedHTTPSConnection", factory)
    monkeypatch.setattr(module, "validate_public_host", lambda hostname: ["93.184.216.34"])
    return connection, response, factory


def test_post_json_sends_one_bounded_tls_request_and_closes_connection(outbound):
    connection, response, factory = outbound
    payload = {"model": "synthetic", "max_tokens": 1024, "messages": []}
    result = ProviderTransport().post_json(
        "https://api.example.com/v1", "/chat/completions", payload,
        token="synthetic-" + "not-real-key", timeout=45,
    )
    assert result == {"choices": []}
    assert connection.request.call_count == 1
    args, kwargs = connection.request.call_args
    assert args[:2] == ("POST", "/v1/chat/completions")
    assert json.loads(kwargs["body"]) == payload
    assert kwargs["headers"]["Authorization"] == "Bearer synthetic-not-real-key"
    assert factory.call_args.kwargs["timeout"] == 45
    response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
    connection.close.assert_called_once()


def test_post_redirect_is_blocked_without_retry(outbound):
    connection, response, _ = outbound
    response.status = 302
    with pytest.raises(AppError):
        ProviderTransport().post_json("https://api.example.com", "/chat/completions", {})
    assert connection.request.call_count == 1
    connection.close.assert_called_once()


def test_post_network_error_does_not_echo_token(outbound):
    connection, _, _ = outbound
    connection.request.side_effect = OSError("Synthetic error synthetic-not-real-key")
    with pytest.raises(AppError) as caught:
        ProviderTransport().post_json("https://api.example.com", "/chat/completions", {},
                                      token="synthetic-" + "not-real-key")
    assert "synthetic-not-real-key" not in str(caught.value)
    assert connection.request.call_count == 1
