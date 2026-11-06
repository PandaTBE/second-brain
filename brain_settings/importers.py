"""Deterministic, redacted local imports; model processing is a separate stage."""

from datetime import UTC, datetime
from pathlib import Path

from .dialogue_parsers import chunk_turns, parse_note, parse_session, redact_turns
from .errors import AppError
from .import_files import read_source_files
from .import_progress import ImportProgress
from .memory_text import redact_text


def import_source(source: dict, store, *, eligible_paths: set[str] | None = None) -> dict:
    source_id, kind = str(source.get("id", "")), source.get("kind")
    stats = {
        "imported": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "errors": [],
        "warnings": [],
        "source_id": source_id,
        "files_processed": 0,
        "files_unchanged": 0,
        "files_skipped": 0,
        "files_deferred": 0,
        "bytes_read": 0,
        "processed_paths": [],
        "deferred_paths": [],
        "has_more": False,
    }
    if kind not in ("codex", "claude", "qwen", "folder"):
        raise AppError("unsupported_import", "Для этого источника локальный импорт недоступен.")
    if source_id in store.deleted_source_ids():
        raise AppError("source_deleted", "Источник удалён; чтение файлов остановлено.", 404)
    if not store.source_policy(source_id).import_enabled:
        raise AppError("source_policy_denied", "Импорт запрещён политикой источника.", 403)
    path = source.get("path")
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise AppError("invalid_source_path", "Выберите источник по полному пути.")
    root = Path(path)
    progress = ImportProgress(store, source_id)
    known = progress.load()
    try:
        events = read_source_files(root, kind, eligible_paths, known_files=known,
                                   allowed_path=lambda relative: store.import_path_allowed(
                                       source_id, relative.as_posix()))
        for event in events:
            absolute = str(root / event.relative)
            warnings = [
                redact_text(str(event.relative)) + ": " + redact_text(message)
                for message in event.warnings
            ]
            stats = {
                **stats,
                "bytes_read": stats["bytes_read"] + event.bytes_read,
                "warnings": [*stats["warnings"], *warnings][:100],
            }
            if event.state == "deferred":
                stats = {
                    **stats,
                    "has_more": True,
                    "files_deferred": stats["files_deferred"] + 1,
                    "deferred_paths": [*stats["deferred_paths"], absolute],
                }
                continue
            if event.state == "unchanged":
                if not event.retryable:
                    stats = {**stats, "processed_paths": [*stats["processed_paths"], absolute]}
                failed = event.cached_state in ("skipped", "error")
                stats = {
                    **stats,
                    "files_unchanged": stats["files_unchanged"] + 1,
                    "files_skipped": stats["files_skipped"] + int(failed),
                    "unchanged": stats["unchanged"] + event.document_count,
                }
                if event.cached_state == "error":
                    stats = {**stats, "errors": [*stats["errors"], *warnings][:100]}
                continue
            if event.state in ("skipped", "error"):
                if not event.retryable:
                    stats = {**stats, "processed_paths": [*stats["processed_paths"], absolute]}
                stats = {
                    **stats,
                    "skipped": stats["skipped"] + 1,
                    "files_skipped": stats["files_skipped"] + 1,
                }
                if event.state == "error":
                    stats = {**stats, "errors": [*stats["errors"], *warnings][:100]}
                if event.signature is not None:
                    progress.save(event, 0, event.warnings, state=event.state)
                continue
            committed = False
            try:
                complete, skipped = event.complete, event.skipped
                if event.turns is not None:
                    turns = list(event.turns)
                elif kind in ("codex", "claude", "qwen"):
                    turns, complete, skipped = parse_session(event.raw.decode("utf-8-sig"), kind)
                else:
                    turns = parse_note(event.raw.decode("utf-8-sig"))
                turns = redact_turns(turns)
                chunks = chunk_turns(turns)
                fallback = datetime.fromtimestamp(event.modified, UTC).isoformat()
                title = next(
                    (turn.text.lstrip("# ")[:100] for turn in turns if turn.text.strip()),
                    event.relative.stem,
                )
                documents = [
                    {
                        "source_id": source_id,
                        "source_kind": kind,
                        "source_object_id": event.relative.as_posix() + f"::chunk:{index:06}",
                        "title": redact_text(title) + f" · {index + 1}",
                        "text": redact_text(chunk.text),
                        "source_uri": Path(redact_text(str(root / event.relative))).as_uri()
                        + f"#L{chunk.first_line}-L{chunk.last_line}",
                        "created_at": next(
                            (turn.timestamp for turn in turns if turn.timestamp), fallback
                        ),
                        "updated_at": chunk.timestamp or fallback,
                    }
                    for index, chunk in enumerate(chunks)
                ]
                results = store.replace_file_documents(
                    source_id, event.relative.as_posix(), documents, complete=complete
                )
                committed = True
                stats = {
                    **stats,
                    "files_processed": stats["files_processed"] + 1,
                    "skipped": stats["skipped"] + skipped,
                    "imported": stats["imported"]
                    + sum(result["changed"] and result["revision"] == 1 for result in results),
                    "updated": stats["updated"]
                    + sum(result["changed"] and result["revision"] > 1 for result in results),
                    "unchanged": stats["unchanged"]
                    + sum(not result["changed"] for result in results),
                }
                if not event.retryable:
                    stats = {**stats, "processed_paths": [*stats["processed_paths"], absolute]}
                    progress.save(
                        event,
                        len(documents),
                        event.warnings,
                        state="done" if complete else "partial",
                    )
            except (ValueError, UnicodeError, AppError) as error:
                message = (
                    error.message if isinstance(error, AppError) else "Не удалось разобрать файл."
                )
                safe = redact_text(str(event.relative)) + ": " + redact_text(message)
                stats = {
                    **stats,
                    "errors": [*stats["errors"], safe][:100],
                    "skipped": stats["skipped"] + int(not committed),
                    "files_skipped": stats["files_skipped"] + int(not committed),
                }
                if isinstance(error, AppError) and error.code in (
                    "source_deleted",
                    "memory_unavailable",
                ):
                    raise
                if not event.retryable and not committed:
                    stats = {**stats, "processed_paths": [*stats["processed_paths"], absolute]}
                progress.save(event, 0, (message,), state="error")
    except (OSError, AppError) as error:
        if not (stats["processed_paths"] or stats["files_processed"] or stats["files_unchanged"]):
            if isinstance(error, AppError):
                raise
            raise AppError(
                "import_unavailable", "Не удалось прочитать выбранную папку.", 403
            ) from error
        message = error.message if isinstance(error, AppError) else "Не удалось дочитать папку."
        stats = {**stats, "errors": [*stats["errors"], redact_text(message)][:100]}
    return stats
