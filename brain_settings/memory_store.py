"""Transactional SQLite memory with immediate lexical search and revision CAS."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .errors import AppError
from .memory_text import normalize_text, redact_text, search_terms
from .source_dates import UNSET, save_metadata, source_metadata
from .source_policy import policy_clause, record_allowed, schema_lock

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
 entry_id INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
 source_id TEXT NOT NULL, source_kind TEXT NOT NULL, source_object_id TEXT NOT NULL,
 title TEXT NOT NULL, text TEXT NOT NULL, source_uri TEXT NOT NULL,
 content_hash TEXT NOT NULL, revision INTEGER NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 card_status TEXT NOT NULL DEFAULT 'pending', card_json TEXT, card_error TEXT,
 model TEXT NOT NULL DEFAULT '', prompt_version TEXT NOT NULL DEFAULT '',
 UNIQUE(source_id,source_object_id)
);
CREATE INDEX IF NOT EXISTS documents_source ON documents(source_id);
CREATE TABLE IF NOT EXISTS deleted_sources (source_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS memory_index_generation (
 id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO memory_index_generation(id,generation) VALUES(1,0);
CREATE TRIGGER IF NOT EXISTS documents_browse_generation_insert AFTER INSERT ON documents
BEGIN UPDATE memory_index_generation SET generation=generation+1 WHERE id=1; END;
CREATE TRIGGER IF NOT EXISTS documents_browse_generation_update AFTER UPDATE ON documents
BEGIN UPDATE memory_index_generation SET generation=generation+1 WHERE id=1; END;
CREATE TRIGGER IF NOT EXISTS documents_browse_generation_delete AFTER DELETE ON documents
BEGIN UPDATE memory_index_generation SET generation=generation+1 WHERE id=1; END;
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(title,body,card,terms);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _timestamp(value, fallback: str) -> str:
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            return fallback
    return fallback


def _row(row: sqlite3.Row) -> dict:
    from .card_retries import INTERNAL_FIELDS, public_fields

    result = {
        key: row[key] for key in row.keys()
        if key not in ("entry_id", "card_json") and key not in INTERNAL_FIELDS
    }
    result["card"] = json.loads(row["card_json"]) if row["card_json"] else None
    result["status"] = row["card_status"]
    result["summary"] = result["card"].get("summary", "") if result["card"] else ""
    return {**result, **public_fields(dict(row))}


def _allowed_clause(allowed_source_ids, column="source_id"):
    if allowed_source_ids is None:
        return "", ()
    identifiers = tuple(dict.fromkeys(str(value) for value in allowed_source_ids))[:1000]
    if not identifiers:
        return " AND 0", ()
    return " AND " + column + " IN (" + ",".join("?" for _ in identifiers) + ")", identifiers


def _safe_file_key(value: str) -> str:
    clean = redact_text(value)
    if clean == value:
        return value
    return clean + "::file:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_object_key(value: str) -> str:
    if "::chunk:" in value:
        file_key, position = value.rsplit("::chunk:", 1)
        if position.isdigit():
            return _safe_file_key(file_key) + "::chunk:" + position
    return _safe_file_key(value)


class MemoryStore:
    def __init__(self, data_dir: Path):
        self.directory = Path(data_dir)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.path = self.directory / "memory.sqlite3"
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with schema_lock(self.directory), self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            from .card_retries import migrate

            migrate(connection)
            from .source_policy import migrate as migrate_policy

            migrate_policy(connection)
            from .embedding_store import migrate as migrate_embeddings

            migrate_embeddings(connection)
            from .source_dates import migrate as migrate_dates

            migrate_dates(connection)
            from .fact_schema import migrate as migrate_facts

            migrate_facts(connection)

    @contextmanager
    def _connection(self, write=False):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except sqlite3.Error as error:
            if connection is not None and write:
                connection.rollback()
            raise AppError(
                "memory_unavailable",
                "Не удалось открыть локальную память. Проверьте папку данных приложения.",
                503,
            ) from error
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _index(connection, row):
        card = row["card_json"] or ""
        connection.execute("DELETE FROM memory_fts WHERE rowid=?", (row["entry_id"],))
        connection.execute(
            "INSERT INTO memory_fts(rowid,title,body,card,terms) VALUES(?,?,?,?,?)",
            (
                row["entry_id"],
                row["title"],
                row["text"],
                card,
                normalize_text(row["title"] + " " + row["text"] + " " + card),
            ),
        )

    @staticmethod
    def _check_source(connection, source_id):
        if connection.execute(
            "SELECT 1 FROM deleted_sources WHERE source_id=?", (source_id,)
        ).fetchone():
            raise AppError("source_deleted", "Источник удалён; его импорт остановлен.", 404)

    def source_policy(self, source_id):
        from .source_policy import load_policy

        with self._connection() as connection:
            return load_policy(connection, source_id)

    def set_source_policy(self, source_id, policy):
        from .source_policy import save_policy

        with self._connection(write=True) as connection:
            self._check_source(connection, source_id)
            return save_policy(connection, source_id, policy)

    def preview_source_policy(self, source_id, policy):
        from .source_policy import preview_policy

        with self._connection() as connection:
            self._check_source(connection, source_id)
            return preview_policy(connection, source_id, policy)

    def import_path_allowed(self, source_id, path):
        with self._connection() as connection:
            return record_allowed(connection, source_id, 'folder', path, visibility='import')

    def _upsert(
        self,
        connection,
        *,
        source_id,
        source_kind,
        source_object_id,
        title,
        text,
        source_uri,
        created_at=None,
        updated_at=None,
        source_folder_id="",
        message_at=UNSET,
        source_created_at=UNSET,
        source_modified_at=UNSET,
        project=UNSET,
    ):
        metadata = source_metadata(message_at=message_at, source_created_at=source_created_at,
                                   source_modified_at=source_modified_at, project=project)
        self._check_source(connection, source_id)
        if not all(
            isinstance(value, str)
            for value in (source_id, source_kind, source_object_id, title, text, source_uri)
        ):
            raise AppError("invalid_document", "Некорректная запись для импорта.")
        if not isinstance(source_folder_id, str) or len(source_folder_id) > 2048:
            raise AppError("invalid_document", "Некорректная папка материала.")
        source_object_id = _safe_object_key(source_object_id)
        if not record_allowed(connection, source_id, source_kind, source_object_id,
                              source_folder_id):
            raise AppError("source_policy_denied",
                           "Импорт материала запрещён политикой источника.", 403)
        clean_text, clean_title = redact_text(text), redact_text(title)
        if not clean_text.strip() or len(clean_text) > 100_000:
            raise AppError("invalid_document", "Запись пуста или превышает размер импорта.")
        digest = hashlib.sha256((clean_title + "\0" + clean_text).encode()).hexdigest()
        previous = connection.execute(
            "SELECT * FROM documents WHERE source_id=? AND source_object_id=?",
            (source_id, source_object_id),
        ).fetchone()
        if previous is not None and previous["content_hash"] == digest:
            save_metadata(connection, previous["id"], metadata)
            if source_folder_id != previous["source_folder_id"]:
                connection.execute("UPDATE documents SET source_folder_id=? WHERE id=?",
                                   (source_folder_id, previous["id"]))
            uri = redact_text(source_uri)
            if uri != previous["source_uri"]:
                saved_card = json.loads(previous["card_json"]) if previous["card_json"] else None
                if saved_card is not None:
                    saved_card = {
                        **saved_card,
                        "atoms": [
                            {**atom, "source_uri": uri} for atom in saved_card.get("atoms", [])
                        ],
                    }
                connection.execute(
                    "UPDATE documents SET source_uri=?,card_json=? WHERE id=?",
                    (
                        uri,
                        json.dumps(saved_card, ensure_ascii=False)
                        if saved_card is not None
                        else None,
                        previous["id"],
                    ),
                )
                self._index(
                    connection,
                    connection.execute(
                        "SELECT * FROM documents WHERE id=?", (previous["id"],)
                    ).fetchone(),
                )
            return {"id": previous["id"], "revision": previous["revision"], "changed": False}
        identifier = str(uuid5(NAMESPACE_URL, source_id + "\0" + source_object_id))
        revision = previous["revision"] + 1 if previous is not None else 1
        created = previous["created_at"] if previous is not None else _timestamp(created_at, _now())
        modified = _timestamp(updated_at, _timestamp(created_at, _now()))
        connection.execute(
            """INSERT INTO documents
          (id,source_id,source_kind,source_object_id,title,text,source_uri,content_hash,revision,
           created_at,updated_at,source_folder_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(source_id,source_object_id) DO UPDATE SET
           title=excluded.title,text=excluded.text,source_uri=excluded.source_uri,
           content_hash=excluded.content_hash,revision=excluded.revision,updated_at=excluded.updated_at,
           source_folder_id=excluded.source_folder_id,
           card_status='pending',card_json=NULL,card_error=NULL,model='',prompt_version='',
           card_attempts=0,card_retry_at=NULL,card_claim_token=NULL,card_claim_until=NULL
          """,
            (
                identifier,
                source_id,
                source_kind,
                source_object_id,
                clean_title,
                clean_text,
                redact_text(source_uri),
                digest,
                revision,
                created,
                modified,
                source_folder_id,
            ),
        )
        save_metadata(connection, identifier, metadata, imported_at=_now())
        row = connection.execute("SELECT * FROM documents WHERE id=?", (identifier,)).fetchone()
        self._index(connection, row)
        if previous is not None:
            from .memory_facts import revalidate_record

            revalidate_record(connection, row)
        return {"id": identifier, "revision": revision, "changed": True}

    def upsert_document(
        self,
        source_id,
        source_kind,
        source_object_id,
        title,
        text,
        source_uri,
        created_at=None,
        updated_at=None,
        source_folder_id="",
        message_at=UNSET,
        source_created_at=UNSET,
        source_modified_at=UNSET,
        project=UNSET,
    ) -> dict:
        with self._connection(write=True) as connection:
            return self._upsert(
                connection,
                source_id=source_id,
                source_kind=source_kind,
                source_object_id=source_object_id,
                title=title,
                text=text,
                source_uri=source_uri,
                created_at=created_at,
                updated_at=updated_at,
                source_folder_id=source_folder_id,
                message_at=message_at,
                source_created_at=source_created_at,
                source_modified_at=source_modified_at,
                project=project,
            )

    def replace_file_documents(self, source_id, file_key, documents, complete=True) -> list[dict]:
        with self._connection(write=True) as connection:
            self._check_source(connection, source_id)
            if not record_allowed(connection, source_id, "folder", _safe_file_key(file_key)):
                raise AppError("source_policy_denied", "Импорт запрещён политикой источника.", 403)
            if any(document["source_id"] != source_id for document in documents):
                raise AppError("invalid_document", "Нельзя смешивать источники при импорте файла.")
            results = [self._upsert(connection, **document) for document in documents]
            if complete:
                keep = {result["id"] for result in results}
                rows = connection.execute(
                    "SELECT entry_id,id,source_object_id,source_kind,source_folder_id "
                    "FROM documents WHERE source_id=?",
                    (source_id,),
                ).fetchall()
                for row in rows:
                    if (
                        row["source_object_id"].startswith(_safe_file_key(file_key) + "::chunk:")
                        and row["id"] not in keep
                    ):
                        if not documents and not record_allowed(
                            connection, source_id, row["source_kind"], row["source_object_id"],
                            row["source_folder_id"],
                        ):
                            raise AppError("source_policy_denied",
                                           "Импорт запрещён политикой источника.", 403)
                        connection.execute(
                            "DELETE FROM memory_fts WHERE rowid=?", (row["entry_id"],)
                        )
                        connection.execute("DELETE FROM documents WHERE id=?", (row["id"],))
            return results

    def get_record(self, identifier: str, allowed_source_ids=None, *, visibility="local") -> dict:
        clause, allowed = _allowed_clause(allowed_source_ids, "d.source_id")
        clause += policy_clause(visibility)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT d.* FROM documents d WHERE d.id=?" + clause,
                (identifier, *allowed),
            ).fetchone()
        if row is None:
            raise AppError("record_not_found", "Запись памяти не найдена.", 404)
        return _row(row)

    def search(self, query: str, limit=8, source_id=None, allowed_source_ids=None,
               *, visibility="local", project=None, date_from=None, date_to=None) -> list[dict]:
        if not isinstance(query, str) or len(query) > 512 or isinstance(limit, bool):
            raise AppError("invalid_search", "Сократите поисковый запрос до 512 символов.")
        if not isinstance(limit, int) or not 1 <= limit <= 50:
            raise AppError("invalid_search", "Лимит результатов должен быть от 1 до 50.")
        from .retrieval_filters import SearchFilters

        filters = SearchFilters(project, date_from, date_to)
        terms = tuple(dict.fromkeys(search_terms(query)))[:16]
        where = "1=1" + policy_clause(visibility)
        arguments = ()
        if source_id is not None:
            where += " AND d.source_id=?"
            arguments = (source_id,)
        allowed_clause, allowed = _allowed_clause(allowed_source_ids, "d.source_id")
        where += allowed_clause
        filter_clause, filter_args = filters.clause()
        where += filter_clause
        arguments = (*arguments, *allowed, *filter_args)
        with self._connection() as connection:
            if terms:
                expression = " OR ".join(
                    '"' + term.replace('"', '""') + '"' + ("*" if len(term) >= 3 else "")
                    for term in terms
                )
                rows = connection.execute(
                    "SELECT d.*,bm25(memory_fts,4,1,3,0.6) AS raw_score FROM memory_fts "
                    "JOIN documents d ON d.entry_id=memory_fts.rowid WHERE memory_fts MATCH ? AND "
                    + where
                    + " ORDER BY raw_score LIMIT 100",
                    (expression, *arguments),
                ).fetchall()
            elif query.strip():
                return []
            else:
                rows = connection.execute(
                    "SELECT d.*,0.0 AS raw_score FROM documents d WHERE "
                    + where
                    + " ORDER BY updated_at DESC,id LIMIT ?",
                    (*arguments, limit),
                ).fetchall()
        hits = []
        for row in rows:
            record = _row(row)
            score = max(0.0, -row["raw_score"])
            if terms:
                try:
                    date = datetime.fromisoformat(record["updated_at"].replace("Z", "+00:00"))
                    age = max(0, (datetime.now(UTC) - date.replace(tzinfo=date.tzinfo or UTC)).days)
                    score *= 0.8 + 0.2 * 2 ** (-age / 120)
                except ValueError:
                    pass
            hits = [*hits, {**record, "snippet": record["text"][:480], "score": score}]
        return sorted(hits, key=lambda item: item["score"], reverse=True)[:limit] if terms else hits

    def browse(self, query="", card_status="all", limit=30, cursor=None,
               *, allowed_source_ids=None, visibility="local") -> dict:
        from .memory_browse import browse_page

        clause, allowed = _allowed_clause(allowed_source_ids, "d.source_id")
        clause += policy_clause(visibility)
        with self._connection() as connection:
            return browse_page(
                connection, query, card_status, limit, cursor,
                allowed_clause=clause, allowed=allowed,
            )

    def atlas(self, *, allowed_source_ids=None, visibility="local") -> dict:
        from .memory_atlas import atlas_snapshot

        clause, allowed = _allowed_clause(allowed_source_ids, "d.source_id")
        clause += policy_clause(visibility)
        with self._connection() as connection:
            return atlas_snapshot(connection, allowed_clause=clause, allowed=allowed)

    def pending(self, limit=10, retry_errors=False, allowed_source_ids=None,
                *, automatic_retries=False, now=None, visibility=None) -> list[dict]:
        import time

        from .card_retries import candidates

        clause, allowed = _allowed_clause(allowed_source_ids)
        clause += policy_clause(visibility, alias="documents")
        with self._connection() as connection:
            rows = candidates(connection, max(1, min(int(limit), 100)), retry_errors,
                              automatic_retries, time.time() if now is None else now,
                              clause, allowed)
        return [_row(row) for row in rows]

    def claim_card_attempt(self, record_id, revision, *, now=None, manual=False,
                           allowed_source_ids=None, visibility=None):
        import time

        from .card_retries import claim

        clause, allowed = _allowed_clause(allowed_source_ids)
        clause += policy_clause(visibility, alias="documents")
        with self._connection(write=True) as connection:
            return claim(connection, record_id, revision, time.time() if now is None else now,
                         manual, clause, allowed)

    def release_card_attempt(self, record_id, revision, token, *, now=None, refund=False):
        import time

        from .card_retries import release

        with self._connection(write=True) as connection:
            release(connection, record_id, revision, token,
                    time.time() if now is None else now, refund=refund)

    def recover_card_attempts(self, *, now=None):
        import time

        from .card_retries import recover

        with self._connection(write=True) as connection:
            recover(connection, time.time() if now is None else now)

    def has_due_card_retries(self, *, now=None, allowed_source_ids=None, visibility=None):
        import time

        from .card_retries import has_due

        clause, allowed = _allowed_clause(allowed_source_ids)
        clause += policy_clause(visibility, alias="documents")
        with self._connection() as connection:
            return has_due(connection, time.time() if now is None else now, clause, allowed)

    def requeue_card_errors(self, allowed_source_ids=None, *, visibility=None):
        from .card_retries import requeue_errors

        clause, allowed = _allowed_clause(allowed_source_ids)
        clause += policy_clause(visibility, alias="documents")
        with self._connection(write=True) as connection:
            return requeue_errors(connection, clause, allowed)

    def retry_status(self, allowed_source_ids=None, *, visibility="local"):
        from .card_retries import counts

        clause, allowed = _allowed_clause(allowed_source_ids)
        clause += policy_clause(visibility, alias="documents")
        with self._connection() as connection:
            return counts(connection, clause, allowed)

    def save_card(
        self, record_id, revision, card, model="", prompt_version="1", processor_version=None,
        *, attempt_token=None, processing_scope=None
    ) -> bool:
        from .memory_validation import validate_card

        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id=? AND revision=?"
                + policy_clause(processing_scope, alias="documents"), (record_id, revision)
            ).fetchone()
            if row is None or (attempt_token is not None
                               and row["card_claim_token"] != attempt_token):
                return False
            clean = validate_card(card, row)
            connection.execute(
                "UPDATE documents SET card_json=?,card_status='ready',card_error=NULL,"
                "card_retry_at=NULL,card_claim_token=NULL,card_claim_until=NULL,"
                "model=?,prompt_version=? WHERE id=? AND revision=?",
                (
                    json.dumps(clean, ensure_ascii=False),
                    redact_text(str(model))[:200],
                    redact_text(str(processor_version or prompt_version))[:100],
                    record_id,
                    revision,
                ),
            )
            row = connection.execute("SELECT * FROM documents WHERE id=?", (record_id,)).fetchone()
            self._index(connection, row)
            from .memory_facts import project_card

            project_card(connection, row, clean)
            return True

    def record_card_error(self, record_id, revision, message, *, attempt_token=None,
                          now=None, processing_scope=None) -> None:
        import time

        from .card_retries import failure

        with self._connection(write=True) as connection:
            allowed = connection.execute(
                "SELECT 1 FROM documents WHERE id=? AND revision=?"
                + policy_clause(processing_scope, alias="documents"), (record_id, revision),
            ).fetchone()
            if allowed is None:
                return
            failure(connection, record_id, revision, redact_text(str(message))[:500],
                    attempt_token, time.time() if now is None else now)

    def delete_source(self, source_id) -> int:
        with self._connection(write=True) as connection:
            rows = connection.execute(
                "SELECT entry_id FROM documents WHERE source_id=?", (source_id,)
            ).fetchall()
            connection.execute("INSERT OR IGNORE INTO deleted_sources VALUES(?)", (source_id,))
            for row in rows:
                connection.execute("DELETE FROM memory_fts WHERE rowid=?", (row["entry_id"],))
            connection.execute("DELETE FROM documents WHERE source_id=?", (source_id,))
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='github_sync_progress'"
            ).fetchone():
                connection.execute("DELETE FROM github_sync_progress WHERE source_id=?",
                                   (source_id,))
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_sync_progress'"
            ).fetchone():
                connection.execute("DELETE FROM mail_sync_progress WHERE source_id=?",
                                   (source_id,))
            for table in ("apple_notes_sync_progress", "outlook_sync_progress"):
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone():
                    connection.execute(f"DELETE FROM {table} WHERE source_id=?", (source_id,))
            return len(rows)

    def deleted_source_ids(self) -> frozenset[str]:
        with self._connection() as connection:
            rows = connection.execute("SELECT source_id FROM deleted_sources").fetchall()
        return frozenset(row["source_id"] for row in rows)

    def status(self, *, allowed_source_ids=None, visibility="local") -> dict:
        clause, allowed = _allowed_clause(allowed_source_ids, "d.source_id")
        clause += policy_clause(visibility)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT d.source_id,d.card_status,COUNT(*) AS n,MAX(d.updated_at) AS t "
                "FROM documents d WHERE 1=1" + clause + " GROUP BY d.source_id,d.card_status",
                allowed,
            ).fetchall()
        by_source = {}
        totals = {"documents": 0, "ready": 0, "pending": 0, "errors": 0}
        for row in rows:
            key = "errors" if row["card_status"] == "error" else row["card_status"]
            current = by_source.get(
                row["source_id"], {"documents": 0, "ready": 0, "pending": 0, "errors": 0}
            )
            by_source = {
                **by_source,
                row["source_id"]: {
                    **current,
                    "documents": current["documents"] + row["n"],
                    key: current[key] + row["n"],
                },
            }
            totals = {
                **totals,
                "documents": totals["documents"] + row["n"],
                key: totals[key] + row["n"],
            }
        return {
            **totals,
            "by_source": by_source,
            "last_updated_at": max((row["t"] for row in rows), default=None),
        }

    def context_brief(self, allowed_source_ids=None, *, visibility="local") -> dict:
        return {
            "records": self.search("", limit=8, allowed_source_ids=allowed_source_ids,
                                   visibility=visibility),
            "message": "Сведения из локальных источников. Проверяйте даты и подтверждения.",
        }

    def facts(self, query="", limit=20, source_id=None, allowed_source_ids=None,
              visibility="local", allowed_provider=None, include_history=False):
        from .fact_queries import facts

        with self._connection() as connection:
            return facts(connection, query, limit, source_id, allowed_source_ids,
                         visibility, allowed_provider, include_history)

    def fact_history(self, fact_id, limit=50, allowed_source_ids=None, visibility="local",
                     before=None, allowed_provider=None):
        from .fact_queries import history

        with self._connection() as connection:
            return history(connection, fact_id, limit, allowed_source_ids, visibility,
                           before, allowed_provider)

    def project_facts(self, limit=100, allowed_source_ids=None, check=None):
        from .memory_facts import project_batch

        return project_batch(self, limit, allowed_source_ids, check)

    def fact_projection_pending(self, allowed_source_ids=None):
        from .memory_facts import pending

        with self._connection() as connection:
            return bool(pending(connection, allowed_source_ids, 1))
