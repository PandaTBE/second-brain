"""Synthetic Qwen Code histories: dialogue only, confined local paths and quiet watch."""

import io
import json
import os

import pytest
from test_memory_service_api import wait_job
from test_settings_api import assert_success

from brain_settings.background import SessionWatcher
from brain_settings.dialogue_parsers import parse_session
from brain_settings.importers import import_source
from brain_settings.local_scan import scan_local
from brain_settings.mcp_server import MemoryTools
from brain_settings.memory_store import MemoryStore
from brain_settings.session_stream import read_session


def record(role, text, **extra):
    return {
        "type": role,
        "timestamp": "2026-11-01T12:00:00Z",
        "message": {"role": "model" if role == "assistant" else role, "parts": [{"text": text}]},
        **extra,
    }


def transcript(rows):
    return "".join(json.dumps(row) + "\n" for row in rows)


def session(root):
    path = root / "demo" / "chats" / "session.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        transcript(
            [
                record("user", "Qwen synthetic astronomy question"),
                record("assistant", "Synthetic astronomy answer"),
            ]
        )
    )
    return path


def test_qwen_parser_extracts_dialogue_but_never_thought_tools_or_injected_events():
    rows = [
        record("user", "Real synthetic question"),
        record("assistant", "Real synthetic answer"),
        record("user", "Mid turn", subtype="mid_turn_user_message"),
        record("system", "DO_NOT_IMPORT_SYSTEM"),
        record("tool_result", "DO_NOT_IMPORT_TOOL"),
        *[
            record("user", "DO_NOT_IMPORT_RUNTIME", subtype=subtype)
            for subtype in (
                "notification",
                "cron",
                "agent_mention",
                "agent_message",
                "goal_runtime",
            )
        ],
        {
            "type": "assistant",
            "message": {
                "role": "model",
                "parts": [
                    {"text": "DO_NOT_IMPORT_THOUGHT", "thought": True},
                    {"functionCall": {"args": {"text": "DO_NOT_IMPORT_CALL"}}},
                    {"text": "Visible answer"},
                ],
            },
        },
        {"type": "user", "message": {"role": "model", "parts": [{"text": "BAD_ROLE"}]}},
        record("user", "User text", systemPayload={"text": "DO_NOT_IMPORT_PAYLOAD"}),
    ]
    raw = transcript(rows)
    turns, complete, _ = parse_session(raw, "qwen")
    text = "\n".join(turn.text for turn in turns)
    assert complete and "Real synthetic question" in text and "Visible answer" in text
    assert "Mid turn" in text and "User text" in text
    assert "DO_NOT_IMPORT" not in text and "BAD_ROLE" not in text
    streamed = read_session(io.BytesIO(raw.encode()), "qwen", len(raw.encode()))
    assert streamed.turns == tuple(turns)


def test_qwen_source_api_import_search_mcp_and_idempotence(harness, tmp_path):
    root = tmp_path / "projects"
    path = session(root)
    response = harness.post(
        "/api/sources", {"kind": "qwen", "name": "Qwen Code", "path": str(root)}
    )
    assert response.status_code == 200
    source = response.json()["data"]["source"]
    checked = harness.post(f"/api/sources/{source['id']}/test", {})
    assert checked.status_code == 200 and checked.json()["data"]["source"]["item_count"] == 1
    job_id = assert_success(harness.post("/api/memory/import", {"source_id": source["id"]}))[
        "job_id"
    ]
    assert wait_job(harness.client, job_id)["status"] == "completed"
    results = assert_success(harness.client.get("/api/memory/search", params={"q": "astronomy"}))
    assert results["results"][0]["source_kind"] == "qwen"
    store = harness.client.app.state.memory_service.store
    assert import_source(source, store)["unchanged"] == 1
    result = MemoryTools(store, harness.data_dir).search_memory(query="astronomy")
    assert result["results"][0]["source_kind"] == "qwen"
    assert path.read_text().endswith("\n")


@pytest.mark.parametrize("selection", ["runtime", "projects", "project", "chats", "archive"])
def test_qwen_reads_only_chat_locations_and_counts_the_same_files(tmp_path, selection):
    runtime = tmp_path / "runtime"
    projects = runtime / "projects"
    path = session(projects)
    archive = path.parent / "archive"
    archive.mkdir()
    (archive / "old.jsonl").write_text(transcript([record("user", "Archived synthetic dialogue")]))
    (runtime / "auth.jsonl").write_text(transcript([record("user", "DO_NOT_IMPORT_AUTH")]))
    (projects / "demo" / "credentials.jsonl").write_text(
        transcript([record("user", "DO_NOT_IMPORT_PROFILE")])
    )
    (path.parent / "notes.md").write_text("DO_NOT_IMPORT_NOTE")
    (path.parent / "auth.jsonl").write_text(
        transcript([record("user", "DO_NOT_IMPORT_AUTH_IN_CHATS")])
    )
    (path.parent / "linked.jsonl").symlink_to(runtime / "auth.jsonl")
    root = {
        "runtime": runtime,
        "projects": projects,
        "project": projects / "demo",
        "chats": path.parent,
        "archive": archive,
    }[selection]
    source = {"id": "qwen-synthetic", "kind": "qwen", "path": str(root)}
    assert scan_local(str(root), "qwen") == (1 if selection == "archive" else 2)
    store = MemoryStore(tmp_path / "memory")
    stats = import_source(source, store)
    assert not stats["errors"]
    text = "\n".join(item["text"] for item in store.search(""))
    assert "Archived synthetic dialogue" in text and "DO_NOT_IMPORT" not in text


def test_qwen_watch_waits_for_quiet_and_ignores_unrelated_files(tmp_path):
    root = tmp_path / "projects"
    path = session(root)
    with path.open("a") as stream:
        stream.write(transcript([{"type": "assistant", "message": {"stop_reason": "end_turn"}}]))
    unrelated = root / "auth.jsonl"
    unrelated.write_text("{}\n")
    os.utime(path, (1000, 1000))
    os.utime(unrelated, (500, 500))
    source = {"id": "qwen-synthetic", "kind": "qwen", "path": str(root)}
    watcher = SessionWatcher(quiet_seconds=90)
    assert not watcher.collect(source, now=1050).paths
    batch = watcher.collect(source, now=1091)
    assert batch.paths == frozenset({str(path)})
    watcher.mark_imported(batch)
    assert not watcher.collect(source, now=1200).paths


def test_qwen_partial_tail_is_deferred_and_forced_stream_matches(tmp_path, monkeypatch):
    from brain_settings import import_files

    root = tmp_path / "projects"
    path = session(root)
    with path.open("a") as stream:
        stream.write('{"type":"user","message":')
    monkeypatch.setattr(import_files, "MAX_FILE_BYTES", 1)
    store = MemoryStore(tmp_path / "memory")
    stats = import_source({"id": "qwen-synthetic", "kind": "qwen", "path": str(root)}, store)
    assert stats["imported"] == 1 and not stats["errors"]
    assert "astronomy" in store.search("")[0]["text"]


def test_qwen_explicit_eligible_paths_cannot_import_nonchat_or_escape(tmp_path):
    root = tmp_path / "projects"
    path = session(root)
    outside = tmp_path / "outside.jsonl"
    outside.write_text(transcript([record("user", "DO_NOT_IMPORT_OUTSIDE")]))
    unrelated = root / "profile.jsonl"
    unrelated.write_text(transcript([record("user", "DO_NOT_IMPORT_PROFILE")]))
    store = MemoryStore(tmp_path / "memory")
    stats = import_source(
        {"id": "qwen-synthetic", "kind": "qwen", "path": str(root)},
        store,
        eligible_paths={str(path), str(unrelated), str(outside)},
    )
    assert stats["imported"] == 1
    assert "DO_NOT_IMPORT" not in store.search("")[0]["text"]


def test_qwen_source_api_rejects_missing_relative_and_symlink_roots(harness, tmp_path):
    real = tmp_path / "projects"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    for path in ("relative/projects", str(tmp_path / "missing"), str(linked)):
        result = harness.post("/api/sources", {"kind": "qwen", "name": "Qwen Code", "path": path})
        assert result.status_code == 400


def test_qwen_watch_and_import_never_follow_ancestor_replacement(tmp_path):
    from brain_settings.errors import AppError

    root = tmp_path / "projects"
    session(root)
    moved = tmp_path / "moved"
    root.rename(moved)
    root.symlink_to(moved, target_is_directory=True)
    source = {"id": "qwen-synthetic", "kind": "qwen", "path": str(root)}
    with pytest.raises(AppError):
        SessionWatcher().collect(source)
    with pytest.raises(AppError):
        import_source(source, MemoryStore(tmp_path / "memory"))
