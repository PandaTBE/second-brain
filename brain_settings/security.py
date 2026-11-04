"""Loopback, origin, CSRF, request-size and response-header boundaries."""

import re
import secrets

from starlette.responses import JSONResponse

from .errors import AppError, error_content

MAX_REQUEST_BYTES = 16 * 1024
SECURITY_HEADERS = (
    (b"content-security-policy", b"default-src 'self'; script-src 'self'; "
     b"style-src 'self'; img-src 'self' data:; connect-src 'self'; "
     b"font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)


class LocalSecurityMiddleware:
    def __init__(self, app, csrf_token: str):
        self.app = app
        self.csrf_token = csrf_token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value.decode("latin-1") for key, value in scope["headers"]}
        is_api = scope["path"].startswith("/api/")

        async def safe_send(message):
            if message["type"] == "http.response.start":
                original = [(key, value) for key, value in message.get("headers", [])
                            if key.lower() not in {item[0] for item in SECURITY_HEADERS}
                            and (not is_api or key.lower() != b"cache-control")]
                extra = ((b"cache-control", b"no-store"),) if is_api else ()
                message = {**message, "headers": [*original, *SECURITY_HEADERS, *extra]}
            await send(message)

        try:
            self._check_host(headers)
            if is_api and scope["method"] in ("POST", "PUT", "PATCH", "DELETE"):
                self._check_mutation(scope, headers)
                body = b""
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    body += event.get("body", b"")
                    if len(body) > MAX_REQUEST_BYTES:
                        raise AppError("request_too_large", "Запрос слишком большой.", 413)
                    if not event.get("more_body", False):
                        break

                async def replay():
                    return {"type": "http.request", "body": body, "more_body": False}

                await self.app(scope, replay, safe_send)
            else:
                await self.app(scope, receive, safe_send)
        except AppError as error:
            await JSONResponse(error_content(error), status_code=error.status_code)(
                scope, receive, safe_send)

    @staticmethod
    def _check_host(headers):
        host = headers.get(b"host", "").lower()
        if not re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?", host):
            raise AppError("host_forbidden", "Приложение доступно только локально.", 400)
        if ":" in host and not 0 < int(host.rsplit(":", 1)[1]) <= 65535:
            raise AppError("host_forbidden", "Недопустимый локальный адрес.", 400)

    def _check_mutation(self, scope, headers):
        host = headers[b"host"].lower()
        expected_origin = scope.get("scheme", "http") + "://" + host
        origin = headers.get(b"origin")
        if origin is not None and origin != expected_origin:
            raise AppError("origin_forbidden", "Запрос с другого сайта запрещён.", 403)
        if headers.get(b"sec-fetch-site") == "cross-site":
            raise AppError("origin_forbidden", "Запрос с другого сайта запрещён.", 403)
        csrf = headers.get(b"x-brain-csrf", "")
        if not csrf.isascii() or not secrets.compare_digest(csrf, self.csrf_token):
            raise AppError("csrf_failed", "Обновите страницу и повторите операцию.", 403)
        if headers.get(b"content-type", "").split(";", 1)[0].lower() != "application/json":
            raise AppError("json_required", "Ожидается запрос в формате JSON.", 415)
