"""Source integration uses only synthetic Notes and Microsoft credentials."""

import json
import time
from contextlib import contextmanager

import pytest
from conftest import FakeTransport, MemoryVault
from fastapi.testclient import TestClient

from brain_settings.app import create_app
from brain_settings.errors import AppError
from brain_settings.source_storage import Source, SourcesStore

ORIGIN = "http://127.0.0.1:8765"
CLIENT_ID = "00000000-0000-4000-8000-000000000001"


class DemoNotes:
    def __init__(self):
        self.calls = 0

    def list_folders(self):
        self.calls += 1
        return {"folders": [{"id": "folder-demo", "name": "Демо", "account": "Пример",
                             "path": "Пример / Демо", "note_count": 3}]}


class DemoTickets:
    def __init__(self):
        self.ticket = True
        self.restored = 0
        self.finished = 0

    def start(self, client_id):
        assert client_id == CLIENT_ID
        return {"flow_id": "flow-demo", "user_code": "DEMO-CODE",
                "verification_uri": "https://microsoft.com/devicelogin",
                "expires_in": 900, "interval": 5}

    def poll(self, flow_id):
        assert flow_id == "flow-demo"
        return {"status": "completed", "auth_id": "ticket-demo",
                "account": {"id": "account-demo", "username": "demo@example.com",
                            "display_name": "Демонстрационный аккаунт"}}

    def cancel(self, _flow_id):
        self.ticket = False

    def take(self, auth_id):
        if auth_id != "ticket-demo" or not self.ticket:
            raise AppError("outlook_auth_expired", "Войдите в Microsoft ещё раз.", 409)
        self.ticket = False
        return {"client_id": CLIENT_ID, "account_id": "account-demo",
                "username": "demo@example.com", "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh", "expires_at": time.time() + 3600}

    def restore(self, _auth_id, _credentials):
        self.ticket = True
        self.restored += 1

    def is_claim_active(self, _auth_id):
        return True

    def finish(self, _auth_id):
        self.finished += 1


class DemoGraph:
    def test(self, token):
        assert token == "synthetic-access"
        return 7

    def refresh(self, credentials):
        return {**credentials, "expires_at": time.time() + 3600}


@contextmanager
def connector_client(tmp_path):
    notes, tickets, graph, vault = DemoNotes(), DemoTickets(), DemoGraph(), MemoryVault()
    app = create_app(tmp_path, vault=vault, transport=FakeTransport(),
                     apple_notes_client=notes, outlook_client=graph, outlook_auth=tickets)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        headers = {"Origin": ORIGIN, "X-Brain-CSRF": csrf,
                   "Content-Type": "application/json"}
        yield client, headers, notes, tickets, graph, vault, app


def add_outlook(client, headers, **extra):
    return client.post("/api/sources", headers=headers, json={
        "kind": "outlook", "name": "Демо Outlook", "outlook_auth_id": "ticket-demo",
        "mail_import_enabled": True, "mail_lookback_days": 90, **extra})


def test_notes_discovery_requires_csrf_and_explicit_folder_selection(tmp_path):
    with connector_client(tmp_path) as (client, headers, notes, *_):
        assert client.post("/api/sources/apple-notes/folders", json={}).status_code == 403
        assert notes.calls == 0
        result = client.post("/api/sources/apple-notes/folders", headers=headers, json={})
        assert result.status_code == 200
        assert result.json()["data"]["folders"][0]["id"] == "folder-demo"
        response = client.post("/api/sources", headers=headers, json={
            "kind": "apple_notes", "name": "Демо Notes", "notes_folder_ids": ["folder-demo"]})
        assert response.status_code == 200
        source = response.json()["data"]["source"]
        assert source["notes_folder_ids"] == ["folder-demo"]
        assert source["credential_saved"] is False
        checked = client.post(f"/api/sources/{source['id']}/test", headers=headers, json={})
        assert checked.status_code == 200 and checked.json()["data"]["source"]["item_count"] == 3


def test_notes_rejects_unselected_or_empty_folders_without_import(tmp_path):
    with connector_client(tmp_path) as (client, headers, *_):
        for ids in ([], ["unknown-folder"]):
            response = client.post("/api/sources", headers=headers, json={
                "kind": "apple_notes", "name": "Демо Notes", "notes_folder_ids": ids})
            assert response.status_code in (400, 409, 422)
        assert client.get("/api/memory/status").json()["data"]["records"] == 0


def test_outlook_login_and_source_store_never_expose_tokens(tmp_path):
    with connector_client(tmp_path) as (client, headers, _, tickets, _, vault, _):
        result = client.post("/api/sources/outlook/auth/start", headers=headers,
                             json={"client_id": CLIENT_ID})
        assert result.status_code == 200
        result = client.post("/api/sources/outlook/auth/poll", headers=headers,
                             json={"flow_id": "flow-demo"})
        assert result.status_code == 200 and result.json()["data"]["auth_id"] == "ticket-demo"
        response = add_outlook(client, headers)
        assert response.status_code == 200
        source = response.json()["data"]["source"]
        assert source["username"] == "demo@example.com" and source["credential_saved"]
        disk = (tmp_path / "sources.json").read_text()
        for private_value in ("synthetic-access", "synthetic-refresh", "ticket-demo"):
            assert private_value not in disk and private_value not in response.text
        assert any("synthetic-refresh" in value for value in vault.entries.values())
        assert tickets.finished == 1
        assert add_outlook(client, headers).status_code == 409


def test_outlook_import_pause_retains_credentials_and_cached_search(tmp_path):
    with connector_client(tmp_path) as (client, headers, _, _, _, vault, app):
        source = add_outlook(client, headers).json()["data"]["source"]
        service = app.state.memory_service
        service.store.upsert_document(source_id=source["id"], source_kind="outlook",
                                      source_object_id="demo", title="Synthetic astronomy",
                                      text="Synthetic astronomy observation", source_uri="https://outlook.office.com/")
        checked = client.post(f"/api/sources/{source['id']}/test", headers=headers, json={})
        assert checked.status_code == 200 and checked.json()["data"]["source"]["item_count"] == 7
        response = client.post(f"/api/sources/{source['id']}/mail-import", headers=headers,
                               json={"enabled": False})
        assert response.status_code == 200
        assert not any(s["id"] == source["id"] for s in service._import_sources())
        assert client.get("/api/memory/search", params={"q": "astronomy"}).json()["data"]["results"]
        assert vault.entries
        assert client.delete(f"/api/sources/{source['id']}", headers=headers).status_code == 200
        assert not vault.entries
        remaining = client.get("/api/memory/search", params={"q": "astronomy"}).json()
        assert not remaining["data"]["results"]


def test_failed_outlook_persistence_restores_auth_ticket_and_vault(tmp_path, monkeypatch):
    with connector_client(tmp_path) as (client, headers, _, tickets, _, vault, app):
        def fail(_sources):
            raise AppError("sources_write_failed", "Synthetic write failure", 503)

        monkeypatch.setattr(app.state.memory_service.source_service.store, "save", fail)
        assert add_outlook(client, headers).status_code == 503
        assert tickets.restored == 1 and not vault.entries


def test_legacy_source_files_omit_new_empty_connector_fields(tmp_path):
    store = SourcesStore(tmp_path)
    source = Source(id="00000000-0000-4000-8000-000000000002", kind="folder",
                    name="Synthetic notes", path="/demo/notes")
    store.save((source,))
    public = json.loads(store.path.read_text())["sources"][0]
    assert not {"notes_folder_ids", "outlook_client_id", "outlook_account_id"}.intersection(public)


@pytest.mark.parametrize("table", ["apple_notes_sync_progress", "outlook_sync_progress"])
def test_source_deletion_clears_connector_progress(tmp_path, table):
    from brain_settings.memory_store import MemoryStore

    store = MemoryStore(tmp_path)
    with store._connection(write=True) as db:
        db.execute(f"CREATE TABLE {table}(source_id TEXT PRIMARY KEY, state TEXT)")
        db.execute(f"INSERT INTO {table} VALUES(?,?)", ("source-demo", "{}"))
    store.delete_source("source-demo")
    with store._connection() as db:
        assert db.execute(f"SELECT * FROM {table}").fetchall() == []
