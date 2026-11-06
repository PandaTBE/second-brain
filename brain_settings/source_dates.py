"""Explicit source dates; legacy import/freshness timestamps are never inferred."""

import sqlite3
from datetime import UTC, datetime

from .errors import AppError
from .memory_text import redact_text

UNSET = object()
DATE_FIELDS = ("message_at", "source_created_at", "source_modified_at", "imported_at")


def migrate(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    for name in (*DATE_FIELDS, "project"):
        if name not in columns:
            connection.execute(f"ALTER TABLE documents ADD COLUMN {name} TEXT")


def source_date(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # A timestamp without a timezone cannot safely be compared across sources.
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC).isoformat()
    except (ValueError, OverflowError):
        return None


def source_metadata(
    *,
    message_at: object = UNSET,
    source_created_at: object = UNSET,
    source_modified_at: object = UNSET,
    project: object = UNSET,
) -> dict[str, str | None]:
    if (
        project is not UNSET
        and project is not None
        and (not isinstance(project, str) or len(project) > 256)
    ):
        raise AppError("invalid_document", "Некорректный проект материала.")
    if project is not UNSET and project is not None:
        try:
            project.encode("utf-8")
        except UnicodeError:
            raise AppError(
                "invalid_document", "Проект должен содержать корректный Unicode."
            ) from None
    dates = {
        "message_at": message_at,
        "source_created_at": source_created_at,
        "source_modified_at": source_modified_at,
    }
    result = {name: source_date(value) for name, value in dates.items() if value is not UNSET}
    if project is not UNSET:
        result = result | {"project": redact_text(project).strip() if project else None}
    return result


def save_metadata(
    connection: sqlite3.Connection, identifier: str, metadata: dict, *, imported_at: object = UNSET
) -> None:
    fields = metadata | ({"imported_at": imported_at} if imported_at is not UNSET else {})
    if fields:
        assignments = ",".join(f"{name}=?" for name in fields)
        connection.execute(
            f"UPDATE documents SET {assignments} WHERE id=?", (*fields.values(), identifier)
        )
