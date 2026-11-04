"""Bounded, source-scoped cursor pages for the native memory browser."""

import base64
import binascii
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError

from .errors import AppError
from .memory_text import search_terms

CardStatus = Literal["all", "ready", "pending", "error"]
MAX_CURSOR_LENGTH = 2048
COLUMNS = (
    "d.id,d.revision,d.source_id,d.source_kind,d.title,d.source_uri,"
    "d.created_at,d.updated_at,d.card_status,d.card_json,substr(d.text,1,480) AS snippet"
)


class BrowseCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    v: Literal[1]
    query_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    card_status: CardStatus
    score: FiniteFloat
    updated_at: str = Field(min_length=1, max_length=100)
    record_id: str = Field(min_length=1, max_length=100)
    generation: int | None = Field(default=None, ge=0)


def _bad_cursor() -> AppError:
    return AppError("invalid_cursor", "Обновите список: указатель страницы недействителен.")


def _decode_cursor(value, query, card_status):
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_CURSOR_LENGTH:
        raise _bad_cursor()
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        data = BrowseCursor.model_validate(json.loads(raw))
    except (ValueError, TypeError, UnicodeError, binascii.Error, ValidationError) as error:
        raise _bad_cursor() from error
    digest = hashlib.sha256(query.encode()).hexdigest()
    if data.query_hash != digest or data.card_status != card_status:
        raise _bad_cursor()
    return data


def _encode_cursor(row, query, card_status, generation=None):
    cursor = BrowseCursor(
        v=1, query_hash=hashlib.sha256(query.encode()).hexdigest(), card_status=card_status,
        score=row["raw_score"], updated_at=row["updated_at"], record_id=row["id"],
        generation=generation,
    )
    raw = cursor.model_dump_json(exclude_none=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _validate(query, card_status, limit):
    if not isinstance(query, str) or len(query) > 512:
        raise AppError("invalid_search", "Сократите поисковый запрос до 512 символов.")
    if card_status not in ("all", "ready", "pending", "error"):
        raise AppError("invalid_filter", "Выберите существующий статус материалов.")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise AppError("invalid_search", "Размер страницы должен быть от 1 до 50.")


def _where(card_status, allowed_clause, allowed):
    where = "d.source_id NOT IN (SELECT source_id FROM deleted_sources)" + allowed_clause
    arguments = tuple(allowed)
    if card_status != "all":
        where += " AND d.card_status=?"
        arguments = (*arguments, card_status)
    return where, arguments


def _recent_rows(connection, where, arguments, cursor, limit):
    if cursor is not None:
        where += " AND (d.updated_at<? OR (d.updated_at=? AND d.id>?))"
        arguments = (*arguments, cursor.updated_at, cursor.updated_at, cursor.record_id)
    return connection.execute(
        "SELECT " + COLUMNS + ",0.0 AS raw_score FROM documents d WHERE " + where
        + " ORDER BY d.updated_at DESC,d.id ASC LIMIT ?", (*arguments, limit + 1),
    ).fetchall()


def _matching_rows(connection, terms, where, arguments, cursor, limit):
    expression = " OR ".join(
        '"' + term.replace('"', '""') + '"' + ("*" if len(term) >= 3 else "")
        for term in terms
    )
    # Materialization keeps the FTS auxiliary function in its valid evaluation context.
    sql = (
        "WITH matches AS MATERIALIZED (SELECT " + COLUMNS
        + ",bm25(memory_fts,4,1,3,0.6) AS raw_score FROM memory_fts "
        "JOIN documents d ON d.entry_id=memory_fts.rowid WHERE memory_fts MATCH ? AND "
        + where + ") SELECT * FROM matches"
    )
    arguments = (expression, *arguments)
    if cursor is not None:
        sql += (
            " WHERE raw_score>? OR (raw_score=? AND "
            "(updated_at<? OR (updated_at=? AND id>?)))"
        )
        arguments = (*arguments, cursor.score, cursor.score, cursor.updated_at,
                     cursor.updated_at, cursor.record_id)
    return connection.execute(
        sql + " ORDER BY raw_score ASC,updated_at DESC,id ASC LIMIT ?",
        (*arguments, limit + 1),
    ).fetchall()


def _matching_snapshot(connection, terms, where, arguments, cursor, limit):
    connection.execute("BEGIN")
    try:
        generation = connection.execute(
            "SELECT generation FROM memory_index_generation WHERE id=1"
        ).fetchone()[0]
        if cursor is not None and cursor.generation != generation:
            raise AppError(
                "browse_refresh_required",
                "Память изменилась. Обновите результаты поиска.",
                409,
            )
        return _matching_rows(connection, terms, where, arguments, cursor, limit), generation
    finally:
        connection.rollback()


def _summary(row):
    result = {key: row[key] for key in row.keys() if key not in ("raw_score", "card_json")}
    card = json.loads(row["card_json"]) if row["card_json"] else None
    summary = (card.get("summary") or None) if card else None
    return {
        **result, "card": {"title": card.get("title"), "summary": summary}
        if card else None, "summary": summary,
        "score": max(0.0, -row["raw_score"]),
    }


def browse_page(connection, query="", card_status="all", limit=30, cursor=None,
                *, allowed_clause="", allowed=()):
    _validate(query, card_status, limit)
    position = _decode_cursor(cursor, query, card_status)
    terms = tuple(dict.fromkeys(search_terms(query)))[:16]
    if not terms and query.strip():
        return {"results": [], "next_cursor": None, "has_more": False}
    where, arguments = _where(card_status, allowed_clause, allowed)
    generation = None
    if terms:
        rows, generation = _matching_snapshot(
            connection, terms, where, arguments, position, limit
        )
    else:
        rows = _recent_rows(connection, where, arguments, position, limit)
    more = len(rows) > limit
    visible = rows[:limit]
    return {
        "results": [_summary(row) for row in visible],
        "next_cursor": _encode_cursor(visible[-1], query, card_status, generation)
        if more else None,
        "has_more": more,
    }
