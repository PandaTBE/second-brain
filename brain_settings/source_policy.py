"""Independent source permissions and SQLite policy predicates.

Policies live in the memory database so existing readers and late writers observe
the same committed decision. Missing rows preserve the legacy permissions.
"""

import re

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .errors import AppError

SCOPES = {
    "local": "local_search_enabled",
    "mcp": "mcp_enabled",
    "model_local": "local_model_enabled",
    "model_external": "external_model_enabled",
    "import": "import_enabled",
}
FLAGS = tuple(SCOPES.values())
LOCAL_KINDS = ("folder", "codex", "claude", "qwen")


class SourcePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    import_enabled: StrictBool = True
    local_search_enabled: StrictBool = True
    mcp_enabled: StrictBool = True
    local_model_enabled: StrictBool = True
    external_model_enabled: StrictBool = True
    excluded_paths: tuple[str, ...] = Field(default=(), max_length=100)
    excluded_notes_folder_ids: tuple[str, ...] = Field(default=(), max_length=50)

    @field_validator("excluded_paths")
    @classmethod
    def paths(cls, values):
        normalized = tuple(value.rstrip("/") for value in values)
        for value in normalized:
            if (
                not value
                or len(value) > 2048
                or value.startswith("/")
                or any(part in ("", ".", "..") for part in value.split("/"))
                or re.search(r"[\\*?\[\]\x00-\x1f\x7f]", value)
            ):
                raise ValueError("Use bounded relative file or directory paths without patterns")
        return tuple(dict.fromkeys(normalized))

    @field_validator("excluded_notes_folder_ids")
    @classmethod
    def folders(cls, values):
        if any(
            not value or len(value) > 2048 or re.search(r"[\x00-\x1f\x7f]", value)
            for value in values
        ):
            raise ValueError("Invalid Notes folder identifier")
        return tuple(dict.fromkeys(values))


def scope_column(visibility):
    if visibility is None:
        return None
    if visibility not in SCOPES:
        raise AppError("invalid_visibility", "Неизвестная область доступа к памяти.")
    return SCOPES[visibility]


def policy_clause(visibility="local", alias="d"):
    """Return a validated correlated SQL AND predicate with no bound arguments."""
    if alias not in ("d", "documents"):
        raise AppError("invalid_visibility", "Неизвестная область доступа к памяти.")
    column = scope_column(visibility)
    prefix = alias + "."
    clause = f" AND {prefix}source_id NOT IN (SELECT source_id FROM deleted_sources)"
    if column:
        clause += (
            " AND NOT EXISTS (SELECT 1 FROM source_policies p WHERE "
            f"p.source_id={prefix}source_id AND p.{column}=0)"
        )
    return clause + (
        " AND NOT EXISTS (SELECT 1 FROM source_exclusions x WHERE "
        f"x.source_id={prefix}source_id AND ("
        f"(x.kind='path' AND {prefix}source_kind IN ('folder','codex','claude','qwen') AND ("
        f"{prefix}source_object_id=x.value OR "
        f"substr({prefix}source_object_id,1,length(x.value)+1)=x.value||'/' OR "
        f"substr({prefix}source_object_id,1,length(x.value)+8)=x.value||'::chunk:' OR "
        f"instr({prefix}source_object_id,'::file:')>0)) OR "
        f"(x.kind='notes' AND {prefix}source_kind='apple_notes' AND ("
        f"{prefix}source_folder_id='' OR {prefix}source_folder_id=x.value))))"
    )


def schema_lock(directory):
    """Bounded interprocess lock for journal initialization and additive migrations."""
    from contextlib import contextmanager

    @contextmanager
    def locked():
        import fcntl
        import os
        import time

        descriptor = os.open(
            directory / ".memory-schema.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise AppError(
                            "memory_unavailable", "Миграция памяти занята другим процессом.", 503
                        ) from None
                    time.sleep(0.02)
            yield
        finally:
            os.close(descriptor)

    return locked()


def migrate(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
        if "source_folder_id" not in columns:
            connection.execute(
                "ALTER TABLE documents ADD COLUMN source_folder_id TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS source_policies ("
            "source_id TEXT PRIMARY KEY, "
            + ",".join(
                f"{name} INTEGER NOT NULL DEFAULT 1 CHECK({name} IN (0,1))" for name in FLAGS
            )
            + ")"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS source_exclusions ("
            "source_id TEXT NOT NULL,"
            "kind TEXT NOT NULL CHECK(kind IN ('path','notes')),"
            "value TEXT NOT NULL,position INTEGER NOT NULL,"
            "PRIMARY KEY(source_id,kind,value))"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def load_policy(connection, source_id):
    row = connection.execute(
        "SELECT * FROM source_policies WHERE source_id=?", (source_id,)
    ).fetchone()
    values = {name: bool(row[name]) for name in FLAGS} if row else {}
    exclusions = connection.execute(
        "SELECT kind,value FROM source_exclusions WHERE source_id=? ORDER BY position", (source_id,)
    ).fetchall()
    return SourcePolicy(
        **values,
        excluded_paths=tuple(item["value"] for item in exclusions if item["kind"] == "path"),
        excluded_notes_folder_ids=tuple(
            item["value"] for item in exclusions if item["kind"] == "notes"
        ),
    )


def save_policy(connection, source_id, policy):
    policy = SourcePolicy.model_validate(policy)
    names = ",".join(FLAGS)
    updates = ",".join(f"{name}=excluded.{name}" for name in FLAGS)
    connection.execute(
        f"INSERT INTO source_policies(source_id,{names}) VALUES(?,?,?,?,?,?) "
        f"ON CONFLICT(source_id) DO UPDATE SET {updates}",
        (source_id, *(int(getattr(policy, name)) for name in FLAGS)),
    )
    connection.execute("DELETE FROM source_exclusions WHERE source_id=?", (source_id,))
    groups = (("path", policy.excluded_paths), ("notes", policy.excluded_notes_folder_ids))
    for kind, values in groups:
        connection.executemany(
            "INSERT INTO source_exclusions(source_id,kind,value,position) VALUES(?,?,?,?)",
            ((source_id, kind, value, index) for index, value in enumerate(values)),
        )
    # Derivatives may not exist yet on a legacy installation.
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "embedding_vectors" in tables:
        connection.execute(
            "DELETE FROM embedding_vectors WHERE record_id NOT IN "
            "(SELECT d.id FROM documents d WHERE 1=1" + policy_clause(None) + ")"
        )
    return policy


def record_allowed(
    connection, source_id, source_kind, source_object_id, source_folder_id="", visibility="import"
):
    return (
        connection.execute(
            "SELECT 1 FROM (SELECT ? AS source_id,? AS source_kind,? AS source_object_id,"
            "? AS source_folder_id) d WHERE 1=1" + policy_clause(visibility),
            (source_id, source_kind, source_object_id, source_folder_id),
        ).fetchone()
        is not None
    )


def excluded_record(policy, record):
    kind, key = record["source_kind"], record["source_object_id"]
    if kind in LOCAL_KINDS and policy.excluded_paths:
        if "::file:" in key:
            return True
        if any(
            key == path or key.startswith(path + "/") or key.startswith(path + "::chunk:")
            for path in policy.excluded_paths
        ):
            return True
    return (
        kind == "apple_notes"
        and bool(policy.excluded_notes_folder_ids)
        and (
            not record["source_folder_id"]
            or record["source_folder_id"] in policy.excluded_notes_folder_ids
        )
    )


def preview_policy(connection, source_id, policy):
    policy = SourcePolicy.model_validate(policy)
    count, samples = 0, []
    cursor = connection.execute(
        "SELECT id,title,source_uri,source_kind,source_object_id,source_folder_id "
        "FROM documents WHERE source_id=? ORDER BY id",
        (source_id,),
    )
    while batch := cursor.fetchmany(128):
        for row in batch:
            if (
                excluded_record(policy, row)
                or not policy.local_search_enabled
                or not policy.mcp_enabled
            ):
                count += 1
                if len(samples) < 20:
                    samples = [
                        *samples,
                        {
                            "id": row["id"][:128],
                            "title": row["title"][:256],
                            "source_uri": row["source_uri"][:2048],
                        },
                    ]
    return {"affected_documents": count, "samples": samples, "sample_limit": 20}
