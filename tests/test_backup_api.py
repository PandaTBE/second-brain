"""Local backup API actions are explicit, CSRF guarded, and use synthetic state."""

from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient

from brain_settings.app import create_app


def test_local_backup_api_lists_and_deletes_only_explicit_copies(tmp_path):
    app = create_app(data_dir=tmp_path, vault=MemoryVault(), transport=FakeTransport())
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        headers = {"Origin": ORIGIN, "X-Brain-CSRF": csrf}
        status = client.get("/api/backups")
        assert status.status_code == 200
        assert status.json()["data"]["backups"] == []
        assert client.post("/api/backups/local", json={}).status_code == 403
        created = client.post("/api/backups/local", json={}, headers=headers)
        assert created.status_code == 200
        row = created.json()["data"]["backup"]
        assert row["encrypted"] is False
        inspected = client.post(
            "/api/backups/inspect",
            json={"path": str(app.state.backup_service.local_path(row["id"]))},
            headers=headers,
        )
        assert inspected.status_code == 200
        result = client.post(
            "/api/backups/restore",
            json={"inspection_id": inspected.json()["data"]["inspection_id"]},
            headers=headers,
        )
        assert result.status_code == 200
        assert result.json()["data"]["paused"] is True
        deleted = client.request("DELETE", "/api/backups/" + row["id"], json={}, headers=headers)
        assert deleted.status_code == 200
        assert client.get("/api/backups").json()["data"]["backups"] == []


def test_backup_settings_are_strict_and_return_native_status_shape(tmp_path):
    app = create_app(data_dir=tmp_path, vault=MemoryVault(), transport=FakeTransport())
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        headers = {"Origin": ORIGIN, "X-Brain-CSRF": csrf}
        assert (
            client.put(
                "/api/backups/settings", json={"enabled": "true"}, headers=headers
            ).status_code
            == 422
        )
        result = client.put("/api/backups/settings", json={"max_copies": 3}, headers=headers)
        assert result.status_code == 200
        assert result.json()["data"]["settings"]["max_copies"] == 3
        assert result.json()["data"]["backups"] == []
