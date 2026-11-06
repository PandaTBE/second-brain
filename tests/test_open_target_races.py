"""Final opening authorization must survive policy-only and same-revision changes."""

import subprocess
import sys

import pytest
from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient
from test_backup import SOURCE, put

from brain_settings.app import create_app
from brain_settings.source_policy import SourcePolicy
from brain_settings.source_storage import Source, SourcesStore


@pytest.mark.parametrize("change", ["policy", "external_policy", "revision", "uri"])
def test_open_target_rechecks_current_authority_and_reference(tmp_path, monkeypatch, change):
    directory, files = tmp_path / "data", tmp_path / "files"
    files.mkdir()
    (files / "note.md").write_text("Synthetic opening target.")
    app = create_app(data_dir=directory, vault=MemoryVault(), transport=FakeTransport())
    service = app.state.memory_service
    SourcesStore(directory).save(
        (Source(id=SOURCE, kind="folder", name="Synthetic", path=str(files)),)
    )
    record = put(service.store, source_uri=(files / "note.md").as_uri())
    original = service.record

    def change_after_read(identifier):
        value = original(identifier)
        if change == "policy":
            service.store.set_source_policy(SOURCE, SourcePolicy(local_search_enabled=False))
        elif change == "external_policy":
            script = """
from pathlib import Path
import sys
from brain_settings.memory_store import MemoryStore
from brain_settings.source_policy import SourcePolicy
store = MemoryStore(Path(sys.argv[1]))
store.set_source_policy(sys.argv[2], SourcePolicy(local_search_enabled=False))
"""
            result = subprocess.run(
                [sys.executable, "-c", script, str(directory), SOURCE],
                capture_output=True,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr.decode()
        elif change == "revision":
            put(service.store, "Synthetic changed text.", source_uri=(files / "note.md").as_uri())
        else:
            put(service.store, source_uri=(files / "changed.md").as_uri())
        return value

    monkeypatch.setattr(service, "record", change_after_read)
    with TestClient(app, base_url=ORIGIN) as client:
        result = client.get("/api/memory/records/" + record["id"] + "/open-target")
    assert result.status_code in (404, 409)
    assert not result.json().get("data", {}).get("available", False)


def test_current_open_target_returns_exact_authorized_reference(tmp_path):
    directory, files = tmp_path / "data", tmp_path / "files"
    files.mkdir()
    (files / "note.md").write_text("Synthetic opening target.")
    app = create_app(data_dir=directory, vault=MemoryVault(), transport=FakeTransport())
    service = app.state.memory_service
    SourcesStore(directory).save(
        (Source(id=SOURCE, kind="folder", name="Synthetic", path=str(files)),)
    )
    record = put(service.store, source_uri=(files / "note.md").as_uri())
    current = service.record(record["id"])["record"]
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/api/memory/records/" + record["id"] + "/open-target")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True
    keys = ("id", "revision", "source_id", "source_kind", "source_object_id", "source_uri")
    assert data["reference"] == {key: current[key] for key in keys}
