"""Bounded, opt-in Outlook Inbox reconciliation with immutable message identities."""

import hashlib

from .dialogue_parsers import chunk_turns, parse_note
from .errors import AppError
from .memory_text import redact_text
from .outlook_client import MAX_MESSAGES, message_path
from .outlook_progress import OutlookProgress


def _stats(source_id):
    return {"imported": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": [],
            "warnings": [], "source_id": source_id, "files_processed": 0,
            "files_unchanged": 0, "files_skipped": 0, "files_deferred": 0,
            "bytes_read": 0, "processed_paths": [], "deferred_paths": [], "has_more": False}


def _commit(store, source_id, message):
    title, text = redact_text(message["subject"]), redact_text(message["text"])
    key = "outlook:" + hashlib.sha256(message["id"].encode("utf-8")).hexdigest()
    documents = [{"source_id": source_id, "source_kind": "outlook",
                  "source_object_id": key + f"::chunk:{index:06}",
                  "title": title + f" · {index + 1}", "text": chunk.text,
                  "source_uri": redact_text(message["source_uri"]),
                  "created_at": message["created_at"],
                  "updated_at": message["updated_at"],
                  "message_at": message["created_at"],
                  "source_modified_at": message.get("source_modified_at")}
                 for index, chunk in enumerate(chunk_turns(parse_note(text)))]
    return key, store.replace_file_documents(source_id, key, documents, complete=True)


def import_outlook(source, store, client, credentials_provider, check_stop=lambda: None):
    if (source.get("kind") != "outlook" or source.get("mail_import_enabled") is not True
            or source.get("enabled", True) is not True):
        raise AppError("mail_import_disabled", "Включите импорт писем для этого подключения.")
    lookback = source.get("mail_lookback_days", 30)
    if type(lookback) is not int or lookback not in (0, 30, 90, 365):
        raise AppError("invalid_mail_period", "Выберите период импорта почты.")
    source_id = source["id"]

    def check():
        check_stop()
        if source_id in store.deleted_source_ids():
            raise AppError("source_deleted", "Источник удалён; импорт почты остановлен.", 404)

    check()
    progress = OutlookProgress(store, source_id)
    stored = progress.load()
    state = stored if stored.get("lookback") == lookback else {
        "version": 1, "lookback": lookback, "next_link": None,
    }
    if state["next_link"] is not None:
        message_path(state["next_link"])
    check()
    credentials = credentials_provider()
    page = client.messages(credentials["access_token"], lookback, next_link=state["next_link"])
    check()
    rows = page.get("messages")
    if not isinstance(rows, list) or len(rows) > MAX_MESSAGES:
        raise AppError("outlook_response_invalid", "Outlook вернул неполную страницу писем.", 502)
    following = page.get("next_link")
    if following is not None:
        message_path(following)
        if following == state["next_link"]:
            raise AppError("outlook_response_invalid", "Outlook повторил страницу писем.", 502)
    stats = {**_stats(source_id), "skipped": page.get("skipped", 0),
             "files_skipped": page.get("skipped", 0), "warnings": page.get("warnings", [])[:100]}
    for row in rows:
        check()
        key, results = _commit(store, source_id, row)
        stats = {**stats, "files_processed": stats["files_processed"] + 1,
                 "bytes_read": stats["bytes_read"] + len(row["text"].encode("utf-8")),
                 "processed_paths": [*stats["processed_paths"], key],
                 "imported": stats["imported"] + sum(
                     result["changed"] and result["revision"] == 1 for result in results),
                 "updated": stats["updated"] + sum(
                     result["changed"] and result["revision"] > 1 for result in results),
                 "unchanged": stats["unchanged"] + sum(not result["changed"] for result in results)}
    check()
    progress.save({**state, "next_link": following})
    if following is not None:
        stats = {**stats, "has_more": True, "files_deferred": 1,
                 "warnings": [*stats["warnings"],
                              "Остальная почта Outlook отложена; продолжение сохранено в SQLite."]}
    return stats
