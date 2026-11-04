"""Read-only IMAP access checks over verified TLS and a pinned public IP."""

import imaplib
import ipaddress
import logging
import re
import socket
import ssl

import certifi

from .errors import AppError
from .transport import validate_public_host

logger = logging.getLogger(__name__)


def validate_imap_host(value: str | None) -> str:
    try:
        host = (value or "").strip().encode("idna").decode("ascii").lower()
        if not host or not re.fullmatch(r"[a-z0-9.-]+", host) or ".." in host:
            raise ValueError
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (not address.is_global or address.is_multicast):
            raise ValueError
        return host
    except (ValueError, UnicodeError) as exc:
        raise AppError("invalid_mail_host", "Укажите публичный адрес IMAP-сервера "
                       "без https:// и номера порта.") from exc


def validate_mail_username(value: str | None) -> str:
    username = (value or "").strip()
    if (not username or not username.isascii()
            or any(ord(char) < 33 or ord(char) > 126 or char in '(){}[]%*"\\'
                   for char in username)):
        raise AppError("invalid_mail_username", "Введите имя пользователя почтового сервера "
                       "без пробелов и служебных символов IMAP.")
    return username


def validate_mail_password(value: str) -> str:
    if (not value.strip() or not value.isascii()
            or any(ord(char) < 32 or ord(char) > 126 for char in value)):
        raise AppError("invalid_mail_password", "Проверьте формат пароля приложения для почты.")
    return value


class PinnedIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, addresses: list[str], *, ssl_context: ssl.SSLContext,
                 timeout: float = 12.0):
        self._addresses = tuple(addresses)
        super().__init__(host=host, port=993, ssl_context=ssl_context, timeout=timeout)

    def _create_socket(self, timeout):
        last_error = None
        for address in self._addresses:
            raw_socket = None
            try:
                raw_socket = socket.create_connection((address, self.port), timeout=timeout)
                return self.ssl_context.wrap_socket(raw_socket, server_hostname=self.host)
            except OSError as exc:
                if raw_socket is not None:
                    raw_socket.close()
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError("No validated IMAP addresses")


class MailTester:
    def test(self, *, host: str, username: str, password: str, port: int = 993,
             mailbox: str = "INBOX") -> int:
        if port != 993 or mailbox != "INBOX":
            raise AppError("invalid_mail_settings", "Поддерживается только INBOX по TLS, порт 993.")
        username = validate_mail_username(username)
        password = validate_mail_password(password)
        host = validate_imap_host(host)
        addresses = validate_public_host(host)
        context = ssl.create_default_context(cafile=certifi.where())
        client = None
        try:
            client = PinnedIMAP4SSL(host, addresses, ssl_context=context, timeout=12.0)
            try:
                result, _ = client.login(username, password)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error as exc:
                raise AppError("mail_auth_failed", "Почтовый сервер отклонил вход. "
                               "Проверьте имя пользователя и пароль приложения.", 401) from exc
            if result != "OK":
                raise AppError("mail_auth_failed", "Не удалось войти в почту.", 401)
            result, data = client.select(mailbox, readonly=True)
            if result != "OK" or not data or data[0] is None:
                raise AppError("mailbox_unavailable", "Не удалось открыть INBOX для чтения.", 502)
            count = int(data[0])
            if count < 0:
                raise ValueError
            return count
        except (OSError, imaplib.IMAP4.error, ValueError) as exc:
            raise AppError("mail_network_error", "Не удалось проверить почту. "
                           "Проверьте интернет, адрес сервера и доступ к IMAP.", 502) from exc
        finally:
            if client is not None:
                try:
                    client.logout()
                except (OSError, imaplib.IMAP4.error):
                    try:
                        client.shutdown()
                    except OSError:
                        logger.warning("Could not complete IMAP connection shutdown")
