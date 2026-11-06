"""Source-bound Outlook paging checkpoints; OAuth credentials never enter SQLite."""

import json

from .errors import AppError
from .outlook_client import message_path


class OutlookProgress:
    def __init__(self, store, source_id):
        self.store, self.source_id = store, source_id
        with store._connection(write=True) as connection:
            store._check_source(connection, source_id)
            connection.execute("""CREATE TABLE IF NOT EXISTS outlook_sync_progress (
                source_id TEXT PRIMARY KEY, state TEXT NOT NULL)""")

    @staticmethod
    def _validated(state):
        if (not isinstance(state, dict) or set(state) != {"version", "lookback", "next_link"}
                or type(state["version"]) is not int or state["version"] != 1
                or type(state["lookback"]) is not int or state["lookback"] not in (0, 30, 90, 365)):
            raise ValueError
        if state["next_link"] is not None:
            message_path(state["next_link"])
        return dict(state)

    def load(self):
        with self.store._connection() as connection:
            row = connection.execute("SELECT state FROM outlook_sync_progress WHERE source_id=?",
                                     (self.source_id,)).fetchone()
        if row is None:
            return {}
        try:
            if len(row["state"]) > 12_288:
                raise ValueError
            return self._validated(json.loads(row["state"]))
        except (ValueError, TypeError, AppError) as exc:
            raise AppError("outlook_progress", "Не удалось прочитать прогресс импорта Outlook.",
                           503) from exc

    def save(self, state):
        try:
            validated = self._validated(state)
            serialized = json.dumps(validated, ensure_ascii=False)
            if len(serialized) > 12_288:
                raise ValueError
        except (ValueError, TypeError, AppError) as exc:
            raise AppError("outlook_progress", "Прогресс импорта Outlook повреждён или слишком "
                           "велик.", 503) from exc
        with self.store._connection(write=True) as connection:
            self.store._check_source(connection, self.source_id)
            connection.execute("""INSERT INTO outlook_sync_progress VALUES(?,?)
                ON CONFLICT(source_id) DO UPDATE SET state=excluded.state""",
                               (self.source_id, serialized))
