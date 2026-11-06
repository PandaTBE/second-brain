"""Fixed Microsoft endpoints with verified TLS and an opaque-token public client."""

import http.client
import json
import ssl
import time
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import certifi

from .errors import AppError
from .transport import PinnedHTTPSConnection, validate_public_host

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SCOPES = ("https://graph.microsoft.com/Mail.Read "
          "https://graph.microsoft.com/User.Read offline_access")
AUTH_HOST = "login.microsoftonline.com"
GRAPH_HOST = "graph.microsoft.com"
AUTH_PREFIX = "/common/oauth2/v2.0/"
GRAPH_PATHS = frozenset({"/v1.0/me", "/v1.0/me/mailFolders/inbox",
                         "/v1.0/me/mailFolders/inbox/messages"})


def validate_client_id(value) -> str:
    try:
        if not isinstance(value, str) or len(value) != 36:
            raise ValueError
        parsed = UUID(value)
        if not parsed.int:
            raise ValueError
        return str(parsed)
    except (ValueError, AttributeError) as exc:
        raise AppError("outlook_client_id", "Укажите Application (client) ID приложения "
                       "Microsoft Entra в формате GUID.") from exc


def _secret(value, *, maximum=32_768) -> str:
    if (not isinstance(value, str) or not value or len(value) > maximum
            or not value.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise AppError("outlook_invalid_response", "Microsoft вернул неподдерживаемые "
                       "данные подключения. Повторите вход.", 502)
    return value


def validate_device_response(value) -> dict:
    try:
        if not isinstance(value, dict):
            raise ValueError
        device = _secret(value.get("device_code"))
        code = value.get("user_code")
        uri = value.get("verification_uri")
        expires, interval = value.get("expires_in"), value.get("interval", 5)
        if (not isinstance(code, str) or not 4 <= len(code) <= 32
                or not all(c.isascii() and (c.isalnum() or c == "-") for c in code)
                or type(expires) is not int or not 1 <= expires <= 1800
                or type(interval) is not int or not 1 <= interval <= 60
                or uri not in ("https://microsoft.com/devicelogin",
                               "https://www.microsoft.com/devicelogin")):
            raise ValueError
        return {"device_code": device, "user_code": code, "verification_uri": uri,
                "expires_in": expires, "interval": interval}
    except (ValueError, TypeError) as exc:
        raise AppError("outlook_invalid_response", "Microsoft вернул неподдерживаемые "
                       "данные входа. Повторите подключение.", 502) from exc


def _endpoint(method, host, path, bearer, form, headers):
    try:
        if not isinstance(path, str) or len(path) > 32_768 or not path.isascii():
            raise ValueError
        parsed = urlsplit(path)
        if (parsed.scheme or parsed.netloc or parsed.fragment
                or any(ord(c) < 32 or ord(c) == 127 for c in path)):
            raise ValueError
        if host == GRAPH_HOST and method == "GET":
            if parsed.path not in GRAPH_PATHS or form is not None:
                raise ValueError
            if bearer is not None:
                _secret(bearer)
            if not isinstance(headers or {}, dict) or set(headers or {}) - {"Prefer"}:
                raise ValueError
            for value in (headers or {}).values():
                if (not isinstance(value, str) or len(value) > 300 or not value.isascii()
                        or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                    raise ValueError
            return
        if host != AUTH_HOST or method != "POST" or parsed.query or bearer is not None or headers:
            raise ValueError
        allowed = ({"client_id", "scope"} if parsed.path == AUTH_PREFIX + "devicecode"
                   else {"client_id", "scope", "grant_type", "device_code", "refresh_token"})
        if (parsed.path not in (AUTH_PREFIX + "devicecode", AUTH_PREFIX + "token")
                or not isinstance(form, dict) or not form or set(form) - allowed
                or any(not isinstance(k, str) or not isinstance(v, str)
                       or len(v) > 32_768 for k, v in form.items())):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise AppError("outlook_endpoint", "Для Outlook разрешены только выбранные "
                       "HTTPS-службы Microsoft и чтение входящих.") from exc


class OutlookTransport:
    def request_json(self, method: str, host: str, path: str, *, bearer=None, form=None,
                     headers=None) -> tuple[int, dict]:
        _endpoint(method, host, path, bearer, form, headers)
        addresses = validate_public_host(host)
        context = ssl.create_default_context(cafile=certifi.where())
        context.set_alpn_protocols(["http/1.1"])
        connection = PinnedHTTPSConnection(host, addresses, context, timeout=12)
        request_headers = {"Accept": "application/json", "Accept-Encoding": "identity",
                           "User-Agent": "SecondBrainLocal/0.1", **(headers or {})}
        if bearer is not None:
            request_headers["Authorization"] = "Bearer " + bearer
        body = None if form is None else urlencode(form).encode("ascii")
        if body is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            if body is not None and len(body) > 131_072:
                raise AppError("outlook_request_limit", "Запрос подключения Outlook слишком велик.")
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise AppError("outlook_redirect", "Перенаправление Microsoft API отклонено.", 502)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise AppError("outlook_response_limit", "Ответ Microsoft превышает безопасный "
                               "объём. Уже импортированные материалы сохранены.", 502)
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
            return response.status, payload
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise AppError("outlook_invalid_response", "Microsoft вернул неподдерживаемый "
                           "ответ. Повторите действие позже.", 502) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise AppError("outlook_network_error", "Не удалось подключиться к Microsoft. "
                           "Проверьте интернет и повторите действие.", 502) from exc
        finally:
            connection.close()


def _oauth_error(payload, status):
    code = payload.get("error")
    errors = payload.get("error_codes", ())
    if isinstance(errors, list) and any(c in (65001, 65004, 90094, 53003) for c in errors):
        return AppError("outlook_admin_consent", "Вход ограничен политикой организации. "
                        "Попросите IT-отдел разрешить приложение и чтение почты.", 403)
    mapping = {
        "authorization_declined": ("outlook_auth_declined", "Вход Microsoft отменён.", 403),
        "expired_token": ("outlook_auth_expired", "Код входа истёк. Начните подключение заново.",
                          410),
        "bad_verification_code": ("outlook_auth_expired", "Код входа недействителен. "
                                  "Начните подключение заново.", 410),
        "invalid_grant": ("outlook_reauth_required", "Доступ Microsoft истёк или отозван. "
                          "Подключите Outlook заново.", 401),
        "invalid_client": ("outlook_client_configuration", "Проверьте Client ID и регистрацию "
                           "публичного приложения Microsoft Entra.", 400),
        "unauthorized_client": ("outlook_client_configuration", "Разрешите public client flows "
                                "в регистрации Microsoft Entra или обратитесь в IT-отдел.", 400),
        "access_denied": ("outlook_auth_declined", "Microsoft не предоставил доступ. "
                          "Проверьте разрешения приложения и политику организации.", 403),
    }
    if isinstance(code, str) and code in mapping:
        return AppError(*mapping[code])
    if status == 429:
        return AppError("outlook_rate_limited", "Microsoft временно ограничил подключения. "
                        "Повторите вход позже.", 429)
    return AppError("outlook_auth_failed", "Не удалось завершить вход Microsoft. "
                    "Проверьте регистрацию приложения и повторите действие.", 502)


def _tokens(payload, now, previous_refresh=None):
    expires = payload.get("expires_in")
    if type(expires) is not int or not 1 <= expires <= 86_400:
        raise AppError("outlook_invalid_response", "Microsoft вернул неподдерживаемый "
                       "срок подключения. Повторите вход.", 502)
    if str(payload.get("token_type", "Bearer")).lower() != "bearer":
        raise AppError("outlook_invalid_response", "Тип подключения Microsoft не поддерживается.",
                       502)
    scope = payload.get("scope")
    if scope is not None:
        if not isinstance(scope, str) or len(scope) > 2048:
            raise AppError("outlook_invalid_response", "Не удалось проверить разрешения Microsoft.",
                           502)
        scopes = {s.lower().removeprefix("https://graph.microsoft.com/") for s in scope.split()}
        if (not {"mail.read", "user.read"} <= scopes
                or scopes - {"mail.read", "user.read", "offline_access",
                             "openid", "profile", "email"}):
            raise AppError("outlook_scope", "Для Outlook нужны только разрешения чтения почты "
                           "и профиля. Повторите подключение с нужными правами.", 403)
    return {"access_token": _secret(payload.get("access_token")),
            "refresh_token": _secret(payload.get("refresh_token", previous_refresh)),
            "expires_at": now + expires}


class MicrosoftOAuthClient:
    def __init__(self, transport=None, *, clock=time.time):
        self.transport = transport if transport is not None else OutlookTransport()
        self.clock = clock

    def start(self, client_id: str) -> dict:
        client_id = validate_client_id(client_id)
        status, payload = self.transport.request_json(
            "POST", AUTH_HOST, AUTH_PREFIX + "devicecode",
            form={"client_id": client_id, "scope": SCOPES})
        if status != 200:
            raise _oauth_error(payload, status)
        return validate_device_response(payload)

    def poll(self, client_id: str, device_code: str) -> dict:
        client_id, device_code = validate_client_id(client_id), _secret(device_code)
        status, payload = self.transport.request_json(
            "POST", AUTH_HOST, AUTH_PREFIX + "token",
            form={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                  "client_id": client_id, "device_code": device_code})
        if status == 400 and payload.get("error") in ("authorization_pending", "slow_down"):
            return {"status": "pending" if payload["error"] == "authorization_pending"
                    else "slow_down"}
        if status != 200:
            raise _oauth_error(payload, status)
        return {"status": "completed", "tokens": _tokens(payload, self.clock())}

    def refresh(self, client_id: str, refresh_token: str) -> dict:
        client_id, refresh_token = validate_client_id(client_id), _secret(refresh_token)
        status, payload = self.transport.request_json(
            "POST", AUTH_HOST, AUTH_PREFIX + "token",
            form={"grant_type": "refresh_token", "client_id": client_id,
                  "refresh_token": refresh_token, "scope": SCOPES})
        if status != 200:
            raise _oauth_error(payload, status)
        return _tokens(payload, self.clock(), refresh_token)


OAuthClient = MicrosoftOAuthClient
