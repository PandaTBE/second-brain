"""New sources reach the same SQLite, search and MCP surface as session memory."""

import pytest
from test_memory_service_api import wait_job
from test_notes_outlook_sources import add_outlook, connector_client
from test_settings_api import assert_success

from brain_settings.background import ScheduleConfig
from brain_settings.mcp_server import MemoryTools


@pytest.mark.parametrize("kind", ["apple_notes", "outlook"])
def test_connected_source_import_update_mcp_and_periodic_pause(tmp_path, kind, monkeypatch):
    with connector_client(tmp_path) as (client, headers, notes, _, graph, _, app):
        text = "Synthetic astronomy decision stored in SQLite."
        fetches = []

        def notes_page(folder_ids, cursor=None, limit=50):
            assert tuple(folder_ids) == ("folder-demo",)
            fetches.append("notes")
            return {"notes": [{"id": "note-demo", "folder_id": "folder-demo",
                               "title": "Astronomy", "text": text,
                               "created_at": "2026-11-01T10:00:00Z",
                               "updated_at": "2026-11-02T10:00:00Z"}],
                    "next_cursor": None, "warnings": [], "skipped": 0}

        def messages(token, lookback_days, next_link=None):
            assert token == "synthetic-access" and lookback_days == 90
            fetches.append("outlook")
            return {"messages": [{"id": "message-demo", "subject": "Astronomy",
                                  "text": text, "created_at": "2026-11-01T10:00:00Z",
                                  "updated_at": "2026-11-02T10:00:00Z",
                                  "source_uri": "https://outlook.office.com/"}],
                    "next_link": None, "warnings": [], "skipped": 0}

        monkeypatch.setattr(notes, "read_page", notes_page, raising=False)
        monkeypatch.setattr(graph, "messages", messages, raising=False)
        if kind == "outlook":
            source = assert_success(add_outlook(client, headers))["source"]
        else:
            assert_success(client.post("/api/sources/apple-notes/folders",
                                       headers=headers, json={}))
            source = assert_success(client.post("/api/sources", headers=headers, json={
                "kind": kind, "name": "Synthetic Notes",
                "notes_folder_ids": ["folder-demo"]}))["source"]
        for expected in ("imported", "unchanged", "updated"):
            if expected == "updated":
                text += " Updated telescope decision."
            started = assert_success(client.post("/api/memory/import", headers=headers,
                                                  json={"source_id": source["id"]}))
            job = wait_job(client, started["job_id"])
            assert job["status"] == "completed", job
            assert job["stats"][expected] == 1
        service = app.state.memory_service
        tools = MemoryTools(service.store, tmp_path)
        hits = tools.search_memory("astronomy")["results"]
        assert len(hits) == 1 and hits[0]["source_kind"] == kind
        assert "telescope" in tools.get_record(hits[0]["id"])["record"]["text"]

        service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                               process_cards=False, paused=True))
        service.tick(now=10**12)
        assert len(fetches) == 3
        service.set_sync_paused(False)
        service.tick(now=10**12)
        job = service.status()["jobs"][0]
        assert wait_job(client, job["id"])["status"] == "completed"
        assert len(fetches) == 4
        assert service.status()["cards_ready"] == 0

        assert_success(client.request("DELETE", f"/api/sources/{source['id']}",
                                      headers=headers, json={}))
        assert tools.search_memory("astronomy")["results"] == []
