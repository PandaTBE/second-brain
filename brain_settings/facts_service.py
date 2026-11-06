"""Thin app integration for deterministic fact jobs under the existing writer lease."""

from .errors import AppError


class FactsServiceMixin:
    def facts(self, query="", limit=20, source_id=None, include_history=False):
        return self.store.facts(query, limit, source_id, visibility="local",
                                allowed_provider=self._allowed, include_history=include_history)

    def fact_history(self, fact_id, limit=50, before=None):
        return self.store.fact_history(fact_id, limit, visibility="local", before=before,
                                       allowed_provider=self._allowed)

    def start_fact_projection(self, limit=100, *, automatic=False):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise AppError("invalid_batch", "Размер пакета фактов должен быть от 1 до 500.")

        def check():
            self._check_stop()
            if automatic and self.schedule_store.load().paused:
                raise AppError("memory_paused", "Автоматическое построение фактов приостановлено.")

        check()

        def run(job_id):
            stats = self.store.project_facts(limit, allowed_source_ids=self._allowed(), check=check)
            self._update(job_id, processed=stats["processed"],
                         total=stats["processed"], stats=stats)

        return self._begin("facts", run)
