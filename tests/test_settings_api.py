import json
import stat

from conftest import DEEPSEEK, FAKE_TOKEN, OPENROUTER, ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient

from brain_settings.app import create_app


def assert_success(response):
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["success"] is True
    return result["data"]


def test_bootstrap_has_safe_defaults_and_live_provider_metadata(harness):
    response = harness.client.get("/api/bootstrap")
    data = assert_success(response)
    assert data["csrf_token"]
    assert data["settings"]["provider"] == "openrouter"
    assert data["settings"]["base_url"] == OPENROUTER
    assert data["settings"]["model_id"] == ""
    assert data["token_saved"] is False
    assert data["storage"]["available"] is True
    assert {item["id"] for item in data["providers"]} == {
        "openrouter", "deepseek", "openai", "simpleclaw", "qwen", "ollama", "custom"
    }
    assert response.headers["cache-control"] == "no-store"
    assert harness.transport.calls == ()


def test_health_identifies_this_local_application_without_credentials(harness):
    response = harness.client.get("/api/health")
    data = assert_success(response)
    assert data["app_id"] == "second-brain-settings"
    assert FAKE_TOKEN not in response.text


def test_save_uses_vault_and_persists_only_nonsecret_settings(harness, selection):
    data = assert_success(harness.post("/api/settings", selection))
    assert data["token_saved"] is True
    assert data["settings"]["model_id"] == selection["model_id"]
    assert FAKE_TOKEN not in json.dumps(data)
    assert tuple(harness.vault.entries.values()) == (FAKE_TOKEN,)
    persisted = harness.data_dir / "settings.json"
    assert persisted.exists()
    assert FAKE_TOKEN not in persisted.read_text()
    assert not (persisted.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO))
    assert harness.transport.calls == ()


def test_restart_restores_selection_but_never_returns_saved_key(harness, selection):
    assert_success(harness.post("/api/settings", selection))
    restarted = create_app(
        data_dir=harness.data_dir, vault=harness.vault, transport=FakeTransport()
    )
    with TestClient(restarted, base_url=ORIGIN) as client:
        response = client.get("/api/bootstrap")
    data = assert_success(response)
    assert data["settings"]["model_id"] == "vendor/low-cost"
    assert data["token_saved"] is True
    assert FAKE_TOKEN not in response.text


def test_omitted_token_retains_only_current_endpoint_key(harness, selection):
    assert_success(harness.post("/api/settings", selection))
    updated = {key: value for key, value in selection.items() if key != "token"}
    updated = {**updated, "model_id": "vendor/second", "model_name": "Second"}
    data = assert_success(harness.post("/api/settings", updated))
    assert data["token_saved"] is True
    assert data["settings"]["model_id"] == "vendor/second"
    assert tuple(harness.vault.entries.values()) == (FAKE_TOKEN,)


def test_saving_without_any_key_fails_without_persisting(harness, selection):
    response = harness.post("/api/settings", {**selection, "token": None})
    assert response.status_code in (400, 422)
    assert response.json()["success"] is False
    assert not (harness.data_dir / "settings.json").exists()


def test_credentials_are_not_shared_between_providers(harness, selection):
    assert_success(harness.post("/api/settings", selection))
    status = assert_success(harness.post("/api/credential-status", {
        "provider": "deepseek", "base_url": DEEPSEEK,
    }))
    assert status["token_saved"] is False
    response = harness.post("/api/models", {"provider": "deepseek", "base_url": DEEPSEEK})
    assert response.status_code in (400, 422)
    assert harness.transport.calls == ()


def test_credentials_are_not_shared_between_custom_endpoints(harness, selection):
    assert_success(harness.post("/api/settings", {
        **selection, "provider": "custom", "base_url": "https://one.example.com/v1",
    }))
    status = assert_success(harness.post("/api/credential-status", {
        "provider": "custom", "base_url": "https://two.example.com/v1",
    }))
    assert status["token_saved"] is False
    response = harness.post("/api/models", {
        "provider": "custom", "base_url": "https://two.example.com/v1",
    })
    assert response.status_code in (400, 422)
    assert harness.transport.calls == ()


def test_deleting_one_scope_keeps_another_provider_key(harness, selection):
    assert_success(harness.post("/api/settings", selection))
    assert_success(harness.post("/api/settings", {
        **selection, "provider": "deepseek", "base_url": DEEPSEEK,
        "token": "synthetic-second-provider-key",
    }))
    response = harness.client.request("DELETE", "/api/token", json={
        "provider": "deepseek", "base_url": DEEPSEEK,
    }, headers=harness.headers)
    assert assert_success(response)["token_saved"] is False
    assert tuple(harness.vault.entries.values()) == (FAKE_TOKEN,)
    status = assert_success(harness.post("/api/credential-status", {
        "provider": "openrouter", "base_url": OPENROUTER,
    }))
    assert status["token_saved"] is True


def test_draft_token_for_catalog_or_check_is_never_saved(harness, selection):
    assert_success(harness.post("/api/models", selection))
    assert_success(harness.post("/api/test-connection", selection))
    assert harness.vault.entries == {}
    assert not (harness.data_dir / "settings.json").exists()


def test_keychain_failure_is_sanitized_and_has_no_file_fallback(tmp_path, selection):
    class FailingVault(MemoryVault):
        def set(self, scope, token):
            raise RuntimeError("Synthetic vault error: " + FAKE_TOKEN)

    app = create_app(data_dir=tmp_path, vault=FailingVault(), transport=FakeTransport())
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        response = client.post("/api/settings", json=selection, headers={
            "Origin": ORIGIN, "X-Brain-CSRF": csrf,
        })
    assert response.status_code == 503
    assert response.json()["success"] is False
    assert FAKE_TOKEN not in response.text
    assert all(FAKE_TOKEN.encode() not in path.read_bytes()
               for path in tmp_path.rglob("*") if path.is_file())


def test_unavailable_secure_storage_still_renders_bootstrap(tmp_path):
    class UnavailableVault(MemoryVault):
        available = False

    app = create_app(data_dir=tmp_path, vault=UnavailableVault(), transport=FakeTransport())
    with TestClient(app, base_url=ORIGIN) as client:
        data = assert_success(client.get("/api/bootstrap"))
    assert data["storage"]["available"] is False
    assert data["token_saved"] is False
