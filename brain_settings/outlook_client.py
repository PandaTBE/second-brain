"""Read-only, bounded Microsoft Graph Inbox access with opaque OAuth credentials."""

import json
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from .errors import AppError

GRAPH_HOST = "graph.microsoft.com"
INBOX_PATH = "/v1.0/me/mailFolders/inbox/messages"
MAX_MESSAGES = 50
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_LINK_BYTES = 8192
MESSAGE_FIELDS = "id,subject,from,receivedDateTime,lastModifiedDateTime,body,webLink"
PREFER = 'IdType="ImmutableId", outlook.body-content-type="text"'
OUTLOOK_HOSTS = frozenset({"outlook.office.com", "outlook.office365.com", "outlook.live.com"})


def _invalid(message="Microsoft Graph вернул неполные или неподдерживаемые данные."):
    return AppError("outlook_response_invalid", message, 502)


def _token(value):
    if (not isinstance(value, str) or not value or len(value) > 32_768
            or not value.isascii() or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise AppError("outlook_auth_required", "Войдите в Outlook заново.", 401)
    return value


def message_path(link: str) -> str:
    """Validate a Graph-provided page URL without rewriting its opaque query."""
    if (not isinstance(link, str) or not link or len(link) > MAX_LINK_BYTES
            or not link.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in link)):
        raise _invalid("Небезопасная ссылка продолжения Outlook отклонена.")
    try:
        parsed = urlsplit(link)
        if (parsed.scheme != "https" or parsed.hostname != GRAPH_HOST
                or parsed.port not in (None, 443) or parsed.username is not None
                or parsed.password is not None or parsed.fragment or parsed.path != INBOX_PATH):
            raise ValueError
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        allowed = {"$skip", "$skiptoken", "$select", "$top", "$filter", "$orderby", "$count"}
        if len(pairs) > 12 or any(key not in allowed for key, _ in pairs):
            raise ValueError
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError
        values = dict(pairs)
        if "$top" in values and (not values["$top"].isdigit()
                                  or not 1 <= int(values["$top"]) <= MAX_MESSAGES):
            raise ValueError
        if "$select" in values and not set(values["$select"].split(",")).issubset(
            MESSAGE_FIELDS.split(",")
        ):
            raise ValueError
    except (ValueError, UnicodeError) as exc:
        raise _invalid("Небезопасная ссылка продолжения Outlook отклонена.") from exc
    return parsed.path + ("?" + parsed.query if parsed.query else "")


def _timestamp(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise _invalid()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC).isoformat()
    except ValueError as exc:
        raise _invalid() from exc


def _header(value, limit=4096):
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise _invalid()
    return value.strip()


class _TextBody(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pieces = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "template", "svg"):
            self.hidden += 1
        elif not self.hidden and tag in ("p", "div", "br", "li", "tr", "blockquote", "hr"):
            self.pieces.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "template", "svg"):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in ("p", "div", "li", "tr", "blockquote"):
            self.pieces.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.pieces.append(data)


def _source_uri(identifier, value):
    if isinstance(value, str) and len(value) <= MAX_LINK_BYTES and not any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        try:
            url = urlsplit(value)
            if (url.scheme == "https" and url.hostname in OUTLOOK_HOSTS
                    and url.port in (None, 443) and url.username is None and url.password is None):
                return value
        except ValueError:
            pass
    return "https://graph.microsoft.com/v1.0/me/messages/" + quote(identifier, safe="")


def _message(value):
    if not isinstance(value, dict):
        raise _invalid()
    try:
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise AppError("outlook_message_large", "Письмо Outlook больше 1 МиБ пропущено.")
        identifier = _header(value.get("id"), 2048)
        if not identifier:
            raise _invalid()
        subject = _header(value.get("subject", "")) or "Без темы"
        received = _timestamp(value.get("receivedDateTime"))
        updated = _timestamp(value.get("lastModifiedDateTime") or value.get("receivedDateTime"))
        body = value.get("body")
        content_type = body.get("contentType") if isinstance(body, dict) else None
        if (not isinstance(content_type, str) or content_type.lower() not in ("text", "html")
                or not isinstance(body.get("content"), str)):
            raise _invalid()
        text = body["content"]
        if body["contentType"].lower() == "html":
            parser = _TextBody()
            parser.feed(text)
            parser.close()
            text = "".join(parser.pieces)
        sender = value.get("from", {})
        sender = sender.get("emailAddress", {}) if isinstance(sender, dict) else {}
        sender = sender if isinstance(sender, dict) else {}
        name = _header(sender.get("name", ""), 320)
        address = _header(sender.get("address", ""), 320)
        byline = " ".join(part for part in (name, "<" + address + ">" if address else "") if part)
        full = "# " + subject + ("\nОт: " + byline if byline else "") + "\nДата: " + received
        return {"id": identifier, "subject": subject, "text": full + "\n\n" + text.strip(),
                "created_at": received, "updated_at": updated,
                "source_uri": _source_uri(identifier, value.get("webLink"))}
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc


class OutlookClient:
    def __init__(self, transport=None, oauth_client=None, *, now=None):
        if transport is None:
            from .outlook_transport import OutlookTransport
            transport = OutlookTransport()
        self.transport = transport
        self.oauth_client = oauth_client
        self.now = now or (lambda: datetime.now(UTC))

    def _get(self, access_token, path):
        status, payload = self.transport.request_json(
            "GET", GRAPH_HOST, path, bearer=_token(access_token), headers={"Prefer": PREFER},
        )
        if status == 401:
            raise AppError("outlook_auth_required", "Войдите в Outlook заново.", 401)
        if status == 403:
            raise AppError("outlook_access_denied", "Нет доступа к Outlook. Проверьте разрешения "
                           "приложения Microsoft или обратитесь к администратору.", 403)
        if status == 404:
            raise AppError("outlook_mailbox_unavailable", "Папка входящих Outlook недоступна. "
                           "Проверьте, что у выбранной учётной записи есть почтовый ящик.", 404)
        if status == 429:
            raise AppError("outlook_rate_limited", "Outlook временно ограничил запросы. "
                           "Повторите синхронизацию позже.", 429)
        if status != 200 or not isinstance(payload, dict):
            raise AppError("outlook_unavailable", "Не удалось прочитать данные Outlook.", 502)
        return payload

    def profile(self, access_token):
        value = self._get(access_token, "/v1.0/me?" + urlencode({
            "$select": "id,mail,userPrincipalName,displayName",
        }))
        identifier = _header(value.get("id"), 256)
        username = _header(value.get("mail") or value.get("userPrincipalName") or "", 320)
        display = _header(value.get("displayName") or username, 320)
        if not identifier or not username:
            raise _invalid("Не удалось определить выбранную учётную запись Outlook.")
        return {"id": identifier, "username": username, "display_name": display or username}

    def test(self, access_token):
        value = self._get(access_token, "/v1.0/me/mailFolders/inbox?" + urlencode({
            "$select": "totalItemCount",
        }))
        count = value.get("totalItemCount")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise _invalid()
        return count

    def messages(self, access_token, lookback_days, next_link=None):
        if type(lookback_days) is not int or lookback_days not in (0, 30, 90, 365):
            raise AppError("invalid_mail_period", "Выберите период импорта почты.")
        if next_link is not None:
            path = message_path(next_link)
        else:
            query = {"$top": str(MAX_MESSAGES), "$select": MESSAGE_FIELDS,
                     "$orderby": "receivedDateTime desc"}
            if lookback_days:
                cutoff = (self.now().astimezone(UTC) - timedelta(days=lookback_days)).isoformat()
                query["$filter"] = "receivedDateTime ge " + cutoff
            path = INBOX_PATH + "?" + urlencode(query, quote_via=quote)
        value = self._get(access_token, path)
        rows = value.get("value")
        if not isinstance(rows, list) or len(rows) > MAX_MESSAGES:
            raise _invalid()
        following = value.get("@odata.nextLink")
        if following is not None:
            message_path(following)
            if following == next_link:
                raise _invalid("Outlook вернул повторяющуюся ссылку продолжения.")
        messages, warnings, skipped = [], [], 0
        for row in rows:
            try:
                messages = [*messages, _message(row)]
            except AppError as error:
                skipped += 1
                warning = (error.message if error.code == "outlook_message_large"
                           else "Письмо Outlook с неполными данными пропущено.")
                if warning not in warnings:
                    warnings = [*warnings, warning]
        return {"messages": messages, "next_link": following,
                "warnings": warnings, "skipped": skipped}

    def refresh(self, credentials):
        if self.oauth_client is None:
            from .outlook_transport import MicrosoftOAuthClient
            self.oauth_client = MicrosoftOAuthClient()
        tokens = self.oauth_client.refresh(credentials["client_id"], credentials["refresh_token"])
        account = self.profile(tokens["access_token"])
        if account["id"] != credentials["account_id"]:
            raise AppError("outlook_account_changed", "Обновление Outlook вернуло другую учётную "
                           "запись. Подключите почту заново.", 401)
        return {**credentials, **tokens, "username": account["username"]}
