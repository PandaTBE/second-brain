import imaplib
import socket
from unittest.mock import Mock

import pytest
from test_sources_mail import SYNTHETIC_PASSWORD

from brain_settings.errors import AppError
from brain_settings.mail import MailTester, PinnedIMAP4SSL


class FakeIMAP:
    def __init__(self, count=b"17", login_error=None, select_status="OK", login_status="OK"):
        self.count = count
        self.login_error = login_error
        self.select_status = select_status
        self.login_status = login_status
        self.calls = ()

    def login(self, username, password):
        self.calls = (*self.calls, ("login", username, password))
        if self.login_error is not None:
            raise self.login_error
        return self.login_status, [b"Synthetic authenticated"]

    def select(self, mailbox, readonly=False):
        self.calls = (*self.calls, ("select", mailbox, readonly))
        return self.select_status, [self.count]

    def logout(self):
        self.calls = (*self.calls, ("logout",))
        return "BYE", [b"Synthetic logout"]


@pytest.fixture
def fake_imap(monkeypatch):
    import brain_settings.mail as module

    def make(**kwargs):
        connection = FakeIMAP(**kwargs)
        monkeypatch.setattr(module, "validate_public_host", lambda host: ["93.184.216.34"])
        monkeypatch.setattr(module, "PinnedIMAP4SSL", lambda *args, **kwargs: connection)
        return connection

    return make


def run_test():
    return MailTester().test(
        host="mail.example.com", username="reader@example.com", password=SYNTHETIC_PASSWORD,
        port=993, mailbox="INBOX",
    )


def test_imap_check_logs_in_selects_readonly_and_never_downloads_bodies(fake_imap):
    connection = fake_imap()
    assert run_test() == 17
    assert connection.calls == (
        ("login", "reader@example.com", SYNTHETIC_PASSWORD),
        ("select", "INBOX", True),
        ("logout",),
    )


def test_imap_empty_inbox_has_real_zero_count(fake_imap):
    fake_imap(count=b"0")
    assert run_test() == 0


@pytest.mark.parametrize("failure", [
    imaplib.IMAP4.error("Synthetic server echoed " + SYNTHETIC_PASSWORD),
    OSError("Synthetic network echoed " + SYNTHETIC_PASSWORD),
])
def test_imap_failures_are_sanitized_and_connection_is_logged_out(fake_imap, failure, caplog):
    connection = fake_imap(login_error=failure)
    with pytest.raises(AppError) as caught:
        run_test()
    assert SYNTHETIC_PASSWORD not in str(caught.value)
    assert SYNTHETIC_PASSWORD not in caplog.text
    assert ("logout",) in connection.calls
    assert not any(call[0] == "select" for call in connection.calls)


@pytest.mark.parametrize("kwargs", [
    {"select_status": "NO"}, {"count": b"not-a-count"}, {"count": b"-1"},
    {"count": None}, {"login_status": "NO"},
])
def test_imap_selection_or_invalid_count_is_safe_failure(fake_imap, kwargs):
    connection = fake_imap(**kwargs)
    with pytest.raises(AppError):
        run_test()
    assert ("logout",) in connection.calls


def test_imap_pin_keeps_original_hostname_for_tls(monkeypatch):
    def parent_init(self, host, port=993, ssl_context=None, timeout=None):
        self.host = host
        self.port = port
        self.ssl_context = ssl_context

    monkeypatch.setattr(imaplib.IMAP4_SSL, "__init__", parent_init)
    raw_socket, tls_socket = Mock(), Mock()
    connect = Mock(return_value=raw_socket)
    monkeypatch.setattr(socket, "create_connection", connect)
    context = Mock()
    context.wrap_socket.return_value = tls_socket
    connection = PinnedIMAP4SSL(
        "mail.example.com", ["93.184.216.34"], ssl_context=context, timeout=8,
    )
    assert connection._create_socket(8) is tls_socket
    assert connect.call_args.args[0] == ("93.184.216.34", 993)
    context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="mail.example.com")


def test_imap_rejects_nonpublic_dns_before_connect(monkeypatch):
    import brain_settings.mail as module

    connection = Mock()
    monkeypatch.setattr(module, "PinnedIMAP4SSL", connection)

    def reject(host):
        raise AppError("invalid_endpoint", "Внутренние адреса запрещены.", 400)

    monkeypatch.setattr(module, "validate_public_host", reject)
    with pytest.raises(AppError):
        run_test()
    connection.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"username": "reader\r\nother"}, {"password": "secret\r\nother"},
    {"username": "reader other"}, {"password": "   "},
    {"port": 143}, {"mailbox": "OTHER"},
])
def test_direct_imap_tester_validates_credentials_and_settings_before_dns(monkeypatch, changes):
    import brain_settings.mail as module

    resolve = Mock(return_value=["93.184.216.34"])
    connect = Mock(return_value=FakeIMAP())
    monkeypatch.setattr(module, "validate_public_host", resolve)
    monkeypatch.setattr(module, "PinnedIMAP4SSL", connect)
    arguments = {
        "host": "mail.example.com", "username": "reader@example.com",
        "password": SYNTHETIC_PASSWORD, "port": 993, "mailbox": "INBOX", **changes,
    }
    with pytest.raises(AppError):
        MailTester().test(**arguments)
    resolve.assert_not_called()
    connect.assert_not_called()


def test_imap_abort_during_login_is_network_failure_not_bad_password(fake_imap):
    fake_imap(login_error=imaplib.IMAP4.abort("Synthetic connection abort " + SYNTHETIC_PASSWORD))
    with pytest.raises(AppError) as caught:
        run_test()
    assert caught.value.status_code == 502
    assert caught.value.code == "mail_network_error"
    assert SYNTHETIC_PASSWORD not in str(caught.value)


def test_imap_pin_failure_closes_raw_socket(monkeypatch):
    def parent_init(self, host, port=993, ssl_context=None, timeout=None):
        self.host, self.port, self.ssl_context = host, port, ssl_context

    monkeypatch.setattr(imaplib.IMAP4_SSL, "__init__", parent_init)
    raw_socket = Mock()
    monkeypatch.setattr(socket, "create_connection", Mock(return_value=raw_socket))
    context = Mock()
    context.wrap_socket.side_effect = OSError("Synthetic TLS failure")
    connection = PinnedIMAP4SSL("mail.example.com", ["93.184.216.34"], ssl_context=context)
    with pytest.raises(OSError):
        connection._create_socket(8)
    raw_socket.close.assert_called_once()
    empty = PinnedIMAP4SSL("mail.example.com", [], ssl_context=context)
    with pytest.raises(OSError):
        empty._create_socket(8)


def test_imap_logout_failure_uses_shutdown_without_exposing_server_text(monkeypatch, caplog):
    import brain_settings.mail as module

    connection = FakeIMAP()
    connection.logout = Mock(side_effect=OSError("Synthetic logout " + SYNTHETIC_PASSWORD))
    connection.shutdown = Mock(side_effect=OSError("Synthetic shutdown " + SYNTHETIC_PASSWORD))
    monkeypatch.setattr(module, "validate_public_host", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(module, "PinnedIMAP4SSL", lambda *args, **kwargs: connection)
    assert run_test() == 17
    connection.shutdown.assert_called_once()
    assert SYNTHETIC_PASSWORD not in caplog.text
