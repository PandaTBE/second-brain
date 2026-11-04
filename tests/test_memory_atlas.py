"""Read-only graph snapshots stay bounded and obey the memory source boundary."""

import json
from datetime import datetime

from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient
from test_memory_store import card, document
from test_settings_api import assert_success

from brain_settings.app import create_app
from brain_settings.memory_store import MemoryStore


def seed(store, source="allowed", count=3):
    return [store.upsert_document(**document(
        source_id=source, source_object_id=f"note:{index}", title=f"Материал {index}",
        updated_at=f"2026-01-{index + 2:02}T00:00:00+00:00",
    )) for index in range(count)]


def test_empty_atlas_has_an_explicit_current_snapshot(tmp_path):
    snapshot = MemoryStore(tmp_path).atlas(allowed_source_ids=frozenset())
    assert snapshot["records"] == []
    assert snapshot["total_records"] == snapshot["returned_records"] == 0
    assert snapshot["truncated"] is False
    assert datetime.fromisoformat(snapshot["generated_at"]).tzinfo is not None


def test_atlas_has_bounded_masked_summaries_without_raw_details(tmp_path):
    store = MemoryStore(tmp_path)
    pending, ready, failed = seed(store)
    store.save_card(ready["id"], ready["revision"], {
        **card(), "title": "Название карточки", "summary": "Резюме карточки",
        "keywords": ["SQLite", " sqlite ", "ＦＴＳ５", "FTS5", "Поиск"],
    })
    store.record_card_error(failed["id"], failed["revision"], "PRIVATE MODEL ERROR")
    snapshot = store.atlas(allowed_source_ids={"allowed"})
    assert snapshot["total_records"] == snapshot["returned_records"] == 3
    assert not snapshot["truncated"]
    assert [record["id"] for record in snapshot["records"]] == [
        failed["id"], ready["id"], pending["id"],
    ]
    by_id = {record["id"]: record for record in snapshot["records"]}
    assert by_id[ready["id"]]["title"] == "Название карточки"
    assert by_id[ready["id"]]["keywords"] == ["SQLite", "FTS5", "Поиск"]
    assert by_id[ready["id"]]["summary"] == "Резюме карточки"
    assert by_id[pending["id"]]["summary"] is None
    assert by_id[pending["id"]]["snippet"]
    assert by_id[failed["id"]]["card_status"] == "error"
    for record in snapshot["records"]:
        assert not {"text", "source_uri", "card_error", "card", "atoms"} & record.keys()
        assert record["revision"] == 1
    assert "PRIVATE MODEL ERROR" not in json.dumps(snapshot)


def test_atlas_limits_are_explicit_and_fields_are_bounded(tmp_path, monkeypatch):
    from brain_settings import memory_atlas

    monkeypatch.setattr(memory_atlas, "MAX_ATLAS_RECORDS", 2)
    store = MemoryStore(tmp_path)
    records = seed(store, count=3)
    with store._connection(write=True) as connection:
        connection.execute("UPDATE documents SET title=?,card_json=? WHERE id=?", (
            "A" * 900, json.dumps({"summary": "S" * 5000,
                                   "keywords": [f"tag{index}" + "K" * 100 for index in range(30)]}),
            records[-1]["id"],
        ))
    snapshot = store.atlas(allowed_source_ids={"allowed"})
    assert snapshot["total_records"] == 3 and snapshot["returned_records"] == 2
    assert snapshot["truncated"] is True
    record = snapshot["records"][0]
    assert len(record["title"]) == 200 and len(record["summary"]) == 1200
    assert len(record["snippet"]) <= 480
    assert len(record["keywords"]) == 16
    assert all(len(keyword) <= 80 for keyword in record["keywords"])


def test_atlas_allowlist_and_deleted_sources_apply_before_count_and_limit(tmp_path):
    store = MemoryStore(tmp_path)
    seed(store, "allowed", 3)
    seed(store, "disabled", 3)
    seed(store, "deleted", 3)
    store.delete_source("deleted")
    snapshot = store.atlas(allowed_source_ids={"allowed", "deleted"})
    assert snapshot["total_records"] == 3
    assert {record["source_id"] for record in snapshot["records"]} == {"allowed"}


def test_atlas_uses_one_short_read_snapshot(tmp_path, monkeypatch):
    from brain_settings import memory_atlas

    store = MemoryStore(tmp_path)
    seed(store, count=2)
    original = memory_atlas._records

    def concurrent_write(connection, where, arguments):
        store.upsert_document(**document(source_id="allowed", source_object_id="new"))
        return original(connection, where, arguments)

    monkeypatch.setattr(memory_atlas, "_records", concurrent_write)
    snapshot = store.atlas(allowed_source_ids={"allowed"})
    assert snapshot["total_records"] == snapshot["returned_records"] == 2
    monkeypatch.setattr(memory_atlas, "_records", original)
    assert store.atlas(allowed_source_ids={"allowed"})["total_records"] == 3


def test_atlas_masks_persisted_strings_and_keeps_html_as_data(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document(
        source_id="allowed", title='<img src=x onerror="alert(1)"> api_key=synthetic-private',
        text='Safe text. api_key=synthetic-private',
    ))
    snapshot = store.atlas(allowed_source_ids={"allowed"})
    record = snapshot["records"][0]
    assert record["id"] == saved["id"]
    assert '<img src=x onerror="alert(1)">' in record["title"]
    assert "synthetic-private" not in json.dumps(snapshot)


def test_atlas_ignores_invalid_and_empty_keywords(tmp_path):
    store = MemoryStore(tmp_path)
    saved = seed(store, count=1)[0]
    with store._connection(write=True) as connection:
        connection.execute("UPDATE documents SET card_json=? WHERE id=?", (
            json.dumps({"keywords": [None, 8, {}, "  ", " SQLite ", "sqlite"]}), saved["id"],
        ))
    record = store.atlas(allowed_source_ids={"allowed"})["records"][0]
    assert record["keywords"] == ["SQLite"]


def test_atlas_redacts_secrets_introduced_by_unicode_normalization(tmp_path):
    store = MemoryStore(tmp_path)
    saved = seed(store, count=1)[0]
    with store._connection(write=True) as connection:
        connection.execute("UPDATE documents SET card_json=? WHERE id=?", (
            json.dumps({"keywords": ["ａｐｉ＿ｋｅｙ＝synthetic-placeholder", "SQLite"]}),
            saved["id"],
        ))
    record = store.atlas(allowed_source_ids={"allowed"})["records"][0]
    assert "synthetic-placeholder" not in json.dumps(record)
    assert "SQLite" in record["keywords"]


def test_service_rechecks_sources_and_counts_after_snapshot(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, vault=MemoryVault(), transport=FakeTransport())
    service = app.state.memory_service
    seed(service.store, "allowed", 3)
    seed(service.store, "disappeared", 2)
    monkeypatch.setattr(service, "_allowed", lambda: frozenset({"allowed", "disappeared"}))
    monkeypatch.setattr(service, "_sources", lambda: [
        {"id": "allowed", "name": "Safe api_key=synthetic-private", "kind": "folder"},
    ])
    result = service.atlas()
    assert result["total_records"] == result["returned_records"] == 3
    assert not result["truncated"]
    assert {record["source_id"] for record in result["records"]} == {"allowed"}
    assert "synthetic-private" not in result["sources"][0]["name"]
    assert not any(key.startswith("_") for key in result)


def test_atlas_api_excludes_unconfigured_sources_and_never_calls_llm(tmp_path):
    class NoLLM:
        def generate(self, _record):
            raise AssertionError("Atlas must not call a model")

    app = create_app(data_dir=tmp_path / "data", vault=MemoryVault(),
                     transport=FakeTransport(), memory_llm=NoLLM())
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = assert_success(client.get("/api/bootstrap"))["csrf_token"]
        folder = tmp_path / "notes"
        folder.mkdir()
        source = assert_success(client.post("/api/sources", json={
            "kind": "folder", "name": "Synthetic notes", "path": str(folder),
        }, headers={"Origin": ORIGIN, "X-Brain-CSRF": csrf}))["source"]
        seed(app.state.memory_service.store, source["id"])
        seed(app.state.memory_service.store, "unconfigured")
        response = client.get("/api/memory/atlas")
        snapshot = assert_success(response)
        assert response.headers["cache-control"] == "no-store"
        assert snapshot["total_records"] == snapshot["returned_records"] == 3
        assert snapshot["sources"] == [{"id": source["id"], "name": "Synthetic notes",
                                         "kind": "folder"}]
        assert not any(key.startswith("_") for key in snapshot)
        assert {record["source_id"] for record in snapshot["records"]} == {source["id"]}
        assert_success(client.delete("/api/sources/" + source["id"], headers={
            "Origin": ORIGIN, "X-Brain-CSRF": csrf, "Content-Type": "application/json",
        }))
        after = assert_success(client.get("/api/memory/atlas"))
        assert after["sources"] == [] and after["records"] == []
        assert after["total_records"] == 0
