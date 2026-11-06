import pytest
from test_embeddings import FakeEmbedding, document

from brain_settings.embedding_store import VectorStore
from brain_settings.mcp_server import MemoryTools
from brain_settings.source_policy import SourcePolicy


def test_embedding_configuration_http_requires_explicit_valid_local_model(harness):
    service = harness.client.app.state.memory_service
    service.embeddings.client = FakeEmbedding()
    assert harness.client.get("/api/memory/embeddings").json()["data"]["enabled"] is False
    missing_csrf = harness.client.post("/api/memory/embeddings", json={"enabled": True})
    assert missing_csrf.status_code == 403
    state = harness.post(
        "/api/memory/embeddings", {"enabled": True, "model_id": "embed:small"}
    ).json()["data"]
    assert state["enabled"] is True
    assert state["dimension"] == 2
    catalog = harness.client.get("/api/memory/embeddings/models").json()["data"]["models"]
    assert catalog[0]["digest"] == "a" * 64
    assert harness.post("/api/memory/embeddings", {"enabled": "true"}).status_code == 422
    assert harness.post("/api/memory/embeddings/index", {"limit": 17}).status_code == 422


def test_http_and_stdio_share_retrieval_without_index_writes(harness, monkeypatch):
    service = harness.client.app.state.memory_service
    saved = document(service.store, text="A fourteen day retention window")
    vectors = VectorStore(service.store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    vectors.save(saved, state["generation"], [1, 0])
    # The app's source list constrains local and MCP visibility as well as SQL policy.
    monkeypatch.setattr(service, "_allowed", lambda: frozenset(["source-one"]))
    service.retrieval.client = FakeEmbedding()
    tools = MemoryTools(service.store)
    tools.retrieval.client = FakeEmbedding()
    http = harness.client.get(
        "/api/memory/search", params={"q": "forget telemetry", "mode": "hybrid"}
    ).json()["data"]
    stdio = tools.search_memory("forget telemetry", mode="hybrid")
    assert http["results"][0]["id"] == stdio["results"][0]["id"] == saved["id"]
    assert http["search"]["used_mode"] == stdio["search"]["used_mode"] == "hybrid"
    assert vectors.settings()["ready"] == 1
    service.store.set_source_policy("source-one", SourcePolicy(mcp_enabled=False))
    assert tools.search_memory("forget telemetry", mode="hybrid")["results"] == []


@pytest.mark.parametrize("mode", ["invalid", "", "HYBRID"])
def test_invalid_modes_are_rejected_without_model_calls(harness, mode):
    assert (
        harness.client.get("/api/memory/search", params={"q": "x", "mode": mode}).status_code == 400
    )
    tools = MemoryTools(harness.client.app.state.memory_service.store)
    assert tools.search_memory("x", mode=mode)["error"]["code"] == "invalid_search_mode"


def test_index_job_obeys_pause_and_shared_writer_lease(harness, monkeypatch):
    from test_memory_service_api import wait_job

    from brain_settings.background import acquire_writer_lease, release_writer_lease

    service = harness.client.app.state.memory_service
    service.embeddings.client = FakeEmbedding()
    monkeypatch.setattr(service, "_allowed", lambda: frozenset(["source-one"]))
    document(service.store)
    VectorStore(service.store).configure("embed:small", "a" * 64, 2)
    lease = acquire_writer_lease(harness.data_dir)
    try:
        assert harness.post("/api/memory/embeddings/index", {}).status_code == 409
    finally:
        release_writer_lease(lease)
    service.set_sync_paused(True)
    assert harness.post("/api/memory/embeddings/index", {}).status_code == 409
    service.set_sync_paused(False)
    job_id = harness.post("/api/memory/embeddings/index", {}).json()["data"]["job_id"]
    completed = wait_job(harness.client, job_id)
    assert completed["status"] == "completed"
    assert VectorStore(service.store).settings()["ready"] == 1
