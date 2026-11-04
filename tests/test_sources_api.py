import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import FAKE_TOKEN, ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient
from test_settings_api import assert_success

from brain_settings.app import create_app


def add_folder(harness, path, kind="codex", name="Тестовый архив"):
    return harness.post("/api/sources", {"kind": kind, "name": name, "path": str(path)})


def test_sources_start_empty_without_scanning_any_directory(harness):
    data = assert_success(harness.client.get("/api/sources"))
    assert data["sources"] == []
    assert harness.transport.calls == ()


def test_add_directory_saves_configuration_without_import_or_scan(harness, tmp_path, monkeypatch):
    directory = tmp_path / "synthetic-codex"
    directory.mkdir()
    (directory / "session.jsonl").write_text('{"message":"synthetic content"}')

    def forbidden(*args, **kwargs):
        raise AssertionError("Adding a connection must not scan files")

    monkeypatch.setattr(Path, "iterdir", forbidden)
    source = assert_success(add_folder(harness, directory))["source"]
    assert str(uuid.UUID(source["id"])) == source["id"]
    assert source["name"] == "Тестовый архив"
    assert source["kind"] == "codex"
    assert source["path"] == str(directory)
    assert source["status"] == "not_checked"
    assert source["last_checked_at"] is None
    assert source["item_count"] is None
    assert source["credential_saved"] is False


def test_directory_connections_survive_restart(harness, tmp_path):
    directory = tmp_path / "synthetic-claude"
    directory.mkdir()
    source = assert_success(add_folder(harness, directory, kind="claude"))["source"]
    app = create_app(data_dir=harness.data_dir, vault=harness.vault, transport=FakeTransport())
    with TestClient(app, base_url=ORIGIN) as client:
        restored = assert_success(client.get("/api/sources"))["sources"]
    assert len(restored) == 1
    assert restored[0]["id"] == source["id"]
    assert restored[0]["path"] == str(directory)


def test_delete_connection_leaves_original_files_untouched(harness, tmp_path):
    directory = tmp_path / "synthetic-notes"
    directory.mkdir()
    original = directory / "note.md"
    original.write_text("User-owned synthetic text")
    source = assert_success(add_folder(harness, directory, kind="folder"))["source"]
    result = harness.client.request(
        "DELETE", f"/api/sources/{source['id']}", json={}, headers=harness.headers,
    )
    assert assert_success(result)["deleted"] is True
    assert original.read_text() == "User-owned synthetic text"
    assert assert_success(harness.client.get("/api/sources"))["sources"] == []


@pytest.mark.parametrize("path_type", ["relative", "missing", "file"])
def test_local_path_requires_existing_absolute_directory(harness, tmp_path, path_type):
    path = {
        "relative": Path("relative-directory"),
        "missing": tmp_path / "does-not-exist",
        "file": tmp_path / "not-a-directory.txt",
    }[path_type]
    if path_type == "file":
        path.write_text("synthetic")
    response = add_folder(harness, path)
    assert response.status_code in (400, 422)
    assert response.json()["success"] is False
    assert assert_success(harness.client.get("/api/sources"))["sources"] == []


@pytest.mark.parametrize("changes", [
    {"kind": "unsupported"}, {"name": ""}, {"name": " "}, {"path": None},
])
def test_invalid_source_configuration_is_rejected(harness, tmp_path, changes):
    response = harness.post("/api/sources", {
        "kind": "folder", "name": "Synthetic", "path": str(tmp_path), **changes,
    })
    assert response.status_code in (400, 422)
    assert response.json()["success"] is False


def test_source_mutations_need_csrf(harness, tmp_path):
    response = harness.client.post("/api/sources", json={
        "kind": "folder", "name": "Synthetic", "path": str(tmp_path),
    })
    assert response.status_code == 403
    assert assert_success(harness.client.get("/api/sources"))["sources"] == []


@pytest.mark.parametrize("method,suffix", [("POST", "/test"), ("DELETE", "")])
def test_existing_source_actions_need_csrf(harness, tmp_path, method, suffix):
    directory = tmp_path / "synthetic-csrf-source"
    directory.mkdir()
    source = assert_success(add_folder(harness, directory))["source"]
    response = harness.client.request(method, f"/api/sources/{source['id']}{suffix}", json={})
    assert response.status_code == 403
    sources = assert_success(harness.client.get("/api/sources"))["sources"]
    assert len(sources) == 1
    assert sources[0]["status"] == "not_checked"


@pytest.mark.parametrize("method,suffix", [("post", "/test"), ("delete", "")])
def test_unknown_source_id_has_safe_not_found_response(harness, method, suffix):
    response = harness.client.request(
        method.upper(), f"/api/sources/{uuid.uuid4()}{suffix}", json={}, headers=harness.headers,
    )
    assert response.status_code == 404
    assert response.json()["success"] is False
    assert FAKE_TOKEN not in response.text


def test_source_preferences_have_restricted_permissions(harness, tmp_path):
    directory = tmp_path / "synthetic-source"
    directory.mkdir()
    assert_success(add_folder(harness, directory))
    configuration = harness.data_dir / "sources.json"
    assert configuration.exists()
    assert not (configuration.stat().st_mode & 0o077)
    assert isinstance(json.loads(configuration.read_text()), (dict, list))


def test_corrupt_source_file_is_explicit_safe_error_not_empty_archive(harness):
    (harness.data_dir / "sources.json").write_text("Synthetic invalid JSON " + FAKE_TOKEN)
    response = harness.client.get("/api/sources")
    assert response.status_code == 503
    assert response.json()["success"] is False
    assert FAKE_TOKEN not in response.text


def test_concurrent_source_additions_do_not_lose_connections(harness, tmp_path):
    directories = tuple(tmp_path / f"synthetic-source-{index}" for index in range(8))
    for directory in directories:
        directory.mkdir()
    headers = harness.headers

    def add(directory):
        response = harness.client.post("/api/sources", json={
            "kind": "folder", "name": directory.name, "path": str(directory),
        }, headers=headers)
        return assert_success(response)["source"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        added = tuple(executor.map(add, directories))
    sources = assert_success(harness.client.get("/api/sources"))["sources"]
    assert {source["id"] for source in sources} == {source["id"] for source in added}


def test_health_reports_only_current_native_instance_identifier(tmp_path, monkeypatch):
    instance = "bde8918e-9d3c-46f8-91a0-7c4bbac97d80"
    monkeypatch.setenv("SECOND_BRAIN_INSTANCE_ID", instance)
    app = create_app(data_dir=tmp_path, vault=MemoryVault(), transport=FakeTransport())
    with TestClient(app, base_url=ORIGIN) as client:
        data = assert_success(client.get("/api/health"))
    assert data["app_id"] == "second-brain-settings"
    assert data["instance_id"] == instance
