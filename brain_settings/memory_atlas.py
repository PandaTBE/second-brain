"""Bounded, read-only metadata for the local memory graph."""

import json
import unicodedata
from datetime import UTC, datetime

from .memory_text import redact_text

MAX_ATLAS_RECORDS = 2000
COLUMNS = (
    "d.id,d.source_id,d.source_kind,d.title,d.created_at,d.updated_at,"
    "d.card_status,d.revision,substr(d.text,1,480) AS snippet,d.card_json"
)


def _keywords(values) -> list[str]:
    if not isinstance(values, list):
        return []
    result, seen = [], frozenset()
    for value in values:
        if not isinstance(value, str):
            continue
        clean = " ".join(redact_text(unicodedata.normalize("NFKC", value)).split())[:80]
        key = clean.casefold()
        if clean and key not in seen:
            result = [*result, clean]
            seen = seen | {key}
        if len(result) == 16:
            break
    return result


def _summary(row) -> dict:
    card = json.loads(row["card_json"]) if row["card_json"] else {}
    summary = card.get("summary")
    return {
        "id": row["id"],
        "title": redact_text(card.get("title") or row["title"])[:200],
        "summary": redact_text(summary)[:1200] if summary else None,
        "snippet": redact_text(row["snippet"])[:480],
        "source_id": row["source_id"],
        "source_kind": row["source_kind"],
        "card_status": row["card_status"],
        "revision": row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "keywords": _keywords(card.get("keywords")),
    }


def _records(connection, where, arguments):
    return connection.execute(
        "SELECT " + COLUMNS + " FROM documents d WHERE " + where
        + " ORDER BY d.updated_at DESC,d.id ASC LIMIT ?",
        (*arguments, MAX_ATLAS_RECORDS),
    ).fetchall()


def atlas_snapshot(connection, *, allowed_clause="", allowed=()) -> dict:
    where = "d.source_id NOT IN (SELECT source_id FROM deleted_sources)" + allowed_clause
    connection.execute("BEGIN")
    try:
        counts = connection.execute(
            "SELECT d.source_id,COUNT(*) AS count FROM documents d WHERE " + where
            + " GROUP BY d.source_id", allowed,
        ).fetchall()
        records = [_summary(row) for row in _records(connection, where, allowed)]
        total = sum(row["count"] for row in counts)
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "total_records": total,
            "returned_records": len(records),
            "truncated": len(records) < total,
            "records": records,
            "_counts_by_source": {row["source_id"]: row["count"] for row in counts},
        }
    finally:
        connection.rollback()
