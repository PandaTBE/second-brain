import json
import sqlite3
import time

import pytest
from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient

from brain_settings.app import create_app
from brain_settings.errors import AppError
from brain_settings.memory_service import MemoryService
from brain_settings.memory_store import MemoryStore
from brain_settings.source_storage import Source, SourcesStore


def legacy_ready(store, source="synthetic", object_id="one", *, quote="Use SQLite."):
    saved = store.upsert_document(
        source_id=source, source_kind="folder", source_object_id=object_id,
        title="Existing card", text=quote, source_uri=f"synthetic:{source}/{object_id}",
        created_at="2001-01-01T00:00:00+00:00", updated_at="2001-01-02T00:00:00+00:00",
    )
    value = {
        "title": "Existing card", "summary": "Preserve existing card search terms.",
        "keywords": ["sqlite", "existing-card-term"], "questions": [],
        "atoms": [{"kind": "decision", "text": quote, "evidence": quote}],
    }
    with store._connection(write=True) as connection:
        connection.execute(
            "UPDATE documents SET card_status='ready',card_json=?,model=?,prompt_version=? "
            "WHERE id=?",
            (json.dumps(value), "synthetic-existing-model", "2", saved["id"]),
        )
    return saved, value


def test_legacy_ready_cards_project_in_bounded_resumable_nonpaid_batches(tmp_path):
    store = MemoryStore(tmp_path)
    legacy_ready(store, object_id="one")
    legacy_ready(store, object_id="two", quote="Use Redis.")
    first = store.project_facts(limit=1)
    assert first == {"processed": 1, "projected": 1, "skipped": 0, "remaining": 1}
    assert len(store.facts()["facts"]) == 1
    second = MemoryStore(tmp_path).project_facts(limit=1)
    assert second == {"processed": 1, "projected": 1, "skipped": 0, "remaining": 0}
    assert len(store.facts()["facts"]) == 2
    assert store.project_facts(limit=1)["processed"] == 0


def test_legacy_projection_uses_seen_revision_card_hash_without_changing_card_or_fts(tmp_path):
    store = MemoryStore(tmp_path)
    saved, original = legacy_ready(store)
    before = store.get_record(saved["id"])
    store.project_facts()
    entity = store.facts()["facts"][0]
    history = store.fact_history(entity["id"])
    assert store.project_facts()["processed"] == 0
    after = store.get_record(saved["id"])
    assert after["card"] == before["card"]
    assert after["model"] == "synthetic-existing-model"
    assert after["prompt_version"] == "2"
    assert store.search("existing-card-term")
    changed = {**original, "keywords": ["changed-existing-term"]}
    with store._connection(write=True) as connection:
        connection.execute("UPDATE documents SET card_json=? WHERE id=?",
                           (json.dumps(changed), saved["id"]))
    assert store.project_facts()["processed"] == 1
    assert len(store.fact_history(entity["id"])["events"]) == len(history["events"])


def test_fact_and_history_reads_never_project_legacy_cards_or_write_database(tmp_path):
    store = MemoryStore(tmp_path)
    legacy_ready(store)
    with sqlite3.connect(store.path) as observer:
        version = observer.execute("PRAGMA data_version").fetchone()[0]
        assert store.facts()["facts"] == []
        with pytest.raises(AppError):
            store.fact_history("not-projected")
        assert observer.execute("PRAGMA data_version").fetchone()[0] == version
    assert store.project_facts()["processed"] == 1


def test_projection_observation_time_never_invents_legacy_decision_history(tmp_path):
    store = MemoryStore(tmp_path)
    legacy_ready(store)
    store.project_facts()
    response = store.facts()
    history = store.fact_history(response["facts"][0]["id"])
    assert len(history["events"]) == 1
    assert history["events"][0]["observed_at"] >= response["history_since"]
    assert not history["events"][0]["observed_at"].startswith("2001-")


def test_projection_skips_invalid_cached_quotes_without_retrying_same_card_forever(tmp_path):
    store = MemoryStore(tmp_path)
    saved, original = legacy_ready(store)
    invalid = {**original, "atoms": [
        {"kind": "decision", "text": "Invented claim.", "evidence": "Absent quote."}
    ]}
    with store._connection(write=True) as connection:
        connection.execute("UPDATE documents SET card_json=? WHERE id=?",
                           (json.dumps(invalid), saved["id"]))
    stats = store.project_facts()
    assert stats == {"processed": 1, "projected": 0, "skipped": 1, "remaining": 0}
    assert store.facts()["facts"] == []
    assert store.project_facts()["processed"] == 0


def test_projection_respects_exclusions_allowed_ids_and_needs_no_model_permission(tmp_path):
    store = MemoryStore(tmp_path)
    legacy_ready(store, source="allowed")
    legacy_ready(store, source="excluded")
    store.set_source_policy("allowed", {
        "local_model_enabled": False, "external_model_enabled": False,
    })
    store.set_source_policy("excluded", {"excluded_paths": ["one"]})
    assert store.project_facts(allowed_source_ids=[])["processed"] == 0
    stats = store.project_facts(allowed_source_ids=["allowed", "excluded"])
    assert stats["processed"] == 1
    assert {proof["source_id"] for proof in store.facts()["facts"][0]["evidence"]} == {"allowed"}


@pytest.mark.parametrize("limit", [0, True, 501])
def test_projection_batch_limits_are_strict(tmp_path, limit):
    with pytest.raises(AppError):
        MemoryStore(tmp_path).project_facts(limit=limit)


def test_explicit_projection_http_job_is_csrf_protected_and_never_calls_generator(tmp_path,
                                                                                monkeypatch):
    store = MemoryStore(tmp_path)
    source_id = "123e4567-e89b-42d3-a456-426614174000"
    legacy_ready(store, source=source_id)
    SourcesStore(tmp_path).save((Source(
        id=source_id, name="Synthetic", kind="folder", path=str(tmp_path),
    ),))
    monkeypatch.setattr(MemoryService, "start_background", lambda self: None)

    class NoGenerator:
        def generate(self, *args, **kwargs):
            raise AssertionError("Projecting cached atoms must not call a paid model")

    app = create_app(data_dir=tmp_path, vault=MemoryVault(), transport=FakeTransport(),
                     memory_llm=NoGenerator())
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        headers = {"Origin": ORIGIN, "X-Brain-CSRF": csrf}
        paused = client.post("/api/memory/sync", json={"paused": True}, headers=headers)
        assert paused.status_code == 200
        assert client.get("/api/memory/facts").json()["data"]["facts"] == []
        assert client.post("/api/memory/facts/project", json={"limit": 1}).status_code == 403
        response = client.post("/api/memory/facts/project", json={"limit": 1}, headers=headers)
        assert response.status_code == 200
        job_id = response.json()["data"]["job_id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = client.get(f"/api/memory/jobs/{job_id}").json()["data"]["job"]
            if job["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        assert job["status"] == "completed"
        assert job["kind"] == "facts"
        assert job["stats"]["projected"] == 1
        response = client.get("/api/memory/facts", params={"q": "SQLite"})
        assert response.status_code == 200
        entity = response.json()["data"]["facts"][0]
        history = client.get(f"/api/memory/facts/{entity['id']}/history")
        assert history.status_code == 200
        assert history.json()["data"]["events"][0]["quote"] == "Use SQLite."
