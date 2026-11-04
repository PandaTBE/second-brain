import json

from brain_settings import import_batch, import_files
from brain_settings.importers import import_source
from brain_settings.memory_store import MemoryStore


def configured(root, kind="folder"):
    return {"id": "batch-source", "kind": kind, "path": str(root), "name": "Batch source"}


def test_budget_batches_keep_progress_across_restarts_and_never_report_false_zero(
    tmp_path, monkeypatch
):
    root = tmp_path / "notes"
    root.mkdir()
    for index in range(3):
        (root / f"note-{index}.md").write_text(f"Note {index}: " + "x" * 300)
    monkeypatch.setattr(import_files, "MAX_TOTAL_BYTES", 500)
    data = tmp_path / "data"
    first = import_source(configured(root), MemoryStore(data))
    assert first["imported"] == 1
    assert first["files_processed"] == 1
    assert first["has_more"] is True
    assert first["bytes_read"] <= 500
    second = import_source(configured(root), MemoryStore(data))
    assert second["imported"] == 1
    assert second["has_more"] is True
    third = import_source(configured(root), MemoryStore(data))
    assert third["imported"] == 1
    assert third["has_more"] is False
    assert MemoryStore(data).status()["documents"] == 3
    unchanged = import_source(configured(root), MemoryStore(data))
    assert unchanged["unchanged"] == 3
    assert unchanged["files_unchanged"] == 3
    assert unchanged["bytes_read"] == 0


def test_session_archives_have_no_aggregate_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(import_files, "MAX_TOTAL_BYTES", 1000)
    monkeypatch.setattr(import_files, "MAX_FILE_BYTES", 100)
    for kind in ("codex", "claude"):
        root = tmp_path / kind
        root.mkdir()
        for index in range(3):
            text = f"Session {index}: " + "x" * 300
            record = (
                message("user", text)
                if kind == "codex"
                else {"type": "user", "message": {"role": "user", "content": text}}
            )
            (root / f"session-{index}.jsonl").write_text(json.dumps(record) + "\n")
        expected_bytes = sum(path.stat().st_size for path in root.iterdir())
        assert expected_bytes > import_files.MAX_TOTAL_BYTES
        data = tmp_path / f"data-{kind}"
        first = import_source(configured(root, kind), MemoryStore(data))
        assert first["imported"] == 3
        assert first["files_processed"] == 3
        assert first["bytes_read"] == expected_bytes
        assert first["has_more"] is False
        assert first["files_deferred"] == 0
        assert first["errors"] == []
        assert MemoryStore(data).status()["documents"] == 3
        repeated = import_source(configured(root, kind), MemoryStore(data))
        assert repeated["files_unchanged"] == 3
        assert repeated["bytes_read"] == 0


def test_session_per_file_bound_remains_after_removing_archive_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(import_batch, "MAX_RAW_SESSION_BYTES", 256)
    for kind in ("codex", "claude"):
        root = tmp_path / kind
        root.mkdir()
        (root / "oversized.jsonl").write_text(json.dumps({"text": "x" * 300}) + "\n")
        events = list(import_files.read_source_files(root, kind))
        assert len(events) == 1
        assert events[0].state == "skipped"
        assert events[0].bytes_read == 0
        assert events[0].warnings


def test_eligible_subset_opens_only_selected_paths_without_enumerating_archive(
    tmp_path, monkeypatch
):
    root = tmp_path / "archive"
    root.mkdir()
    chosen = root / "chosen.md"
    chosen.write_text("Selected note")
    (root / "unrelated.md").write_text("x" * 100_000)

    def forbidden(*args, **kwargs):
        raise AssertionError("An eligible subset must not enumerate the whole archive")

    monkeypatch.setattr(import_files.os, "scandir", forbidden)
    store = MemoryStore(tmp_path / "data")
    result = import_source(configured(root), store, eligible_paths={str(chosen)})
    assert result["imported"] == 1
    assert result["bytes_read"] == chosen.stat().st_size


def message(role, text):
    return {
        "type": "response_item",
        "timestamp": "2026-01-01T00:00:00Z",
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
        },
    }


def test_large_jsonl_streams_dialogue_skips_large_tool_lines_and_retains_line_citations(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "sessions"
    root.mkdir()
    rows = [
        message("user", "Вопрос про устойчивость системы"),
        {
            "type": "response_item",
            "payload": {"type": "function_call_output", "output": "OPAQUE_TOOL_MATERIAL" * 500},
        },
        message("assistant", "Ответ: используем очередь и повторные попытки"),
    ]
    path = root / "large.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    monkeypatch.setattr(import_files, "MAX_FILE_BYTES", 1024)
    from brain_settings import session_stream

    monkeypatch.setattr(session_stream, "MAX_LINE_BYTES", 1024)
    store = MemoryStore(tmp_path / "data")
    stats = import_source(configured(root, "codex"), store)
    assert stats["imported"] == 1
    assert stats["warnings"]
    record = store.search("")[0]
    assert "Вопрос про устойчивость" in record["text"]
    assert "используем очередь" in record["text"]
    assert "OPAQUE_TOOL_MATERIAL" not in record["text"]
    assert record["source_uri"].endswith("#L1-L3")


def test_file_error_does_not_erase_previous_batch_stats_or_starve_other_files(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "first.md").write_text("Successful note")
    (root / "second.txt").write_bytes(b"\xff\xfe\xff")
    (root / "third.md").write_text("Another successful note")
    store = MemoryStore(tmp_path / "data")
    result = import_source(configured(root), store)
    assert result["imported"] == 2
    assert result["errors"]
    assert store.status()["documents"] == 2


def test_stream_parse_failure_still_counts_bytes_against_batch_budget(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    root.mkdir()
    path = root / "bad.jsonl"
    path.write_text(json.dumps({"type": "metadata", "value": "x" * 200}) + "\n{broken\n")
    monkeypatch.setattr(import_files, "MAX_FILE_BYTES", 100)
    events = list(import_files.read_source_files(root, "codex"))
    assert events[0].state == "error"
    assert events[0].bytes_read == path.stat().st_size


def test_manual_retry_recovers_transient_read_error_without_metadata_change(tmp_path, monkeypatch):
    from brain_settings import import_batch

    root = tmp_path / "notes"
    root.mkdir()
    (root / "retry.md").write_text("Recovered note")
    store = MemoryStore(tmp_path / "data")
    original = import_batch.os.open
    failed = False

    def transient(path, flags, *args, **kwargs):
        nonlocal failed
        if path == "retry.md" and not failed:
            failed = True
            raise PermissionError("Synthetic transient access failure")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(import_batch.os, "open", transient)
    first = import_source(configured(root), store)
    assert first["errors"] and first["imported"] == 0
    assert first["processed_paths"] == []
    second = import_source(configured(root), store)
    assert second["imported"] == 1
    assert store.status()["documents"] == 1


def test_streaming_deadline_also_bounds_drain_of_oversized_line(monkeypatch):
    from io import BytesIO

    from brain_settings import session_stream

    first = (json.dumps(message("assistant", "Preserved answer")) + "\n").encode()
    raw = first + b"x" * 10_000 + b"\n"
    stream = BytesIO(raw)
    monkeypatch.setattr(session_stream, "MAX_LINE_BYTES", 256)
    monkeypatch.setattr(
        session_stream.time, "monotonic", lambda: 21 if stream.tell() >= len(first) + 257 else 0
    )
    result = session_stream.read_session(stream, "codex", len(raw))
    assert result.complete is False
    assert result.turns and "Preserved answer" in result.turns[0].text
    assert result.bytes_read < len(raw)
    assert any("времен" in warning for warning in result.warnings)


def test_checkpoint_failure_preserves_already_committed_document_counts(tmp_path, monkeypatch):
    from brain_settings.errors import AppError
    from brain_settings.import_progress import ImportProgress

    root = tmp_path / "notes"
    root.mkdir()
    (root / "note.md").write_text("Committed note")
    store = MemoryStore(tmp_path / "data")

    def failed(*args, **kwargs):
        raise AppError("memory_unavailable", "Synthetic checkpoint write failure", 503)

    monkeypatch.setattr(ImportProgress, "save", failed)
    result = import_source(configured(root), store)
    assert store.status()["documents"] == 1
    assert result["imported"] == 1
    assert result["files_processed"] == 1
    assert result["files_skipped"] == 0
    assert result["errors"]


def test_assignment_filename_is_redacted_before_uri_encoding_and_database_write(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    secret = "synthetic-" + "filename-credential"
    (root / f"api_key={secret}.md").write_text("Safe note body")
    data = tmp_path / "data"
    store = MemoryStore(data)
    import_source(configured(root), store)
    row = store.search("")[0]
    assert secret not in row["source_uri"]
    for path in data.glob("memory.sqlite3*"):
        assert secret.encode() not in path.read_bytes()


def test_later_traversal_error_preserves_retryable_partial_commit_without_ack_paths(
    tmp_path,
    monkeypatch,
):
    from brain_settings import importers
    from brain_settings.dialogue_parsers import Turn
    from brain_settings.errors import AppError
    from brain_settings.import_batch import ImportRead

    root = tmp_path / "sessions"
    root.mkdir()
    store = MemoryStore(tmp_path / "data")

    def events(*args, **kwargs):
        yield ImportRead(
            relative=type(root)("partial.jsonl"),
            raw=None,
            modified=1000,
            signature=(1, 1, 20),
            turns=(Turn("Ассистент: Сохранённый ответ", 1, 1),),
            complete=False,
            retryable=True,
            bytes_read=20,
        )
        raise AppError("import_limit", "Synthetic later traversal limit")

    monkeypatch.setattr(importers, "read_source_files", events)
    result = import_source(configured(root, "codex"), store)
    assert store.status()["documents"] == 1
    assert result["imported"] == 1
    assert result["files_processed"] == 1
    assert result["processed_paths"] == []
    assert result["errors"]


def test_eligible_metadata_access_failure_is_unacknowledged_and_retryable(tmp_path, monkeypatch):
    from brain_settings import import_batch

    root = tmp_path / "notes"
    root.mkdir()
    path = root / "retry.md"
    path.write_text("Eligible recovered note")
    store = MemoryStore(tmp_path / "data")
    original = import_batch.os.stat
    failed = False

    def transient(name, *args, **kwargs):
        nonlocal failed
        if name == "retry.md" and not failed:
            failed = True
            raise PermissionError("Synthetic metadata access failure")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(import_batch.os, "stat", transient)
    first = import_source(configured(root), store, eligible_paths={str(path)})
    assert first["errors"]
    assert first["processed_paths"] == []
    second = import_source(configured(root), store, eligible_paths={str(path)})
    assert second["imported"] == 1
