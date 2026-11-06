"""Deterministic projection of already-validated card atoms; no model calls."""

import hashlib
import json

from .errors import AppError
from .fact_relations import canonical_claim, fact_id, identity, support_status, validate_relation
from .fact_schema import now
from .source_policy import policy_clause


def _hash(card_json: str) -> str:
    return hashlib.sha256(card_json.encode()).hexdigest()


def _snapshot(connection, row) -> None:
    fields = ("source_id", "source_kind", "source_object_id", "source_folder_id", "title",
              "source_uri", "text", "project", "message_at", "source_created_at",
              "source_modified_at", "imported_at")
    connection.execute(
        "INSERT OR IGNORE INTO fact_snapshots(record_id,revision," + ",".join(fields)
        + ") VALUES(" + ",".join("?" for _ in range(len(fields) + 2)) + ")",
        (row["id"], row["revision"], *(row[name] for name in fields)),
    )


def _deactivate(connection, record_id) -> None:
    for table in ("fact_evidence", "fact_relations"):
        connection.execute(f"UPDATE {table} SET active=0 WHERE record_id=?", (record_id,))


def _support(connection, row, *, entity, quote, status, model, prompt_version) -> None:
    start = row["text"].find(quote)
    if start < 0:
        return
    end = start + len(quote)
    connection.execute(
        "INSERT INTO fact_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,1) "
        "ON CONFLICT(id) DO UPDATE SET active=1",
        (identity(entity, row["id"], row["revision"], start, end, quote), entity, row["id"],
         row["revision"], start, end, quote, status, now(), model, prompt_version),
    )


def _relation(connection, row, entity, relation) -> None:
    target = fact_id(relation["target_kind"], relation["target_text"])
    quote = relation["evidence"]
    start = row["text"].find(quote)
    end = start + len(quote)
    connection.execute(
        "INSERT INTO fact_relations VALUES(?,?,?,?,?,?,?,?,?,?,1) "
        "ON CONFLICT(id) DO UPDATE SET active=1",
        (identity(entity, target, row["id"], row["revision"], start, end, relation["kind"]),
         entity, target, row["id"], row["revision"], start, end, quote, relation["kind"], now()),
    )


def project_card(connection, row, card: dict) -> None:
    """Project atom evidence within the same transaction that saves the card."""
    _deactivate(connection, row["id"])
    atoms = [atom for atom in card.get("atoms", []) if atom["kind"] in ("fact", "decision")]
    if atoms:
        _snapshot(connection, row)
    for atom in atoms:
        text = canonical_claim(atom["text"])
        if not text:
            continue
        entity = fact_id(atom["kind"], text)
        connection.execute("INSERT OR IGNORE INTO fact_entities VALUES(?,?,?)",
                           (entity, atom["kind"], text))
        status = support_status(atom["text"], atom["evidence"], row["text"])
        _support(connection, row, entity=entity, quote=atom["evidence"], status=status,
                 model=row["model"], prompt_version=row["prompt_version"])
        relation = validate_relation(atom, row["text"])
        if relation and status == "source_supported":
            _relation(connection, row, entity, relation)
    connection.execute(
        "INSERT INTO fact_projection VALUES(?,?,?,'projected') ON CONFLICT(record_id) "
        "DO UPDATE SET revision=excluded.revision,card_hash=excluded.card_hash,outcome='projected'",
        (row["id"], row["revision"], _hash(row["card_json"])),
    )


def revalidate_record(connection, row) -> None:
    """Retain only exact quotes still present, with a new revision snapshot."""
    supports = connection.execute(
        "SELECT * FROM fact_evidence WHERE record_id=? AND active=1", (row["id"],)
    ).fetchall()
    relations = connection.execute(
        "SELECT * FROM fact_relations WHERE record_id=? AND active=1", (row["id"],)
    ).fetchall()
    _deactivate(connection, row["id"])
    valid = [support for support in supports if support["quote"] in row["text"]]
    if valid:
        _snapshot(connection, row)
    for support in valid:
        claim = connection.execute("SELECT text FROM fact_entities WHERE id=?",
                                   (support["fact_id"],)).fetchone()
        status = support_status(claim["text"], support["quote"], row["text"])
        _support(connection, row, entity=support["fact_id"], quote=support["quote"],
                 status=status, model=support["model"],
                 prompt_version=support["prompt_version"])
    for relation in relations:
        if relation["quote"] not in row["text"]:
            continue
        target = connection.execute("SELECT * FROM fact_entities WHERE id=?",
                                    (relation["target_fact"],)).fetchone()
        source = connection.execute("SELECT text FROM fact_entities WHERE id=?",
                                    (relation["from_fact"],)).fetchone()
        if target and source:
            try:
                checked = validate_relation({"text": source["text"], "relation": {
                    "target_kind": target["kind"], "target_text": target["text"],
                    "kind": relation["kind"], "evidence": relation["quote"],
                }}, row["text"])
            except AppError:
                continue  # Previous proof remains historical; current assertion was withdrawn.
            _relation(connection, row, relation["from_fact"], checked)


def _allowed(allowed_source_ids) -> tuple[str, tuple]:
    if allowed_source_ids is None:
        return "", ()
    values = tuple(dict.fromkeys(allowed_source_ids))
    if not values:
        return " AND 0", ()
    return " AND d.source_id IN (" + ",".join("?" for _ in values) + ")", values


def pending(connection, allowed_source_ids=None, limit=1) -> list[dict]:
    clause, values = _allowed(allowed_source_ids)
    # The card/revision update trigger invalidates seen identities, including
    # writes by older processes. Only bounded pending rows materialize as text.
    rows = connection.execute(
        "SELECT d.* FROM documents d LEFT JOIN fact_projection p ON p.record_id=d.id "
        "WHERE d.card_status=\'ready\' AND d.card_json IS NOT NULL "
        "AND (p.record_id IS NULL OR p.revision<>d.revision)"
        + policy_clause(None) + clause + " ORDER BY d.id LIMIT ?", (*values, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def pending_count(connection, allowed_source_ids=None) -> int:
    clause, values = _allowed(allowed_source_ids)
    return connection.execute(
        "SELECT COUNT(*) FROM documents d LEFT JOIN fact_projection p ON p.record_id=d.id "
        "WHERE d.card_status=\'ready\' AND d.card_json IS NOT NULL "
        "AND (p.record_id IS NULL OR p.revision<>d.revision)"
        + policy_clause(None) + clause, values,
    ).fetchone()[0]


def project_batch(store, limit=100, allowed_source_ids=None, check=None) -> dict:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise AppError("invalid_batch", "Размер пакета фактов должен быть от 1 до 500.")
    from .memory_validation import validate_card

    with store._connection() as connection:
        candidates = pending(connection, allowed_source_ids, limit)
    processed = projected = skipped = 0
    for candidate in candidates:
        if check is not None:
            check()
        with store._connection(write=True) as connection:
            row = connection.execute(
                "SELECT d.* FROM documents d WHERE d.id=? AND d.revision=? "
                "AND d.card_status='ready'" + policy_clause(None),
                (candidate["id"], candidate["revision"]),
            ).fetchone()
            if row is None or row["card_json"] != candidate["card_json"]:
                continue
            processed += 1
            try:
                clean = validate_card(json.loads(row["card_json"]), row)
                project_card(connection, row, clean)
                projected += 1
            except (AppError, ValueError, TypeError, RecursionError):
                _deactivate(connection, row["id"])
                connection.execute(
                    "INSERT INTO fact_projection VALUES(?,?,?,'invalid') "
                    "ON CONFLICT(record_id) DO UPDATE SET revision=excluded.revision,"
                    "card_hash=excluded.card_hash,outcome='invalid'",
                    (row["id"], row["revision"], _hash(row["card_json"])),
                )
                skipped += 1
    with store._connection() as connection:
        remaining = pending_count(connection, allowed_source_ids)
    return {"processed": processed, "projected": projected, "skipped": skipped,
            "remaining": remaining}
