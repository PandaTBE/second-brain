"""Selected-page Notion import into the same redacted SQLite/FTS5 memory."""

from .dialogue_parsers import chunk_turns, parse_note
from .errors import AppError
from .memory_text import redact_text
from .notion_models import normalize_page_id


def _check_source(source_id, store, check_stop):
    check_stop()
    if source_id in store.deleted_source_ids():
        raise AppError("source_deleted", "Источник удалён; импорт Notion остановлен.", 404)


def import_notion(source: dict, store, client, token: str, check_stop=lambda: None) -> dict:
    source_id = source["id"]
    selected = source.get("notion_page_ids", ())
    try:
        if not isinstance(selected, (list, tuple)) or not 1 <= len(selected) <= 100:
            raise ValueError
        page_ids = tuple(dict.fromkeys(normalize_page_id(value) for value in selected))
    except (ValueError, TypeError) as exc:
        raise AppError("notion_scope", "Выберите от 1 до 100 страниц Notion.") from exc
    stats = {"imported": 0, "updated": 0, "unchanged": 0, "skipped": 0,
             "errors": [], "warnings": [], "source_id": source_id, "files_processed": 0,
             "files_unchanged": 0, "files_skipped": 0, "files_deferred": 0, "bytes_read": 0,
             "processed_paths": [], "deferred_paths": [], "has_more": False}
    for page_id in page_ids:
        _check_source(source_id, store, check_stop)
        try:
            page = client.retrieve_page(token, page_id, check_stop=check_stop)
            _check_source(source_id, store, check_stop)
            # Redact the whole page before chunking so credentials spanning a chunk stay masked.
            title = redact_text(page["title"])
            text = redact_text("# " + title + "\n\n" + page["text"])
            chunks = chunk_turns(parse_note(text))
            documents = [{"source_id": source_id, "source_kind": "notion",
                          "source_object_id": page_id + f"::chunk:{index:06}",
                          "title": title + f" · {index + 1}", "text": chunk.text,
                          "source_uri": "https://www.notion.so/" + page_id.replace("-", ""),
                          "created_at": page["created_at"], "updated_at": page["updated_at"],
                          "source_created_at": page["created_at"],
                          "source_modified_at": page["updated_at"]}
                         for index, chunk in enumerate(chunks)]
            results = store.replace_file_documents(source_id, page_id, documents, complete=True)
            stats = {**stats, "files_processed": stats["files_processed"] + 1,
                     "bytes_read": stats["bytes_read"] + len(text.encode()),
                     "processed_paths": [*stats["processed_paths"], page_id],
                     "imported": stats["imported"] + sum(
                         result["changed"] and result["revision"] == 1 for result in results),
                     "updated": stats["updated"] + sum(
                         result["changed"] and result["revision"] > 1 for result in results),
                     "unchanged": stats["unchanged"] + sum(
                         not result["changed"] for result in results)}
        except AppError as error:
            if error.code in ("source_deleted", "memory_unavailable", "memory_cancelled",
                              "sync_paused"):
                raise
            stats = {**stats, "skipped": stats["skipped"] + 1,
                     "files_skipped": stats["files_skipped"] + 1,
                     "errors": [*stats["errors"], page_id + ": " + error.message][:100]}
    return stats
