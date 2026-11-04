import json
import sqlite3

import pytest

from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


def document(**changes):
    return {
        "source_id": "source-one",
        "source_kind": "folder",
        "source_object_id": "note:1",
        "title": "Ошибки бота",
        "text": "Исправлена ошибка бота. Решение: изменить таймаут.",
        "source_uri": "file:///synthetic/note.md#L1-L2",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-02T00:00:00+00:00",
        **changes,
    }


def card():
    return {
        "title": "Настройка бота",
        "summary": "Увеличить таймаут",
        "keywords": ["таймаут"],
        "questions": ["Как исправить ошибку бота?"],
        "atoms": [
            {
                "text": "Изменить таймаут",
                "kind": "decision",
                "evidence": "Решение: изменить таймаут.",
            }
        ],
    }


def test_idempotent_import_survives_restart_and_uses_restricted_wal(tmp_path, store):
    first = store.upsert_document(**document())
    second = store.upsert_document(**document())
    assert first["changed"] is True and first["revision"] == 1
    assert second == {"id": first["id"], "revision": 1, "changed": False}
    restarted = MemoryStore(tmp_path)
    record = restarted.get_record(first["id"])
    assert record["text"] == document()["text"]
    assert record["updated_at"] == document()["updated_at"]
    assert restarted.status()["documents"] == 1
    assert not ((tmp_path / "memory.sqlite3").stat().st_mode & 0o077)
    with sqlite3.connect(tmp_path / "memory.sqlite3") as connection:
        assert connection.execute("pragma journal_mode").fetchone()[0] == "wal"


def test_source_scoped_identity_and_russian_search(store):
    first = store.upsert_document(**document())
    other = store.upsert_document(**document(source_id="source-two", text="Заметки о поездке"))
    assert first["id"] != other["id"]
    hits = store.search("ошибка бота", source_id="source-one")
    assert hits[0]["id"] == first["id"]
    assert hits[0]["source_uri"].startswith("file:///synthetic/")
    assert hits[0]["snippet"]
    assert len(store.search("")) == 2


def test_changed_revision_hides_stale_card_and_indexes_fresh_text(store):
    first = store.upsert_document(**document())
    assert store.save_card(first["id"], 1, card(), model="synthetic-model") is True
    ready = store.get_record(first["id"])
    assert ready["card_status"] == "ready"
    assert ready["card"]["atoms"][0]["record_id"] == first["id"]
    updated = store.upsert_document(**document(text="Теперь используем очереди RabbitMQ"))
    assert updated["revision"] == 2
    current = store.get_record(first["id"])
    assert current["card"] is None and current["card_status"] == "pending"
    assert store.search("RabbitMQ")[0]["id"] == first["id"]
    assert store.search("таймаут") == []
    assert store.save_card(first["id"], 1, card()) is False


def test_card_evidence_must_match_sanitized_record(store):
    result = store.upsert_document(**document())
    invalid = {
        **card(),
        "atoms": [{"text": "Выдумка", "kind": "fact", "evidence": "Несуществующее"}],
    }
    with pytest.raises(AppError):
        store.save_card(result["id"], 1, invalid)
    assert store.pending()[0]["id"] == result["id"]


def test_pending_errors_are_explicit_retry_and_stale_errors_do_not_replace_current(store):
    result = store.upsert_document(**document())
    store.record_card_error(result["id"], 1, "Провайдер недоступен")
    assert store.pending() == []
    assert store.pending(retry_errors=True)[0]["card_status"] == "error"
    store.upsert_document(**document(text="Новое решение"))
    store.record_card_error(result["id"], 1, "Старая ошибка")
    assert store.get_record(result["id"])["card_status"] == "pending"


def test_deletion_tombstone_blocks_inflight_reimport_and_known_id(store, tmp_path):
    record = store.upsert_document(**document())
    assert store.delete_source("source-one") == 1
    assert store.search("") == []
    assert store.pending() == []
    assert store.context_brief()["records"] == []
    with pytest.raises(AppError):
        store.get_record(record["id"])
    with pytest.raises(AppError):
        MemoryStore(tmp_path).upsert_document(**document())


def test_plain_queries_cannot_inject_fts_or_sql(store):
    store.upsert_document(**document())
    for query in ['" OR *', "NOT (", "'; DROP TABLE documents; --", "{} []", ""]:
        assert isinstance(store.search(query), list)
    assert store.status()["documents"] == 1
    with pytest.raises(AppError):
        store.search("x" * 1000)


def test_secret_redaction_occurs_before_database_and_card_storage(store, tmp_path):
    secret = "sk-proj-" + "SYNTHETIC_SECRET_VALUE_123456789"
    result = store.upsert_document(**document(text=f"API_KEY={secret}\nНе секрет"))
    assert secret not in store.get_record(result["id"])["text"]
    clean = {**card(), "summary": f"API_KEY={secret}", "atoms": []}
    store.save_card(result["id"], 1, clean)
    assert secret not in json.dumps(store.get_record(result["id"]), ensure_ascii=False)
    for path in tmp_path.glob("memory.sqlite3*"):
        assert secret.encode() not in path.read_bytes()


def test_file_reconciliation_removes_obsolete_chunks_atomically(store):
    rows = [document(source_object_id=f"note.md::chunk:{index}") for index in range(3)]
    store.replace_file_documents("source-one", "note.md", rows)
    assert store.status()["documents"] == 3
    store.replace_file_documents("source-one", "note.md", rows[:1], complete=False)
    assert store.status()["documents"] == 3
    store.replace_file_documents("source-one", "note.md", rows[:1], complete=True)
    assert store.status()["documents"] == 1


def test_simpleclaw_and_card_metadata_secrets_are_redacted(store):
    secret = "sc_" + "SYNTHETIC1234567890" * 3
    result = store.upsert_document(**document(text="Use this key: " + secret))
    assert secret not in store.get_record(result["id"])["text"]
    key = "sk-" + "SYNTHETIC1234567890"
    with pytest.raises(AppError):
        store.save_card(
            result["id"],
            1,
            {
                **card(),
                "atoms": [
                    {"text": "Ключ", "kind": key, "evidence": "Use this key:"},
                ],
            },
        )
    assert key not in json.dumps(store.get_record(result["id"]))


def test_ready_cards_keep_soft_freshness_and_scope_before_limit(store):
    old = store.upsert_document(
        **document(source_object_id="old", updated_at="2020-01-01T00:00:00Z")
    )
    fresh = store.upsert_document(**document(source_id="second", updated_at="2026-11-04T00:00:00Z"))
    for item in (old, fresh):
        store.save_card(item["id"], 1, {**card(), "atoms": []})
    assert store.search("бота")[0]["id"] == fresh["id"]


def test_unchanged_content_refreshes_citation_without_reprocessing_or_new_freshness(store):
    result = store.upsert_document(**document())
    store.save_card(result["id"], 1, card())
    uri = "file:///synthetic/note.md#L10-L12"
    changed = store.upsert_document(**document(source_uri=uri, updated_at="2026-11-04T00:00:00Z"))
    assert changed["changed"] is False
    record = store.get_record(result["id"])
    assert record["source_uri"] == uri
    assert record["card"]["atoms"][0]["source_uri"] == uri
    assert record["updated_at"] == document()["updated_at"]


def test_persisted_deleted_source_ids_are_available_before_any_import(store, tmp_path):
    assert store.deleted_source_ids() == frozenset()
    store.delete_source("disconnected-source")
    assert MemoryStore(tmp_path).deleted_source_ids() == frozenset({"disconnected-source"})


def test_secret_filenames_keep_opaque_identity_without_disk_leaks_or_pruning_collisions(
    store, tmp_path
):
    first_key = "sc_" + "A" * 40 + ".md"
    second_key = "sc_" + "B" * 40 + ".md"
    first = document(source_object_id=first_key + "::chunk:000000")
    second = document(source_object_id=second_key + "::chunk:000000")
    first_id = store.replace_file_documents("source-one", first_key, [first])[0]["id"]
    second_id = store.replace_file_documents("source-one", second_key, [second])[0]["id"]
    assert first_id != second_id
    assert first_key not in store.get_record(first_id)["source_object_id"]
    store.replace_file_documents("source-one", first_key, [])
    assert store.status()["documents"] == 1
    assert store.get_record(second_id)["id"] == second_id
    for path in tmp_path.glob("memory.sqlite3*"):
        assert first_key.encode() not in path.read_bytes()
        assert second_key.encode() not in path.read_bytes()


def test_untrusted_timestamp_metadata_cannot_store_a_secret(store):
    secret = "sc_" + "C" * 40
    result = store.upsert_document(**document(created_at=secret, updated_at=secret))
    row = store.get_record(result["id"])
    assert secret not in row["created_at"]
    assert secret not in row["updated_at"]


def test_short_language_query_does_not_prefix_match_unrelated_claude_text(store):
    relevant = store.upsert_document(
        **document(
            source_id="codex-source",
            source_kind="codex",
            title="Выбор языка",
            text="Используем C++ для системной части проекта.",
        )
    )
    store.upsert_document(
        **document(
            source_id="claude-source",
            source_kind="claude",
            title="Настройка Claude",
            text="Claude Code помогает разбирать задачи и документацию.",
        )
    )
    assert [record["id"] for record in store.search("C++")] == [relevant["id"]]
