from test_memory_service_api import memory_client as memory_client
from test_memory_service_api import wait_job
from test_settings_api import assert_success

from brain_settings import import_files
from brain_settings.background import SessionWatcher


def test_manual_job_drains_bounded_batches_with_true_cumulative_statistics(
    memory_client,
    tmp_path,
    monkeypatch,
):
    client, headers, llm = memory_client
    root = tmp_path / "notes"
    root.mkdir()
    for index in range(3):
        (root / f"note-{index}.md").write_text(f"Note {index}: " + "x" * 300)
    monkeypatch.setattr(import_files, "MAX_TOTAL_BYTES", 500)
    source = assert_success(
        client.post(
            "/api/sources",
            json={
                "kind": "folder",
                "name": "Batch notes",
                "path": str(root),
            },
            headers=headers,
        )
    )["source"]
    job_id = assert_success(
        client.post(
            "/api/memory/import",
            json={
                "source_id": source["id"],
            },
            headers=headers,
        )
    )["job_id"]
    job = wait_job(client, job_id)
    assert job["status"] == "completed"
    assert job["stats"]["imported"] == 3
    assert job["stats"]["files_processed"] == 3
    assert job["stats"]["has_more"] is False
    assert assert_success(client.get("/api/memory/status"))["records"] == 3
    assert llm.calls == ()


def test_watcher_acknowledges_only_paths_actually_processed(tmp_path):
    import os

    for name in ("first.jsonl", "deferred.jsonl"):
        path = tmp_path / name
        path.write_text("{}\n")
        os.utime(path, (1000, 1000))
    source = {"id": "source", "kind": "codex", "path": str(tmp_path), "enabled": True}
    watcher = SessionWatcher()
    batch = watcher.collect(source, now=1200)
    watcher.mark_imported(batch, paths=frozenset({str(tmp_path / "first.jsonl")}))
    assert watcher.collect(source, now=1200).paths == frozenset({str(tmp_path / "deferred.jsonl")})


def test_automatic_retry_recovers_read_failure_without_file_metadata_change(
    memory_client,
    tmp_path,
    monkeypatch,
):
    import json
    import os

    from test_importers_batches import message

    from brain_settings import import_batch

    client, headers, llm = memory_client
    root = tmp_path / "sessions"
    root.mkdir()
    path = root / "retry.jsonl"
    path.write_text(json.dumps(message("assistant", "Recovered automatic session")) + "\n")
    os.utime(path, (1000, 1000))
    assert_success(
        client.post(
            "/api/sources",
            json={
                "kind": "codex",
                "name": "Retry sessions",
                "path": str(root),
            },
            headers=headers,
        )
    )
    original = import_batch.os.open
    failed = False

    def transient(name, flags, *args, **kwargs):
        nonlocal failed
        if name == "retry.jsonl" and not failed:
            failed = True
            raise PermissionError("Synthetic transient failure")
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(import_batch.os, "open", transient)
    service = client.app.state.memory_service
    service.tick(now=1200)
    first_id = assert_success(client.get("/api/memory/status"))["jobs"][0]["id"]
    assert wait_job(client, first_id)["stats"]["files_skipped"] == 1
    service.tick(now=1200)
    second_id = assert_success(client.get("/api/memory/status"))["jobs"][0]["id"]
    assert second_id != first_id
    assert wait_job(client, second_id)["stats"]["imported"] == 1
    assert llm.calls == ()


def test_automatic_retry_never_acknowledges_a_failed_database_commit(
    memory_client,
    tmp_path,
    monkeypatch,
):
    import json
    import os

    from test_importers_batches import message

    from brain_settings.errors import AppError

    client, headers, _llm = memory_client
    root = tmp_path / "sessions"
    root.mkdir()
    path = root / "retry.jsonl"
    path.write_text(json.dumps(message("assistant", "Durable answer")) + "\n")
    os.utime(path, (1000, 1000))
    assert_success(
        client.post(
            "/api/sources",
            json={
                "kind": "codex",
                "name": "Commit retry",
                "path": str(root),
            },
            headers=headers,
        )
    )
    service = client.app.state.memory_service
    original = service.store.replace_file_documents
    failed = False

    def transient(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise AppError("memory_unavailable", "Synthetic transient commit failure", 503)
        return original(*args, **kwargs)

    monkeypatch.setattr(service.store, "replace_file_documents", transient)
    service.tick(now=1200)
    first_id = assert_success(client.get("/api/memory/status"))["jobs"][0]["id"]
    wait_job(client, first_id)
    assert service.store.status()["documents"] == 0
    service.tick(now=1200)
    second_id = assert_success(client.get("/api/memory/status"))["jobs"][0]["id"]
    assert second_id != first_id
    assert wait_job(client, second_id)["stats"]["imported"] == 1
