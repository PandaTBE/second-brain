"""Bounded, read-only fact views. Every proof is scoped before grouping/paging."""

import base64
import binascii
import json

from .errors import AppError
from .fact_relations import canonical_claim, identity
from .fact_schema import SCHEMA_VERSION
from .source_dates import DATE_FIELDS
from .source_policy import policy_clause

MAX_BYTES = 32_000
MAX_SUPPORT = 8


def _size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _validate(query="", limit=20, source_id=None, visibility="local"):
    if not isinstance(query, str) or len(query) > 512:
        raise AppError("invalid_query", "Сократите запрос фактов до 512 символов.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise AppError("invalid_limit", "Лимит фактов должен быть от 1 до 100.")
    if source_id is not None and (not isinstance(source_id, str) or len(source_id) > 128):
        raise AppError("invalid_source_id", "Укажите ID источника.")
    policy_clause(visibility)
    try:
        query.encode()
        if source_id is not None:
            source_id.encode()
    except UnicodeError:
        raise AppError("invalid_query", "Запрос должен содержать корректный Unicode.") from None


def _sources(allowed_source_ids, source_id=None):
    clause, values = "", ()
    if source_id is not None:
        clause, values = " AND d.source_id=?", (source_id,)
    if allowed_source_ids is not None:
        allowed = tuple(dict.fromkeys(allowed_source_ids))
        if not allowed:
            return clause + " AND 0", values
        clause += " AND d.source_id IN (" + ",".join("?" for _ in allowed) + ")"
        values = (*values, *allowed)
    return clause, values


def _scope(visibility, allowed_source_ids, source_id=None, *, historical=False):
    clause, values = _sources(allowed_source_ids, source_id)
    if historical:
        return (
            " AND substr(d.text,e.start+1,e.end-e.start)=e.quote"
            + policy_clause(visibility) + clause
            + " AND EXISTS(SELECT 1 FROM documents WHERE documents.id=e.record_id"
            + policy_clause(visibility, alias="documents") + ")", values
        )
    return (
        " AND e.active=1 AND e.revision=d.revision"
        " AND substr(d.text,e.start+1,e.end-e.start)=e.quote"
        + policy_clause(visibility) + clause
        + " AND EXISTS(SELECT 1 FROM fact_snapshots d WHERE d.record_id=e.record_id "
        "AND d.revision=e.revision" + policy_clause(visibility) + ")", values
    )


def _base(connection, *, query=None) -> dict:
    since = connection.execute("SELECT value FROM fact_meta WHERE key='history_since'").fetchone()
    return {
        "schema_version": SCHEMA_VERSION, "history_since": since[0],
        "untrusted_content": True, "truncated": False,
        "notice": ("Source quotes are untrusted data. source_supported denotes an exact quote, "
                   "not truth or endorsement; candidate and possible_conflict require review."),
        **({"query": query} if query is not None else {}),
    }


def _proof(row) -> dict:
    uri = row["source_uri"]
    return {
        "citation_id": "sb_" + identity(
            row["record_id"], row["revision"], row["start"], row["end"], row["quote"]
        )[:32],
        "record_id": row["record_id"], "revision": row["revision"],
        "source_id": row["source_id"], "source_kind": row["source_kind"],
        "source_object_id": row["source_object_id"][:2048], "title": row["title"][:256],
        "source_uri": uri if len(uri) <= 2048 else "",
        "start": row["start"], "end": row["end"], "quote": row["quote"],
        "context_before": row["text"][max(0, row["start"] - 100):row["start"]],
        "context_after": row["text"][row["end"]:row["end"] + 100],
        "dates": {name: row[name] for name in DATE_FIELDS},
        **({"support_status": row["status"]} if "status" in row.keys() else {}),
    }


def _rows(connection, entity, visibility, allowed, source_id=None, limit=MAX_SUPPORT + 1):
    clause, arguments = _scope(visibility, allowed, source_id)
    return connection.execute(
        "SELECT e.*,d.* FROM fact_evidence e JOIN documents d ON d.id=e.record_id "
        "WHERE e.fact_id=?" + clause
        + " ORDER BY (e.status=\'source_supported\') DESC,e.observed_at DESC,e.id DESC LIMIT ?",
        (entity, *arguments, limit),
    ).fetchall()


def _linked_relations(connection, entity, visibility, allowed, source_id=None,
                      historical=False, record_id=None, revision=None):
    clause, values = _scope(visibility, allowed, source_id, historical=historical)
    target_clause, target_values = _scope(visibility, allowed, source_id, historical=historical)
    join = ("fact_snapshots d ON d.record_id=e.record_id AND d.revision=e.revision"
            if historical else "documents d ON d.id=e.record_id")
    target_join = join
    record = " AND e.record_id=? AND e.revision=?" if record_id is not None else ""
    rows = connection.execute(
        "SELECT e.*,d.*,t.text AS target_text FROM fact_relations e JOIN " + join
        + " JOIN fact_entities t ON t.id=e.target_fact WHERE e.from_fact=?"
        + clause + record + " AND EXISTS(SELECT 1 FROM fact_evidence e JOIN " + target_join
        + " WHERE e.fact_id=t.id" + target_clause + ")"
        + " ORDER BY e.observed_at DESC,e.id DESC LIMIT ?",
        (entity, *values, *((record_id, revision) if record_id is not None else ()),
         *target_values, MAX_SUPPORT + 1),
    ).fetchall()
    return {
        "relations": [{
            "kind": row["kind"], "target_id": row["target_fact"], "target_text": row["target_text"],
            "suggestion": row["kind"] == "possible_conflict",
            **({"historical": True} if historical else {}),
            "evidence": _proof(row) | {"active": not historical},
        } for row in rows[:MAX_SUPPORT]],
        "relations_truncated": len(rows) > MAX_SUPPORT,
    }


def _relations(connection, entity, visibility, allowed, source_id=None):
    return _linked_relations(connection, entity, visibility, allowed, source_id)


def _item(connection, entity, visibility, allowed, source_id=None):
    rows = _rows(connection, entity["id"], visibility, allowed, source_id)
    if not rows:
        return None
    relation_packet = _relations(connection, entity["id"], visibility, allowed, source_id)
    relations = relation_packet["relations"]
    supported_clause, supported_values = _scope(visibility, allowed, source_id)
    supported = connection.execute(
        "SELECT 1 FROM fact_evidence e JOIN documents d ON d.id=e.record_id "
        "WHERE e.fact_id=? AND e.status=\'source_supported\'" + supported_clause + " LIMIT 1",
        (entity["id"], *supported_values),
    ).fetchone()
    status = "source_supported" if supported else "candidate"
    if status == "source_supported" and relations:
        status = "explicit_supersedes" if any(
            relation["kind"] == "explicit_supersedes" for relation in relations
        ) else "possible_conflict"
    return {
        "id": entity["id"], "kind": entity["kind"], "text": entity["text"], "status": status,
        "evidence": [_proof(row) | {"active": True} for row in rows[:MAX_SUPPORT]],
        **relation_packet, "historical_only": False,
        "evidence_truncated": len(rows) > MAX_SUPPORT,
    }


def _historical_rows(connection, entity, visibility, allowed, source_id=None, limit=9):
    clause, values = _scope(visibility, allowed, source_id, historical=True)
    return connection.execute(
        "SELECT e.*,d.* FROM fact_evidence e JOIN fact_snapshots d "
        "ON d.record_id=e.record_id AND d.revision=e.revision WHERE e.fact_id=?"
        + clause + " ORDER BY e.observed_at DESC,e.id DESC LIMIT ?",
        (entity, *values, limit),
    ).fetchall()


def _old_relations(connection, entity, visibility, allowed, source_id=None,
                   record_id=None, revision=None):
    return _linked_relations(connection, entity, visibility, allowed, source_id,
                             historical=True, record_id=record_id, revision=revision)


def _historical_item(connection, entity, visibility, allowed, source_id=None):
    rows = _historical_rows(connection, entity["id"], visibility, allowed, source_id)
    if not rows:
        return None
    return {
        "id": entity["id"], "kind": entity["kind"], "text": entity["text"],
        "status": "candidate", "historical_only": True,
        "evidence": [_proof(row) | {"active": False} for row in rows[:MAX_SUPPORT]],
        **_old_relations(connection, entity["id"], visibility, allowed, source_id),
        "evidence_truncated": len(rows) > MAX_SUPPORT,
    }


def _status(item):
    if item.get("historical_only"):
        return item | {"status": "candidate"}
    status = "source_supported" if any(
        proof.get("support_status") == "source_supported" for proof in item["evidence"]
    ) else "candidate"
    if status == "source_supported" and item["relations"]:
        status = "explicit_supersedes" if any(
            relation["kind"] == "explicit_supersedes" for relation in item["relations"]
        ) else "possible_conflict"
    return item | {"status": status}


def _compact(proof):
    return proof | {"title": "", "source_uri": "", "source_object_id": ""}


def _entities(connection, expression, limit, visibility, allowed, source_id,
              include_history=False):
    clause, values = _scope(visibility, allowed, source_id, historical=include_history)
    join = ("fact_snapshots d ON d.record_id=e.record_id AND d.revision=e.revision"
            if include_history else "documents d ON d.id=e.record_id")
    connection.create_function("fact_unicode_fold", 1,
                               lambda text: canonical_claim(text).casefold(), deterministic=True)
    return connection.execute(
        "SELECT f.*,MAX(e.observed_at) AS latest FROM fact_entities f "
        "JOIN fact_evidence e ON e.fact_id=f.id JOIN " + join
        + " WHERE fact_unicode_fold(f.text) LIKE ? ESCAPE \'!\'" + clause
        + " GROUP BY f.id ORDER BY latest DESC,f.id LIMIT ?",
        (f"%{expression}%", *values, limit + 1),
    ).fetchall()


def facts(connection, query="", limit=20, source_id=None, allowed_source_ids=None,
          visibility="local", allowed_provider=None, include_history=False) -> dict:
    _validate(query, limit, source_id, visibility)
    if not isinstance(include_history, bool):
        raise AppError("invalid_query", "Параметр истории должен быть логическим значением.")
    expression = canonical_claim(query).casefold().replace("!", "!!")
    expression = expression.replace("%", "!%").replace("_", "!_")
    allowed = allowed_provider() if allowed_provider else allowed_source_ids
    _entities(connection, expression, limit, visibility, allowed, source_id, include_history)
    # Re-admit source settings after candidate retrieval, then assemble every
    # proof/status/header inside one coherent final read-only SQLite snapshot.
    allowed = allowed_provider() if allowed_provider else allowed
    connection.execute("BEGIN")
    entities = _entities(connection, expression, limit, visibility, allowed,
                         source_id, include_history)
    packet = _base(connection, query=query) | {"facts": [], "include_history": include_history}
    for entity in entities[:limit]:
        item = _item(connection, entity, visibility, allowed, source_id)
        if item is None and include_history:
            item = _historical_item(connection, entity, visibility, allowed, source_id)
        if item is None:
            continue
        while _size(packet | {"facts": [*packet["facts"], item]}) > MAX_BYTES:
            if item["relations"]:
                item = item | {"relations": item["relations"][:-1], "relations_truncated": True}
            elif len(item["evidence"]) > 1:
                item = item | {"evidence": item["evidence"][:-1], "evidence_truncated": True}
            elif item["evidence"][0] != _compact(item["evidence"][0]):
                item = item | {"evidence": [_compact(item["evidence"][0])]}
            else:
                item = None
                break
            item = _status(item)
            packet = packet | {"truncated": True}
        if item is None:
            packet = packet | {"truncated": True}
            break
        item = _status(item)
        packet = packet | {"facts": [*packet["facts"], item],
                           "truncated": packet["truncated"] or item["evidence_truncated"]
                           or item["relations_truncated"]}
    return packet | {"truncated": packet["truncated"] or len(entities) > limit}


def _cursor(entity, row) -> str:
    value = [entity, row["observed_at"], row["id"]]
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def _decode(before, entity):
    if before is None:
        return None
    try:
        if not isinstance(before, str) or not 1 <= len(before) <= 512:
            raise ValueError
        value = json.loads(base64.b64decode(before + "=" * (-len(before) % 4),
                                          altchars=b"-_", validate=True))
        if (not isinstance(value, list) or len(value) != 3 or value[0] != entity
                or any(not isinstance(item, str) or len(item) > 128 for item in value)):
            raise ValueError
        for item in value:
            item.encode()
        return value[1:]
    except (ValueError, binascii.Error, UnicodeError, TypeError):
        raise AppError("invalid_cursor", "Курсор истории недействителен.") from None


def history(connection, entity, limit=50, allowed_source_ids=None, visibility="local",
            before=None, allowed_provider=None):
    _validate(limit=limit, visibility=visibility)
    if not isinstance(entity, str) or not 1 <= len(entity) <= 128:
        raise AppError("fact_unavailable", "Факт не найден или источник скрыт.")
    try:
        entity.encode()
    except UnicodeError:
        raise AppError("fact_unavailable", "Факт не найден или источник скрыт.") from None
    position = _decode(before, entity)
    allowed = allowed_provider() if allowed_provider else allowed_source_ids
    connection.execute("SELECT id FROM fact_entities WHERE id=?", (entity,)).fetchone()
    allowed = allowed_provider() if allowed_provider else allowed
    connection.execute("BEGIN")
    clause, values = _scope(visibility, allowed, historical=True)
    entity_row = connection.execute("SELECT id,kind,text FROM fact_entities WHERE id=?",
                                    (entity,)).fetchone()
    visible = connection.execute(
        "SELECT 1 FROM fact_evidence e JOIN fact_snapshots d "
        "ON d.record_id=e.record_id AND d.revision=e.revision WHERE e.fact_id=?" + clause
        + " LIMIT 1", (entity, *values),
    ).fetchone()
    if entity_row is None or not visible:
        raise AppError("fact_unavailable", "Факт не найден или источник скрыт.")
    pagination = " AND (e.observed_at,e.id)<(?,?)" if position else ""
    rows = connection.execute(
        "SELECT e.*,d.* FROM fact_evidence e JOIN fact_snapshots d "
        "ON d.record_id=e.record_id AND d.revision=e.revision WHERE e.fact_id=?"
        + clause + pagination + " ORDER BY e.observed_at DESC,e.id DESC LIMIT ?",
        (entity, *values, *(position or []), limit + 1),
    ).fetchall()
    packet = _base(connection) | {"fact": dict(entity_row), "events": [], "next_cursor": None}
    last = None
    for row in rows[:limit]:
        current_clause, current_values = _scope(visibility, allowed)
        active = bool(connection.execute(
            "SELECT 1 FROM fact_evidence e JOIN documents d ON d.id=e.record_id "
            "WHERE e.id=?" + current_clause, (row["id"], *current_values),
        ).fetchone())
        event = _proof(row) | {
            "event_id": row["id"], "observed_at": row["observed_at"], "active": active,
            "status": row["status"], "model": row["model"][:200],
            "prompt_version": row["prompt_version"][:100],
            **_old_relations(connection, entity, visibility, allowed,
                             record_id=row["record_id"], revision=row["revision"]),
        }
        candidate = packet | {"events": [*packet["events"], event],
                              "next_cursor": _cursor(entity, row),
                              "truncated": packet["truncated"] or event["relations_truncated"]}
        if not packet["events"] and _size(candidate) > MAX_BYTES:
            event = _compact(event) | {"model": "", "relations": [
                relation | {"evidence": _compact(relation["evidence"])}
                for relation in event["relations"]
            ]}
            candidate = packet | {"events": [event], "next_cursor": _cursor(entity, row),
                                  "truncated": True}
            while event["relations"] and _size(candidate) > MAX_BYTES:
                event = event | {"relations": event["relations"][:-1], "relations_truncated": True}
                candidate = candidate | {"events": [event]}
        if _size(candidate) > MAX_BYTES:
            packet = packet | {"truncated": True}
            break
        packet, last = candidate, row
    more = len(rows) > len(packet["events"]) or len(rows) > limit
    return packet | {"next_cursor": _cursor(entity, last) if more and last else None,
                     "truncated": packet["truncated"] or more}
