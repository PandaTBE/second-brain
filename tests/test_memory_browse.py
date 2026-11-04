"""Cursor paging and status filters for the native memory list."""

import base64
import json

import pytest
from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient
from test_memory_store import card, document
from test_settings_api import assert_success

from brain_settings.app import create_app
from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore


def seed(store, count=121, source_id="allowed"):
    records = []
    for index in range(count):
        saved = store.upsert_document(**document(
            source_id=source_id, source_object_id=f"note:{index}",
            title=f"Материал {index}",
        ))
        if index % 3 == 0:
            store.save_card(saved["id"], saved["revision"], card())
        elif index % 3 == 1:
            store.record_card_error(saved["id"], saved["revision"], "Synthetic failure")
        records.append(saved["id"])
    return records


def all_pages(store, **options):
    pages, cursor = [], None
    for _ in range(100):
        page = store.browse(cursor=cursor, **options)
        pages.append(page)
        assert page["has_more"] == (page["next_cursor"] is not None)
        if not page["has_more"]:
            return pages
        assert page["next_cursor"] != cursor
        cursor = page["next_cursor"]
    pytest.fail("Pagination did not terminate")


@pytest.mark.parametrize("query", ["", "таймаут"])
def test_browse_pages_all_records_beyond_100_with_deterministic_ties(tmp_path, query):
    store = MemoryStore(tmp_path)
    expected = seed(store)
    pages = all_pages(store, query=query, limit=17, allowed_source_ids={"allowed"})
    records = [record for page in pages for record in page["results"]]
    assert len(records) == 121
    assert len({record["id"] for record in records}) == 121
    assert {record["id"] for record in records} == set(expected)
    assert all(len(page["results"]) <= 17 for page in pages)
    assert len(pages[-1]["results"]) == 2
    assert all("text" not in record for record in records)
    assert all(len(record["snippet"]) <= 480 for record in records)
    if not query:
        assert [record["id"] for record in records] == sorted(expected)


@pytest.mark.parametrize("status", ["ready", "pending", "error"])
def test_filters_apply_before_paging_and_search(tmp_path, status):
    store = MemoryStore(tmp_path)
    seed(store)
    pages = all_pages(store, query="бот", card_status=status, limit=7)
    results = [record for page in pages for record in page["results"]]
    assert len(results) == (41 if status == "ready" else 40)
    assert {record["card_status"] for record in results} == {status}


def test_browse_empty_final_page_and_fulltext_no_match(tmp_path):
    store = MemoryStore(tmp_path)
    assert store.browse() == {"results": [], "next_cursor": None, "has_more": False}
    seed(store, count=3)
    assert store.browse(query="unfindableword")["results"] == []
    assert store.browse(query="!!!")["results"] == []
    page = store.browse(limit=3)
    assert len(page["results"]) == 3 and not page["has_more"]


def test_pending_browse_summary_keeps_native_snippet_fallback(tmp_path):
    store = MemoryStore(tmp_path)
    seed(store, count=3)
    pending = store.browse(card_status="pending")["results"][0]
    assert pending["summary"] is None
    assert pending["snippet"] and pending["card"] is None
    ready = store.browse(card_status="ready")["results"][0]
    assert ready["summary"] == card()["summary"]


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 51}, {"limit": True}, {"limit": "2"},
    {"query": "x" * 513}, {"query": None}, {"card_status": "unknown"},
    {"cursor": "not valid base64"}, {"cursor": "x" * 2049},
    {"cursor": base64.urlsafe_b64encode(b'{"v":100}').decode()},
])
def test_browse_rejects_invalid_input_as_controlled_400(tmp_path, options):
    with pytest.raises(AppError) as error:
        MemoryStore(tmp_path).browse(**options)
    assert error.value.status_code == 400


def test_cursor_is_bound_to_query_and_status_and_rejects_nonfinite_rank(tmp_path):
    store = MemoryStore(tmp_path)
    seed(store, count=12)
    cursor = store.browse(limit=2)["next_cursor"]
    for changed in ({"query": "бот"}, {"card_status": "pending"}):
        with pytest.raises(AppError):
            store.browse(cursor=cursor, **changed)
    payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    payload["score"] = float("nan")
    bad = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(AppError):
        store.browse(cursor=bad)


def test_disabled_and_deleted_sources_never_leak_on_next_page(tmp_path):
    store = MemoryStore(tmp_path)
    seed(store, count=12)
    hidden = seed(store, count=12, source_id="hidden")
    first = store.browse(limit=3, allowed_source_ids={"allowed"})
    assert not ({record["id"] for record in first["results"]} & set(hidden))
    disabled = store.browse(cursor=first["next_cursor"], allowed_source_ids=set())
    assert disabled == {"results": [], "next_cursor": None, "has_more": False}
    store.delete_source("allowed")
    deleted = store.browse(cursor=first["next_cursor"], allowed_source_ids={"allowed"})
    assert deleted["results"] == []


def test_pending_status_changes_do_not_skip_later_rows(tmp_path):
    store = MemoryStore(tmp_path)
    seed(store, count=30)
    first = store.browse(card_status="pending", limit=3)
    before = store.browse(card_status="pending", limit=50)["results"]
    first_row = first["results"][0]
    store.save_card(first_row["id"], first_row["revision"], card())
    store.upsert_document(**document(
        source_id="allowed", source_object_id="newer", updated_at="2026-11-04T00:00:00Z",
    ))
    rest = store.browse(card_status="pending", limit=50, cursor=first["next_cursor"])
    assert [row["id"] for row in rest["results"]] == [row["id"] for row in before[3:]]


def test_fulltext_browse_preserves_bm25_relevance_not_only_recency(tmp_path):
    store = MemoryStore(tmp_path)
    relevant = store.upsert_document(**document(
        source_object_id="relevant", title="Needle", text="needle " * 30,
        updated_at="2025-01-01T00:00:00Z",
    ))
    less_relevant = store.upsert_document(**document(
        source_object_id="weak", title="Other", text="needle " + "unrelated " * 30,
        updated_at="2026-11-04T00:00:00Z",
    ))
    first = store.browse(query="needle", limit=1)
    assert first["results"][0]["id"] == relevant["id"]
    second = store.browse(query="needle", limit=1, cursor=first["next_cursor"])
    assert second["results"][0]["id"] == less_relevant["id"]
    assert not second["has_more"]


def needle_records(store, count=4):
    return [store.upsert_document(**document(
        source_object_id=f"needle:{index}", title="needle", text="needle evidence",
    )) for index in range(count)]


def test_changed_card_invalidates_fts_cursor_instead_of_losing_unseen_records(tmp_path):
    store = MemoryStore(tmp_path)
    records = needle_records(store)
    first = store.browse(query="needle", limit=1)
    unseen = next(record for record in records if record["id"] != first["results"][0]["id"])
    store.save_card(unseen["id"], unseen["revision"], {
        "title": "needle", "summary": "needle " * 100,
        "keywords": ["needle"], "questions": ["needle?"],
        "atoms": [{"text": "needle evidence", "kind": "decision", "evidence": "needle evidence"}],
    })
    with pytest.raises(AppError) as error:
        store.browse(query="needle", limit=1, cursor=first["next_cursor"])
    assert error.value.code == "browse_refresh_required"
    assert error.value.status_code == 409
    restarted = MemoryStore(tmp_path)
    pages = all_pages(restarted, query="needle", limit=1)
    assert {row["id"] for page in pages for row in page["results"]} == {
        record["id"] for record in records
    }


def test_fts_generation_and_rows_share_one_short_read_snapshot(tmp_path, monkeypatch):
    from brain_settings import memory_browse

    store = MemoryStore(tmp_path)
    original_records = needle_records(store)
    original = memory_browse._matching_rows

    def write_before_rows(connection, *args):
        assert connection.in_transaction
        store.upsert_document(**document(
            source_object_id="new-needle", title="needle", text="needle needle evidence",
        ))
        return original(connection, *args)

    monkeypatch.setattr(memory_browse, "_matching_rows", write_before_rows)
    page = store.browse(query="needle", limit=2)
    assert {row["id"] for row in page["results"]} <= {
        record["id"] for record in original_records
    }
    monkeypatch.setattr(memory_browse, "_matching_rows", original)
    with pytest.raises(AppError) as error:
        store.browse(query="needle", cursor=page["next_cursor"])
    assert error.value.code == "browse_refresh_required"


@pytest.mark.parametrize("change", ["insert", "update", "delete"])
def test_fts_generation_tracks_all_document_mutations_across_restart(tmp_path, change):
    store = MemoryStore(tmp_path)
    needle_records(store)
    search_page = store.browse(query="needle", limit=1)
    recent_page = store.browse(limit=1)
    cursor_data = json.loads(base64.urlsafe_b64decode(
        recent_page["next_cursor"] + "=" * (-len(recent_page["next_cursor"]) % 4)
    ))
    assert "generation" not in cursor_data
    if change == "insert":
        store.upsert_document(**document(
            source_object_id="inserted", title="needle", text="needle evidence",
        ))
    elif change == "update":
        store.upsert_document(**document(
            source_object_id="needle:0", title="needle", text="needle changed evidence",
        ))
    else:
        store.delete_source("source-one")
    restarted = MemoryStore(tmp_path)
    with pytest.raises(AppError) as error:
        restarted.browse(query="needle", cursor=search_page["next_cursor"])
    assert error.value.code == "browse_refresh_required"
    restarted.browse(cursor=recent_page["next_cursor"])


def test_unchanged_import_does_not_invalidate_search_cursor(tmp_path):
    store = MemoryStore(tmp_path)
    needle_records(store)
    first = store.browse(query="needle", limit=1)
    needle_records(store)
    second = MemoryStore(tmp_path).browse(query="needle", limit=1, cursor=first["next_cursor"])
    assert second["results"] and second["results"][0]["id"] != first["results"][0]["id"]


def test_api_browse_uses_allowed_sources_and_returns_pagination(tmp_path):
    class NoLLM:
        def generate(self, _record):
            raise AssertionError("Browsing must not call a model")

    app = create_app(data_dir=tmp_path / "data", vault=MemoryVault(),
                     transport=FakeTransport(), memory_llm=NoLLM())
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = assert_success(client.get("/api/bootstrap"))["csrf_token"]
        directory = tmp_path / "notes"
        directory.mkdir()
        source = assert_success(client.post("/api/sources", json={
            "kind": "folder", "name": "Synthetic", "path": str(directory),
        }, headers={"Origin": ORIGIN, "X-Brain-CSRF": csrf}))["source"]
        seed(app.state.memory_service.store, count=40, source_id=source["id"])
        seed(app.state.memory_service.store, count=10, source_id="unconfigured")
        first = assert_success(client.get("/api/memory/browse", params={"limit": 7}))
        assert len(first["results"]) == 7 and first["has_more"]
        second = assert_success(client.get("/api/memory/browse", params={
            "limit": 50, "cursor": first["next_cursor"],
        }))
        assert len(second["results"]) == 33 and not second["has_more"]
        assert {row["source_id"] for row in second["results"]} == {source["id"]}
        filtered = assert_success(client.get("/api/memory/browse", params={
            "card_status": "error", "q": "бот",
        }))
        assert len(filtered["results"]) == 13
        assert {row["card_status"] for row in filtered["results"]} == {"error"}
        search_first = assert_success(client.get("/api/memory/browse", params={
            "q": "бот", "limit": 1,
        }))
        app.state.memory_service.store.upsert_document(**document(
            source_id=source["id"], source_object_id="new-index-material",
        ))
        stale = client.get("/api/memory/browse", params={
            "q": "бот", "cursor": search_first["next_cursor"],
        })
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "browse_refresh_required"
        assert client.get("/api/memory/browse", params={"cursor": "invalid!"}).status_code == 400
        assert client.get("/api/memory/browse", params={"cursor": "x" * 2049}).status_code == 400
        detail = assert_success(client.get("/api/memory/records/" + first["results"][0]["id"]))
        assert detail["record"]["text"]
        assert client.get("/api/memory/browse", params={"limit": 51}).status_code == 422
        assert client.get("/api/memory/browse", params={"card_status": "bad"}).status_code == 422
