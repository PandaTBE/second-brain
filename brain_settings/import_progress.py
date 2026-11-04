"""Durable, content-free per-file checkpoints for bounded archive passes."""

import hashlib
import json

PARSER_VERSION = "bounded-stream-v1"


def file_identity(relative) -> str:
    return hashlib.sha256(str(relative).encode("utf-8")).hexdigest()


class ImportProgress:
    def __init__(self, store, source_id: str):
        self.store = store
        self.source_id = source_id
        with store._connection(write=True) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS import_checkpoints (
              source_id TEXT NOT NULL, file_key TEXT NOT NULL, signature TEXT NOT NULL,
              parser_version TEXT NOT NULL, document_count INTEGER NOT NULL,
              warnings TEXT NOT NULL, state TEXT NOT NULL,
              PRIMARY KEY(source_id,file_key))""")

    def load(self) -> dict:
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM import_checkpoints WHERE source_id=? AND parser_version=?",
                (self.source_id, PARSER_VERSION),
            ).fetchall()
        return {
            row["file_key"]: {
                "signature": tuple(json.loads(row["signature"])),
                "document_count": row["document_count"],
                "warnings": tuple(json.loads(row["warnings"])),
                "state": row["state"],
            }
            for row in rows
        }

    def save(self, event, document_count: int, warnings=(), state="done") -> None:
        with self.store._connection(write=True) as connection:
            if connection.execute(
                "SELECT 1 FROM deleted_sources WHERE source_id=?", (self.source_id,)
            ).fetchone():
                return
            connection.execute(
                """INSERT INTO import_checkpoints
              (source_id,file_key,signature,parser_version,document_count,warnings,state)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id,file_key) DO UPDATE SET
              signature=excluded.signature,parser_version=excluded.parser_version,
              document_count=excluded.document_count,warnings=excluded.warnings,state=excluded.state
              """,
                (
                    self.source_id,
                    file_identity(event.relative),
                    json.dumps(event.signature),
                    PARSER_VERSION,
                    document_count,
                    json.dumps(tuple(warnings)),
                    state,
                ),
            )
