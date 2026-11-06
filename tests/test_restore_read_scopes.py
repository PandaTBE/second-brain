"""A restore cannot split source configuration and SQL inside one memory response."""

import json
import subprocess
import sys

import pytest
from conftest import FakeTransport, MemoryVault
from test_backup import SOURCE, local_path, put

from brain_settings.app import create_app
from brain_settings.errors import AppError
from brain_settings.mcp_server import MemoryTools
from brain_settings.source_storage import Source, SourcesStore


def setup_scope(tmp_path):
    app = create_app(data_dir=tmp_path, vault=MemoryVault(), transport=FakeTransport())
    memory, backups = app.state.memory_service, app.state.backup_service
    backups.legacy_handles = lambda: ()
    source = Source(id=SOURCE, name="Synthetic", kind="folder", path="/synthetic")
    SourcesStore(tmp_path).save((source,))
    record = put(memory.store)
    memory.store.save_card(
        record["id"],
        1,
        {
            "title": "Synthetic",
            "summary": "Synthetic evidence.",
            "keywords": [],
            "questions": [],
            "atoms": [
                {
                    "kind": "fact",
                    "text": "Synthetic original evidence.",
                    "evidence": "Synthetic original evidence.",
                }
            ],
        },
    )
    memory.store.project_facts()
    fact_id = memory.facts()["facts"][0]["id"]
    SourcesStore(tmp_path).save((source.model_copy(update={"enabled": False}),))
    archive = local_path(backups)
    SourcesStore(tmp_path).save((source,))
    preview = backups.inspect(archive)
    return memory, backups, preview["inspection_id"], record["id"], fact_id


@pytest.mark.parametrize(
    "surface", ["status", "search", "evidence", "browse", "atlas", "record", "facts", "history"]
)
def test_http_read_scope_fences_allowed_ids_through_result_construction(
    tmp_path, monkeypatch, surface
):
    memory, backups, inspection, record_id, fact_id = setup_scope(tmp_path)
    original, fired = memory._allowed, False

    def interleave_restore():
        nonlocal fired
        allowed = original()
        if not fired:
            fired = True
            backups.restore(inspection)
        return allowed

    calls = {
        "status": memory.status,
        "search": lambda: memory.search("Synthetic"),
        "evidence": lambda: memory.evidence("Synthetic", mode="lexical"),
        "browse": memory.browse,
        "atlas": memory.atlas,
        "record": lambda: memory.record(record_id),
        "facts": memory.facts,
        "history": lambda: memory.fact_history(fact_id),
    }
    monkeypatch.setattr(memory, "_allowed", interleave_restore)
    with pytest.raises(AppError) as caught:
        calls[surface]()
    assert caught.value.code == "maintenance_lock_upgrade"
    assert fired and SourcesStore(tmp_path).load()[0].enabled is True
    backups.restore(inspection)
    assert memory.search("Synthetic")["results"] == []


@pytest.mark.parametrize(
    "surface", ["search", "evidence", "facts", "history", "record", "brief", "status"]
)
def test_mcp_whole_read_scope_returns_controlled_error_without_stale_ids(
    tmp_path, monkeypatch, surface
):
    memory, backups, inspection, record_id, fact_id = setup_scope(tmp_path)
    tools = MemoryTools(memory.store, tmp_path)
    original, fired = tools._allowed_ids, False

    def interleave_restore():
        nonlocal fired
        allowed = original()
        if not fired:
            fired = True
            backups.restore(inspection)
        return allowed

    calls = {
        "search": lambda: tools.search_memory("Synthetic"),
        "evidence": lambda: tools.get_evidence_bundle("Synthetic", mode="lexical"),
        "facts": tools.get_facts,
        "history": lambda: tools.get_fact_history(fact_id),
        "record": lambda: tools.get_record(record_id),
        "brief": tools.get_context_brief,
        "status": tools.get_sync_status,
    }
    monkeypatch.setattr(tools, "_allowed_ids", interleave_restore)
    result = calls[surface]()
    assert "error" in result
    assert fired and SourcesStore(tmp_path).load()[0].enabled is True
    backups.restore(inspection)
    assert tools.search_memory("Synthetic")["results"] == []


def test_other_process_restore_waits_for_mcp_source_and_sql_read_scope(tmp_path):
    memory, backups, inspection, _, _ = setup_scope(tmp_path)
    script = """
import json, sys
from pathlib import Path
from brain_settings.memory_store import MemoryStore
from brain_settings.mcp_server import MemoryTools
tools = MemoryTools(MemoryStore(Path(sys.argv[1])), Path(sys.argv[1]))
original = tools._allowed_ids
paused = False
def allowed():
    global paused
    result = original()
    if not paused:
        paused = True
        print('scoped', flush=True)
        sys.stdin.readline()
    return result
tools._allowed_ids = allowed
print(json.dumps(tools.search_memory('Synthetic')), flush=True)
sys.stdin.readline()
print(json.dumps(tools.search_memory('Synthetic')), flush=True)
"""
    reader = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert reader.stdout.readline().strip() == "scoped"
        with pytest.raises(AppError) as caught:
            backups.restore(inspection)
        assert caught.value.code == "memory_maintenance"
        reader.stdin.write("continue\n")
        reader.stdin.flush()
        assert json.loads(reader.stdout.readline())["results"]
        backups.restore(inspection)
        reader.stdin.write("again\n")
        reader.stdin.flush()
        assert json.loads(reader.stdout.readline())["results"] == []
    finally:
        reader.stdin.close()
        reader.wait(timeout=10)
