"""Synthetic Microsoft identity/Graph protocol boundaries; no account or network."""

import json
from unittest.mock import Mock

import pytest

from brain_settings.errors import AppError
from brain_settings.outlook_transport import MicrosoftOAuthClient, OutlookTransport

CLIENT_ID = "11111111-2222-4333-8444-555555555555"
SCOPES = ("https://graph.microsoft.com/Mail.Read "
          "https://graph.microsoft.com/User.Read offline_access")


class FakeTransport:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def request_json(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return next(self.replies)


def tokens(**extra):
    return {"access_token": "demo-access", "refresh_token": "demo-refresh",
            "token_type": "Bearer", "expires_in": 3600, **extra}


def test_device_start_has_fixed_host_public_client_and_minimum_scopes():
    transport = FakeTransport([(200, {"device_code": "device-demo", "user_code": "DEMO-CODE",
                                      "verification_uri": "https://microsoft.com/devicelogin",
                                      "expires_in": 900, "interval": 5})])
    result = MicrosoftOAuthClient(transport=transport).start(CLIENT_ID)
    assert result["user_code"] == "DEMO-CODE"
    args, kwargs = transport.calls[0]
    assert args == ("POST", "login.microsoftonline.com", "/common/oauth2/v2.0/devicecode")
    assert kwargs["form"] == {"client_id": CLIENT_ID, "scope": SCOPES}


@pytest.mark.parametrize("value", ["", "not-an-id", "00000000-0000-0000-0000-000000000000", True])
def test_invalid_client_id_never_contacts_identity_service(value):
    transport = FakeTransport([])
    with pytest.raises(AppError):
        MicrosoftOAuthClient(transport=transport).start(value)
    assert transport.calls == []


@pytest.mark.parametrize("uri", ["http://microsoft.com/devicelogin", "https://example.com/login",
                                  "https://microsoft.com@evil.example/devicelogin",
                                  "https://microsoft.com/devicelogin?secret=demo"])
def test_device_start_rejects_untrusted_verification_uri(uri):
    transport = FakeTransport([(200, {"device_code": "device-demo", "user_code": "DEMO-CODE",
                                      "verification_uri": uri, "expires_in": 900, "interval": 5})])
    with pytest.raises(AppError):
        MicrosoftOAuthClient(transport=transport).start(CLIENT_ID)


@pytest.mark.parametrize(("error", "expected"), [("authorization_pending", "pending"),
                                                ("slow_down", "slow_down")])
def test_device_poll_preserves_expected_pending_status(error, expected):
    transport = FakeTransport([(400, {"error": error, "error_description": "DO_NOT_EXPOSE"})])
    assert MicrosoftOAuthClient(transport=transport).poll(CLIENT_ID, "device-demo") == {
        "status": expected}


@pytest.mark.parametrize("error", ["authorization_declined", "expired_token", "invalid_client",
                                    "invalid_grant", "unauthorized_client", "unknown_error"])
def test_terminal_oauth_errors_are_sanitized(error):
    transport = FakeTransport([(400, {"error": error, "error_description": "DO_NOT_EXPOSE"})])
    with pytest.raises(AppError) as caught:
        MicrosoftOAuthClient(transport=transport).poll(CLIENT_ID, "device-demo")
    assert "DO_NOT_EXPOSE" not in caught.value.message


def test_poll_and_refresh_normalize_opaque_tokens_and_keep_rotated_refresh():
    transport = FakeTransport([(200, tokens()), (200, tokens(refresh_token="rotated-demo")),
                               (200, {"access_token": "new-demo", "expires_in": 1800})])
    client = MicrosoftOAuthClient(transport=transport, clock=lambda: 1000)
    result = client.poll(CLIENT_ID, "device-demo")
    assert result == {"status": "completed", "tokens": {
        "access_token": "demo-access", "refresh_token": "demo-refresh", "expires_at": 4600}}
    assert client.refresh(CLIENT_ID, "demo-refresh")["refresh_token"] == "rotated-demo"
    assert client.refresh(CLIENT_ID, "rotated-demo")["refresh_token"] == "rotated-demo"
    assert transport.calls[1][1]["form"]["grant_type"] == "refresh_token"


@pytest.mark.parametrize("payload", [tokens(expires_in=True), tokens(expires_in=0),
                                     tokens(access_token="bad\r\nheader"),
                                     {"access_token": "demo", "expires_in": 3600},
                                     tokens(scope="Mail.ReadWrite User.Read"),
                                     tokens(token_type="Basic")])
def test_malformed_or_excessive_scope_tokens_are_not_accepted(payload):
    with pytest.raises(AppError):
        MicrosoftOAuthClient(transport=FakeTransport([(200, payload)])).poll(CLIENT_ID, "device")


def test_transport_pins_tls_and_prevents_auth_header_override(monkeypatch):
    import brain_settings.outlook_transport as module

    response = Mock(status=200)
    response.read.return_value = b'{"value":[]}'
    connection = Mock()
    connection.getresponse.return_value = response
    factory = Mock(return_value=connection)
    monkeypatch.setattr(module, "validate_public_host", Mock(return_value=["1.1.1.1"]))
    monkeypatch.setattr(module, "PinnedHTTPSConnection", factory)
    status, payload = OutlookTransport().request_json(
        "GET", "graph.microsoft.com", "/v1.0/me/mailFolders/inbox/messages?$top=50",
        bearer="demo-access", headers={"Prefer": 'IdType="ImmutableId"'})
    assert status == 200 and payload == {"value": []}
    assert factory.call_args.args[:2] == ("graph.microsoft.com", ["1.1.1.1"])
    assert factory.call_args.args[2].check_hostname is True
    assert connection.request.call_args.kwargs["headers"]["Authorization"] == "Bearer demo-access"
    connection.close.assert_called_once()
    with pytest.raises(AppError):
        OutlookTransport().request_json("GET", "graph.microsoft.com", "/v1.0/me",
                                        bearer="demo", headers={"Authorization": "bad"})


@pytest.mark.parametrize(("method", "host", "path"), [
    ("GET", "evil.example", "/v1.0/me"),
    ("POST", "graph.microsoft.com", "/v1.0/me/messages"),
    ("GET", "graph.microsoft.com", "/v1.0/users/other/messages"),
    ("POST", "login.microsoftonline.com", "/other/oauth2/token"),
    ("GET", "graph.microsoft.com", "https://evil.example/v1.0/me"),
    ("GET", "graph.microsoft.com", "/v1.0/me#fragment"),
])
def test_transport_denies_wrong_host_write_paths_or_absolute_urls(method, host, path):
    with pytest.raises(AppError):
        OutlookTransport().request_json(method, host, path)


@pytest.mark.parametrize(("status", "raw"), [(302, b'{}'), (200, b'[]'), (200, b'broken')])
def test_transport_fails_closed_for_redirects_or_bad_json(monkeypatch, status, raw):
    import brain_settings.outlook_transport as module

    connection = Mock()
    connection.getresponse.return_value.status = status
    connection.getresponse.return_value.read.return_value = raw
    monkeypatch.setattr(module, "validate_public_host", lambda _: ["1.1.1.1"])
    monkeypatch.setattr(module, "PinnedHTTPSConnection", lambda *a, **kw: connection)
    with pytest.raises(AppError):
        OutlookTransport().request_json("GET", "graph.microsoft.com", "/v1.0/me")
    connection.close.assert_called_once()


def test_transport_bounds_json_and_never_sends_credentials_after_dns_rejection(monkeypatch):
    import brain_settings.outlook_transport as module

    connection = Mock()
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 4)
    monkeypatch.setattr(module, "validate_public_host", lambda _: ["1.1.1.1"])
    monkeypatch.setattr(module, "PinnedHTTPSConnection", lambda *a, **kw: connection)
    connection.getresponse.return_value.status = 200
    connection.getresponse.return_value.read.return_value = json.dumps({"large": "demo"}).encode()
    with pytest.raises(AppError):
        OutlookTransport().request_json("GET", "graph.microsoft.com", "/v1.0/me")
    factory = Mock()
    monkeypatch.setattr(module, "PinnedHTTPSConnection", factory)
    def denied(_):
        raise AppError("unsafe_endpoint", "Blocked")
    monkeypatch.setattr(module, "validate_public_host", denied)
    with pytest.raises(AppError):
        OutlookTransport().request_json("GET", "graph.microsoft.com", "/v1.0/me", bearer="demo")
    factory.assert_not_called()
