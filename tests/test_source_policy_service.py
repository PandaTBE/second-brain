"""Policy HTTP/admission checks use temporary sources and synthetic content."""

import threading

from fastapi.testclient import TestClient

from brain_settings.app import create_app
from brain_settings.importers import import_source
from brain_settings.memory_store import MemoryStore
from conftest import ORIGIN, FakeTransport, MemoryVault


def source(client, headers, directory):
    directory.mkdir()
    (directory / "note.md").write_text("Synthetic evidence for policy checks.")
    response = client.post("/api/sources", json={"kind": "folder", "name": "Synthetic",
                                              "path": str(directory)}, headers=headers)
    assert response.status_code == 200
    return response.json()["data"]["source"]


def test_policy_api_is_csrf_guarded_and_preview_does_not_change_visibility(tmp_path):
    app = create_app(data_dir=tmp_path / "data", vault=MemoryVault(), transport=FakeTransport())
    with TestClient(app, base_url=ORIGIN) as client:
        token = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        headers = {"Origin": ORIGIN, "X-Brain-CSRF": token}
        row = source(client, headers, tmp_path / "notes")
        sid = row["id"]
        service = app.state.memory_service
        import_source(row, service.store)
        endpoint = f"/api/sources/{sid}/policy"
        result = client.get(endpoint)
        assert result.status_code == 200
        policy = result.json()["data"]["policy"]
        candidate = {**policy, "excluded_paths": ["note.md"], "mcp_enabled": False}
        denied = client.put(endpoint, json=candidate)
        assert denied.status_code == 403
        preview = client.post(endpoint + "/preview", json=candidate, headers=headers)
        assert preview.status_code == 200
        assert preview.json()["data"]["affected_documents"] == 1
        assert service.search("Synthetic")["results"]
        applied = client.put(endpoint, json=candidate, headers=headers)
        assert applied.status_code == 200
        assert applied.json()["data"]["policy"] == candidate
        assert service.search("Synthetic")["results"] == []
        listed = client.get("/api/sources").json()["data"]["sources"]
        assert listed[0]["policy"] == candidate
        invalid = client.put(endpoint, json={**policy, "import_enabled": 0}, headers=headers)
        assert invalid.status_code == 422


def test_excluded_path_is_never_opened_by_local_import(tmp_path, monkeypatch):
    from brain_settings.source_policy import SourcePolicy

    root = tmp_path / "notes"
    (root / "private").mkdir(parents=True)
    (root / "private" / "hidden.md").write_text("Synthetic private content")
    (root / "public.md").write_text("Synthetic visible content")
    store = MemoryStore(tmp_path / "data")
    store.set_source_policy("synthetic", SourcePolicy(excluded_paths=("private",)))
    import os

    original = os.open
    opened = []

    def recording(path, flags, *args, **kwargs):
        opened.append(str(path))
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording)
    stats = import_source({"id": "synthetic", "kind": "folder", "path": str(root)}, store)
    assert stats["imported"] == 1
    assert not any(path in {"private", "hidden.md"} for path in opened)


def test_policy_change_during_model_request_drops_result_and_retains_memory(tmp_path):
    from brain_settings.source_policy import SourcePolicy

    started, finish = threading.Event(), threading.Event()

    class BlockingGenerator:
        def generate(self, record):
            started.set()
            assert finish.wait(5)
            return {"card": {"title": "Synthetic", "summary": "Synthetic evidence",
                             "keywords": [], "atoms": []}, "model": "synthetic"}

    app = create_app(data_dir=tmp_path / "data", vault=MemoryVault(), transport=FakeTransport(),
                     memory_llm=BlockingGenerator())
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        headers = {"Origin": ORIGIN, "X-Brain-CSRF": csrf}
        row = source(client, headers, tmp_path / "notes")
        service = app.state.memory_service
        import_source(row, service.store)
        record = service.store.search("Synthetic")[0]
        results = []
        thread = threading.Thread(target=lambda: results.append(service._process_card(
            record, expected_scope="injected")))
        thread.start()
        assert started.wait(5)
        service.store.set_source_policy(row["id"], SourcePolicy(external_model_enabled=False))
        finish.set()
        thread.join(5)
        assert not thread.is_alive()
        assert results[0]["ready"] == 0
        assert service.store.get_record(record["id"])["card_status"] == "pending"
        assert service.search("Synthetic")["results"]


def test_excluded_notes_folders_are_removed_before_connector_read(tmp_path):
    from brain_settings.apple_notes_import import import_apple_notes
    from brain_settings.source_policy import SourcePolicy

    class Notes:
        def __init__(self):
            self.calls = []

        def read_page(self, folders, cursor=None, limit=50):
            self.calls.append(folders)
            return {"notes": [], "next_cursor": None, "warnings": [], "skipped": 0}

    store = MemoryStore(tmp_path)
    store.set_source_policy("synthetic", SourcePolicy(excluded_notes_folder_ids=("secret",)))
    client = Notes()
    stats = import_apple_notes({"id": "synthetic", "kind": "apple_notes",
                                "notes_folder_ids": ("public", "secret")}, store, client)
    assert client.calls == [("public",)]
    assert stats["imported"] == 0
