"""Real worker orchestration with deterministic fake-provider barriers."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_card_retry_service import await_idle
from test_llm_cards import EVIDENCE, cards_fixture, valid_card
from test_memory_service_api import add_text_source, memory_client, wait_job

from brain_settings.background import ScheduleConfig, ScheduleStore, acquire_writer_lease
from brain_settings.errors import AppError

__all__ = ["memory_client", "cards_fixture"]


def prepare(memory_client, tmp_path, *, concurrency=3, count=6):
    client, headers, _ = memory_client
    source = add_text_source(client, headers, tmp_path / "notes")
    service = client.app.state.memory_service
    for index in range(count):
        service.store.upsert_document(**{
            "source_id": source["id"], "source_kind": "folder",
            "source_object_id": f"parallel-{index}", "title": f"Synthetic {index}",
            "text": EVIDENCE, "source_uri": f"file:///fixture/{index}.md",
        })
    service.update_schedule(ScheduleConfig(
        enabled=True, auto_import_enabled=False, process_cards=True,
        card_concurrency=concurrency,
    ))
    service._next_reconcile = float("inf")
    return client, headers, service


class BlockingProvider:
    def __init__(self):
        self.condition = threading.Condition()
        self.calls = []
        self.release = {}
        self.active = 0
        self.maximum = 0
        self.release_all = threading.Event()

    def generate(self, record):
        identifier = record["id"]
        with self.condition:
            self.calls.append(identifier)
            self.release[identifier] = threading.Event()
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.condition.notify_all()
        try:
            while not self.release_all.is_set():
                if self.release[identifier].wait(timeout=0.01):
                    break
            return {"card": valid_card(), "model": "synthetic/parallel",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def wait_calls(self, count):
        with self.condition:
            assert self.condition.wait_for(lambda: len(self.calls) >= count, timeout=3)


def test_rolling_pool_overlaps_three_refills_one_and_keeps_total_batch(memory_client, tmp_path):
    client, _, service = prepare(memory_client, tmp_path)
    provider = service.generator = BlockingProvider()
    window_reported = threading.Event()
    update = service._update

    def observe_update(job_id, **values):
        update(job_id, **values)
        if values.get("in_flight") == 3:
            window_reported.set()

    service._update = observe_update
    job_id = service.start_cards(limit=5)["job_id"]
    try:
        provider.wait_calls(3)
        assert window_reported.wait(timeout=3)
        assert len(provider.calls) == provider.active == 3
        assert service.job(job_id)["job"]["in_flight"] == 3
        attempts = [record["card_attempts"] for record in service.store.search("", limit=10)]
        assert sorted(attempts) == [0, 0, 0, 1, 1, 1]
        window_reported.clear()
        provider.release[provider.calls[1]].set()
        provider.wait_calls(4)
        assert window_reported.wait(timeout=3)
        job = service.job(job_id)["job"]
        assert job["processed"] == 1 and job["stats"]["ready"] == 1
        assert job["in_flight"] == provider.maximum == 3
    finally:
        provider.release_all.set()
    job = wait_job(client, job_id)
    await_idle(service)
    assert job["status"] == "completed" and job["processed"] == 5
    assert len(provider.calls) == 5 and provider.maximum == 3
    assert job["concurrency"] == 3 and job["in_flight"] == 0
    assert job["stats"]["ready"] == 5 and job["stats"]["usage"]["total_tokens"] == 75
    assert service.store.status()["pending"] == 1


def test_parallel_pause_drains_admitted_requests_without_refilling(memory_client, tmp_path):
    client, _, service = prepare(memory_client, tmp_path, count=8)
    provider = service.generator = BlockingProvider()
    job_id = service.start_cards(limit=8, automatic=True)["job_id"]
    try:
        provider.wait_calls(3)
        service.set_sync_paused(True)
    finally:
        provider.release_all.set()
    job = wait_job(client, job_id)
    await_idle(service)
    assert job["status"] == "completed" and job["processed"] == 3
    assert len(provider.calls) == 3 and job["stats"]["ready"] == 3
    assert [record["card_attempts"] for record in service.store.pending(limit=10)] == [0] * 5


def test_parallel_failure_keeps_writer_lease_until_other_requests_settle(memory_client, tmp_path):
    client, _, service = prepare(memory_client, tmp_path, count=6)
    provider = BlockingProvider()
    original = service._process_card
    entered = threading.Barrier(3)
    failure_reached = threading.Event()
    ordinal_lock = threading.Lock()
    ordinals = []

    def process(record, **kwargs):
        with ordinal_lock:
            ordinal = len(ordinals)
            ordinals.append(record["id"])
        entered.wait(timeout=3)
        if ordinal == 0:
            failure_reached.set()
            raise AppError("synthetic_failure", "Synthetic pre-provider failure")
        return original(record, **kwargs)

    service.generator = provider
    service._process_card = process
    job_id = service.start_cards(limit=6)["job_id"]
    try:
        assert failure_reached.wait(timeout=3)
        provider.wait_calls(2)
        assert service.job(job_id)["job"]["status"] == "running"
        with pytest.raises(AppError):
            acquire_writer_lease(service.schedule_store.path.parent)
    finally:
        provider.release_all.set()
    job = wait_job(client, job_id)
    await_idle(service)
    assert job["status"] == "error" and job["processed"] == 3
    assert len(provider.calls) == 2 and len(ordinals) == 3
    assert job["stats"]["ready"] == 2 and job["in_flight"] == 0


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "10", None])
def test_card_concurrency_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        ScheduleConfig(card_concurrency=value)


def test_legacy_schedule_updates_preserve_configured_parallelism(memory_client):
    client, _, _ = memory_client
    service = client.app.state.memory_service
    service.update_schedule(ScheduleConfig(card_concurrency=1000, paused=True))
    service.update_schedule(ScheduleConfig(batch_limit=4))
    assert service.schedule_store.load().card_concurrency == 1000
    assert service.schedule_store.load().paused

def test_missing_concurrency_in_legacy_preferences_defaults_to_ten(tmp_path):
    store = ScheduleStore(tmp_path)
    assert store.load().card_concurrency == 10
    legacy = ScheduleConfig(batch_limit=7, process_cards=True)
    store.path.write_text(legacy.model_dump_json(exclude={"card_concurrency"}))
    loaded = store.load()
    assert loaded.card_concurrency == 10
    assert loaded.batch_limit == 7 and loaded.process_cards


def test_huge_parallelism_admits_only_available_batch_materials(memory_client, tmp_path):
    client, _, service = prepare(
        memory_client, tmp_path, concurrency=1_000_000, count=4,
    )
    provider = service.generator = BlockingProvider()
    job_id = service.start_cards(limit=3)["job_id"]
    try:
        provider.wait_calls(3)
        assert provider.active == provider.maximum == 3
        assert len(provider.calls) == 3
    finally:
        provider.release_all.set()
    job = wait_job(client, job_id)
    await_idle(service)
    assert job["status"] == "completed" and len(provider.calls) == 3
    assert job["concurrency"] == 1_000_000 and job["in_flight"] == 0
    assert service.store.status()["pending"] == 1


@pytest.mark.parametrize("concurrency", [1, 6, 10, 12])
def test_configured_parallelism_is_honored_within_the_total_batch(
    memory_client, tmp_path, concurrency,
):
    batch = max(8, concurrency)
    client, _, service = prepare(
        memory_client, tmp_path, concurrency=concurrency, count=batch + 1,
    )
    provider = service.generator = BlockingProvider()
    job_id = service.start_cards(limit=batch)["job_id"]
    try:
        provider.wait_calls(concurrency)
        assert provider.active == provider.maximum == concurrency
    finally:
        provider.release_all.set()
    job = wait_job(client, job_id)
    await_idle(service)
    assert job["status"] == "completed" and len(provider.calls) == batch
    assert provider.maximum == concurrency and service.store.status()["pending"] == 1


def test_stop_drains_workers_and_preserves_unstarted_budget(memory_client, tmp_path):
    client, _, service = prepare(memory_client, tmp_path, count=8)
    provider = service.generator = BlockingProvider()
    job_id = service.start_cards(limit=8)["job_id"]
    try:
        provider.wait_calls(3)
        service._stop.set()
        assert service.job(job_id)["job"]["status"] == "running"
        with pytest.raises(AppError):
            acquire_writer_lease(service.schedule_store.path.parent)
    finally:
        provider.release_all.set()
    job = wait_job(client, job_id)
    await_idle(service)
    assert job["status"] == "error" and job["processed"] == 3
    assert len(provider.calls) == 3 and job["stats"]["ready"] == 3
    assert [row["card_attempts"] for row in service.store.pending(limit=10)] == [0] * 5


def test_schedule_api_exposes_default_and_accepts_any_positive_parallelism(memory_client):
    client, headers, _ = memory_client
    service = client.app.state.memory_service
    assert client.get("/api/memory/status").json()["data"]["scheduler"]["card_concurrency"] == 10
    for value in [0, -1, True, False, 1.5, "10", None]:
        assert client.post("/api/memory/schedule", headers=headers,
                           json={"card_concurrency": value}).status_code == 422
    for value in [1, 6, 7, 99, 1000, 1_000_000]:
        response = client.post("/api/memory/schedule", headers=headers,
                               json={"card_concurrency": value})
        assert response.status_code == 200
        assert response.json()["data"]["scheduler"]["card_concurrency"] == value
        assert service.schedule_store.load().card_concurrency == value
        assert service.schedule_store.load().batch_limit == 10
    for paused in (True, False):
        response = client.post("/api/memory/sync", headers=headers, json={"paused": paused})
        assert response.status_code == 200
        assert response.json()["data"]["scheduler"]["card_concurrency"] == 1_000_000


def test_generator_serializes_credentials_but_not_provider_requests(cards_fixture, monkeypatch):
    settings, chat, generator, record = cards_fixture
    original_get, original_post = settings.vault.get, chat.post_json
    guard = threading.Lock()
    active, peak = [0], [0]
    network = threading.Barrier(3)

    def get(scope):
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        try:
            threading.Event().wait(0.02)
            return original_get(scope)
        finally:
            with guard:
                active[0] -= 1

    def post(*args, **kwargs):
        network.wait(timeout=3)
        with guard:
            return original_post(*args, **kwargs)

    monkeypatch.setattr(settings.vault, "get", get)
    monkeypatch.setattr(chat, "post_json", post)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(generator.generate, record) for _ in range(3)]
        assert all(future.result(timeout=4)["card"] == valid_card() for future in futures)
    assert peak[0] == 1 and len(chat.calls) == 3
