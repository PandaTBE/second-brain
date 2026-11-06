"""Synthetic Graph responses, scoped pagination and account-bound refresh only."""

from datetime import UTC, datetime

import pytest

from brain_settings.errors import AppError
from brain_settings.outlook_client import OutlookClient, message_path


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, method, host, path, **kwargs):
        self.calls.append((method, host, path, kwargs))
        return self.responses.pop(0)


def message(**updates):
    return {
        "id": "Case-Sensitive-Immutable-ID",
        "subject": "Synthetic astronomy plan",
        "from": {"emailAddress": {"name": "Demo sender", "address": "demo@example.com"}},
        "receivedDateTime": "2026-11-01T12:00:00Z",
        "lastModifiedDateTime": "2026-11-02T12:00:00Z",
        "body": {"contentType": "html", "content": "<p>Look at the stars.</p>"},
        "webLink": "https://outlook.office.com/mail/inbox/id/demo",
        **updates,
    }


@pytest.mark.parametrize("days", [0, 30, 90, 365])
def test_inbox_reads_are_bounded_immutable_and_never_change_read_flags(days):
    transport = Transport((200, {"value": [message()]}))
    client = OutlookClient(transport=transport, now=lambda: datetime(2026, 11, 6, tzinfo=UTC))
    page = client.messages("demo", days)
    assert page["messages"][0]["id"] == "Case-Sensitive-Immutable-ID"
    assert "Look at the stars." in page["messages"][0]["text"]
    method, host, path, kwargs = transport.calls[0]
    assert method == "GET" and host == "graph.microsoft.com"
    assert path.startswith("/v1.0/me/mailFolders/inbox/messages?")
    assert "%24top=50" in path and "attachments" not in path and "isRead" not in path
    assert ("receivedDateTime%20ge" in path) == bool(days)
    assert 'IdType="ImmutableId"' in kwargs["headers"]["Prefer"]
    assert 'outlook.body-content-type="text"' in kwargs["headers"]["Prefer"]
    assert kwargs["bearer"] == "demo" and not page["next_link"]


def test_opaque_next_link_is_used_whole_and_html_never_fetches_resources():
    link = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skiptoken=A%2BB%3D"
    transport = Transport((200, {"value": [message(body={
        "contentType": "html", "content": '<p>Visible</p><script>HIDDEN</script>'
        '<style>HIDDEN</style><img src="https://unrelated.invalid/private">',
    })], "@odata.nextLink": link}), (200, {"value": []}))
    client = OutlookClient(transport=transport)
    page = client.messages("demo", 0)
    assert "Visible" in page["messages"][0]["text"]
    assert "HIDDEN" not in page["messages"][0]["text"]
    client.messages("demo", 0, next_link=page["next_link"])
    assert transport.calls[1][2] == "/v1.0/me/mailFolders/inbox/messages?$skiptoken=A%2BB%3D"
    assert len(transport.calls) == 2


@pytest.mark.parametrize("link", [
    "http://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
    "https://unrelated.invalid/v1.0/me/mailFolders/inbox/messages",
    "https://graph.microsoft.com:8443/v1.0/me/mailFolders/inbox/messages",
    "https://user@graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
    "https://graph.microsoft.com/v1.0/me/messages",
    "https://graph.microsoft.com/v1.0/me/mailFolders/sentitems/messages",
    "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages#fragment",
    "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?x=\nheader",
])
def test_bad_page_link_is_rejected_before_any_token_transmission(link):
    transport = Transport()
    with pytest.raises(AppError):
        OutlookClient(transport=transport).messages("demo", 0, next_link=link)
    assert transport.calls == []


def test_invalid_returned_next_link_rejects_whole_page():
    client = OutlookClient(transport=Transport((200, {"value": [message()],
        "@odata.nextLink": "https://unrelated.invalid/collect"})))
    with pytest.raises(AppError):
        client.messages("demo", 30)


@pytest.mark.parametrize("body", [
    {"value": {}}, {"value": [message()] * 51}, {},
])
def test_malformed_or_oversized_page_is_not_accepted_as_complete(body):
    with pytest.raises(AppError):
        OutlookClient(transport=Transport((200, body))).messages("demo", 0)


def test_bad_and_oversized_messages_skip_without_raw_text_in_diagnostics():
    raw = [message(body={"contentType": "text", "content": "x" * (1024 * 1024 + 1)}),
           message(id=""), message(receivedDateTime="DO_NOT_ECHO_PRIVATE"), message()]
    page = OutlookClient(transport=Transport((200, {"value": raw}))).messages("demo", 0)
    assert len(page["messages"]) == 1 and page["skipped"] == 3
    assert "DO_NOT_ECHO_PRIVATE" not in str(page["warnings"])


@pytest.mark.parametrize("code", [301, 401, 403, 404, 429, 500])
def test_graph_errors_are_safe_and_do_not_leak_response_body(code):
    client = OutlookClient(transport=Transport((code, {"error": "DO_NOT_ECHO_PRIVATE"})))
    with pytest.raises(AppError) as caught:
        client.test("demo")
    assert "DO_NOT_ECHO_PRIVATE" not in caught.value.message


def test_profile_and_check_only_read_profile_or_inbox_metadata():
    transport = Transport((200, {"id": "AccountCase", "mail": "demo@example.com",
                                 "displayName": "Demo account"}),
                          (200, {"totalItemCount": 42}))
    client = OutlookClient(transport=transport)
    assert client.profile("demo") == {"id": "AccountCase", "username": "demo@example.com",
                                      "display_name": "Demo account"}
    assert client.test("demo") == 42
    assert all(call[0] == "GET" for call in transport.calls)
    assert all("messages" not in call[2] for call in transport.calls)


def test_refresh_keeps_account_binding_and_never_decodes_access_tokens():
    class OAuth:
        def refresh(self, client_id, refresh_token):
            assert (client_id, refresh_token) == ("demo-client", "demo-refresh")
            return {"access_token": "opaque-new", "refresh_token": "opaque-refresh",
                    "expires_at": 2_000_000_000}

    original = {"client_id": "demo-client", "account_id": "AccountCase",
                "username": "demo@example.com", "access_token": "opaque-old",
                "refresh_token": "demo-refresh", "expires_at": 1}
    client = OutlookClient(transport=Transport((200, {"id": "AccountCase",
        "userPrincipalName": "demo@example.com"})), oauth_client=OAuth())
    result = client.refresh(original)
    assert result["account_id"] == "AccountCase" and result["access_token"] == "opaque-new"
    assert original["access_token"] == "opaque-old"
    client = OutlookClient(transport=Transport((200, {"id": "DifferentAccount",
        "mail": "demo@example.com"})), oauth_client=OAuth())
    with pytest.raises(AppError, match="учётную"):
        client.refresh(original)


def test_web_link_must_be_outlook_https_and_fallback_is_an_honest_graph_reference():
    row = OutlookClient(transport=Transport((200, {"value": [message(
        webLink="https://unrelated.invalid/collect")]}))).messages("demo", 0)["messages"][0]
    assert row["source_uri"].startswith("https://graph.microsoft.com/v1.0/me/messages/")


@pytest.mark.parametrize("days", [True, "30", 1, -1, 366])
def test_invalid_lookback_never_connects(days):
    transport = Transport()
    with pytest.raises(AppError):
        OutlookClient(transport=transport).messages("demo", days)
    assert not transport.calls


def test_message_path_preserves_valid_absolute_cursor_query():
    link = "https://graph.microsoft.com:443/v1.0/me/mailFolders/inbox/messages?$skip=52"
    assert message_path(link) == "/v1.0/me/mailFolders/inbox/messages?$skip=52"


@pytest.mark.parametrize("query", [
    "$expand=attachments", "$top=51", "$top=0", "$top=invalid",
    "$select=id,attachments", "$skip=1&$skip=2", "unknown=value",
])
def test_cursor_cannot_expand_attachment_scope_or_override_page_bounds(query):
    transport = Transport()
    with pytest.raises(AppError):
        OutlookClient(transport=transport).messages(
            "demo", 0, "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?" + query,
        )
    assert not transport.calls


@pytest.mark.parametrize("value", ["", None, "bad\r\nheader", "with space"])
def test_invalid_token_is_rejected_before_transport(value):
    transport = Transport()
    with pytest.raises(AppError):
        OutlookClient(transport=transport).profile(value)
    assert not transport.calls


@pytest.mark.parametrize("value", [{}, {"id": "demo"}, {"id": True, "mail": "a@example.com"}])
def test_profile_requires_bound_account_id_and_account_label(value):
    with pytest.raises(AppError):
        OutlookClient(transport=Transport((200, value))).profile("demo")


@pytest.mark.parametrize("count", [None, True, -1, "42"])
def test_inbox_check_rejects_missing_or_non_integer_count(count):
    with pytest.raises(AppError):
        OutlookClient(transport=Transport((200, {"totalItemCount": count}))).test("demo")


def test_bad_content_type_skips_one_message_and_same_cursor_cannot_loop_forever():
    transport = Transport((200, {"value": [message(body={"contentType": [], "content": "x"})]}))
    page = OutlookClient(transport=transport).messages("demo", 0)
    assert not page["messages"] and page["skipped"] == 1
    link = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=50"
    client = OutlookClient(transport=Transport((200, {"value": [], "@odata.nextLink": link})))
    with pytest.raises(AppError):
        client.messages("demo", 0, link)
