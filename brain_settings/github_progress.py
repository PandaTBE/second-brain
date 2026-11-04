"""Content-free, source-scoped GitHub cursors survive bounded synchronization passes."""

import json

from .errors import AppError


class GitHubProgress:
    def __init__(self, store, source_id):
        self.store, self.source_id = store, source_id
        with store._connection(write=True) as connection:
            store._check_source(connection, source_id)
            connection.execute("""CREATE TABLE IF NOT EXISTS github_sync_progress (
                source_id TEXT NOT NULL, repository TEXT NOT NULL, scope TEXT NOT NULL,
                state TEXT NOT NULL, PRIMARY KEY(source_id,repository,scope))""")

    def get(self, repository, scope):
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT state FROM github_sync_progress "
                "WHERE source_id=? AND repository=? AND scope=?",
                (self.source_id, repository, scope),
            ).fetchone()
        if row is None:
            return {}
        try:
            if len(row["state"]) > 16384:
                raise ValueError
            state = json.loads(row["state"])
            if not isinstance(state, dict):
                raise ValueError
            return state
        except (ValueError, TypeError) as exc:
            raise AppError("github_progress",
                           "Не удалось прочитать состояние синхронизации GitHub.", 503) from exc

    def save(self, repository, scope, state):
        serialized = json.dumps(state, ensure_ascii=False)
        if len(serialized) > 16384:
            raise AppError("github_progress",
                           "Состояние синхронизации GitHub слишком большое.", 503)
        with self.store._connection(write=True) as connection:
            self.store._check_source(connection, self.source_id)
            connection.execute("""INSERT INTO github_sync_progress VALUES(?,?,?,?)
                ON CONFLICT(source_id,repository,scope) DO UPDATE SET state=excluded.state""",
                               (self.source_id, repository, scope, serialized))
