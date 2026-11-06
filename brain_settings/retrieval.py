"""Shared read-only lexical, semantic and reciprocal-rank-fusion retrieval."""

import time

from .embedding_store import VectorStore
from .errors import AppError
from .ollama import OllamaClient

MODES = ("lexical", "semantic", "hybrid")
RRF_K = 60


def rank_fusion(lexical, semantic, limit):
    scores = {}
    records = {}
    for rows in (lexical, semantic):
        for rank, row in enumerate(rows, 1):
            identifier = row["id"]
            records.setdefault(identifier, row)
            scores[identifier] = scores.get(identifier, 0) + 1 / (RRF_K + rank)
    order = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
    return [records[key] | {"score": scores[key]} for key in order]


class Retrieval:
    def __init__(self, store, client=None):
        self.store = store
        self.vectors = VectorStore(store)
        self.client = client if client is not None else OllamaClient()

    def _finish(self, results, diagnostics, arguments):
        from .retrieval_filters import SearchFilters

        filters = SearchFilters(arguments["project"], arguments["date_from"], arguments["date_to"])
        fresh = []
        for row in results:
            try:
                current = self.store.get_record(
                    row["id"],
                    allowed_source_ids=arguments["allowed_source_ids"],
                    visibility=arguments["visibility"],
                )
                if (
                    current["revision"] == row["revision"]
                    and current["content_hash"] == row["content_hash"]
                    and filters.matches(current)
                ):
                    fresh.append(row | current)
            except AppError as error:
                if error.code != "record_not_found":
                    raise
        return {"results": fresh, "search": diagnostics | self.vectors.coverage(**arguments)}

    def search(
        self,
        query,
        limit=8,
        source_id=None,
        allowed_source_ids=None,
        *,
        visibility="local",
        mode="lexical",
        project=None,
        date_from=None,
        date_to=None,
    ):
        if mode not in MODES:
            raise AppError("invalid_search_mode", "Выберите lexical, semantic или hybrid.")
        if (
            not isinstance(query, str)
            or len(query) > 512
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 50
        ):
            raise AppError("invalid_search", "Проверьте запрос и размер выдачи.")
        from .retrieval_filters import SearchFilters

        SearchFilters(project, date_from, date_to)
        allowed = None if allowed_source_ids is None else frozenset(allowed_source_ids)
        arguments = {
            "source_id": source_id,
            "allowed_source_ids": allowed,
            "visibility": visibility,
            "project": project,
            "date_from": date_from,
            "date_to": date_to,
        }
        lexical = self.store.search(query, limit=min(50, max(limit, 20)), **arguments)
        state = self.vectors.config()
        coverage = self.vectors.coverage(**arguments)
        diagnostics = {
            "requested_mode": mode,
            "used_mode": "lexical",
            "fallback_reason": None,
            "generation": state["generation"],
            "query_embedding_ms": None,
            "vector_search_ms": None,
            **coverage,
        }
        if mode == "lexical" or not query.strip():
            return self._finish(lexical[:limit], diagnostics, arguments)
        reason = (
            "embeddings_disabled"
            if not state["enabled"]
            else "index_not_ready"
            if not coverage["ready"]
            else None
        )
        if reason:
            return self._finish(
                lexical[:limit], diagnostics | {"fallback_reason": reason}, arguments
            )
        try:
            started = time.perf_counter()
            embedded = self.client.embed(
                state["model_id"],
                [query],
                expected_digest=state["digest"],
                dimension=state["dimension"],
                timeout=3,
            )
            diagnostics = diagnostics | {
                "query_embedding_ms": (time.perf_counter() - started) * 1000
            }
            current = self.vectors.config()
            if current != state:
                raise AppError("embedding_stale", "Настройки поиска изменились.", 409)
            started = time.perf_counter()
            semantic = self.vectors.search(
                embedded["embeddings"][0], limit=50, generation=state["generation"], **arguments
            )
            diagnostics = diagnostics | {
                "used_mode": mode,
                "vector_search_ms": (time.perf_counter() - started) * 1000,
            }
            results = (
                rank_fusion(lexical, semantic, limit) if mode == "hybrid" else semantic[:limit]
            )
            return self._finish(results, diagnostics, arguments)
        except AppError as error:
            return self._finish(
                self.store.search(query, limit=limit, **arguments),
                diagnostics | {"used_mode": "lexical", "fallback_reason": error.code},
                arguments,
            )
