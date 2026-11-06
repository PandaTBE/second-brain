"""Policy HTTP/admission checks use temporary sources and synthetic content."""

import threading

from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient

from brain_settings.app import create_app
from brain_settings.importers import import_source
from brain_settings.memory_store import MemoryStore


def source(client, headers, directory):
    directory.mkdir()
    (directory / "note.md").write_text("Synthetic evidence for policy checks.")
    response = client.post(
        "/api/sources",
        json={"kind": "folder", "name": "Synthetic", "path": str(directory)},
        headers=headers,
    )
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
    (root / "excluded-content").mkdir(parents=True)
    (root / "excluded-content" / "hidden.md").write_text("Synthetic private content")
    (root / "public.md").write_text("Synthetic visible content")
    store = MemoryStore(tmp_path / "data")
    store.set_source_policy("synthetic", SourcePolicy(excluded_paths=("excluded-content",)))
    import os

    original = os.open
    opened = []

    def recording(path, flags, *args, **kwargs):
        opened.append(str(path))
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording)
    stats = import_source({"id": "synthetic", "kind": "folder", "path": str(root)}, store)
    assert stats["imported"] == 1
    from pathlib import Path

    assert not any(Path(path).name in {"excluded-content", "hidden.md"} for path in opened)


def test_policy_change_during_model_request_drops_result_and_retains_memory(tmp_path):
    from brain_settings.source_policy import SourcePolicy

    started, finish = threading.Event(), threading.Event()

    class BlockingGenerator:
        def generate(self, record):
            started.set()
            assert finish.wait(5)
            return {
                "card": {
                    "title": "Synthetic",
                    "summary": "Synthetic evidence",
                    "keywords": [],
                    "questions": [],
                    "atoms": [],
                },
                "model": "synthetic",
            }

    app = create_app(
        data_dir=tmp_path / "data",
        vault=MemoryVault(),
        transport=FakeTransport(),
        memory_llm=BlockingGenerator(),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        headers = {"Origin": ORIGIN, "X-Brain-CSRF": csrf}
        row = source(client, headers, tmp_path / "notes")
        service = app.state.memory_service
        import_source(row, service.store)
        record = service.store.search("Synthetic")[0]
        results = []
        thread = threading.Thread(
            target=lambda: results.append(service._process_card(record, expected_scope="injected"))
        )
        thread.start()
        assert started.wait(5)
        service.store.set_source_policy(row["id"], SourcePolicy(external_model_enabled=False))
        finish.set()
        thread.join(5)
        assert not thread.is_alive()
        assert results[0]["ready"] == 0
        assert service.store.get_record(record["id"])["card_status"] == "pending"
        assert service.search("Synthetic")["results"]


def test_revoked_invalid_model_response_does_not_save_error_or_relabel_local(tmp_path):
    from brain_settings.source_policy import SourcePolicy

    class Generator:
        scope = "model_external"
        store = None
        sid = None

        def processing_scope(self):
            return self.scope

        def generate(self, record):
            self.store.set_source_policy(self.sid, SourcePolicy(external_model_enabled=False))
            self.scope = "model_local"
            return {"card": {}, "model": "synthetic"}

    generator = Generator()
    app = create_app(
        data_dir=tmp_path / "data",
        vault=MemoryVault(),
        transport=FakeTransport(),
        memory_llm=generator,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        row = source(client, {"Origin": ORIGIN, "X-Brain-CSRF": csrf}, tmp_path / "notes")
        service = app.state.memory_service
        import_source(row, service.store)
        record = service.store.search("Synthetic")[0]
        generator.store, generator.sid = service.store, row["id"]
        result = service._process_card(record, expected_scope="injected")
        assert result["stale"] == 1 and result["errors"] == 0
        assert service.store.get_record(record["id"])["card_status"] == "pending"


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
    stats = import_apple_notes(
        {"id": "synthetic", "kind": "apple_notes", "notes_folder_ids": ("public", "secret")},
        store,
        client,
    )
    assert client.calls == [("public",)]
    assert stats["imported"] == 0


def test_notes_exclusion_changed_during_admission_is_not_fetched(tmp_path):
    from brain_settings.apple_notes_import import import_apple_notes
    from brain_settings.source_policy import SourcePolicy

    store = MemoryStore(tmp_path)
    calls = []

    class Notes:
        def read_page(self, folders, cursor=None, limit=50):
            calls.append(folders)
            return {"notes": [], "next_cursor": None, "warnings": [], "skipped": 0}

    def admission():
        store.set_source_policy("synthetic", SourcePolicy(excluded_notes_folder_ids=("secret",)))

    import_apple_notes(
        {"id": "synthetic", "kind": "apple_notes", "notes_folder_ids": ("public", "secret")},
        store,
        Notes(),
        admission,
    )
    assert calls == [("public",)]


def test_auto_watcher_never_opens_policy_excluded_session(tmp_path, monkeypatch):
    from pathlib import Path

    from brain_settings.background import SessionWatcher
    from brain_settings.source_policy import SourcePolicy

    root = tmp_path / "sessions"
    (root / "excluded-content").mkdir(parents=True)
    (root / "excluded-content" / "hidden.jsonl").write_text('{"type":"synthetic"}')
    store = MemoryStore(tmp_path / "data")
    store.set_source_policy("synthetic", SourcePolicy(excluded_paths=("excluded-content",)))
    import os

    original = os.open
    opened = []

    def recording(path, flags, *args, **kwargs):
        opened.append(Path(path).name if isinstance(path, (str, Path)) else path)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording)
    watcher = SessionWatcher()
    batch = watcher.collect(
        {"id": "synthetic", "kind": "codex", "path": str(root)},
        allowed_path=lambda path: store.import_path_allowed("synthetic", path.as_posix()),
    )
    assert batch.paths == frozenset()
    assert "excluded-content" not in opened and "hidden.jsonl" not in opened


def test_hidden_source_is_absent_from_graph_source_metadata(tmp_path):
    from brain_settings.source_policy import SourcePolicy

    app = create_app(data_dir=tmp_path / "data", vault=MemoryVault(), transport=FakeTransport())
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        row = source(client, {"Origin": ORIGIN, "X-Brain-CSRF": csrf}, tmp_path / "notes")
        service = app.state.memory_service
        import_source(row, service.store)
        service.store.set_source_policy(row["id"], SourcePolicy(local_search_enabled=False))
        graph = service.atlas()
        assert graph["sources"] == [] and graph["records"] == []
        assert graph["total_records"] == 0
