"""Read-only IMAP commands against a fake connection, never a real mailbox."""

import imaplib

import pytest
from test_mail_message import message

from brain_settings.errors import AppError
from brain_settings.mail_client import BoundedIMAP4SSL, MailClient

SOURCE = {"host": "imap.gmail.com", "username": "reader@example.com", "port": 993,
          "mailbox": "INBOX"}


class Connection:
    def __init__(self, raw=None):
        self.raw = raw if raw is not None else message().as_bytes()
        self.calls = ()
        self.search_result = b"1 2 3"
        self.literal_size = None
        self.error = None

    def login(self, username, password):
        self.calls = (*self.calls, ("login", username, password))
        if self.error:
            raise self.error
        return "OK", []

    def select(self, mailbox, readonly=False):
        self.calls = (*self.calls, ("select", mailbox, readonly))
        return "OK", [b"3"]

    def response(self, key):
        return key, [b"17" if key == "UIDVALIDITY" else b"4"]

    def uid(self, command, *args):
        self.calls = (*self.calls, (command, *args))
        if command == "SEARCH":
            return "OK", [self.search_result]
        uid = args[0]
        size = len(self.raw) if self.literal_size is None else self.literal_size
        if "BODY.PEEK" not in args[1]:
            return "OK", [f"1 (UID {uid} RFC822.SIZE {size})".encode()]
        return "OK", [(f"1 (UID {uid} RFC822.SIZE {size} BODY[]<0> {{{size}}}".encode(),
                       self.raw), b")"]

    def logout(self):
        self.calls = (*self.calls, ("logout",))

    def shutdown(self):
        self.calls = (*self.calls, ("shutdown",))


@pytest.fixture
def connection(monkeypatch):
    import brain_settings.mail_client as module
    fake = Connection()
    monkeypatch.setattr(module, "validate_public_host", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(module, "BoundedIMAP4SSL", lambda *args, **kwargs: fake)
    return fake


def test_session_uses_examine_uidsearch_and_peek_only(connection):
    from datetime import date
    with MailClient().open(SOURCE, "synthetic") as session:
        assert session.uid_validity == 17 and session.uid_next == 4
        assert session.search(1, 2000, date(2026, 10, 5)) == (1, 2, 3)
        assert session.fetch(1) == connection.raw
    assert ("select", "INBOX", True) in connection.calls
    search = next(call for call in connection.calls if call[0] == "SEARCH")
    assert "1:2000" in search and "05-Oct-2026" in search
    fetches = [call for call in connection.calls if call[0] == "FETCH"]
    assert len(fetches) == 2 and "BODY.PEEK[]<0." in fetches[1][2]
    assert connection.calls[-1] == ("logout",)
    assert not any(call[0] in ("STORE", "EXPUNGE", "CLOSE") for call in connection.calls)


def test_oversized_message_rejected_before_body_download(connection):
    connection.literal_size = 2_000_000
    with MailClient().open(SOURCE, "synthetic") as session:
        with pytest.raises(AppError) as caught:
            session.fetch(1)
    assert caught.value.code == "mail_message_large"
    assert len([call for call in connection.calls if call[0] == "FETCH"]) == 1


@pytest.mark.parametrize("result", [b"1 BAD", b"0 1", b"1 2 3 4000"])
def test_malformed_or_outofrange_search_is_error(connection, result):
    connection.search_result = result
    with MailClient().open(SOURCE, "synthetic") as session:
        with pytest.raises(AppError):
            session.search(1, 2000)


def test_authentication_error_does_not_echo_password(connection):
    connection.error = imaplib.IMAP4.error("server echoed synthetic-secret")
    with pytest.raises(AppError) as caught:
        with MailClient().open(SOURCE, "synthetic-secret"):
            pass
    assert "synthetic-secret" not in caught.value.message
    assert connection.calls[-1] == ("logout",)


def test_stop_callback_cancels_before_connection(connection):
    def stop():
        raise AppError("sync_paused", "Пауза.", 409)
    with pytest.raises(AppError) as caught:
        with MailClient().open(SOURCE, "synthetic", check_stop=stop):
            pass
    assert caught.value.code == "sync_paused" and not connection.calls


def test_wire_literal_bound_prevents_large_allocation():
    connection = object.__new__(BoundedIMAP4SSL)
    with pytest.raises(AppError):
        connection.read(100_000_000)
    assert connection._literal_rejected is True


@pytest.mark.parametrize("response", [("UIDVALIDITY", [None]), ("UIDVALIDITY", [b"0"]),
                                      ("UIDVALIDITY", [b"99999999999"]), ("UIDVALIDITY", [])])
def test_invalid_uidvalidity_is_rejected_without_search(connection, response):
    connection.response = lambda name: response
    with pytest.raises(AppError):
        with MailClient().open(SOURCE, "synthetic"):
            pass
    assert not any(call[0] == "SEARCH" for call in connection.calls)


def test_truncated_wire_literal_is_not_accepted_as_complete_email(connection):
    original = connection.uid
    def truncated(command, *args):
        status, rows = original(command, *args)
        if command == "FETCH" and "BODY.PEEK" in args[1]:
            rows = [(rows[0][0], connection.raw[:-1]), b")"]
        return status, rows
    connection.uid = truncated
    with MailClient().open(SOURCE, "synthetic") as session:
        with pytest.raises(AppError) as caught:
            session.fetch(1)
    assert caught.value.code == "mail_protocol"


def test_logout_failure_closes_socket_and_rejected_literal_skips_logout(connection):
    def fail_logout():
        raise imaplib.IMAP4.error("Synthetic private server text")
    connection.logout = fail_logout
    with MailClient().open(SOURCE, "synthetic"):
        pass
    assert connection.calls[-1] == ("shutdown",)
    connection._literal_rejected = True
    with MailClient().open(SOURCE, "synthetic"):
        pass
    assert connection.calls[-1] == ("shutdown",)


@pytest.mark.parametrize("changes", [{"port": 143}, {"mailbox": "SENT"},
                                      {"host": "127.0.0.1"}, {"username": "bad\r\nname"}])
def test_invalid_configuration_never_opens_connection(connection, changes):
    with pytest.raises(AppError):
        with MailClient().open({**SOURCE, **changes}, "synthetic"):
            pass
    assert not connection.calls


def test_network_abort_is_safe_error(connection):
    connection.error = imaplib.IMAP4.abort("Synthetic server echoed sensitive-password")
    with pytest.raises(AppError) as caught:
        with MailClient().open(SOURCE, "sensitive-password"):
            pass
    assert caught.value.code == "mail_network_error"
    assert "sensitive-password" not in caught.value.message
