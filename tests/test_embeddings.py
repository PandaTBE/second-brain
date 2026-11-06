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
    client, calls = ollama(catalog(), {"model": "embed:small", "embeddings": [[1, 0]]},
                           catalog())
    result = client.embed("embed:small", ["synthetic text"], expected_digest="a" * 64)
    assert result["embeddings"] == [[1, 0]]
    request = json.loads(calls[1].requests[0][1]["body"])
    assert calls[1].requests[0][0][:2] == ("POST", "/api/embed")
    assert request["model"] == "embed:small:local"
    assert request["truncate"] is False
    assert request["input"] == ["synthetic text"]


@pytest.mark.parametrize("vectors", [[[math.nan, 0]], [[0, 0]], [[True, 0]], [[1]],
                                      [[1, 0], [0, 1]], []])
def test_embedding_rejects_invalid_count_nonfinite_zero_and_dimensions(vectors):
    client, _ = ollama(catalog(), {"model": "embed:small", "embeddings": vectors}, catalog())
    with pytest.raises(AppError):
        client.embed("embed:small", ["synthetic"], expected_digest="a" * 64, dimension=2)


def test_digest_change_discards_response_and_cannot_mix_model_generations():
    client, _ = ollama(catalog(), {"model": "embed:small", "embeddings": [[1, 0]]},
                       catalog("b" * 64))
    with pytest.raises(AppError, match="."):
        client.embed("embed:small", ["synthetic"], expected_digest="a" * 64)


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
    saved = store.upsert_document(source_id=source_id, source_kind="folder",
                                 source_object_id=key, title=key, text=text,
                                 source_uri="file:///synthetic/" + key)
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


def test_vector_candidates_are_independent_of_lexical_matches_and_source_filtered(store):
    """The meaningful hit deliberately shares no terms with the query."""
    target = document(store, "retention", "Delete logs after fourteen days")
    noise = document(store, "sports", "How to bake a cake", "source-two")
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    vectors.save(target, state["generation"], [1, 0])
    vectors.save(noise, state["generation"], [0, 1])
    client = FakeEmbedding()
    result = Retrieval(store, client=client).search("forget old telemetry", mode="hybrid",
                                                   allowed_source_ids=["source-one"])
    assert result["results"][0]["id"] == target["id"]
    assert all(r["source_id"] == "source-one" for r in result["results"])
    assert result["search"]["used_mode"] == "hybrid"


class FakeEmbedding:
    def embedding_catalog(self, **kwargs):
        return [{"id": "embed:small", "digest": "a" * 64}]

    def embed(self, model_id, texts, **kwargs):
        return {"embeddings": [[1, 0] for _ in texts], "load_duration": 0,
                "total_duration": 0}


def test_unavailable_model_falls_back_without_changing_lexical_order(store):
    target = document(store)
    VectorStore(store).configure("embed:small", "a" * 64, 2)

    class Unavailable(FakeEmbedding):
        def embed(self, *args, **kwargs):
            raise AppError("local_model_unavailable", "safe error", 503)

    result = Retrieval(store, client=Unavailable()).search("synthetic", mode="hybrid")
    assert result["results"][0]["id"] == target["id"]
    assert result["search"]["used_mode"] == "lexical"
    assert result["search"]["fallback_reason"] == "local_model_unavailable"


def test_disabling_index_during_request_discards_late_vector(store):
    record = document(store)
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
