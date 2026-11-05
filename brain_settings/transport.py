"""Bounded HTTPS reads with validated, pinned public destinations."""

import http.client
import ipaddress
import json
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from urllib.parse import urlsplit

import certifi

from .errors import AppError
from .network_policy import address_allowed, hostname_allowed
from .provider_errors import MAX_ERROR_BYTES, rate_limit_error

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="brain-dns")


def validate_public_host(hostname: str, *, allow_private_network: bool = False) -> list[str]:
    if type(allow_private_network) is not bool:
        raise AppError("invalid_network_policy", "Укажите явное разрешение внутренней сети.")
    if not hostname_allowed(hostname, allow_private_network=allow_private_network):
        raise AppError("unsafe_endpoint", "Подключение к внутренним именам требует разрешения "
                       "для своего API. Локальные службы недоступны.")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        future = DNS_POOL.submit(socket.getaddrinfo, hostname, 443, type=socket.SOCK_STREAM)
        try:
            answers = future.result(timeout=3)
        except (OSError, FutureTimeout) as exc:
            future.cancel()
            raise AppError("network_error", "Не удалось определить адрес провайдера. "
                           "Проверьте подключение к интернету.", 502) from exc
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:
        raise AppError("network_error", "Провайдер не вернул доступный адрес.", 502)
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise AppError("unsafe_endpoint", "Провайдер вернул недопустимый адрес.") from exc
        if not address_allowed(parsed, allow_private_network=allow_private_network):
            message = ("Этот адрес недоступен: локальные, служебные и metadata-адреса запрещены."
                       if allow_private_network else
                       "Подключение к внутренним и локальным адресам запрещено. "
                       "Для корпоративного сервера разрешите внутреннюю сеть "
                       "в настройках своего API.")
            raise AppError("unsafe_endpoint", message)
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a validated numeric IP; verify TLS against the original host."""

    def __init__(self, host: str, addresses: list[str], context: ssl.SSLContext,
                 timeout: float = 12.0):
        super().__init__(host, port=443, context=context, timeout=timeout)
        self._addresses = tuple(addresses)

    def connect(self):
        last_error = None
        for address in self._addresses:
            raw_socket = None
            try:
                raw_socket = socket.create_connection((address, 443), timeout=self.timeout)
                self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
                return
            except OSError as exc:
                if raw_socket is not None:
                    raw_socket.close()
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError("No validated addresses")


def _check_status(status: int) -> None:
    if status in (401, 403):
        raise AppError("authentication_failed", "Провайдер отклонил API-токен. "
                       "Проверьте ключ и его права.", 401)
    if status == 429:
        raise rate_limit_error()
    if 300 <= status < 400:
        raise AppError("redirect_blocked", "Провайдер перенаправляет запрос. "
                       "Укажите конечный HTTPS-адрес API.", 502)
    if status < 200 or status >= 300:
        raise AppError("upstream_error", "Провайдер не смог выполнить запрос. "
                       "Проверьте адрес API или повторите позже.", 502)


class ProviderTransport:
    def post_json(self, base_url: str, path: str, payload: dict,
                  token: str | None = None, timeout: float = 45,
                  *, allow_private_network: bool = False) -> dict:
        """Send one POST; queue-level retry policy controls additional paid requests."""
        url = urlsplit(base_url)
        if (url.scheme != "https" or url.username or url.password or url.query or url.fragment
                or url.port not in (None, 443) or path != "/chat/completions"):
            raise AppError("invalid_endpoint", "Укажите конечный публичный HTTPS-адрес API.")
        hostname = url.hostname or ""
        if type(allow_private_network) is not bool:
            raise AppError("invalid_network_policy", "Укажите явное разрешение внутренней сети.")
        addresses = (validate_public_host(hostname, allow_private_network=True)
                     if allow_private_network else validate_public_host(hostname))
        context = ssl.create_default_context(cafile=certifi.where())
        context.set_alpn_protocols(["http/1.1"])
        connection = PinnedHTTPSConnection(hostname, addresses, context, timeout=min(timeout, 60))
        headers = {"Accept": "application/json", "Accept-Encoding": "identity",
                   "Content-Type": "application/json", "User-Agent": "SecondBrainLocal/0.1"}
        if token is not None:
            headers = {**headers, "Authorization": "Bearer " + token}
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(body) > 131_072:
                raise AppError("request_too_large", "Текст для карточки превышает "
                               "допустимый объём.")
            connection.request("POST", url.path.rstrip("/") + path, body=body, headers=headers)
            response = connection.getresponse()
            if response.status == 429:
                retry_after = response.getheader("Retry-After")
                try:
                    error_body = response.read(MAX_ERROR_BYTES)
                except (OSError, http.client.HTTPException):
                    error_body = b""
                raise rate_limit_error(retry_after, error_body)
            _check_status(response.status)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise AppError("response_too_large", "Ответ модели превышает допустимый объём.",
                               502)
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (json.JSONDecodeError, UnicodeError, ValueError, TypeError) as exc:
            raise AppError("invalid_response", "Модель вернула неподдерживаемый ответ.",
                           502) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise AppError("network_error", "Не удалось подключиться к модели. "
                           "Исходный текст остаётся доступным для поиска.", 502) from exc
        finally:
            connection.close()

    def request(self, base_url: str, path: str, token: str | None = None,
                *, allow_private_network: bool = False) -> dict:
        url = urlsplit(base_url)
        if (url.scheme != "https" or url.username or url.password or url.query or url.fragment
                or url.port not in (None, 443)):
            raise AppError("invalid_endpoint", "Укажите HTTPS-адрес API на стандартном порту.")
        hostname = url.hostname or ""
        if type(allow_private_network) is not bool:
            raise AppError("invalid_network_policy", "Укажите явное разрешение внутренней сети.")
        addresses = (validate_public_host(hostname, allow_private_network=True)
                     if allow_private_network else validate_public_host(hostname))
        context = ssl.create_default_context(cafile=certifi.where())
        context.set_alpn_protocols(["http/1.1"])
        connection = PinnedHTTPSConnection(hostname, addresses, context, timeout=12.0)
        headers = {"Accept": "application/json", "Accept-Encoding": "identity",
                   "User-Agent": "SecondBrainLocal/0.1"}
        if token is not None:
            headers = {**headers, "Authorization": "Bearer " + token}
        try:
            connection.request("GET", url.path.rstrip("/") + path, headers=headers)
            response = connection.getresponse()
            _check_status(response.status)
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise AppError("response_too_large", "Каталог провайдера слишком большой. "
                               "Укажите ID модели вручную.", 502)
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
            raise AppError("invalid_response", "Провайдер вернул неподдерживаемый ответ. "
                           "ID модели можно указать вручную.", 502) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise AppError("network_error", "Не удалось подключиться к провайдеру. "
                           "Проверьте интернет и адрес API.", 502) from exc
        finally:
            connection.close()
