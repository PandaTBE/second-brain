"""Durable content-free UID cursors; deletion tombstones prevent late resurrection."""

import json

from .errors import AppError


class MailProgress:
    def __init__(self, store, source_id):
        self.store, self.source_id = store, source_id
        with store._connection(write=True) as connection:
            store._check_source(connection, source_id)
            connection.execute("""CREATE TABLE IF NOT EXISTS mail_sync_progress (
                source_id TEXT PRIMARY KEY, state TEXT NOT NULL)""")

    def load(self) -> dict:
        with self.store._connection() as connection:
            row = connection.execute("SELECT state FROM mail_sync_progress WHERE source_id=?",
                                     (self.source_id,)).fetchone()
        if row is None:
            return {}
        try:
            if len(row["state"]) > 4096:
                raise ValueError
            result = json.loads(row["state"])
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, TypeError) as exc:
            raise AppError("mail_progress",
                           "Не удалось прочитать прогресс импорта почты.", 503) from exc

    def save(self, state: dict) -> None:
        serialized = json.dumps(state, ensure_ascii=False)
        if len(serialized) > 4096:
            raise AppError("mail_progress",
                           "Прогресс импорта почты превышает допустимый размер.", 503)
        with self.store._connection(write=True) as connection:
            self.store._check_source(connection, self.source_id)
            connection.execute("""INSERT INTO mail_sync_progress VALUES(?,?)
                ON CONFLICT(source_id) DO UPDATE SET state=excluded.state""",
                               (self.source_id, serialized))
