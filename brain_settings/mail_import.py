"""Bounded newest-first mail backfill plus live arrivals into shared local memory."""

import hashlib
from datetime import UTC, date, datetime, timedelta

from .dialogue_parsers import chunk_turns, parse_note
from .errors import AppError
from .mail_client import MAX_UID, UID_WINDOW
from .mail_message import parse_message
from .mail_progress import MailProgress
from .memory_text import redact_text

MAX_MESSAGES = 50
MAX_WINDOWS = 4
STOP_CODES = frozenset(("source_deleted", "sync_paused", "memory_cancelled", "memory_unavailable"))
SKIP_CODES = frozenset(("mail_message_large", "mail_message_missing", "mail_message_empty",
                        "mail_message_invalid"))


def _state(previous, session, lookback):
    validity, upper = session.uid_validity, session.uid_next - 1
    if (isinstance(validity, bool) or not isinstance(validity, int) or not 1 <= validity <= MAX_UID
            or isinstance(upper, bool) or not isinstance(upper, int) or not 0 <= upper <= MAX_UID):
        raise AppError("mail_protocol", "Почта вернула некорректные идентификаторы писем.", 502)
    if previous.get("uid_validity") != validity or previous.get("lookback_days") != lookback:
        since = None if lookback == 0 else (
            datetime.now(UTC).date() - timedelta(days=lookback)
        ).isoformat()
        if previous.get("lookback_days") == lookback:
            since = previous.get("since", since)
        return {"uid_validity": validity, "live_uid": upper, "backfill_uid": upper,
                "lookback_days": lookback, "since": since}
    if any(isinstance(previous.get(key), bool) or not isinstance(previous.get(key), int)
           or not 0 <= previous[key] <= MAX_UID for key in ("live_uid", "backfill_uid")):
        raise AppError("mail_progress", "Повреждён прогресс импорта почты.", 503)
    return previous


def _commit(store, source, uid, validity, raw):
    parsed = parse_message(raw)
    text = redact_text(parsed["text"])
    title = redact_text(parsed["title"])
    identity = parsed["message_id"] + "\0" + parsed["text"]
    key = ("message:" + hashlib.sha256(identity.encode()).hexdigest()
           if parsed["message_id"] else f"uid:{validity}:{uid}")
    uri = f"mail://{source['id']}/INBOX/{validity}/{uid}"
    documents = [{"source_id": source["id"], "source_kind": "imap",
                  "source_object_id": key + f"::chunk:{index:06}",
                  "title": title + f" · {index + 1}", "text": chunk.text, "source_uri": uri,
                  "created_at": parsed["timestamp"], "updated_at": parsed["timestamp"]}
                 for index, chunk in enumerate(chunk_turns(parse_note(text)))]
    return store.replace_file_documents(source["id"], key, documents, complete=True)


def _advance(state, live, boundary):
    return {**state, "live_uid" if live else "backfill_uid": boundary}


def import_mail(source: dict, store, client, password: str, check_stop=lambda: None) -> dict:
    if source.get("kind") != "imap" or source.get("mail_import_enabled") is not True:
        raise AppError("mail_import_disabled", "Включите импорт писем для этого подключения.")
    lookback = source.get("mail_lookback_days", 30)
    if isinstance(lookback, bool) or lookback not in (0, 30, 90, 365):
        raise AppError("invalid_mail_period", "Выберите период импорта почты.")
    source_id = source["id"]
    def check():
        check_stop()
        if source_id in store.deleted_source_ids():
            raise AppError("source_deleted", "Источник удалён; импорт почты остановлен.", 404)
    check()
    progress = MailProgress(store, source_id)
    stats = {"imported": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": [],
             "warnings": [], "source_id": source_id, "files_processed": 0,
             "files_unchanged": 0, "files_skipped": 0, "files_deferred": 0, "bytes_read": 0,
             "processed_paths": [], "deferred_paths": [], "has_more": False}
    used, interrupted = 0, False
    with client.open(source, password, check_stop=check) as session:
        state = _state(progress.load(), session, lookback)
        progress.save(state)
        try:
            since = date.fromisoformat(state["since"]) if state.get("since") else None
        except (ValueError, TypeError) as exc:
            raise AppError("mail_progress", "Повреждён период импорта почты.", 503) from exc
        for _ in range(MAX_WINDOWS):
            check()
            upper = session.uid_next - 1
            live = state["live_uid"] < upper
            if live:
                start, end = state["live_uid"] + 1, min(upper, state["live_uid"] + UID_WINDOW)
            elif state["backfill_uid"] > 0:
                end = state["backfill_uid"]
                start = max(1, end - UID_WINDOW + 1)
            else:
                break
            uids = session.search(start, end, since)
            ordered = sorted(uids, reverse=not live)
            selected = ordered[:MAX_MESSAGES - used]
            for uid in selected:
                check()
                try:
                    raw = session.fetch(uid)
                    check()
                    results = _commit(store, source, uid, state["uid_validity"], raw)
                    stats = {**stats, "files_processed": stats["files_processed"] + 1,
                             "bytes_read": stats["bytes_read"] + len(raw),
                             "imported": stats["imported"] + sum(
                                 row["changed"] and row["revision"] == 1 for row in results),
                             "updated": stats["updated"] + sum(
                                 row["changed"] and row["revision"] > 1 for row in results),
                             "unchanged": stats["unchanged"] + sum(
                                 not row["changed"] for row in results)}
                except AppError as error:
                    if error.code in STOP_CODES:
                        raise
                    stats = {**stats, "errors": [*stats["errors"], error.message][:100],
                             "skipped": stats["skipped"] + 1,
                             "files_skipped": stats["files_skipped"] + 1}
                    if error.code not in SKIP_CODES:
                        interrupted = True
                        break
                check()
                state = _advance(state, live, uid if live else uid - 1)
                progress.save(state)
                used += 1
            if interrupted:
                break
            if len(selected) == len(ordered):
                state = _advance(state, live, end if live else start - 1)
                check()
                progress.save(state)
            if used >= MAX_MESSAGES:
                break
        more = interrupted or state["live_uid"] < session.uid_next - 1 or state["backfill_uid"] > 0
    if more:
        stats = {**stats, "has_more": True, "files_deferred": 1,
                 "warnings": ["Остальная почта отложена; продолжение сохранено в SQLite."]}
    return stats
