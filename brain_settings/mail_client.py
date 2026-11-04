"""Read-only pinned IMAP access with bounded UID windows and wire literals."""

import imaplib
import logging
import re
import ssl
from contextlib import contextmanager
from datetime import date

import certifi

from .errors import AppError
from .mail import (
    PinnedIMAP4SSL,
    validate_imap_host,
    validate_mail_password,
    validate_mail_username,
)
from .mail_message import MAX_MESSAGE_BYTES
from .transport import validate_public_host

logger = logging.getLogger(__name__)
MAX_UID = 4_294_967_295
UID_WINDOW = 2000
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _noop():
    pass


def _protocol() -> AppError:
    return AppError("mail_protocol", "Почта вернула неполный или некорректный ответ. "
                    "Повторите импорт позже.", 502)


def _uid(value, *, next_uid=False) -> int:
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 1 <= value <= MAX_UID + int(next_uid)):
        raise _protocol()
    return value


class BoundedIMAP4SSL(PinnedIMAP4SSL):
    def read(self, size):
        # imaplib otherwise allocates any literal size announced by the remote server.
        if not isinstance(size, int) or size < 0 or size > MAX_MESSAGE_BYTES + 1:
            self._literal_rejected = True
            raise AppError("mail_literal_large", "Почта вернула слишком большой фрагмент. "
                           "Загрузка остановлена без изменения писем.", 502)
        return super().read(size)


def _response_integer(client, name, *, next_uid=False):
    response = client.response(name)
    try:
        values = response[1]
        if not isinstance(values, list) or len(values) != 1:
            raise ValueError
        raw = values[0]
        if not isinstance(raw, bytes) or not re.fullmatch(rb"[0-9]{1,10}", raw):
            raise ValueError
        return _uid(int(raw), next_uid=next_uid)
    except (ValueError, TypeError, IndexError) as exc:
        raise _protocol() from exc


class MailSession:
    def __init__(self, client, check_stop):
        self.client, self.check_stop = client, check_stop
        self.uid_validity = _response_integer(client, "UIDVALIDITY")
        self.uid_next = _response_integer(client, "UIDNEXT", next_uid=True)

    def search(self, start_uid: int, end_uid: int, since: date | None = None) -> tuple[int, ...]:
        start, end = _uid(start_uid), _uid(end_uid)
        if end < start or end - start >= UID_WINDOW:
            raise _protocol()
        criteria = (None, "UID", f"{start}:{end}")
        if since is not None:
            if not isinstance(since, date):
                raise _protocol()
            formatted = f"{since.day:02d}-{MONTHS[since.month - 1]}-{since.year:04d}"
            criteria = (*criteria, "SINCE", formatted)
        self.check_stop()
        status, data = self.client.uid("SEARCH", *criteria)
        self.check_stop()
        if (status != "OK" or not isinstance(data, list) or len(data) != 1
                or not isinstance(data[0], bytes) or len(data[0]) > 64_000):
            raise _protocol()
        values = data[0].split(maxsplit=UID_WINDOW)
        if len(values) > UID_WINDOW or any(not re.fullmatch(rb"[0-9]{1,10}", x) for x in values):
            raise _protocol()
        result = tuple(sorted(int(value) for value in values))
        if len(set(result)) != len(result) or any(not start <= value <= end for value in result):
            raise _protocol()
        return result

    @staticmethod
    def _size(rows, uid):
        if not isinstance(rows, list):
            raise _protocol()
        if not rows or rows == [None]:
            raise AppError("mail_message_missing", "Письмо больше не находится во входящих.", 404)
        for row in rows:
            header = row[0] if isinstance(row, tuple) else row
            if not isinstance(header, bytes):
                continue
            identifier = re.search(rb"\bUID ([0-9]{1,10})\b", header)
            size = re.search(rb"\bRFC822\.SIZE ([0-9]{1,12})\b", header)
            if identifier and size and int(identifier[1]) == uid:
                return int(size[1])
        raise _protocol()

    def fetch(self, uid: int) -> bytes:
        uid = _uid(uid)
        self.check_stop()
        status, rows = self.client.uid("FETCH", str(uid), "(UID RFC822.SIZE)")
        self.check_stop()
        if status != "OK":
            raise _protocol()
        size = self._size(rows, uid)
        if size > MAX_MESSAGE_BYTES:
            raise AppError("mail_message_large", "Письмо превышает 1 МиБ и пропущено без загрузки "
                           "тела. Большие вложения также входят в размер письма.", 422)
        self.check_stop()
        status, rows = self.client.uid(
            "FETCH", str(uid), f"(UID RFC822.SIZE BODY.PEEK[]<0.{MAX_MESSAGE_BYTES + 1}>)"
        )
        self.check_stop()
        if status != "OK" or self._size(rows, uid) != size:
            raise _protocol()
        parts = [row[1] for row in rows if isinstance(row, tuple) and len(row) == 2]
        if (len(parts) != 1 or not isinstance(parts[0], bytes) or len(parts[0]) != size
                or len(parts[0]) > MAX_MESSAGE_BYTES):
            raise _protocol()
        return parts[0]


class MailClient:
    @contextmanager
    def open(self, source: dict, password: str, check_stop=_noop):
        check_stop()
        if source.get("port", 993) != 993 or source.get("mailbox", "INBOX") != "INBOX":
            raise AppError("invalid_mail_settings", "Поддерживаются только входящие по TLS.")
        host = validate_imap_host(source.get("host"))
        username = validate_mail_username(source.get("username"))
        password = validate_mail_password(password)
        addresses = validate_public_host(host)
        check_stop()
        context = ssl.create_default_context(cafile=certifi.where())
        client = None
        try:
            client = BoundedIMAP4SSL(host, addresses, ssl_context=context, timeout=12.0)
            check_stop()
            try:
                status, _ = client.login(username, password)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error as exc:
                raise AppError("mail_auth_failed", "Почта отклонила вход. Проверьте адрес "
                               "и пароль приложения, а также доступ к IMAP.", 401) from exc
            check_stop()
            if status != "OK":
                raise AppError("mail_auth_failed", "Не удалось войти в почту.", 401)
            status, _ = client.select("INBOX", readonly=True)
            check_stop()
            if status != "OK":
                raise AppError("mailbox_unavailable",
                               "Не удалось открыть входящие для чтения.", 502)
            yield MailSession(client, check_stop)
        except (OSError, imaplib.IMAP4.error, ValueError) as exc:
            raise AppError("mail_network_error", "Не удалось прочитать почту. Проверьте интернет "
                           "и доступ к IMAP; исходные письма сохранены.", 502) from exc
        finally:
            if client is not None:
                try:
                    if getattr(client, "_literal_rejected", False):
                        client.shutdown()
                    else:
                        client.logout()
                except (OSError, imaplib.IMAP4.error):
                    try:
                        client.shutdown()
                    except OSError:
                        logger.warning("Could not complete read-only mail shutdown")
