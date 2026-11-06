"""Bounded exact vector retrieval in the primary SQLite database."""

import heapq
import math
import struct
import time
from uuid import uuid4

from .errors import AppError
from .source_policy import policy_clause

MAX_DIMENSIONS = 4096
PREP_VERSION = 1
READ_BATCH = 128


def migrate(connection):
    connection.execute(
        "CREATE TABLE IF NOT EXISTS embedding_settings (singleton INTEGER PRIMARY KEY "
        "CHECK(singleton=1),enabled INTEGER NOT NULL DEFAULT 0,model_id TEXT,digest TEXT,"
        "dimension INTEGER,generation TEXT,prep_version INTEGER NOT NULL DEFAULT 1)"
    )
    connection.execute("INSERT OR IGNORE INTO embedding_settings(singleton) VALUES(1)")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS embedding_vectors (generation TEXT NOT NULL,"
        "record_id TEXT NOT NULL,revision INTEGER NOT NULL,content_hash TEXT NOT NULL,"
        "dimension INTEGER NOT NULL,vector BLOB NOT NULL,PRIMARY KEY(generation,record_id))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS embedding_jobs (generation TEXT NOT NULL,"
        "record_id TEXT NOT NULL,"
        "revision INTEGER NOT NULL,content_hash TEXT NOT NULL,status TEXT NOT NULL,"
        "attempts INTEGER NOT NULL DEFAULT 0,retry_at REAL NOT NULL DEFAULT 0,error TEXT,"
        "PRIMARY KEY(generation,record_id))"
    )


def normalized(vector, dimension=None):
    try:
        valid = (
            isinstance(vector, (list, tuple))
            and 1 <= len(vector) <= MAX_DIMENSIONS
            and (dimension is None or len(vector) == dimension)
            and all(
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
                for value in vector
            )
        )
        norm = math.hypot(*vector) if valid else 0
    except (OverflowError, ValueError, TypeError):
        valid, norm = False, 0
    if not valid or not math.isfinite(norm) or norm == 0:
        raise AppError("invalid_embedding", "Модель вернула неподдерживаемый вектор.", 502)
    return tuple(float(value / norm) for value in vector)


def _where(visibility="local", allowed_source_ids=None, source_id=None,
           project=None, date_from=None, date_to=None):
    from .retrieval_filters import SearchFilters

    filters = SearchFilters(project, date_from, date_to)
    sql = "1=1" + policy_clause(visibility, alias="d")
    args = ()
    if source_id is not None:
        sql += " AND d.source_id=?"
        args = (source_id,)
    if allowed_source_ids is not None:
        allowed = tuple(sorted(set(allowed_source_ids)))
        if not allowed:
            return "0=1", ()
        sql += " AND d.source_id IN (" + ",".join("?" for _ in allowed) + ")"
        args += allowed
    clause, values = filters.clause()
    return sql + clause, (*args, *values)


class VectorStore:
    def __init__(self, store):
        self.store = store

    def config(self):
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM embedding_settings WHERE singleton=1"
            ).fetchone()
        return dict(row) | {"enabled": bool(row["enabled"])}

    def coverage(self, *, visibility="local", allowed_source_ids=None, source_id=None,
                 project=None, date_from=None, date_to=None, _connection=None):
        if _connection is None:
            with self.store._connection() as connection:
                return self.coverage(visibility=visibility, allowed_source_ids=allowed_source_ids,
                                     source_id=source_id, project=project, date_from=date_from,
                                     date_to=date_to, _connection=connection)
        state = _connection.execute("SELECT * FROM embedding_settings WHERE singleton=1").fetchone()
        where, args = _where(visibility, allowed_source_ids, source_id, project, date_from, date_to)
        row = _connection.execute(
            "SELECT COUNT(*) AS eligible,COUNT(v.record_id) AS ready FROM documents d "
            "LEFT JOIN embedding_vectors v ON v.record_id=d.id AND v.revision=d.revision "
            "AND v.content_hash=d.content_hash AND v.generation=? AND v.dimension=? WHERE "
            + where, (state["generation"], state["dimension"], *args),
        ).fetchone()
        return {"eligible": row["eligible"], "ready": row["ready"],
                "partial": row["ready"] < row["eligible"]}

    def settings(self):
        state = self.config()
        counts = self.coverage(visibility="model_local")
        with self.store._connection() as connection:
            errors = connection.execute(
                "SELECT COUNT(*) FROM embedding_jobs j JOIN documents d ON d.id=j.record_id "
                "AND d.revision=j.revision AND d.content_hash=j.content_hash "
                "WHERE j.generation=? AND j.status='error'" + policy_clause("model_local"),
                (state["generation"],),
            ).fetchone()[0]
        return state | {
            "ready": counts["ready"],
            "pending": counts["eligible"] - counts["ready"] - errors,
            "errors": errors,
            "message": "Локальная embedding-модель выбирается отдельно от модели карточек."
            if not state["enabled"]
            else None,
        }

    def configure(self, model_id, digest, dimension, *, enabled=True):
        if (
            not isinstance(model_id, str)
            or not model_id
            or not isinstance(digest, str)
            or not digest
            or isinstance(dimension, bool)
            or not isinstance(dimension, int)
            or not 1 <= dimension <= MAX_DIMENSIONS
        ):
            raise AppError("invalid_embedding_settings", "Проверьте локальную embedding-модель.")
        with self.store._connection(write=True) as connection:
            old = connection.execute(
                "SELECT * FROM embedding_settings WHERE singleton=1"
            ).fetchone()
            identity = (model_id, digest, dimension, PREP_VERSION)
            previous = tuple(
                old[key] for key in ("model_id", "digest", "dimension", "prep_version")
            )
            generation = old["generation"] if identity == previous else str(uuid4())
            connection.execute(
                "UPDATE embedding_settings SET enabled=?,model_id=?,digest=?,dimension=?,"
                "generation=?,prep_version=? WHERE singleton=1",
                (int(enabled), model_id, digest, dimension, generation, PREP_VERSION),
            )
            if enabled and identity == previous:
                # Explicit model resave retries failures while preserving ready vectors.
                connection.execute(
                    "DELETE FROM embedding_jobs WHERE generation=? AND status='error'",
                    (generation,),
                )
        return self.settings()

    def disable(self):
        with self.store._connection(write=True) as connection:
            connection.execute("UPDATE embedding_settings SET enabled=0 WHERE singleton=1")
        return self.settings()

    def pending(self, limit=8, *, allowed_source_ids=None):
        state = self.config()
        if not state["enabled"]:
            return []
        where, args = _where("model_local", allowed_source_ids)
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT d.id FROM documents d LEFT JOIN embedding_vectors v ON v.record_id=d.id "
                "AND v.generation=? AND v.revision=d.revision AND v.content_hash=d.content_hash "
                "LEFT JOIN embedding_jobs j ON j.record_id=d.id AND j.generation=? "
                "AND j.revision=d.revision AND j.content_hash=d.content_hash WHERE "
                + where
                + " AND v.record_id IS NULL AND (j.record_id IS NULL OR j.status='ready' OR "
                "(j.status='error' AND j.attempts<3 AND j.retry_at<=?)) ORDER BY d.id LIMIT ?",
                (state["generation"], state["generation"], *args, time.time(), limit),
            ).fetchall()
        records = []
        for row in rows:
            try:
                records.append(
                    self.store.get_record(
                        row["id"], allowed_source_ids=allowed_source_ids, visibility="model_local"
                    )
                )
            except AppError as error:
                if error.code != "record_not_found":
                    raise
        return records

    def save(self, record, generation, vector):
        state = self.config()
        if not state["enabled"] or state["generation"] != generation:
            return False
        values = normalized(vector, state["dimension"])
        with self.store._connection(write=True) as connection:
            active = connection.execute(
                "SELECT * FROM embedding_settings WHERE singleton=1 AND enabled=1 AND generation=?",
                (generation,),
            ).fetchone()
            row = connection.execute(
                "SELECT d.id FROM documents d WHERE d.id=? AND d.revision=? AND d.content_hash=?"
                + policy_clause("model_local"),
                (record["id"], record["revision"], record["content_hash"]),
            ).fetchone()
            if active is None or row is None or active["dimension"] != len(values):
                return False
            connection.execute(
                "INSERT OR REPLACE INTO embedding_vectors VALUES(?,?,?,?,?,?)",
                (
                    generation,
                    record["id"],
                    record["revision"],
                    record["content_hash"],
                    len(values),
                    struct.pack(f"<{len(values)}f", *values),
                ),
            )
            connection.execute(
                "INSERT OR REPLACE INTO embedding_jobs VALUES(?,?,?,?, 'ready',0,0,NULL)",
                (generation, record["id"], record["revision"], record["content_hash"]),
            )
        return True

    def error(self, record, generation, code):
        with self.store._connection(write=True) as connection:
            row = connection.execute(
                "SELECT d.id FROM documents d WHERE d.id=? AND d.revision=? AND d.content_hash=?"
                + policy_clause("model_local"),
                (record["id"], record["revision"], record["content_hash"]),
            ).fetchone()
            active = connection.execute(
                "SELECT 1 FROM embedding_settings WHERE singleton=1 AND enabled=1 AND generation=?",
                (generation,),
            ).fetchone()
            if row is None or active is None:
                return
            connection.execute(
                "INSERT INTO embedding_jobs VALUES(?,?,?,?,'error',1,?,?) ON CONFLICT "
                "(generation,record_id) DO UPDATE SET revision=excluded.revision,"
                "content_hash=excluded.content_hash,status='error',"
                "attempts=CASE WHEN embedding_jobs.revision=excluded.revision THEN "
                "embedding_jobs.attempts+1 ELSE 1 END,retry_at=excluded.retry_at,"
                "error=excluded.error",
                (
                    generation,
                    record["id"],
                    record["revision"],
                    record["content_hash"],
                    time.time() + 60,
                    code[:64],
                ),
            )

    def purge(self):
        with self.store._connection(write=True) as connection:
            for table in ("embedding_vectors", "embedding_jobs"):
                connection.execute(
                    f"DELETE FROM {table} WHERE record_id NOT IN (SELECT d.id FROM documents d "
                    "WHERE 1=1" + policy_clause(None) + ") OR generation NOT IN "
                    "(SELECT generation FROM embedding_settings WHERE generation IS NOT NULL)"
                )

    def search(
        self,
        vector,
        limit=20,
        *,
        visibility="local",
        allowed_source_ids=None,
        source_id=None,
        generation=None,
        project=None,
        date_from=None,
        date_to=None,
    ):
        state = self.config()
        if not state["enabled"] or (generation is not None and generation != state["generation"]):
            return []
        values = normalized(vector, state["dimension"])
        where, args = _where(visibility, allowed_source_ids, source_id, project, date_from, date_to)
        best = []
        with self.store._connection() as connection:
            cursor = connection.execute(
                "SELECT d.id,d.revision,d.content_hash,v.vector FROM embedding_vectors v "
                "JOIN documents d "
                "ON d.id=v.record_id AND d.revision=v.revision AND d.content_hash=v.content_hash "
                "WHERE v.generation=? AND v.dimension=? AND " + where,
                (state["generation"], state["dimension"], *args),
            )
            while rows := cursor.fetchmany(READ_BATCH):
                for row in rows:
                    if len(row["vector"]) != len(values) * 4:
                        continue
                    stored = struct.unpack(f"<{len(values)}f", row["vector"])
                    score = sum(a * b for a, b in zip(values, stored, strict=True))
                    if not math.isfinite(score) or score <= 0:
                        continue
                    item = (score, row["id"], row["revision"], row["content_hash"])
                    if len(best) < limit:
                        heapq.heappush(best, item)
                    elif item > best[0]:
                        heapq.heapreplace(best, item)
        hits = []
        for score, identifier, revision, digest in sorted(best, reverse=True):
            try:
                record = self.store.get_record(
                    identifier, allowed_source_ids=allowed_source_ids, visibility=visibility
                )
                from .retrieval_filters import SearchFilters

                if (record["revision"] == revision and record["content_hash"] == digest
                        and SearchFilters(project, date_from, date_to).matches(record)):
                    hits.append(record | {"score": score, "snippet": record["text"][:480]})
            except AppError as error:
                if error.code != "record_not_found":
                    raise
        if self.config() != state:
            raise AppError("embedding_stale", "Настройки индекса изменились во время поиска.", 409)
        return hits
