"""Explicit local embedding selection and bounded writer-owned indexing."""

from .embedding_store import VectorStore
from .errors import AppError
from .ollama import OllamaClient


class EmbeddingManager:
    def __init__(self, store, client=None):
        self.store = store
        self.vectors = VectorStore(store)
        self.client = client if client is not None else OllamaClient()

    def configure(self, *, enabled, model_id=None):
        if not isinstance(enabled, bool):
            raise AppError("invalid_embedding_settings", "Укажите состояние локального индекса.")
        if not enabled:
            return self.vectors.disable()
        candidates = self.client.embedding_catalog()
        selected = next((row for row in candidates if row["id"] == model_id), None)
        if selected is None:
            raise AppError(
                "local_model_required",
                "Выберите установленную embedding-модель Ollama."
                " Загрузка моделей выполняется отдельно в Ollama.",
                400,
            )
        probe = self.client.embed(
            model_id,
            ["Second Brain synthetic embedding capability check"],
            expected_digest=selected["digest"],
            timeout=5,
        )
        return self.vectors.configure(model_id, selected["digest"], len(probe["embeddings"][0]))

    def index_batch(self, limit=8, *, allowed_source_ids=None, check=None):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 16:
            raise AppError("invalid_batch", "Размер пакета embeddings должен быть от 1 до 16.")
        check = check or (lambda: None)
        check()
        self.vectors.purge()
        state = self.vectors.config()
        records = self.vectors.pending(limit, allowed_source_ids=allowed_source_ids)
        stats = {"saved": 0, "errors": 0, "stale": 0, "processed": len(records)}
        usable = []
        for record in records:
            prepared = record["title"] + "\n" + record["text"]
            if len(prepared) > 12000 or len(prepared.encode("utf-8")) > 100000:
                self.vectors.error(record, state["generation"], "embedding_input_too_long")
                stats = stats | {"errors": stats["errors"] + 1}
            else:
                usable.append((record, prepared))
        # Each request stays under the loopback adapter's UTF-8 body budget.
        for record, prepared in usable:

            def admission(record=record):
                check()
                current = self.store.get_record(
                    record["id"], visibility="model_local", allowed_source_ids=allowed_source_ids
                )
                if (
                    current["revision"] != record["revision"]
                    or current["content_hash"] != record["content_hash"]
                    or self.vectors.config() != state
                ):
                    raise AppError(
                        "embedding_stale", "Запись или настройки индекса изменились.", 409
                    )

            try:
                admission()
                result = self.client.embed(
                    state["model_id"],
                    [prepared],
                    expected_digest=state["digest"],
                    dimension=state["dimension"],
                    timeout=10,
                    before_request=admission,
                )
                check()
                saved = self.vectors.save(record, state["generation"], result["embeddings"][0])
                stats = stats | {
                    "saved": stats["saved"] + int(saved),
                    "stale": stats["stale"] + int(not saved),
                }
            except AppError as error:
                if error.code in ("memory_cancelled", "sync_paused"):
                    raise
                if error.code in ("embedding_stale", "record_not_found"):
                    stats = stats | {"stale": stats["stale"] + 1}
                else:
                    self.vectors.error(record, state["generation"], error.code)
                    stats = stats | {"errors": stats["errors"] + 1}
        return stats
