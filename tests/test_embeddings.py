"""Synthetic-only embedding lifecycle and independent retrieval checks."""

import json
import math

import pytest

from brain_settings.embedding_store import VectorStore, migrate
from brain_settings.embeddings import EmbeddingManager
from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore
from brain_settings.ollama import OllamaClient
from brain_settings.retrieval import Retrieval


class Response:
    def __init__(self, payload):
        self.status = 200
        self.raw = json.dumps(payload).encode()

    def read(self, size):
        return self.raw[:size]

    def getheader(self, name, default=None):
        return default


class Connection:
    def __init__(self, response):
        self.response = Response(response)
        self.requests = []

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        pass


def catalog(digest="a" * 64):
    return {"models": [{"name": "embed:small", "size": 1234, "digest": digest}]}


def ollama(*responses):
    connections = [Connection(payload) for payload in responses]
    pending = iter(connections)
    return OllamaClient(connection_factory=lambda *a, **kw: next(pending)), connections


def test_embedding_uses_fixed_local_qualified_model_and_disables_truncation():
    client, calls = ollama(catalog(), {"model": "embed:small", "embeddings": [[1, 0]]}, catalog())
    result = client.embed("embed:small", ["synthetic text"], expected_digest="a" * 64)
    assert result["embeddings"] == [[1, 0]]
    request = json.loads(calls[1].requests[0][1]["body"])
    assert calls[1].requests[0][0][:2] == ("POST", "/api/embed")
    assert request["model"] == "embed:small:local"
    assert request["truncate"] is False
    assert request["input"] == ["synthetic text"]


@pytest.mark.parametrize(
    "vectors", [[[math.nan, 0]], [[0, 0]], [[True, 0]], [[1]], [[1, 0], [0, 1]], []]
)
def test_embedding_rejects_invalid_count_nonfinite_zero_and_dimensions(vectors):
    client, _ = ollama(catalog(), {"model": "embed:small", "embeddings": vectors}, catalog())
    with pytest.raises(AppError):
        client.embed("embed:small", ["synthetic"], expected_digest="a" * 64, dimension=2)


def test_digest_change_discards_response_and_cannot_mix_model_generations():
    client, _ = ollama(
        catalog(), {"model": "embed:small", "embeddings": [[1, 0]]}, catalog("b" * 64)
    )
    with pytest.raises(AppError, match="."):
        client.embed("embed:small", ["synthetic"], expected_digest="a" * 64)


def test_embedding_deadline_is_shared_across_catalog_embed_and_final_catalog(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("brain_settings.ollama.time.monotonic", lambda: clock[0])
    responses = [catalog(), {"model": "embed:small", "embeddings": [[1, 0]]}, catalog()]
    timeouts = []

    class SlowResponse(Response):
        def read(self, amount):
            clock[0] += 1.1
            return super().read(amount)

    class SlowConnection(Connection):
        def __init__(self, payload):
            super().__init__(payload)
            self.response = SlowResponse(payload)

    pending = iter([SlowConnection(payload) for payload in responses])

    def factory(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return next(pending)

    client = OllamaClient(connection_factory=factory)
    with pytest.raises(AppError) as caught:
        client.embed("embed:small", ["synthetic"], expected_digest="a" * 64, timeout=3)
    assert caught.value.code == "local_model_unavailable"
    assert len(timeouts) == 3
    assert timeouts[-1] < 1


@pytest.mark.parametrize("texts", [[], [""], ["x" * 12001], ["x"] * 17])
def test_oversized_or_empty_embedding_input_never_connects(texts):
    client, connections = ollama()
    with pytest.raises(AppError):
        client.embed("embed:small", texts)
    assert connections == []


@pytest.fixture
def store(tmp_path):
    store = MemoryStore(tmp_path)
    with store._connection(write=True) as connection:
        migrate(connection)
    return store


def document(store, key="one", text="A synthetic secret", source_id="source-one"):
    saved = store.upsert_document(
        source_id=source_id,
        source_kind="folder",
        source_object_id=key,
        title=key,
        text=text,
        source_uri="file:///synthetic/" + key,
    )
    return store.get_record(saved["id"])


def test_embeddings_default_disabled_without_loading_any_model(store):
    vectors = VectorStore(store)
    assert vectors.settings()["enabled"] is False
    assert vectors.pending() == []
    result = Retrieval(store, client=object()).search("synthetic", mode="hybrid")
    assert result["search"]["used_mode"] == "lexical"
    assert result["search"]["fallback_reason"] == "embeddings_disabled"


def test_index_revision_generation_and_hash_are_enforced_transactionally(store):
    record = document(store)
    vectors = VectorStore(store)
    first = vectors.configure("embed:small", "a" * 64, 2)
    assert vectors.save(record, first["generation"], [1, 0])
    assert len(vectors.search([1, 0])) == 1
    updated = document(store, text="The synthetic content changed")
    assert not vectors.save(record, first["generation"], [1, 0])
    assert vectors.search([1, 0]) == []
    assert vectors.pending()[0]["revision"] == updated["revision"]
    second = vectors.configure("embed:small", "b" * 64, 3)
    assert second["generation"] != first["generation"]
    assert not vectors.save(updated, first["generation"], [1, 0])
    assert vectors.search([1, 0, 0]) == []


def test_reindex_is_idempotent_and_delete_blocks_late_save(store):
    record = document(store)
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    assert vectors.save(record, state["generation"], [1, 0])
    assert vectors.save(record, state["generation"], [1, 0])
    assert vectors.pending() == []
    assert vectors.settings()["ready"] == 1
    store.delete_source(record["source_id"])
    assert not vectors.save(record, state["generation"], [1, 0])
    assert vectors.search([1, 0]) == []


def test_revision_change_between_vector_scan_and_materialization_is_not_returned(
    store, monkeypatch
):
    record = document(store)
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    vectors.save(record, state["generation"], [1, 0])
    original = store.get_record

    def update_before_read(identifier, **kwargs):
        store.upsert_document(
            source_id="source-one",
            source_kind="folder",
            source_object_id="one",
            title="one",
            text="Completely different revision",
            source_uri="file:///synthetic/one",
        )
        return original(identifier, **kwargs)

    monkeypatch.setattr(store, "get_record", update_before_read)
    assert vectors.search([1, 0]) == []


def test_vector_candidates_are_independent_of_lexical_matches_and_source_filtered(store):
    """The meaningful hit deliberately shares no terms with the query."""
    target = document(store, "retention", "Delete logs after fourteen days")
    noise = document(store, "sports", "How to bake a cake", "source-two")
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    vectors.save(target, state["generation"], [1, 0])
    vectors.save(noise, state["generation"], [0, 1])
    client = FakeEmbedding()
    result = Retrieval(store, client=client).search(
        "forget old telemetry", mode="hybrid", allowed_source_ids=["source-one"]
    )
    assert result["results"][0]["id"] == target["id"]
    assert all(r["source_id"] == "source-one" for r in result["results"])
    assert result["search"]["used_mode"] == "hybrid"


class FakeEmbedding:
    def embedding_catalog(self, **kwargs):
        return [{"id": "embed:small", "digest": "a" * 64}]

    def embed(self, model_id, texts, **kwargs):
        return {"embeddings": [[1, 0] for _ in texts], "load_duration": 0, "total_duration": 0}


def test_unavailable_model_falls_back_without_changing_lexical_order(store):
    target = document(store)
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    vectors.save(target, state["generation"], [1, 0])

    class Unavailable(FakeEmbedding):
        def embed(self, *args, **kwargs):
            raise AppError("local_model_unavailable", "safe error", 503)

    result = Retrieval(store, client=Unavailable()).search("synthetic", mode="hybrid")
    assert result["results"][0]["id"] == target["id"]
    assert result["search"]["used_mode"] == "lexical"
    assert result["search"]["fallback_reason"] == "local_model_unavailable"


def test_policy_change_during_query_hides_counts_and_results(store):
    from brain_settings.source_policy import SourcePolicy

    target = document(store)
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    vectors.save(target, state["generation"], [1, 0])

    class Revoke(FakeEmbedding):
        def embed(self, *args, **kwargs):
            store.set_source_policy("source-one", SourcePolicy(mcp_enabled=False))
            return super().embed(*args, **kwargs)

    result = Retrieval(store, Revoke()).search("forget telemetry", visibility="mcp", mode="hybrid")
    assert result["results"] == []
    assert result["search"]["eligible"] == result["search"]["ready"] == 0


def test_reincluded_material_with_purged_vector_becomes_pending_again(store):
    from brain_settings.source_policy import SourcePolicy

    target = document(store)
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    vectors.save(target, state["generation"], [1, 0])
    store.set_source_policy("source-one", SourcePolicy(excluded_paths=("one",)))
    store.set_source_policy("source-one", SourcePolicy())
    assert vectors.pending()[0]["id"] == target["id"]


def test_huge_json_number_is_invalid_embedding_and_uses_lexical_fallback(store):
    target = document(store)
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    vectors.save(target, state["generation"], [1, 0])
    client, _ = ollama(catalog(), {"model": "embed:small", "embeddings": [[10**400, 1]]})
    result = Retrieval(store, client).search("synthetic", mode="hybrid")
    assert result["results"][0]["id"] == target["id"]
    assert result["search"]["fallback_reason"] == "invalid_embedding"


def test_disabling_index_during_request_discards_late_vector(store):
    document(store)
    vectors = VectorStore(store)
    vectors.configure("embed:small", "a" * 64, 2)

    class DisableDuringEmbedding(FakeEmbedding):
        def embed(self, *args, **kwargs):
            vectors.disable()
            return super().embed(*args, **kwargs)

    result = EmbeddingManager(store, DisableDuringEmbedding()).index_batch()
    assert result["saved"] == 0
    assert result["stale"] == 1
    assert vectors.settings()["ready"] == 0


def test_indexing_resumes_and_does_not_process_beyond_bounded_batch(store):
    for number in range(5):
        document(store, str(number))
    vectors = VectorStore(store)
    vectors.configure("embed:small", "a" * 64, 2)
    manager = EmbeddingManager(store, FakeEmbedding())
    assert manager.index_batch(limit=2)["saved"] == 2
    assert manager.index_batch(limit=2)["saved"] == 2
    assert manager.index_batch(limit=2)["saved"] == 1
    assert vectors.pending() == []


def test_embedding_configuration_probes_a_separate_model_and_preserves_existing_on_failure(store):
    manager = EmbeddingManager(store, FakeEmbedding())
    state = manager.configure(enabled=True, model_id="embed:small")
    assert state["dimension"] == 2
    assert state["digest"] == "a" * 64
    assert state["enabled"] is True
    assert manager.configure(enabled=False)["enabled"] is False


def test_index_errors_have_a_bounded_retry_budget_and_preserve_lexical_text(store, monkeypatch):
    document(store)
    vectors = VectorStore(store)
    vectors.configure("embed:small", "a" * 64, 2)

    class Broken(FakeEmbedding):
        def embed(self, *args, **kwargs):
            raise AppError("local_model_unavailable", "safe failure", 503)

    clock = [1000.0]
    monkeypatch.setattr("brain_settings.embedding_store.time.time", lambda: clock[0])
    manager = EmbeddingManager(store, Broken())
    for _attempt in range(3):
        assert manager.index_batch()["errors"] == 1
        assert vectors.pending() == []
        clock[0] += 61
    assert manager.index_batch()["processed"] == 0
    assert vectors.settings()["errors"] == 1
    assert store.search("synthetic")


def test_successful_same_model_reprobe_requeues_errors_and_preserves_ready_vectors(store):
    ready = document(store, key="ready")
    failed = document(store, key="failed")
    vectors = VectorStore(store)
    before = vectors.configure("embed:small", "a" * 64, 2)
    vectors.save(ready, before["generation"], [1, 0])
    for _ in range(3):
        vectors.error(failed, before["generation"], "local_model_unavailable")
    assert vectors.pending() == []
    assert vectors.settings()["errors"] == 1

    class RejectProbe(FakeEmbedding):
        def embed(self, *args, **kwargs):
            raise AppError("local_model_unavailable", "safe unavailable model", 503)

    with pytest.raises(AppError):
        EmbeddingManager(store, RejectProbe()).configure(enabled=True, model_id="embed:small")
    assert vectors.settings()["errors"] == 1
    assert vectors.pending() == []
    after = EmbeddingManager(store, FakeEmbedding()).configure(enabled=True, model_id="embed:small")
    assert after["generation"] == before["generation"]
    assert after["ready"] == 1
    assert after["errors"] == 0
    assert [row["id"] for row in vectors.pending()] == [failed["id"]]
    assert EmbeddingManager(store, FakeEmbedding()).index_batch()["saved"] == 1
    assert vectors.settings()["ready"] == 2


def test_too_long_input_is_explicit_error_without_model_call(store):
    document(store, text="x" * 12001)
    vectors = VectorStore(store)
    vectors.configure("embed:small", "a" * 64, 2)

    class NeverCall(FakeEmbedding):
        def embed(self, *args, **kwargs):
            raise AssertionError("oversized text must never reach the model")

    result = EmbeddingManager(store, NeverCall()).index_batch()
    assert result["errors"] == 1
    with store._connection() as connection:
        assert connection.execute("SELECT error FROM embedding_jobs").fetchone()[0] == (
            "embedding_input_too_long"
        )


def test_local_model_policy_revoke_blocks_pending_and_late_writes_but_not_reads(store):
    from brain_settings.source_policy import SourcePolicy

    target = document(store)
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    store.set_source_policy("source-one", SourcePolicy(local_model_enabled=False))
    assert vectors.pending() == []
    assert vectors.settings()["pending"] == 0
    assert not vectors.save(target, state["generation"], [1, 0])
    vectors.error(target, state["generation"], "local_model_unavailable")
    assert vectors.settings()["errors"] == 0
    assert store.search("synthetic")


def test_late_error_after_disable_is_not_persisted(store):
    target = document(store)
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    vectors.disable()
    vectors.error(target, state["generation"], "local_model_unavailable")
    assert vectors.settings()["errors"] == 0


def test_separate_store_instances_observe_current_generation_and_permissions(store):
    from brain_settings.source_policy import SourcePolicy

    target = document(store)
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    vectors.save(target, state["generation"], [1, 0])
    other = MemoryStore(store.directory)
    assert VectorStore(other).search([1, 0], visibility="mcp")
    other.set_source_policy("source-one", SourcePolicy(mcp_enabled=False))
    assert vectors.search([1, 0], visibility="mcp") == []
    VectorStore(other).disable()
    assert vectors.search([1, 0]) == []
