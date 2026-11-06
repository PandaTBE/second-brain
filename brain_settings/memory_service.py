"""One bounded writer for local imports, explicit card jobs and optional automation."""

import asyncio
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .apple_notes_import import import_apple_notes
from .background import (
    BackgroundRunner,
    ScheduleConfig,
    ScheduleStore,
    SessionWatcher,
    acquire_writer_lease,
    release_writer_lease,
)
from .card_parallel import run_card_window
from .embeddings import EmbeddingManager
from .errors import AppError
from .facts_service import FactsServiceMixin
from .github_import import import_github
from .importers import import_source, redact_text
from .llm_cards import LLMCardGenerator, validate_card
from .mail_import import import_mail
from .memory_store import MemoryStore
from .notion_import import import_notion
from .outlook_import import import_outlook
from .provider_backoff import ProviderBackoff, paused_error
from .retrieval import Retrieval

logger = logging.getLogger(__name__)
MAX_IMPORT_BATCHES = 8


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_error(error: Exception) -> str:
    if isinstance(error, AppError):
        return redact_text(error.message)[:500]
    return "Не удалось выполнить операцию памяти. Исходные данные сохранены; повторите действие."


def _add_usage(stats: dict, usage: dict) -> dict:
    return {
        **stats,
        "usage": {
            **stats["usage"],
            **{
                key: stats["usage"].get(key, 0) + value
                for key, value in usage.items()
                if key in ("prompt_tokens", "completion_tokens", "total_tokens")
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            },
        },
    }


class MemoryService(FactsServiceMixin):
    def __init__(
        self,
        data_dir: Path,
        source_service,
        settings_service,
        *,
        store=None,
        generator=None,
        importer=None,
    ):
        self.store = store if store is not None else MemoryStore(data_dir)
        self.provider_backoff = ProviderBackoff(self.store)
        self.embeddings = EmbeddingManager(self.store)
        self.retrieval = Retrieval(self.store, client=self.embeddings.client)
        self.source_service = source_service
        self.source_service.memory_store = self.store
        self.settings_service = settings_service
        self.generator = generator if generator is not None else LLMCardGenerator(settings_service)
        self.importer = importer if importer is not None else import_source
        self.schedule_store = ScheduleStore(data_dir)
        self.watcher = SessionWatcher(quiet_seconds=90)
        self.runner = BackgroundRunner(self.tick, poll_seconds=30)
        self._lock = threading.RLock()
        self._jobs = {}
        self._active_job = None
        self._thread = None
        self._stop = threading.Event()
        self._last_auto_check = None
        self._background_error = None
        try:
            config = self.schedule_store.load()
            self._next_reconcile = time.time() + config.interval_minutes * 60
        except AppError as error:
            self._background_error = error.message
            self._next_reconcile = float("inf")

    def _sources(self) -> list[dict]:
        with self.source_service._lock:
            deleted = self.store.deleted_source_ids()
            return [
                source.model_dump()
                for source in self.source_service.store.load()
                if source.kind in ("codex", "claude", "qwen", "folder", "notion", "github",
                                   "imap", "apple_notes", "outlook")
                and source.enabled
                and source.id not in deleted
            ]

    def _import_sources(self) -> list[dict]:
        return [source for source in self._sources()
                if self.store.source_policy(source["id"]).import_enabled
                and (source["kind"] not in ("imap", "outlook")
                     or source.get("mail_import_enabled", False))]

    def _allowed(self) -> frozenset[str]:
        return frozenset(source["id"] for source in self._sources())

    def status(self) -> dict:
        allowed = self._allowed()
        counts = self.store.status(allowed_source_ids=allowed)
        config = self.schedule_store.load()
        by_source = counts.get("by_source", {})
        selected = [value for key, value in by_source.items() if key in allowed]
        names = {
            "records": "documents",
            "cards_pending": "pending",
            "cards_ready": "ready",
            "cards_error": "errors",
        }
        retries = self.store.retry_status(allowed_source_ids=allowed)
        with self._lock:
            return {
                **retries,
                **{
                    public: sum(row.get(stored, 0) for row in selected)
                    for public, stored in names.items()
                },
                "jobs": list(self._jobs.values())[-20:][::-1],
                "provider_backoff": self._provider_status(config),
                "scheduler": {**config.model_dump(), "poll_seconds": 30, "quiet_seconds": 90},
                "scheduler_running": self.runner._task is not None,
                "last_auto_check_at": self._last_auto_check,
                "warning": self._background_error,
                "last_updated_at": counts.get("last_updated_at"),
            }

    def search(self, query: str, limit: int = 8, mode: str = "lexical") -> dict:
        allowed = self._allowed()
        result = self.retrieval.search(query, limit=limit, allowed_source_ids=allowed, mode=mode)
        return result | {"results": [
            record for record in result["results"] if record["source_id"] in self._allowed()
        ]}

    def evidence(self, query: str, *, source_id=None, project=None, date_from=None, date_to=None,
                 budget_chars=12000, mode="hybrid") -> dict:
        from .evidence import EvidenceService

        return EvidenceService(self.store, retrieval=self.retrieval).bundle(
            query, source_id=source_id, project=project, date_from=date_from, date_to=date_to,
            budget_chars=budget_chars, mode=mode, allowed_source_ids=self._allowed(),
            allowed_provider=self._allowed,
        )

    def embedding_settings(self):
        return self.embeddings.vectors.settings()

    def embedding_models(self):
        return {"models": self.embeddings.client.embedding_catalog(),
                "message": "Установите embedding-модель отдельно в Ollama, затем обновите список."}

    def configure_embeddings(self, enabled, model_id=None):
        return self.embeddings.configure(enabled=enabled, model_id=model_id)

    def _check_embedding_work(self):
        self._check_stop()
        if self.schedule_store.load().paused:
            raise AppError("sync_paused", "Синхронизация приостановлена.", 409)

    def start_embedding_index(self, limit=8):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 16:
            raise AppError("invalid_batch", "Размер пакета embeddings должен быть от 1 до 16.")
        self._check_embedding_work()
        def run(job_id):
            stats = self.embeddings.index_batch(
                limit=limit, allowed_source_ids=self._allowed(), check=self._check_embedding_work
            )
            self._update(job_id, processed=stats["processed"],
                         total=stats["processed"], stats=stats)
        return self._begin("embeddings", run)

    def browse(self, query="", card_status="all", limit=30, cursor=None) -> dict:
        page = self.store.browse(
            query, card_status, limit, cursor, allowed_source_ids=self._allowed()
        )
        allowed = self._allowed()
        return {
            **page,
            "results": [record for record in page["results"] if record["source_id"] in allowed],
        }

    def atlas(self) -> dict:
        snapshot = self.store.atlas(allowed_source_ids=self._allowed())
        sources = [source for source in self._sources()
                   if self.store.source_policy(source["id"]).local_search_enabled]
        allowed = frozenset(source["id"] for source in sources)
        records = [record for record in snapshot["records"] if record["source_id"] in allowed]
        total = sum(count for source, count in snapshot["_counts_by_source"].items()
                    if source in allowed)
        return {
            "generated_at": snapshot["generated_at"],
            "total_records": total,
            "returned_records": len(records),
            "truncated": len(records) < total,
            "records": records,
            "sources": [{"id": source["id"], "name": redact_text(source["name"])[:200],
                         "kind": source["kind"]} for source in sources],
        }

    def record(self, record_id: str) -> dict:
        record = self.store.get_record(record_id, allowed_source_ids=self._allowed())
        if not record or record["source_id"] not in self._allowed():
            raise AppError("record_not_found", "Запись памяти не найдена.", 404)
        return {"record": record}

    def job(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise AppError("job_not_found", "Операция памяти не найдена.", 404)
            return {"job": job}

    def update_schedule(self, config: ScheduleConfig) -> dict:
        with self._lock:
            current = self.schedule_store.load()
            preserved = {name: getattr(current, name) for name in ("paused", "card_concurrency")
                         if name not in config.model_fields_set}
            config = config.model_copy(update=preserved)
            self.schedule_store.save(config)
            self._next_reconcile = time.time() + config.interval_minutes * 60
            self._background_error = None
        return {"scheduler": {**config.model_dump(), "poll_seconds": 30, "quiet_seconds": 90}}

    def set_sync_paused(self, paused: bool) -> dict:
        if not isinstance(paused, bool):
            raise AppError("invalid_sync_pause", "Укажите состояние паузы синхронизации.", 422)
        with self._lock:
            config = self.schedule_store.load().model_copy(update={"paused": paused})
            self.schedule_store.save(config)
        return {"scheduler": {**config.model_dump(), "poll_seconds": 30, "quiet_seconds": 90}}

    def _provider_scope(self):
        scope = getattr(self.generator, "provider_scope", None)
        return scope() if callable(scope) else "injected"

    def _provider_status(self, config=None):
        config = config if config is not None else self.schedule_store.load()
        status = self.provider_backoff.status(self._provider_scope(), config.card_concurrency)
        limit = getattr(self.generator, "concurrency_limit", lambda: None)()
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
            return {**status, "effective_concurrency": min(status["effective_concurrency"], limit),
                    "execution_mode": "local"}
        return {**status, "execution_mode": "cloud"}

    def resume_provider(self) -> dict:
        with self._lock:
            self.provider_backoff.resume(self._provider_scope())
            return {"provider_backoff": self._provider_status()}

    def _update(self, job_id: str, **values) -> None:
        with self._lock:
            self._jobs = {**self._jobs, job_id: {**self._jobs[job_id], **values}}

    def _begin(self, kind: str, operation) -> dict:
        with self._lock:
            if self._stop.is_set():
                raise AppError("memory_stopping", "Служба памяти завершает работу.", 503)
            if self._active_job is not None:
                raise AppError("memory_busy", "Дождитесь завершения текущей операции памяти.", 409)
            lease = acquire_writer_lease(self.schedule_store.path.parent)
            job_id = str(uuid4())
            job = {
                "id": job_id,
                "kind": kind,
                "phase": kind,
                "status": "queued",
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "processed": 0,
                "total": 0,
                "stats": {},
                "error": None,
            }
            recent = list(self._jobs.items())[-19:]
            self._jobs = {**dict(recent), job_id: job}
            self._active_job = job_id
            self._thread = threading.Thread(
                target=self._run,
                args=(job_id, operation, lease),
                name="brain-memory-writer",
                daemon=True,
            )
            try:
                self._thread.start()
            except RuntimeError as error:
                release_writer_lease(lease)
                self._active_job = None
                self._update(
                    job_id,
                    status="error",
                    finished_at=_now(),
                    error="Не удалось запустить обработку памяти.",
                )
                raise AppError(
                    "worker_unavailable", "Не удалось запустить обработку памяти.", 503
                ) from error
        return {"job_id": job_id}

    def _run(self, job_id: str, operation, lease: int) -> None:
        self._update(job_id, status="running", started_at=_now())
        try:
            operation(job_id)
            self._check_stop()
            self._update(job_id, status="completed", finished_at=_now())
        except Exception as error:
            self._update(job_id, status="error", finished_at=_now(), error=_safe_error(error))
            logger.warning("Memory job failed (%s)", getattr(error, "code", type(error).__name__))
        finally:
            try:
                release_writer_lease(lease)
            except OSError:
                logger.warning("Could not release the memory worker file lock")
            finally:
                with self._lock:
                    self._active_job = None

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise AppError(
                "memory_cancelled", "Операция остановлена при завершении приложения.", 503
            )

    def _check_remote_import(self, source_id: str, automatic: bool) -> None:
        self._check_stop()
        if source_id not in frozenset(source["id"] for source in self._import_sources()):
            raise AppError("source_deleted", "Источник отключён; чтение остановлено.", 404)
        if automatic and self.schedule_store.load().paused:
            raise AppError("sync_paused", "Синхронизация приостановлена пользователем.", 409)

    def start_import(
        self,
        source_id: str | None = None,
        *,
        batches=None,
        automatic: bool = False,
        process_cards: bool = False,
        batch_limit: int = 10,
    ) -> dict:
        sources = self._import_sources()
        if source_id is not None:
            sources = [source for source in sources if source["id"] == source_id]
            if not sources:
                raise AppError(
                    "source_not_importable", "Источник не найден или отключён.", 404
                )
        if batches is not None:
            sources = [source for source in sources if source["id"] in batches]
        if not sources:
            raise AppError("no_memory_sources",
                           "Подключите источник или включите импорт писем "
                           "для Gmail / Яндекс Почты.")
        return self._begin(
            "import",
            lambda job_id: self._import(job_id, sources, batches, process_cards, batch_limit,
                                        automatic=automatic),
        )

    def _import(self, job_id, sources, batches, process_cards, batch_limit,
                *, automatic=False) -> None:
        sums = (
            "imported",
            "updated",
            "unchanged",
            "skipped",
            "files_processed",
            "files_unchanged",
            "files_skipped",
            "bytes_read",
        )
        stats = {
            **{key: 0 for key in sums},
            "errors": [],
            "warnings": [],
            "files_deferred": 0,
            "has_more": False,
        }
        unresolved = frozenset()
        remote_deferred = 0
        self._update(job_id, phase="import", total=len(sources), stats=stats)
        for index, source in enumerate(sources):
            batch = batches.get(source["id"]) if batches is not None else None
            eligible = set(batch.paths) if batch is not None else None
            remaining = frozenset()
            for _ in range(MAX_IMPORT_BATCHES):
                self._check_stop()
                if source["id"] not in {item["id"] for item in self._import_sources()}:
                    break
                kwargs = {"eligible_paths": eligible} if eligible is not None else {}
                if source["kind"] == "apple_notes":
                    result = import_apple_notes(
                        source, self.store, self.source_service.apple_notes_client,
                        check_stop=lambda sid=source["id"]: self._check_remote_import(
                            sid, automatic),
                    )
                elif source["kind"] == "outlook":
                    result = import_outlook(
                        source, self.store, self.source_service.outlook_client,
                        credentials_provider=lambda sid=source["id"]:
                        self.source_service.outlook_credentials(sid),
                        check_stop=lambda sid=source["id"]: self._check_remote_import(
                            sid, automatic),
                    )
                elif source["kind"] == "notion":
                    result = import_notion(
                        source, self.store, self.source_service.notion_client,
                        self.source_service.notion_token(source["id"]),
                        check_stop=lambda sid=source["id"]: self._check_remote_import(
                            sid, automatic),
                    )
                elif source["kind"] == "github":
                    result = import_github(
                        source, self.store, self.source_service.github_client,
                        self.source_service.github_token(source["id"]),
                        check_stop=lambda sid=source["id"]: self._check_remote_import(
                            sid, automatic),
                    )
                elif source["kind"] == "imap":
                    result = import_mail(
                        source, self.store, self.source_service.mail_client,
                        self.source_service.mail_password(source["id"]),
                        check_stop=lambda sid=source["id"]: self._check_remote_import(
                            sid, automatic),
                    )
                else:
                    result = self.importer(source, self.store, **kwargs)
                if (source["kind"] in ("notion", "github", "imap", "apple_notes", "outlook")
                        and result.get("has_more")):
                    remote_deferred += max(1, result.get("files_deferred", 0))
                stats = {
                    **stats,
                    **{key: stats[key] + result.get(key, 0) for key in sums},
                    "errors": [
                        *stats["errors"],
                        *[redact_text(str(error))[:300] for error in result.get("errors", [])],
                    ][:100],
                    "warnings": [
                        *stats["warnings"],
                        *[
                            redact_text(str(warning))[:300]
                            for warning in result.get("warnings", [])
                        ],
                    ][:100],
                }
                if batch is not None:
                    processed = result.get("processed_paths")
                    if processed is None and not result.get("errors"):
                        processed = batch.paths
                    self.watcher.mark_imported(batch, paths=frozenset(processed or ()))
                remaining = frozenset(result.get("deferred_paths", ()))
                self._update(
                    job_id,
                    processed=index,
                    stats={
                        **stats,
                        "files_deferred": len(unresolved | remaining) + remote_deferred,
                        "has_more": bool(unresolved | remaining) or bool(remote_deferred),
                    },
                )
                if not result.get("has_more") or not remaining:
                    break
                eligible = set(remaining)
            unresolved = unresolved | remaining
            stats = {**stats, "files_deferred": len(unresolved) + remote_deferred,
                     "has_more": bool(unresolved) or bool(remote_deferred)}
            if remaining:
                stats = {
                    **stats,
                    "warnings": [
                        *stats["warnings"],
                        "Часть архива отложена до следующего ограниченного прохода.",
                    ][:100],
                }
            self._update(job_id, processed=index + 1, stats=stats)
        if process_cards:
            self._cards(job_id, batch_limit, retry_errors=False, import_stats=stats,
                        automatic_retries=True)

    def retry_all_card_errors(self) -> dict:
        with self._lock:
            if self._stop.is_set():
                raise AppError("memory_stopping", "Служба памяти завершает работу.", 503)
            if self._active_job is not None:
                raise AppError("memory_busy", "Дождитесь завершения текущей операции памяти.", 409)
            config = self.schedule_store.load()
            state = self._retry_processing_state(config)
            lease = acquire_writer_lease(self.schedule_store.path.parent)
            try:
                requeued = self.store.requeue_card_errors(allowed_source_ids=self._allowed())
            finally:
                release_writer_lease(lease)
            state = state if requeued else "empty"
            job_id = None
            if state == "started":
                try:
                    job_id = self.start_cards(
                        limit=config.batch_limit, automatic=True,
                    )["job_id"]
                except AppError as error:
                    if error.code == "provider_paused":
                        state = "waiting_provider"
                    else:
                        logger.warning("Requeued cards await worker (%s)", error.code)
                        state = "waiting_worker"
            return {"requeued": requeued, "processing_state": state, "job_id": job_id}

    def _retry_processing_state(self, config):
        if config.paused:
            return "paused"
        if not config.process_cards or not (config.enabled or config.auto_import_enabled):
            return "automation_disabled"
        if self._provider_scope() is None:
            return "model_required"
        if self._provider_status(config)["blocked"]:
            return "waiting_provider"
        return "started"

    def start_cards(self, limit: int = 10, retry_errors: bool = False, *, automatic=False) -> dict:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise AppError("invalid_batch", "Размер пакета должен быть от 1 до 50.")
        self.provider_backoff.ensure_ready(self._provider_scope())
        return self._begin("cards", lambda job_id: self._cards(
            job_id, limit, retry_errors, automatic_retries=automatic
        ))

    def _cards(self, job_id, limit, retry_errors, import_stats=None, *, automatic_retries=False):
        self.store.recover_card_attempts()
        pending = self.store.pending(
            limit=limit, retry_errors=retry_errors, allowed_source_ids=self._allowed(),
            automatic_retries=automatic_retries, visibility=self._processing_scope(),
        )
        batch_scope = self._provider_scope()
        provider = self._provider_status()
        concurrency = provider["effective_concurrency"]
        limited = threading.Event()
        stats = {"ready": 0, "errors": 0, "stale": 0, "usage": {}}
        processed = 0
        self._update(job_id, phase="cards", total=len(pending), processed=0,
                     concurrency=concurrency, in_flight=0,
                     stats={**stats, "import": import_stats} if import_stats else stats)

        def can_start():
            with self._lock:
                return (self._provider_scope() == batch_scope
                        and not self._stop.is_set() and not limited.is_set()) and (
                    not automatic_retries or self._automatic_cards_enabled()
                ) and not self._provider_status()["blocked"]

        def process(record):
            result = self._process_card(record, expected_scope=batch_scope,
                                        manual=retry_errors, automatic=automatic_retries)
            if result.get("provider_limited"):
                limited.set()
            return result

        def settled(result, error):
            nonlocal stats, processed
            result = result if error is None else {"ready": 0, "errors": 1, "stale": 0}
            stats = _add_usage(stats, result.get("usage", {}))
            stats = {**stats, **{key: stats[key] + result[key]
                                for key in ("ready", "errors", "stale")}}
            processed += 1
            self._update(job_id, processed=processed,
                         stats={**stats, "import": import_stats} if import_stats else stats)

        run_card_window(pending, concurrency, process, can_start, settled,
                        lambda count: self._update(job_id, in_flight=count))

    def _automatic_cards_enabled(self):
        config = self.schedule_store.load()
        return (not config.paused and config.process_cards
                and (config.enabled or config.auto_import_enabled))

    def _process_card(self, record, *, expected_scope, manual=False, automatic=False):
        stats = {"ready": 0, "errors": 0, "stale": 0, "usage": {}}
        scope = expected_scope
        processing_scope = self._processing_scope()
        if self._provider_scope() != scope:
            return {**stats, "stale": 1}
        try:
            self.provider_backoff.ensure_ready(scope)
        except AppError as error:
            if error.code == "provider_paused":
                return {**stats, "provider_limited": True}
            raise
        if self._current_candidate(record, processing_scope) is None:
            return {**stats, "stale": 1}
        token = self.store.claim_card_attempt(
            record["id"], record["revision"], manual=manual, allowed_source_ids=self._allowed(),
            visibility=processing_scope,
        )
        if token is None:
            return {**stats, "stale": 1}
        called_provider = False
        try:
            # Admission shares the pause lock, but network work never holds it.
            self._admit_provider(scope, record, automatic, processing_scope)
            called_provider = True
            generate = getattr(self.generator, "generate_with_admission", None)
            result = (generate(record, lambda: self._admit_provider(
                scope, record, automatic, processing_scope))
                      if callable(generate) else self.generator.generate(record))
            stats = _add_usage(stats, result.get("usage", {}))
            self.provider_backoff.success(scope, self.schedule_store.load().card_concurrency)
            active = (self.generator.is_current(result)
                      if hasattr(self.generator, "is_current") else True)
            if (not active or self._provider_scope() != scope
                    or self._current_candidate(record, processing_scope) is None):
                return {**stats, "stale": 1}
            card = validate_card(result["card"], redact_text(record["text"]))
            saved = self.store.save_card(
                record["id"], record["revision"], card, model=result.get("model", ""),
                prompt_version=result.get("prompt_version", "1"), attempt_token=token,
                processing_scope=processing_scope,
            )
            return {**stats, "ready": int(saved), "stale": int(not saved)}
        except Exception as error:
            if getattr(error, "request_sent", None) is False:
                called_provider = False
                if getattr(error, "code", None) == "provider_paused":
                    return {**stats, "provider_limited": True}
                raise
            if not called_provider:
                raise
            throttled = getattr(error, "code", None) == "rate_limited"
            if throttled:
                self.provider_backoff.limit(scope, error)
            stats = _add_usage(stats, getattr(error, "usage", {}))
            if (self._provider_scope() != scope
                    or self._current_candidate(record, processing_scope) is None):
                return {**stats, "stale": 1, "provider_limited": throttled}
            self.store.record_card_error(record["id"], record["revision"], _safe_error(error),
                                         attempt_token=token, processing_scope=processing_scope)
            return {**stats, "errors": 1, "provider_limited": throttled}
        finally:
            self.store.release_card_attempt(record["id"], record["revision"], token,
                                            refund=not called_provider)

    def _admit_provider(self, scope, record, automatic, processing_scope=None):
        with self._lock:
            if (self._stop.is_set() or self._provider_scope() != scope
                    or self._current_candidate(record, processing_scope) is None
                    or (automatic and not self._automatic_cards_enabled())):
                raise paused_error()
            self.provider_backoff.ensure_ready(scope)

    def _processing_scope(self):
        scope = getattr(self.generator, "processing_scope", None)
        if callable(scope):
            return scope()
        settings = self.settings_service.store.load()
        return "model_local" if settings.provider == "ollama" else "model_external"

    def _current_candidate(self, record: dict, processing_scope=None) -> dict | None:
        try:
            current = self.store.get_record(record["id"], allowed_source_ids=self._allowed(),
                                            visibility=processing_scope or self._processing_scope())
        except AppError as error:
            if error.status_code == 404:
                return None
            raise
        if current["revision"] != record["revision"] or current.get("card_status") == "ready":
            return None
        return current

    def disconnect_source(self, source_id: str) -> dict:
        with self.source_service._lock:
            self.source_service._find(self.source_service.store.load(), source_id)
            self.store.delete_source(source_id)
            try:
                return self.source_service.delete(source_id)
            except AppError as error:
                raise AppError(
                    error.code,
                    "Данные источника уже исключены из памяти. "
                    "Не удалось удалить подключение; повторите удаление. "
                    "Исходные файлы и письма сохранены.",
                    error.status_code,
                ) from error

    def tick(self, now: float | None = None) -> None:
        cleanup = getattr(self.source_service, "cleanup_auth", None)
        if cleanup is not None:
            cleanup()
        if self._stop.is_set():
            return
        config = self.schedule_store.load()
        if config.paused:
            return
        now = time.time() if now is None else now
        with self._lock:
            if self._active_job is not None:
                return
            self._last_auto_check = _now()
        batches = {}
        if config.auto_import_enabled:
            for source in self._import_sources():
                try:
                    batch = self.watcher.collect(
                        source, now=now, allowed_path=lambda path, sid=source["id"]:
                        self.store.import_path_allowed(sid, path.as_posix()))
                    if batch.paths:
                        batches = {**batches, source["id"]: batch}
                except AppError as error:
                    self._background_error = error.message
        with self._lock:
            config = self.schedule_store.load()
            if config.paused or self._stop.is_set() or self._active_job is not None:
                return
            if batches and config.auto_import_enabled:
                self.start_import(
                    batches=batches, process_cards=config.process_cards, automatic=True,
                    batch_limit=config.batch_limit,
                )
            elif (self._automatic_cards_enabled() and not self._provider_status()["blocked"]
                  and self.store.has_due_card_retries(
                now=now, allowed_source_ids=self._allowed()
            )):
                self.start_cards(limit=config.batch_limit, automatic=True)
            elif config.enabled and now >= self._next_reconcile:
                self._next_reconcile = now + config.interval_minutes * 60
                if self._import_sources():
                    self.start_import(
                        process_cards=config.process_cards, batch_limit=config.batch_limit,
                        automatic=True,
                    )
            elif self.embeddings.vectors.pending(limit=1, allowed_source_ids=self._allowed()):
                self.start_embedding_index(limit=8)
            elif self.store.fact_projection_pending(allowed_source_ids=self._allowed()):
                self.start_fact_projection(limit=100, automatic=True)

    def start_background(self) -> None:
        self.runner.start()

    async def close(self) -> None:
        self._stop.set()
        await self.runner.close()
        if self._thread is not None and self._thread.is_alive():
            await asyncio.to_thread(self._thread.join, 0.2)
