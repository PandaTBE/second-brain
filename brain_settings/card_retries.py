"""Durable provider-call budgets. Recovery is invoked only by the leased writer."""

from datetime import UTC, datetime
from uuid import uuid4

MAX_AUTOMATIC_ATTEMPTS = 3
CLAIM_SECONDS = 300
RETRY_DELAYS = (60, 300)
INTERNAL_FIELDS = frozenset({"card_claim_token", "card_claim_until"})
ATTEMPTS = "CASE WHEN card_status='error' AND card_attempts=0 THEN 1 ELSE card_attempts END"


def migrate(connection):
    connection.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    additions = {
        "card_attempts": "INTEGER NOT NULL DEFAULT 0",
        "card_retry_at": "REAL",
        "card_claim_token": "TEXT",
        "card_claim_until": "REAL",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
    if "card_attempts" not in columns:
        connection.execute("UPDATE documents SET card_attempts=1 WHERE card_status='error'")
    connection.commit()


def public_fields(record):
    attempts = record.get("card_attempts", 0)
    if record["card_status"] == "error":
        attempts = max(1, attempts)
    retry_at = record.get("card_retry_at")
    return {
        "card_attempts": attempts,
        "card_retry_at": datetime.fromtimestamp(retry_at, UTC).isoformat()
        if retry_at is not None else None,
        "card_waiting_for_user": record["card_status"] == "error"
        and attempts >= MAX_AUTOMATIC_ATTEMPTS and not record.get("card_claim_token"),
    }


def candidates(connection, limit, retry_errors, automatic_retries, now, clause, allowed):
    due = "(card_retry_at IS NULL OR card_retry_at<=?)"
    budget = f"({ATTEMPTS})<?"
    pending = f"(card_status='pending' AND {budget} AND {due})"
    arguments = (MAX_AUTOMATIC_ATTEMPTS, now)
    if retry_errors:
        pending += " OR card_status='error'"
    elif automatic_retries:
        pending += f" OR (card_status='error' AND {budget} AND {due})"
        arguments = (*arguments, MAX_AUTOMATIC_ATTEMPTS, now)
    order = "CASE WHEN card_status='error' THEN 0 ELSE 1 END," if automatic_retries else ""
    return connection.execute(
        "SELECT * FROM documents WHERE (" + pending + ") "
        "AND (card_claim_until IS NULL OR card_claim_until<=?) "
        "AND source_id NOT IN (SELECT source_id FROM deleted_sources) " + clause
        + " ORDER BY " + order + "updated_at,id LIMIT ?",
        (*arguments, now, *allowed, limit),
    ).fetchall()


def claim(connection, record_id, revision, now, manual, clause, allowed):
    token = str(uuid4())
    eligible = (
        f"(card_status='pending' AND ({ATTEMPTS})<? "
        "AND (card_retry_at IS NULL OR card_retry_at<=?))"
    )
    arguments = (MAX_AUTOMATIC_ATTEMPTS, now)
    if manual:
        eligible += " OR card_status='error'"
    else:
        eligible += (
            f" OR (card_status='error' AND ({ATTEMPTS})<? "
            "AND (card_retry_at IS NULL OR card_retry_at<=?))"
        )
        arguments = (*arguments, MAX_AUTOMATIC_ATTEMPTS, now)
    result = connection.execute(
        f"UPDATE documents SET card_attempts=({ATTEMPTS})+1,card_retry_at=NULL,"
        "card_claim_token=?,card_claim_until=? WHERE id=? AND revision=? "
        "AND (" + eligible + ") AND (card_claim_until IS NULL OR card_claim_until<=?) "
        "AND source_id NOT IN (SELECT source_id FROM deleted_sources) " + clause,
        (token, now + CLAIM_SECONDS, record_id, revision, *arguments, now, *allowed),
    )
    return token if result.rowcount else None


def failure(connection, record_id, revision, message, token, now):
    clause = " AND card_claim_token=?" if token is not None else ""
    parameters = (record_id, revision, token) if token is not None else (record_id, revision)
    row = connection.execute(
        "SELECT * FROM documents WHERE id=? AND revision=? AND card_status!='ready' "
        "AND source_id NOT IN (SELECT source_id FROM deleted_sources)" + clause, parameters,
    ).fetchone()
    if row is None:
        return
    attempts = max(1, row["card_attempts"])
    retry_at = now + RETRY_DELAYS[min(attempts - 1, 1)] if attempts < 3 else None
    connection.execute(
        "UPDATE documents SET card_status='error',card_error=?,card_attempts=?,"
        "card_retry_at=?,card_claim_token=NULL,card_claim_until=NULL WHERE id=? AND revision=?",
        (message, attempts, retry_at, record_id, revision),
    )


def release(connection, record_id, revision, token, now, refund=False):
    if refund:
        connection.execute(
            "UPDATE documents SET card_attempts=MAX(0,card_attempts-1),card_retry_at=NULL,"
            "card_claim_token=NULL,card_claim_until=NULL "
            "WHERE id=? AND revision=? AND card_claim_token=? AND card_status!='ready'",
            (record_id, revision, token),
        )
        return
    connection.execute(
        "UPDATE documents SET card_claim_token=NULL,card_claim_until=NULL,"
        "card_retry_at=CASE WHEN card_attempts=1 THEN ? WHEN card_attempts=2 THEN ? "
        "ELSE NULL END,"
        "card_status=CASE WHEN card_attempts>=3 THEN 'error' ELSE card_status END,"
        "card_error=CASE WHEN card_attempts>=3 THEN "
        "'Лимит автоматических попыток исчерпан. Повторите обработку вручную.' "
        "ELSE card_error END "
        "WHERE id=? AND revision=? AND card_claim_token=? AND card_status!='ready'",
        (now + RETRY_DELAYS[0], now + RETRY_DELAYS[1], record_id, revision, token),
    )


def recover(connection, now):
    connection.execute(
        "UPDATE documents SET card_status='error',"
        "card_error='Попытка обработки прервалась. Исходный текст сохранён.',"
        "card_retry_at=CASE WHEN card_attempts<3 THEN ? ELSE NULL END,"
        "card_claim_token=NULL,card_claim_until=NULL "
        "WHERE card_status!='ready' AND card_claim_token IS NOT NULL AND card_claim_until<=?",
        (now, now),
    )


def has_due(connection, now, clause, allowed):
    return connection.execute(
        "SELECT 1 FROM documents WHERE source_id NOT IN "
        "(SELECT source_id FROM deleted_sources) " + clause
        + f" AND ((((card_status='error') OR "
        "card_status='pending') "
        f"AND ({ATTEMPTS})<3 AND (card_retry_at IS NULL OR card_retry_at<=?) "
        "AND (card_claim_until IS NULL OR card_claim_until<=?)) OR "
        "(card_status!='ready' AND card_claim_token IS NOT NULL AND card_claim_until<=?)) LIMIT 1",
        (*allowed, now, now, now),
    ).fetchone() is not None


def counts(connection, clause, allowed):
    row = connection.execute(
        f"SELECT COALESCE(SUM(({ATTEMPTS})<3),0) AS retrying,"
        f"COALESCE(SUM(({ATTEMPTS})>=3 AND card_claim_token IS NULL),0) "
        "AS waiting FROM documents "
        "WHERE card_status='error' AND source_id NOT IN "
        "(SELECT source_id FROM deleted_sources) " + clause, allowed,
    ).fetchone()
    return {"cards_retry_pending": row["retrying"], "cards_waiting_user": row["waiting"]}
