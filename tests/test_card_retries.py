"""Paid retry budgets are durable and consumed before a provider can be called."""

import sqlite3

from test_memory_store import card, document

from brain_settings.memory_store import MemoryStore


def fail(store, saved, now, *, manual=False):
    token = store.claim_card_attempt(saved["id"], saved["revision"], now=now, manual=manual)
    assert token is not None
    store.record_card_error(saved["id"], saved["revision"], "Synthetic failure",
                            attempt_token=token, now=now)


def test_initial_plus_two_retries_stop_durably_and_manual_does_not_rearm(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    fail(store, saved, 1000)
    assert store.pending(automatic_retries=True, now=1059) == []
    assert len(store.pending(automatic_retries=True, now=1060)) == 1
    fail(store, saved, 1060)
    assert store.pending(automatic_retries=True, now=1359) == []
    fail(store, saved, 1360)
    restarted = MemoryStore(tmp_path)
    assert restarted.pending(automatic_retries=True, now=9999) == []
    assert restarted.get_record(saved["id"])["card_waiting_for_user"] is True
    assert restarted.retry_status()["cards_waiting_user"] == 1
    assert restarted.upsert_document(**document())["changed"] is False
    fail(restarted, saved, 10000, manual=True)
    assert restarted.get_record(saved["id"])["card_attempts"] == 4
    assert restarted.pending(automatic_retries=True, now=99999) == []
    changed = restarted.upsert_document(**document(text=document()["text"] + " Новое решение."))
    assert changed["revision"] == 2
    assert restarted.get_record(saved["id"])["card_attempts"] == 0


def test_success_and_compare_and_swap_claims_stop_retrying(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    fail(store, saved, 1000)
    token = store.claim_card_attempt(saved["id"], 1, now=1060)
    assert token
    assert store.claim_card_attempt(saved["id"], 1, now=1060) is None
    assert store.save_card(saved["id"], 1, card(), attempt_token=token)
    assert store.pending(automatic_retries=True, now=9999) == []
    assert store.claim_card_attempt(saved["id"], 1, now=9999, manual=True) is None
    assert store.claim_card_attempt(saved["id"], 0, now=9999) is None
    assert store.retry_status() == {"cards_retry_pending": 0, "cards_waiting_user": 0}


def test_crashed_claims_keep_budget_and_recover_only_in_writer_operation(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    for now in (1000, 1300, 1600):
        assert store.claim_card_attempt(saved["id"], 1, now=now)
    restarted = MemoryStore(tmp_path)
    assert restarted.get_record(saved["id"])["card_status"] == "pending"
    assert restarted.has_due_card_retries(now=1899) is False
    assert restarted.has_due_card_retries(now=1900) is True
    restarted.recover_card_attempts(now=1900)
    record = restarted.get_record(saved["id"])
    assert record["card_status"] == "error"
    assert record["card_waiting_for_user"]
    assert restarted.pending(automatic_retries=True, now=2000) == []
    assert "card_claim_token" not in record and "card_claim_until" not in record


def test_legacy_errors_receive_only_two_additional_attempts(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE documents SET card_status='error',card_attempts=0")
    record = MemoryStore(tmp_path).get_record(saved["id"])
    assert record["card_attempts"] == 1
    fail(store, saved, 1000)
    assert store.get_record(saved["id"])["card_attempts"] == 2
    fail(store, saved, 1300)
    assert store.pending(automatic_retries=True, now=9999) == []


def test_claims_honor_source_access_revision_and_due_boundary(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    assert store.claim_card_attempt(saved["id"], 1, now=0, allowed_source_ids=set()) is None
    fail(store, saved, 1000)
    assert store.claim_card_attempt(saved["id"], 1, now=1059) is None
    store.upsert_document(**document(text="Новая версия записи."))
    assert store.claim_card_attempt(saved["id"], 1, now=2000) is None
    store.delete_source("source-one")
    assert store.claim_card_attempt(saved["id"], 2, now=2000) is None


def test_old_schema_is_migrated_without_resetting_existing_error_budget(tmp_path):
    from brain_settings.memory_store import SCHEMA

    path = tmp_path / "memory.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO documents(id,source_id,source_kind,source_object_id,title,text,"
            "source_uri,content_hash,revision,created_at,updated_at,card_status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("legacy", "source-one", "folder", "note", "Legacy", "Stored synthetic text",
             "file:///synthetic/note", "hash", 1, "2026-01-01", "2026-01-02", "error"),
        )
    store = MemoryStore(tmp_path)
    record = store.get_record("legacy")
    assert record["card_attempts"] == 1 and record["text"] == "Stored synthetic text"
    assert record["card_waiting_for_user"] is False
    assert store.claim_card_attempt("legacy", 1, now=1000)
    assert MemoryStore(tmp_path).get_record("legacy")["card_attempts"] == 2


def test_old_claim_response_cannot_overwrite_new_reservation(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    old = store.claim_card_attempt(saved["id"], 1, now=0)
    new = store.claim_card_attempt(saved["id"], 1, now=300)
    assert new and old != new
    store.record_card_error(saved["id"], 1, "Old response", attempt_token=old, now=301)
    assert store.get_record(saved["id"])["card_status"] == "pending"
    assert not store.save_card(saved["id"], 1, card(), attempt_token=old)
    store.record_card_error(saved["id"], 1, "New failure", attempt_token=new, now=301)
    assert store.get_record(saved["id"])["card_attempts"] == 2


def test_stale_results_follow_the_same_sixty_then_three_hundred_second_backoff(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    token = store.claim_card_attempt(saved["id"], 1, now=1000)
    store.release_card_attempt(saved["id"], 1, token, now=1000)
    token = store.claim_card_attempt(saved["id"], 1, now=1060)
    store.release_card_attempt(saved["id"], 1, token, now=1060)
    assert store.has_due_card_retries(now=1120) is False
    assert store.pending(automatic_retries=True, now=1359) == []
    assert store.has_due_card_retries(now=1360) is True


def test_final_retry_in_flight_is_not_yet_waiting_for_user(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    fail(store, saved, 1000)
    fail(store, saved, 1060)
    token = store.claim_card_attempt(saved["id"], 1, now=1360)
    assert store.get_record(saved["id"])["card_waiting_for_user"] is False
    assert store.retry_status()["cards_waiting_user"] == 0
    store.record_card_error(saved["id"], 1, "Final failure", attempt_token=token, now=1360)
    assert store.get_record(saved["id"])["card_waiting_for_user"] is True


def test_unattempted_pending_queue_is_due_without_waiting_for_import(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    assert store.has_due_card_retries(now=1000, allowed_source_ids={"source-one"})
    token = store.claim_card_attempt(saved["id"], saved["revision"], now=1000)
    assert token is not None
    assert not store.has_due_card_retries(now=1000)
    store.release_card_attempt(saved["id"], saved["revision"], token, now=1000, refund=True)
    record = store.get_record(saved["id"])
    assert record["card_attempts"] == 0 and record["card_status"] == "pending"
    assert store.has_due_card_retries(now=1000)


def test_pending_queue_due_detection_preserves_safety_boundaries(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    assert not store.has_due_card_retries(now=1000, allowed_source_ids=set())
    assert not store.has_due_card_retries(now=1000, allowed_source_ids={"other-source"})
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE documents SET card_retry_at=1060 WHERE id=?", (saved["id"],))
    assert not store.has_due_card_retries(now=1059)
    assert store.has_due_card_retries(now=1060)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE documents SET card_attempts=3 WHERE id=?", (saved["id"],))
    assert not store.has_due_card_retries(now=1060)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE documents SET card_status='error' WHERE id=?", (saved["id"],))
    assert not store.has_due_card_retries(now=1060)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE documents SET card_status='ready',card_attempts=0 WHERE id=?",
                           (saved["id"],))
    assert not store.has_due_card_retries(now=1060)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE documents SET card_status='pending' WHERE id=?", (saved["id"],))
    assert store.has_due_card_retries(now=1060)
    store.delete_source("source-one")
    assert not store.has_due_card_retries(now=1060)
