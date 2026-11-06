"""Selected Notes folders and claimed Microsoft identities join the source store."""

import json
import math
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

from .errors import AppError
from .source_storage import Source
from .storage import vault_operation


class ConnectedSourceSupport:
    def __init__(self, owner, credential_scope):
        self.owner = owner
        self.credential_scope = credential_scope
        self._folders = ()
        self._folders_until = 0.0
        self._discovery_after = 0.0
        self._notes_busy = False
        self._refresh_locks = {}

    def notes_folders(self):
        with self.owner._lock:
            now = time.monotonic()
            if self._notes_busy or now < self._discovery_after:
                raise AppError("notes_discovery_busy",
                               "Подождите перед повторной загрузкой папок.", 429)
            self._notes_busy = True
            self._discovery_after = now + 2
            self._folders, self._folders_until = (), 0.0
        try:
            result = self.owner.apple_notes_client.list_folders()
            with self.owner._lock:
                self._folders = tuple(result["folders"])
                self._folders_until = time.monotonic() + 900
        finally:
            with self.owner._lock:
                self._notes_busy = False
        return result

    def add_notes(self, request):
        if request.password is not None or request.token is not None:
            raise AppError("notes_credentials_unused", "Apple Заметки не требуют пароль или токен.")
        with self.owner._lock:
            chosen = frozenset(request.notes_folder_ids)
            available = frozenset(folder["id"] for folder in self._folders)
            if not chosen or time.monotonic() >= self._folders_until or not chosen <= available:
                raise AppError("notes_folders_required",
                               "Загрузите папки Apple Заметок и выберите нужные.")
            current = self.owner.store.load()
            self._capacity(current)
            source = Source(id=str(uuid4()), kind="apple_notes", name=request.name,
                            notes_folder_ids=request.notes_folder_ids)
            self.owner.store.save((*current, source))
        return {"source": self.owner._public(source)}

    @staticmethod
    def _capacity(sources):
        if len(sources) >= 100:
            raise AppError("source_limit", "Можно подключить не более 100 источников.")

    def add_outlook(self, request):
        if not request.outlook_auth_id:
            raise AppError("outlook_auth_required", "Сначала войдите через Microsoft.")
        if request.password is not None or request.token is not None:
            raise AppError("outlook_auth_required",
                           "Используйте вход Microsoft, без пароля или токена.")
        with self.owner._lock:
            current = self.owner.store.load()
            self._capacity(current)
            credentials = self.owner.outlook_auth.take(request.outlook_auth_id)
            return self._persist_outlook(request, current, credentials)

    def _persist_outlook(self, request, current, credentials):
        source = None
        saved = False
        try:
            source = Source(id=str(uuid4()), kind="outlook", name=request.name,
                            username=credentials["username"], credential_saved=True,
                            outlook_client_id=credentials["client_id"],
                            outlook_account_id=credentials["account_id"],
                            mail_import_enabled=request.mail_import_enabled,
                            mail_lookback_days=request.mail_lookback_days)
            self._decode_credentials(json.dumps(credentials), source)
            self._claim_active(request.outlook_auth_id)
            vault_operation(self.owner.vault, "set", self.credential_scope(source),
                            json.dumps(credentials, separators=(",", ":")))
            self._claim_active(request.outlook_auth_id)
            self.owner.store.save((*current, source))
            saved = True
            if self.owner.outlook_auth.finish(request.outlook_auth_id) is False:
                raise AppError("outlook_auth_expired", "Вход Microsoft отменён или истёк.", 409)
        except Exception:
            try:
                if saved:
                    self.owner.store.save(current)
            finally:
                if source is not None:
                    self.owner._rollback_credential(source, None)
                self.owner.outlook_auth.restore(request.outlook_auth_id, credentials)
            raise
        return {"source": self.owner._public(source)}

    def _claim_active(self, auth_id):
        if not self.owner.outlook_auth.is_claim_active(auth_id):
            raise AppError("outlook_auth_expired", "Вход Microsoft отменён или истёк.", 409)

    def _read_outlook(self, source):
        self.owner._require_authenticated(source)
        raw = vault_operation(self.owner.vault, "get", self.credential_scope(source))
        return raw, self._decode_credentials(raw, source)

    @staticmethod
    def _decode_credentials(raw, source):
        try:
            if not isinstance(raw, str) or len(raw.encode()) > 131_072:
                raise ValueError
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            if (value.get("client_id") != source.outlook_client_id
                    or value.get("account_id") != source.outlook_account_id):
                raise ValueError
            for key in ("access_token", "refresh_token"):
                token = value.get(key)
                if (not isinstance(token, str) or not 1 <= len(token) <= 65_536
                        or any(ord(c) <= 32 or ord(c) >= 127 for c in token)):
                    raise ValueError
            expiry = value.get("expires_at")
            if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                    or not math.isfinite(expiry)):
                raise ValueError
            return value
        except (ValueError, TypeError, RecursionError) as exc:
            raise AppError("outlook_reconnect",
                           "Подключите Outlook заново через вход Microsoft.", 401) from exc

    def outlook_credentials(self, source_id):
        with self.owner._lock:
            lock = self._refresh_locks.get(source_id)
            if lock is None:
                lock = threading.Lock()
                self._refresh_locks = {**self._refresh_locks, source_id: lock}
        with lock:
            with self.owner._lock:
                source = self._outlook_source(source_id)
                previous, credentials = self._read_outlook(source)
            if credentials["expires_at"] > time.time() + 60:
                return credentials
            refreshed = self.owner.outlook_client.refresh(credentials)
            self._decode_credentials(json.dumps(refreshed), source)
            with self.owner._lock:
                current = self._outlook_source(source_id)
                current_raw, current_credentials = self._read_outlook(current)
                if current_raw != previous:
                    return current_credentials
                vault_operation(self.owner.vault, "set", self.credential_scope(current),
                                json.dumps(refreshed, separators=(",", ":")))
            return refreshed

    def _outlook_source(self, source_id):
        source = self.owner._find(self.owner.store.load(), source_id)
        if source.kind != "outlook" or not source.enabled:
            raise AppError("outlook_unavailable", "Источник Outlook отключён или удалён.", 404)
        return source

    def test(self, source):
        checked_at = datetime.now(UTC).isoformat()
        error = None
        try:
            if source.kind == "apple_notes":
                count = self._notes_count(source)
            else:
                count = self.owner.outlook_client.test(
                    self.outlook_credentials(source.id)["access_token"])
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise AppError("source_check_failed", "Источник вернул недопустимый счётчик.", 502)
            message = (f"В выбранных папках заметок: {count}. Защищённые заметки пропускаются."
                       if source.kind == "apple_notes" else
                       f"Во входящих писем: {count}. Проверка не читает содержимое писем.")
            updates = {"status": "ok", "message": message, "item_count": count}
        except AppError as caught:
            error = caught
            updates = {"status": "error", "message": caught.message, "item_count": None}
        with self.owner._lock:
            current = self.owner.store.load()
            live = self.owner._find(current, source.id)
            checked = live.model_copy(update={**updates, "last_checked_at": checked_at})
            self.owner.store.save(tuple(
                checked if item.id == source.id else item for item in current))
        if error is not None:
            raise error
        return {"source": self.owner._public(checked)}

    def _notes_count(self, source):
        with self.owner._lock:
            if self._notes_busy:
                raise AppError("notes_discovery_busy",
                               "Дождитесь завершения проверки Заметок.", 429)
            self._notes_busy = True
        try:
            folders = self.owner.apple_notes_client.list_folders()["folders"]
        finally:
            with self.owner._lock:
                self._notes_busy = False
        selected = frozenset(source.notes_folder_ids)
        if not selected <= frozenset(folder["id"] for folder in folders):
            raise AppError("notes_folder_missing", "Выбранная папка Apple Заметок недоступна.", 404)
        return sum(folder["note_count"] for folder in folders if folder["id"] in selected)

    def discard_source(self, source_id):
        with self.owner._lock:
            self._refresh_locks = {key: value for key, value in self._refresh_locks.items()
                                   if key != source_id}

    def cleanup(self):
        with self.owner._lock:
            if time.monotonic() >= self._folders_until:
                self._folders, self._folders_until = (), 0.0
