"""Synthetic policy regression tests: no personal data, model, or connector access."""

from uuid import uuid4

import pytest

from brain_settings.errors import AppError
from brain_settings.mcp_server import MemoryTools
from brain_settings.memory_store import MemoryStore
from brain_settings.source_storage import Source, SourcesStore


def policy(**changes):
    from brain_settings.source_policy import SourcePolicy

    return SourcePolicy(**changes)


def document(source_id="synthetic", **changes):
    return {
        "source_id": source_id,
        "source_kind": "folder",
        "source_object_id": "public/note.md::chunk:000000",
        "title": "Synthetic policy note",
        "text": "Synthetic policy evidence stays private.",
        "source_uri": "file:///synthetic/public/note.md",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        **changes,
    }


def test_legacy_missing_policy_preserves_existing_read_and_import(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    assert store.source_policy("synthetic") == policy()
    assert store.get_record(saved["id"])["text"].startswith("Synthetic")
    assert store.search("Synthetic", visibility="mcp")


@pytest.mark.parametrize(
    "scope,flag",
    [
        ("local", "local_search_enabled"),
        ("mcp", "mcp_enabled"),
        ("model_local", "local_model_enabled"),
        ("model_external", "external_model_enabled"),
    ],
)
def test_scopes_are_independent_and_direct_reads_fail_closed(tmp_path, scope, flag):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    store.set_source_policy("synthetic", policy(**{flag: False}))
    assert store.search("Synthetic", visibility=scope) == []
    with pytest.raises(AppError) as caught:
        store.get_record(saved["id"], visibility=scope)
    assert caught.value.status_code == 404
    for other_scope in {"local", "mcp", "model_local", "model_external"} - {scope}:
        assert store.get_record(saved["id"], visibility=other_scope)["id"] == saved["id"]


def test_two_existing_mcp_processes_see_policy_revocation_without_restart(tmp_path):
    sid = str(uuid4())
    SourcesStore(tmp_path).save(
        (Source(id=sid, kind="folder", name="Synthetic", path="/synthetic"),)
    )
    writer, reader = MemoryStore(tmp_path), MemoryStore(tmp_path)
    saved = writer.upsert_document(**document(sid))
    tools = (MemoryTools(reader, tmp_path), MemoryTools(MemoryStore(tmp_path), tmp_path))
    assert all(tool.search_memory("Synthetic")["results"] for tool in tools)
    writer.set_source_policy(sid, policy(mcp_enabled=False))
    for tool in tools:
        assert tool.search_memory("Synthetic")["results"] == []
        assert "error" in tool.get_record(saved["id"])
        assert tool.get_context_brief()["records"] == []
        status = tool.get_sync_status()["memory"]
        assert status["documents"] == 0
        assert status["by_source"] == {}
        assert status["last_updated_at"] is None
    assert writer.get_record(saved["id"])["id"] == saved["id"]


@pytest.mark.parametrize("operation", ["search", "brief", "record"])
def test_mcp_rechecks_exclusions_changed_after_fetch(tmp_path, monkeypatch, operation):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    tools = MemoryTools(store)
    method = {"search": "search", "brief": "context_brief", "record": "get_record"}[operation]
    original = getattr(store, method)
    changed = False

    def revoke(*args, **kwargs):
        nonlocal changed
        value = original(*args, **kwargs)
        if not changed:
            changed = True
            store.set_source_policy("synthetic", policy(excluded_paths=("public",)))
        return value

    monkeypatch.setattr(store, method, revoke)
    if operation == "search":
        assert tools.search_memory("Synthetic")["results"] == []
    elif operation == "brief":
        assert tools.get_context_brief()["records"] == []
    else:
        assert "error" in tools.get_record(saved["id"])


def test_mcp_record_recheck_infrastructure_failure_is_reported(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path)
    store.upsert_document(**document())

    def unavailable(*args, **kwargs):
        raise AppError("memory_unavailable", "Synthetic failure", 503)

    monkeypatch.setattr(store, "get_record", unavailable)
    result = MemoryTools(store).search_memory("Synthetic")
    assert "error" in result
    assert "Synthetic failure" not in str(result)


def test_policy_filters_before_result_limit_and_browse_graph_counts(tmp_path):
    store = MemoryStore(tmp_path)
    for index in range(110):
        store.upsert_document(**document("hidden", source_object_id=f"{index}.md", text="needle"))
    allowed = store.upsert_document(**document("visible", text="needle"))
    store.set_source_policy("hidden", policy(local_search_enabled=False))
    assert [hit["id"] for hit in store.search("needle", limit=1)] == [allowed["id"]]
    assert store.status(visibility="local")["documents"] == 1
    page = store.browse()
    assert [hit["id"] for hit in page["results"]] == [allowed["id"]]
    assert {node["id"] for node in store.atlas()["records"]} == {allowed["id"]}


def test_path_subtree_exclusion_hides_existing_material_and_late_import(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document(source_object_id="private/a.md::chunk:000000"))
    adjacent = store.upsert_document(
        **document(source_object_id="private-other/a.md::chunk:000000")
    )
    candidate = policy(excluded_paths=("private",))
    preview = store.preview_source_policy("synthetic", candidate)
    assert preview["affected_documents"] == 1
    assert preview["samples"][0]["id"] == saved["id"]
    store.set_source_policy("synthetic", candidate)
    assert [r["id"] for r in store.search("Synthetic")] == [adjacent["id"]]
    with pytest.raises(AppError) as caught:
        store.upsert_document(**document(source_object_id="private/new.md::chunk:000000"))
    assert caught.value.code == "source_policy_denied"
    with pytest.raises(AppError):
        store.get_record(saved["id"], visibility="mcp")


def test_import_disable_does_not_hide_cached_material_and_blocks_late_write(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    store.set_source_policy("synthetic", policy(import_enabled=False))
    assert store.get_record(saved["id"])["id"] == saved["id"]
    assert store.search("Synthetic", visibility="mcp")
    with pytest.raises(AppError) as caught:
        store.upsert_document(**document(text="Changed content"))
    assert caught.value.code == "source_policy_denied"


@pytest.mark.parametrize("candidate", [{"import_enabled": False}, {"excluded_paths": ("public",)}])
def test_empty_file_reconciliation_cannot_delete_cache_after_revocation(tmp_path, candidate):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    store.set_source_policy("synthetic", policy(**candidate))
    with pytest.raises(AppError) as caught:
        store.replace_file_documents("synthetic", "public/note.md", [], complete=True)
    assert caught.value.code == "source_policy_denied"
    store.set_source_policy("synthetic", policy())
    assert store.get_record(saved["id"])["id"] == saved["id"]


def test_external_model_revocation_prevents_atomic_late_card_save(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(**document())
    store.set_source_policy("synthetic", policy(external_model_enabled=False))
    assert not store.save_card(saved["id"], 1, {}, processing_scope="model_external")
    assert store.get_record(saved["id"])["card_status"] == "pending"
    assert store.search("Synthetic")


def test_notes_exclusion_uses_folder_metadata_and_hides_legacy_unknown_folder(tmp_path):
    store = MemoryStore(tmp_path)
    hidden = store.upsert_document(
        **document(source_kind="apple_notes", source_folder_id="folder-secret")
    )
    visible = store.upsert_document(
        **document(
            source_kind="apple_notes", source_folder_id="folder-public", source_object_id="second"
        )
    )
    legacy = store.upsert_document(**document(source_kind="apple_notes", source_object_id="legacy"))
    store.set_source_policy("synthetic", policy(excluded_notes_folder_ids=("folder-secret",)))
    assert [r["id"] for r in store.search("Synthetic")] == [visible["id"]]
    for identifier in (hidden["id"], legacy["id"]):
        with pytest.raises(AppError):
            store.get_record(identifier)


def test_empty_notes_reconciliation_cannot_delete_excluded_folder_cache(tmp_path):
    store = MemoryStore(tmp_path)
    saved = store.upsert_document(
        **document(
            source_kind="apple_notes",
            source_folder_id="secret",
            source_object_id="apple-note:synthetic::chunk:000000",
        )
    )
    store.set_source_policy("synthetic", policy(excluded_notes_folder_ids=("secret",)))
    with pytest.raises(AppError):
        store.replace_file_documents("synthetic", "apple-note:synthetic", [], complete=True)
    store.set_source_policy("synthetic", policy())
    assert store.get_record(saved["id"])["id"] == saved["id"]


@pytest.mark.parametrize(
    "changes",
    [
        {"mcp_enabled": "false"},
        {"import_enabled": 0},
        {"unexpected": True},
        {"excluded_paths": ["../private"]},
        {"excluded_paths": ["/private"]},
        {"excluded_paths": ["a\\b"]},
        {"excluded_paths": ["*.md"]},
        {"excluded_paths": ["a\x00b"]},
        {"excluded_notes_folder_ids": [""]},
    ],
)
def test_policy_input_is_strict_and_paths_are_bounded_relative_subtrees(changes):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        policy(**changes)


def test_unknown_visibility_cannot_be_interpolated_into_sql(tmp_path):
    store = MemoryStore(tmp_path)
    store.upsert_document(**document())
    with pytest.raises(AppError):
        store.search("Synthetic", visibility="mcp'; DROP TABLE documents; --")
    assert store.status()["documents"] == 1


def test_two_processes_migrate_legacy_database_without_duplicate_column_race(tmp_path):
    import sqlite3
    import subprocess
    import sys

    from brain_settings.memory_store import SCHEMA

    with sqlite3.connect(tmp_path / "memory.sqlite3") as connection:
        connection.executescript(SCHEMA)
    script = (
        "from pathlib import Path; from brain_settings.memory_store import MemoryStore; "
        "import sys; s=MemoryStore(Path(sys.argv[1])); "
        "assert s.source_policy('synthetic').mcp_enabled"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
    with sqlite3.connect(tmp_path / "memory.sqlite3") as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(documents)")]
        assert columns.count("source_folder_id") == 1
