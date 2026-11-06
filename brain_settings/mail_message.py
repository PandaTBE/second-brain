"""Bounded RFC822 to readable text; no attachments or remote resources are loaded."""

import re
from datetime import UTC
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from .errors import AppError

MAX_MESSAGE_BYTES = 1024 * 1024
MAX_PARTS = 100
MAX_DEPTH = 20


def _invalid() -> AppError:
    return AppError("mail_message_invalid", "Не удалось разобрать полный текст письма. "
                    "Письмо пропущено; исходник в почте сохранён.", 502)


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pieces = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "template", "svg"):
            self.hidden += 1
        if not self.hidden and tag in ("p", "div", "br", "li", "tr", "blockquote", "hr"):
            self.pieces.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "template", "svg"):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in ("p", "div", "li", "tr", "blockquote"):
            self.pieces.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.pieces.append(data)


def _check_parts(message) -> None:
    pending, count = [(message, 0)], 0
    while pending:
        part, depth = pending[-1]
        pending, count = pending[:-1], count + 1
        if count > MAX_PARTS or depth > MAX_DEPTH:
            raise _invalid()
        payload = part.get_payload()
        if isinstance(payload, list):
            pending = [*pending, *((child, depth + 1) for child in payload)]


def _extract(part, *, related_root=False) -> tuple[str, bool]:
    if (part.get_content_disposition() == "attachment"
            or (part.get_filename() and not related_root)
            or (part.get("Content-ID") and not related_root)
            or part.get_content_type() == "message/rfc822"):
        return "", False
    if part.is_multipart():
        if part.get_content_subtype() == "related":
            children = part.get_payload()
            start = part.get_param("start")
            root = (next((child for child in children if child.get("Content-ID") == start), None)
                    if start else (children[0] if children else None))
            return _extract(root, related_root=True) if root is not None else ("", False)
        values = [_extract(child) for child in part.get_payload()]
        if part.get_content_subtype() == "alternative":
            return next((value for value in values if value[0].strip() and value[1]),
                        next((value for value in values if value[0].strip()), ("", False)))
        return "\n\n".join(value[0] for value in values if value[0].strip()), any(
            value[1] for value in values
        )
    content_type = part.get_content_type()
    if content_type not in ("text/plain", "text/html"):
        return "", False
    raw = part.get_payload(decode=True)
    if not isinstance(raw, bytes) or part.defects:
        raise _invalid()
    try:
        text = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    if content_type == "text/html":
        parser = _HTMLText()
        parser.feed(text)
        parser.close()
        return "".join(parser.pieces), False
    return text, True


def _header(message, name, default="") -> str:
    value = str(message.get(name, default))
    return " ".join(value.replace("\x00", "").split())[:2000]


def parse_message(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or len(raw) > MAX_MESSAGE_BYTES or b"\x00" in raw:
        raise _invalid()
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        _check_parts(message)
        if message.defects:
            raise _invalid()
        body, _ = _extract(message)
        if not body.strip():
            raise AppError("mail_message_empty", "В письме нет доступного текстового содержимого. "
                           "Вложения и зашифрованные части не импортируются.", 422)
        title = _header(message, "Subject", "Без темы") or "Без темы"
        date = _header(message, "Date")
        try:
            timestamp = parsedate_to_datetime(date)
            timestamp = (timestamp.astimezone(UTC).isoformat()
                         if timestamp.tzinfo is not None else None)
        except (ValueError, TypeError, OverflowError):
            timestamp = None
        message_id = _header(message, "Message-ID")
        if not re.fullmatch(r"<[^\s<>]{1,996}>", message_id):
            message_id = ""
        headers = "\n".join(f"{key}: {_header(message, key)}"
                            for key in ("From", "To", "Date") if message.get(key))
        text = "# " + title + "\n\n" + headers + "\n\n" + body.strip()
        return {"title": title[:256], "text": text, "timestamp": timestamp,
                "message_id": message_id}
    except (RecursionError, ValueError, TypeError, UnicodeError, AttributeError) as exc:
        raise _invalid() from exc
