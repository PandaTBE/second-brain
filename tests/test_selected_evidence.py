"""Selected-material context stays scoped before ranking and model work."""

import pytest
from fastapi.testclient import TestClient
from test_settings_api import FakeTransport, MemoryVault

from brain_settings.app import create_app
from brain_settings.embedding_store import VectorStore
from brain_settings.errors import AppError
from brain_settings.evidence import EvidenceService, encoded_size
from brain_settings.mcp_server import MemoryTools
from brain_settings.memory_store import MemoryStore
from brain_settings.source_policy import SourcePolicy
from brain_settings.source_storage import Source, SourcesStore

SOURCE = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"


class NeverRank:
    def search(self, *args, **kwargs):
        raise AssertionError("Selected evidence must not rank or embed the query")


@pytest.fixture
def selected(tmp_path):
    store = MemoryStore(tmp_path)
    SourcesStore(tmp_path).save(
        (
            Source(id=SOURCE, name="Synthetic selected", kind="folder", path=str(tmp_path)),
            Source(id=OTHER, name="Synthetic other", kind="folder", path=str(tmp_path)),
        )
    )
    saved = store.upsert_document(
        source_id=SOURCE,
        source_kind="folder",
        source_object_id="selected.md::chunk:000000",
        title="Retention",
        text="Delete logs after fourteen days.",
        source_uri="file:///synthetic/selected.md",
        project="memory",
        message_at="2026-11-01T12:00:00+00:00",
    )
    noise = store.upsert_document(
        source_id=OTHER,
        source_kind="folder",
        source_object_id="duplicate.md::chunk:000000",
        title="Forget old telemetry",
        text="Delete logs after fourteen days.",
        source_uri="file:///synthetic/duplicate.md",
        project="other",
    )
    vectors = VectorStore(store)
    state = vectors.configure("embed:synthetic", "a" * 64, 2)
    for item in (saved, noise):
        assert vectors.save(store.get_record(item["id"]), state["generation"], [1, 0])
    return store, saved, noise


def selected_bundle(store, identifier, **changes):
    return EvidenceService(store, retrieval=NeverRank()).bundle(
        "forget old telemetry", record_id=identifier, budget_chars=6000, **changes
    )


def test_selected_semantic_material_bypasses_ranking_and_has_only_its_current_proof(selected):
    store, saved, _ = selected
    packet = selected_bundle(store, saved["id"], mode="hybrid")
    assert {item["record_id"] for item in packet["citations"]} == {saved["id"]}
    assert all(item["revision"] == saved["revision"] for item in packet["citations"])
    assert all(
        support["record_id"] == saved["id"]
        for item in packet["citations"]
        for support in item.get("also_supported_by", [])
    )
    assert packet["filters"]["record_id"] == saved["id"]
    assert packet["search"]["used_mode"] == "record"
    assert packet["search"]["selection"] == "record_id"
    assert packet["search"]["query_embedding_ms"] is None
    assert packet["search"]["eligible"] == packet["search"]["ready"] == 1
    assert packet["incomplete"] is True
    assert "unlocalized_evidence" in packet["reasons"]
    assert packet["citations"][0]["quote_relevance"] == "uncertain"
    assert packet["used_chars"] == encoded_size(packet) <= 6000


def test_selected_context_reads_current_revision_instead_of_search_snapshot(selected):
    store, saved, _ = selected
    updated = store.upsert_document(
        source_id=SOURCE,
        source_kind="folder",
        source_object_id="selected.md::chunk:000000",
        title="Retention",
        text="Delete logs after thirty days.",
        source_uri="file:///synthetic/selected.md",
    )
    packet = selected_bundle(store, saved["id"])
    assert packet["citations"][0]["revision"] == updated["revision"]
    assert "thirty" in packet["citations"][0]["quote"]
    assert packet["search"]["ready"] == 0


@pytest.mark.parametrize(
    "filters",
    [
        {"source_id": OTHER},
        {"project": "other"},
        {"date_from": "2026-11-02"},
        {"allowed_source_ids": []},
    ],
)
def test_selected_record_keeps_all_existing_filters_and_never_substitutes_other_hits(
    selected, filters
):
    store, saved, _ = selected
    packet = selected_bundle(store, saved["id"], **filters)
    assert packet["citations"] == []
    assert packet["search"]["eligible"] == packet["search"]["ready"] == 0


def test_selected_record_policy_exclusions_and_deletion_are_fresh_across_store_instances(selected):
    store, saved, _ = selected
    second = MemoryStore(store.directory)
    second.set_source_policy(SOURCE, SourcePolicy(mcp_enabled=False))
    assert selected_bundle(store, saved["id"], visibility="mcp")["citations"] == []
    assert selected_bundle(store, saved["id"], visibility="local")["citations"]
    second.set_source_policy(SOURCE, SourcePolicy(excluded_paths=("selected.md",)))
    packet = selected_bundle(store, saved["id"])
    assert packet["citations"] == []
    assert packet["search"]["eligible"] == packet["search"]["ready"] == 0
    second.delete_source(SOURCE)
    assert selected_bundle(store, saved["id"])["citations"] == []


def test_missing_selected_id_does_not_leak_other_record_counts(selected):
    store, _, _ = selected
    packet = selected_bundle(store, "missing-record")
    assert packet["citations"] == []
    assert packet["search"]["eligible"] == packet["search"]["ready"] == 0


@pytest.mark.parametrize("identifier", ["", "x" * 129, True, 1, "\ud800"])
def test_malformed_selected_id_is_rejected_before_any_query(selected, identifier):
    store, _, _ = selected
    with pytest.raises(AppError) as error:
        selected_bundle(store, identifier)
    assert error.value.code == "invalid_record_id"


def test_selected_http_and_mcp_packets_match_with_only_selected_material(selected, monkeypatch):
    def never_embed(*args, **kwargs):
        raise AssertionError("Selected HTTP/MCP context must not embed a query")

    monkeypatch.setattr("brain_settings.ollama.OllamaClient.embed", never_embed)
    store, saved, _ = selected
    app = create_app(data_dir=store.directory, vault=MemoryVault(), transport=FakeTransport())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get(
            "/api/memory/evidence",
            params={
                "q": "forget old telemetry",
                "record_id": saved["id"],
                "budget_chars": 6000,
            },
        )
        assert response.status_code == 200
        packet = response.json()["data"]
        assert packet == MemoryTools(store, store.directory).get_evidence_bundle(
            "forget old telemetry",
            record_id=saved["id"],
            budget_chars=6000,
        )
        assert {item["record_id"] for item in packet["citations"]} == {saved["id"]}


@pytest.mark.parametrize("change", ["revision", "revoke", "delete"])
def test_selection_rechecks_revision_and_policy_after_extracting_quotes(
    selected, monkeypatch, change
):
    from brain_settings import evidence

    store, saved, _ = selected
    original = evidence._extract

    def change_after_extraction(records, query):
        result = original(records, query)
        if change == "revision":
            store.upsert_document(
                source_id=SOURCE,
                source_kind="folder",
                source_object_id="selected.md::chunk:000000",
                title="Retention",
                text="Delete logs after sixty days.",
                source_uri="file:///synthetic/selected.md",
            )
        elif change == "revoke":
            store.set_source_policy(SOURCE, SourcePolicy(mcp_enabled=False))
        else:
            store.delete_source(SOURCE)
        return result

    monkeypatch.setattr(evidence, "_extract", change_after_extraction)
    packet = selected_bundle(store, saved["id"], visibility="mcp")
    assert packet["citations"] == []
    assert "source_changed" in packet["reasons"]
    assert packet["search"]["eligible"] == (1 if change == "revision" else 0)
    assert packet["search"]["ready"] == 0


def test_selected_id_http_and_mcp_boundaries_are_strict(selected):
    store, _, _ = selected
    tools = MemoryTools(store, store.directory)
    for invalid in ("", "  ", "x" * 129, "\0", "\ud800", True):
        assert tools.get_evidence_bundle("retry", record_id=invalid)["error"]["code"] == (
            "invalid_record_id"
        )
    app = create_app(data_dir=store.directory, vault=MemoryVault(), transport=FakeTransport())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        for invalid in ("", "x" * 129):
            response = client.get(
                "/api/memory/evidence", params={"q": "retry", "record_id": invalid}
            )
            assert response.status_code == 422
        response = client.get("/api/memory/evidence", params={"q": "retry", "record_id": "  "})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_record_id"
