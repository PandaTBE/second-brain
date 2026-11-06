"""Ephemeral Microsoft device sessions and one-use, cancellation-bound account tickets."""

import hashlib
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass, replace

from .errors import AppError
from .outlook_transport import _secret, validate_client_id, validate_device_response

MAX_FLOWS = 8
TICKET_SECONDS = 300
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{20,128}\Z")


@dataclass(frozen=True)
class _Flow:
    client_id: str
    deadline: float
    interval: int = 5
    next_poll: float = 0
    device_code: str = ""
    inflight: bool = True
    status: str = "starting"
    auth_id: str | None = None
    account: dict | None = None


@dataclass(frozen=True)
class _Claim:
    flow_id: str
    deadline: float
    fingerprint: str


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise AppError("outlook_auth_id", "Начните подключение Outlook заново.")
    return value


def _expired():
    return AppError("outlook_auth_expired", "Код подключения истёк или вход отменён. "
                    "Начните подключение Outlook заново.", 410)


def _account(value):
    result = {}
    if not isinstance(value, dict):
        raise AppError("outlook_profile", "Microsoft не вернул профиль выбранного аккаунта.", 502)
    for key, maximum in (("id", 256), ("username", 320), ("display_name", 256)):
        text = value.get(key, "" if key == "display_name" else None)
        if (not isinstance(text, str) or len(text) > maximum
                or (key != "display_name" and not text.strip())
                or any(ord(c) < 32 or ord(c) == 127 for c in text)):
            raise AppError("outlook_profile", "Microsoft вернул неподдерживаемый профиль.", 502)
        result[key] = text.strip()
    return result


def _credentials(client_id, account, tokens):
    if not isinstance(tokens, dict):
        raise AppError("outlook_invalid_response", "Не удалось получить подключение Microsoft.",
                       502)
    expiry = tokens.get("expires_at")
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0:
        raise AppError("outlook_invalid_response", "Microsoft вернул недопустимый срок доступа.",
                       502)
    return {"client_id": client_id, "account_id": account["id"], "username": account["username"],
            "access_token": _secret(tokens.get("access_token")),
            "refresh_token": _secret(tokens.get("refresh_token")), "expires_at": expiry}


def _fingerprint(value):
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(serialized).hexdigest()


class OutlookAuthManager:
    def __init__(self, oauth_client, graph_client, *, clock=time.monotonic):
        self.oauth_client = oauth_client
        self.graph_client = graph_client
        self.clock = clock
        self._lock = threading.Lock()
        self._flows = {}
        self._tickets = {}
        self._claims = {}
        self._starts = ()

    def _drop(self, flow_id):
        flow = self._flows.pop(flow_id, None)
        if flow is not None and flow.auth_id:
            self._tickets.pop(flow.auth_id, None)
            self._claims.pop(flow.auth_id, None)

    def _cleanup(self):
        now = self.clock()
        for flow_id, flow in tuple(self._flows.items()):
            if flow.deadline <= now:
                self._drop(flow_id)

    def cleanup(self):
        """Drop expired secrets from the application idle tick, even while sync is paused."""
        with self._lock:
            self._cleanup()

    def close(self):
        """Invalidate active requests and forget all ephemeral OAuth material."""
        with self._lock:
            self._flows = {}
            self._tickets = {}
            self._claims = {}
            self._starts = ()

    def start(self, client_id):
        client_id = validate_client_id(client_id)
        flow_id = secrets.token_urlsafe(32)
        with self._lock:
            self._cleanup()
            if len(self._flows) >= MAX_FLOWS:
                raise AppError("outlook_flow_limit", "Завершите или отмените предыдущие "
                               "подключения Outlook.", 429)
            now = self.clock()
            recent = tuple(started for started in self._starts if started > now - 60)
            if len(recent) >= MAX_FLOWS:
                raise AppError("outlook_start_limit", "Слишком много попыток входа. "
                               "Подождите минуту и повторите подключение.", 429)
            self._starts = (*recent, now)
            reserved = _Flow(client_id, now + 1800)
            self._flows[flow_id] = reserved
        try:
            result = validate_device_response(self.oauth_client.start(client_id))
        except Exception as error:
            with self._lock:
                self._drop(flow_id)
            if isinstance(error, AppError):
                raise
            raise AppError("outlook_auth_failed", "Не удалось начать вход Microsoft.",
                           502) from None
        with self._lock:
            self._cleanup()
            if self._flows.get(flow_id) is not reserved:
                raise _expired()
            now = self.clock()
            self._flows[flow_id] = _Flow(
                client_id, now + result["expires_in"], result["interval"],
                now + result["interval"], result["device_code"], False, "pending")
        return {"flow_id": flow_id, **{k: result[k] for k in (
            "user_code", "verification_uri", "expires_in", "interval")}}

    def poll(self, flow_id):
        flow_id = _id(flow_id)
        with self._lock:
            self._cleanup()
            flow = self._flows.get(flow_id)
            if flow is None:
                raise _expired()
            if flow.status == "completed":
                return {"status": "completed", "auth_id": flow.auth_id,
                        "account": dict(flow.account)}
            if flow.status == "claimed":
                raise AppError("outlook_auth_used", "Подключение уже сохраняется.", 409)
            if flow.inflight or self.clock() < flow.next_poll:
                return self._pending(flow)
            polling = replace(flow, inflight=True, next_poll=self.clock() + flow.interval)
            self._flows[flow_id] = polling
        try:
            result = self.oauth_client.poll(flow.client_id, flow.device_code)
            if not isinstance(result, dict) or result.get("status") not in (
                    "pending", "slow_down", "completed"):
                raise ValueError
            if result["status"] == "completed":
                tokens = result.get("tokens")
                if not isinstance(tokens, dict):
                    raise ValueError
                account = _account(self.graph_client.profile(_secret(tokens.get("access_token"))))
                credentials = _credentials(flow.client_id, account, tokens)
        except Exception as error:
            with self._lock:
                if self._flows.get(flow_id) is polling:
                    self._drop(flow_id)
            if isinstance(error, AppError):
                raise
            raise AppError("outlook_auth_failed", "Не удалось завершить вход Microsoft. "
                           "Повторите подключение.", 502) from None
        with self._lock:
            self._cleanup()
            if self._flows.get(flow_id) is not polling:
                raise _expired()
            if result["status"] != "completed":
                interval = min(60, flow.interval + (5 if result["status"] == "slow_down" else 0))
                current = replace(polling, inflight=False, interval=interval,
                                  next_poll=self.clock() + interval)
                self._flows[flow_id] = current
                return self._pending(current)
            auth_id = secrets.token_urlsafe(32)
            deadline = min(flow.deadline, self.clock() + TICKET_SECONDS)
            self._tickets[auth_id] = credentials
            self._flows[flow_id] = replace(polling, deadline=deadline, device_code="",
                                           inflight=False, status="completed", auth_id=auth_id,
                                           account=account)
            return {"status": "completed", "auth_id": auth_id, "account": dict(account)}

    def _pending(self, flow):
        return {"status": "pending", "interval": flow.interval,
                "retry_after": max(1, math.ceil(flow.next_poll - self.clock())),
                "expires_in": max(0, math.ceil(flow.deadline - self.clock()))}

    def cancel(self, flow_id):
        flow_id = _id(flow_id)
        with self._lock:
            self._cleanup()
            self._drop(flow_id)
        return {"cancelled": True}

    def take(self, auth_id):
        auth_id = _id(auth_id)
        with self._lock:
            self._cleanup()
            flow_id = next((key for key, value in self._flows.items()
                            if value.auth_id == auth_id and value.status == "completed"), None)
            credentials = self._tickets.pop(auth_id, None)
            if flow_id is None or credentials is None:
                raise _expired()
            flow = self._flows[flow_id]
            self._claims[auth_id] = _Claim(flow_id, flow.deadline, _fingerprint(credentials))
            self._flows[flow_id] = replace(flow, status="claimed")
            return dict(credentials)

    def is_claim_active(self, auth_id):
        with self._lock:
            self._cleanup()
            claim = self._claims.get(auth_id)
            return claim is not None and claim.flow_id in self._flows

    def restore(self, auth_id, credentials):
        with self._lock:
            self._cleanup()
            claim = self._claims.get(auth_id)
            if claim is None or claim.flow_id not in self._flows:
                return False
            if not isinstance(credentials, dict) or _fingerprint(credentials) != claim.fingerprint:
                return False
            self._tickets[auth_id] = dict(credentials)
            self._flows[claim.flow_id] = replace(self._flows[claim.flow_id], status="completed")
            self._claims.pop(auth_id, None)
            return True

    def finish(self, auth_id):
        with self._lock:
            self._cleanup()
            claim = self._claims.get(auth_id)
            if claim is None:
                return False
            self._drop(claim.flow_id)
            return True
