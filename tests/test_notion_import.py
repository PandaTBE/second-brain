"""Integration checks for selected Notion pages and the real local SQLite index."""

from uuid import uuid4

import pytest

from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore
from brain_settings.notion_import import import_notion

PAGE = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER = "11111111-1111-4111-8111-111111111111"


class Client:
    def __init__(self, text="Project memory", failure=None):
        self.text, self.failure, self.calls = text, failure, []

    def retrieve_page(self, token, page_id, check_stop=lambda: None):
        check_stop()
        self.calls.append(page_id)
        if self.failure:
            raise self.failure
        return {"id": page_id, "title": "Notion project", "text": self.text,
                "url": "https://www.notion.so/" + page_id.replace("-", ""),
                "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"}


@pytest.fixture
def setup(tmp_path):
    return {"id": str(uuid4()), "kind": "notion", "notion_page_ids": [PAGE]}, MemoryStore(tmp_path)


def test_selected_pages_import_idempotence_revisions_and_mcp_search(setup):
    source, store = setup
    api = Client()
    first = import_notion(source, store, api, "synthetic")
    assert first["imported"] == 1
    assert api.calls == [PAGE]
    record = store.search("Project")[0]
    assert record["source_kind"] == "notion"
    assert record["source_object_id"] == PAGE + "::chunk:000000"
    assert import_notion(source, store, api, "synthetic")["unchanged"] == 1
    api.text = "Changed project memory"
    assert import_notion(source, store, api, "synthetic")["updated"] == 1
    assert store.search("Changed")[0]["revision"] == 2


def test_redaction_before_chunking_and_removed_chunks(setup):
    source, store = setup
    secret = "sk-" + "SYNTHETIC1234567890" * 3
    api = Client("x" * 11990 + "\nAPI_KEY=" + secret + "\n" + "z" * 13000)
    assert import_notion(source, store, api, "synthetic")["imported"] >= 2
    rows = [store.get_record(row["id"]) for row in store.browse(limit=50)["results"]]
    assert secret not in "".join(row["text"] for row in rows)
    api.text = "Short fresh content"
    import_notion(source, store, api, "synthetic")
    assert store.status()["documents"] == 1


def test_failed_or_partial_page_preserves_previous_complete_memory(setup):
    source, store = setup
    import_notion(source, store, Client(), "synthetic")
    result = import_notion(source, store, Client(failure=AppError("notion_incomplete", "Неполно")),
                           "synthetic")
    assert len(result["errors"]) == 1
    assert store.search("Project")[0]["revision"] == 1


def test_error_on_one_page_does_not_block_other_selected_page(setup):
    source, store = setup
    source = {**source, "notion_page_ids": [PAGE, OTHER]}
    api = Client()
    original = api.retrieve_page
    def retrieve(token, page_id, check_stop=lambda: None):
        if page_id == PAGE:
            raise AppError("notion_access", "Нет доступа")
        return original(token, page_id, check_stop)
    api.retrieve_page = retrieve
    result = import_notion(source, store, api, "synthetic")
    assert result["imported"] == 1 and len(result["errors"]) == 1


def test_stop_and_deleted_source_prevent_requests(setup):
    source, store = setup
    api = Client()
    def stopped():
        raise AppError("memory_cancelled", "Остановлено")
    with pytest.raises(AppError):
        import_notion(source, store, api, "synthetic", check_stop=stopped)
    assert api.calls == []
    store.delete_source(source["id"])
    with pytest.raises(AppError):
        import_notion(source, store, api, "synthetic")
    assert api.calls == []


@pytest.mark.parametrize("code", ["sync_paused", "memory_cancelled", "source_deleted"])
def test_pause_or_disconnect_during_remote_read_propagates(setup, code):
    source, store = setup
    import_notion(source, store, Client(), "synthetic")
    with pytest.raises(AppError) as caught:
        import_notion(source, store, Client(failure=AppError(code, "Остановлено")), "synthetic")
    assert caught.value.code == code
    assert store.search("Project")[0]["revision"] == 1


def test_invalid_scope_never_reads_workspace(setup):
    source, store = setup
    api = Client()
    for ids in [[], ["bad"], [PAGE] * 101]:
        with pytest.raises(AppError):
            import_notion({**source, "notion_page_ids": ids}, store, api, "synthetic")
    assert api.calls == []
