"""Synthetic Notes imports retain evidence and bounded progress in SQLite."""

import pytest

from brain_settings.apple_notes_import import import_apple_notes
from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore

SOURCE = {"id": "apple-notes-synthetic", "kind": "apple_notes", "notes_folder_ids": ["folder-1"]}


def note(identifier="note-1", text="Synthetic astronomy decision"):
    return {
        "id": identifier,
        "folder_id": "folder-1",
        "title": "Astronomy",
        "text": text,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-02T10:00:00Z",
    }


class Client:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def read_page(self, folders, cursor=None, limit=50):
        self.calls.append((folders, cursor, limit))
        return self.pages[len(self.calls) - 1]


def page(notes, next_cursor=None, **extra):
    return {"notes": notes, "next_cursor": next_cursor, "warnings": [], "skipped": 0, **extra}


def test_notes_import_is_searchable_idempotent_and_keeps_original_identifier(tmp_path):
    store = MemoryStore(tmp_path)
    client = Client([page([note()]), page([note()])])
    first = import_apple_notes(SOURCE, store, client)
    second = import_apple_notes(SOURCE, store, client)
    hit = store.search("astronomy")[0]
    assert first["imported"] == 1 and second["unchanged"] == 1
    assert hit["source_kind"] == "apple_notes" and hit["revision"] == 1
    assert hit["source_uri"] == "note-1"


def test_pagination_is_persistent_and_resets_after_cycle(tmp_path):
    store = MemoryStore(tmp_path)
    cursor = {"folder_index": 0, "offset": 50}
    first = import_apple_notes(SOURCE, store, Client([page([note()], cursor)]))
    client = Client([page([note("note-2")]), page([note()])])
    assert first["has_more"] and first["files_deferred"] == 1
    assert not import_apple_notes(SOURCE, store, client)["has_more"]
    import_apple_notes(SOURCE, store, client)
    assert client.calls[0][1] == cursor and client.calls[1][1] is None


def test_selection_change_restarts_cursor(tmp_path):
    store = MemoryStore(tmp_path)
    import_apple_notes(SOURCE, store, Client([page([], {"folder_index": 0, "offset": 50})]))
    new_source = {**SOURCE, "notes_folder_ids": ["folder-2"]}
    client = Client([page([])])
    import_apple_notes(new_source, store, client)
    assert client.calls[0][1] is None


def test_locked_skips_and_read_failure_preserve_existing_material(tmp_path):
    store = MemoryStore(tmp_path)
    import_apple_notes(SOURCE, store, Client([page([note()])]))
    skipped = import_apple_notes(
        SOURCE, store, Client([page([], skipped=1, warnings=["Закрытая заметка пропущена."])])
    )
    assert skipped["skipped"] == 1 and store.search("astronomy")

    class Failed:
        def read_page(self, *args, **kwargs):
            raise AppError("apple_notes_permission", "Нет доступа к заметкам.")

    with pytest.raises(AppError):
        import_apple_notes(SOURCE, store, Failed())
    assert store.search("astronomy")


def test_cancel_after_fetch_prevents_commit_and_checkpoint(tmp_path):
    store = MemoryStore(tmp_path)
    checks = 0

    def stop():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise AppError("sync_paused", "Пауза.")

    with pytest.raises(AppError):
        import_apple_notes(SOURCE, store, Client([page([note()])]), stop)
    assert not store.search("")


def test_deleted_source_never_fetches_notes(tmp_path):
    store = MemoryStore(tmp_path)
    store.delete_source(SOURCE["id"])
    client = Client([page([note()])])
    with pytest.raises(AppError):
        import_apple_notes(SOURCE, store, client)
    assert client.calls == []


def test_changed_note_replaces_stale_chunks_without_touching_other_notes(tmp_path):
    store = MemoryStore(tmp_path)
    import_apple_notes(SOURCE, store, Client([page([note(text="x" * 24_001), note("note-2")])]))
    assert len(store.search("", limit=20)) == 4
    import_apple_notes(SOURCE, store, Client([page([note(text="Updated astronomy decision")])]))
    assert len(store.search("", limit=20)) == 2


def test_body_is_redacted_before_chunks(tmp_path):
    store = MemoryStore(tmp_path)
    marker = "sk-" + "SYNTHETIC" * 8
    import_apple_notes(SOURCE, store, Client([page([note(text="Decision " + marker)])]))
    assert marker not in store.search("")[0]["text"]
