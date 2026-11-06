"""Selected Apple Notes folders become redacted, revisioned SQLite material."""

import hashlib
import json

from .apple_notes import normalize_notes_folder_ids, validate_notes_page
from .dialogue_parsers import chunk_turns, parse_note
from .errors import AppError
from .memory_text import redact_text


class AppleNotesProgress:
    def __init__(self, store, source_id):
        self.store, self.source_id = store, source_id
        with store._connection(write=True) as connection:
            store._check_source(connection, source_id)
            connection.execute("""CREATE TABLE IF NOT EXISTS apple_notes_sync_progress (
                source_id TEXT PRIMARY KEY, scope TEXT NOT NULL, state TEXT NOT NULL)""")

    def load(self, scope):
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT scope,state FROM apple_notes_sync_progress WHERE source_id=?",
                (self.source_id,),
            ).fetchone()
        if row is None or row["scope"] != scope:
            return None
        try:
            if len(row["state"]) > 4096:
                raise ValueError
            value = json.loads(row["state"])
            if value is not None and not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, TypeError) as exc:
            raise AppError(
                "apple_notes_progress", "Не удалось прочитать прогресс импорта заметок.", 503
            ) from exc

    def save(self, scope, state):
        serialized = json.dumps(state)
        if len(serialized) > 4096:
            raise AppError(
                "apple_notes_progress", "Состояние импорта заметок слишком большое.", 503
            )
        with self.store._connection(write=True) as connection:
            self.store._check_source(connection, self.source_id)
            connection.execute(
                """INSERT INTO apple_notes_sync_progress VALUES(?,?,?)
                ON CONFLICT(source_id) DO UPDATE SET scope=excluded.scope,state=excluded.state""",
                (self.source_id, scope, serialized),
            )


def _check(source_id, store, check_stop):
    check_stop()
    if not store.source_policy(source_id).import_enabled:
        raise AppError("source_policy_denied", "Импорт заметок запрещён политикой источника.", 403)
    if source_id in store.deleted_source_ids():
        raise AppError("source_deleted", "Источник заметок удалён; импорт остановлен.", 404)


def import_apple_notes(source, store, client, check_stop=lambda: None):
    source_id = source["id"]
    policy = store.source_policy(source_id)
    if not policy.import_enabled:
        raise AppError("source_policy_denied", "Импорт запрещён политикой источника.", 403)
    _check(source_id, store, check_stop)
    policy = store.source_policy(source_id)
    selected = normalize_notes_folder_ids(source.get("notes_folder_ids", ()))
    selected = tuple(folder for folder in selected
                     if folder not in policy.excluded_notes_folder_ids)
    if not selected:
        return {"imported": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": [],
                "warnings": [], "source_id": source_id, "has_more": False}
    progress = AppleNotesProgress(store, source_id)
    scope = hashlib.sha256(json.dumps(selected).encode()).hexdigest()
    cursor = progress.load(scope)
    response = client.read_page(selected, cursor=cursor, limit=50)
    _check(source_id, store, check_stop)
    page = validate_notes_page(response, selected)
    stats = {
        "imported": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": page["skipped"],
        "errors": [],
        "warnings": page["warnings"],
        "source_id": source_id,
        "files_processed": 0,
        "files_unchanged": 0,
        "files_skipped": page["skipped"],
        "files_deferred": int(page["next_cursor"] is not None),
        "bytes_read": 0,
        "processed_paths": [],
        "deferred_paths": [],
        "has_more": page["next_cursor"] is not None,
    }
    for note in page["notes"]:
        _check(source_id, store, check_stop)
        if not note["text"].strip():
            stats = {
                **stats,
                "skipped": stats["skipped"] + 1,
                "files_skipped": stats["files_skipped"] + 1,
            }
            continue
        title = redact_text(note["title"].strip() or "Без названия")
        text = redact_text("# " + title + "\n\n" + note["text"])
        file_key = "apple-note:" + hashlib.sha256(note["id"].encode()).hexdigest()
        documents = [
            {
                "source_id": source_id,
                "source_kind": "apple_notes",
                "source_object_id": file_key + f"::chunk:{index:06}",
                "title": title + f" · {index + 1}",
                "text": chunk.text,
                "source_uri": redact_text(note["id"]),
                "source_folder_id": note["folder_id"],
                "created_at": note["created_at"],
                "updated_at": note["updated_at"],
            }
            for index, chunk in enumerate(chunk_turns(parse_note(text)))
        ]
        _check(source_id, store, check_stop)
        results = store.replace_file_documents(source_id, file_key, documents, complete=True)
        stats = {
            **stats,
            "files_processed": stats["files_processed"] + 1,
            "bytes_read": stats["bytes_read"] + len(text.encode()),
            "processed_paths": [*stats["processed_paths"], file_key],
            "imported": stats["imported"]
            + sum(r["changed"] and r["revision"] == 1 for r in results),
            "updated": stats["updated"] + sum(r["changed"] and r["revision"] > 1 for r in results),
            "unchanged": stats["unchanged"] + sum(not r["changed"] for r in results),
        }
    _check(source_id, store, check_stop)
    progress.save(scope, page["next_cursor"])
    return stats
