import pytest
from conftest import FAKE_TOKEN, OPENROUTER
from test_settings_api import assert_success

from brain_settings.errors import AppError


def test_public_catalog_needs_no_token_and_normalizes_prices(harness):
    data = assert_success(harness.post("/api/models", {
        "provider": "openrouter", "base_url": OPENROUTER,
    }))
    by_id = {model["id"]: model for model in data["models"]}
    assert by_id["vendor/low-cost"]["input_price"] == pytest.approx(0.1)
    assert by_id["vendor/low-cost"]["output_price"] == pytest.approx(0.3)
    assert by_id["vendor/free"]["input_price"] == 0
    assert by_id["vendor/free"]["output_price"] == 0
    assert by_id["vendor/unknown"]["input_price"] is None
    assert by_id["vendor/unknown"]["output_price"] is None
    assert "vendor/image-only" not in by_id
    assert data["fetched_at"]
    assert data["price_unit"] == "USD / 1M tokens"
    assert data["currency"] == "USD"
    assert all(call[1] == "/models" for call in harness.transport.calls)


def test_empty_real_catalog_remains_empty(harness):
    harness.transport.models = {"data": []}
    data = assert_success(harness.post("/api/models", {
        "provider": "openrouter", "base_url": OPENROUTER,
    }))
    assert data["models"] == []


def test_invalid_prices_are_unknown_not_free(harness):
    harness.transport.models = {"data": [{
        "id": "synthetic/model", "pricing": {"prompt": "not-a-price", "completion": "-1"},
    }]}
    data = assert_success(harness.post("/api/models", {
        "provider": "openrouter", "base_url": OPENROUTER,
    }))
    assert data["models"][0]["input_price"] is None
    assert data["models"][0]["output_price"] is None


def test_simpleclaw_prices_keep_rubles_per_million_and_display_name(harness):
    harness.transport.models = {"object": "list", "data": [
        {
            "id": "synthetic/priced", "display_name": "Название модели",
            "protocols": ["chat_completions"],
            "pricing": {"inputRublesPerMillion": 87.5, "outputRublesPerMillion": 437.5},
        },
        {
            "id": "synthetic/free", "display_name": "Бесплатная",
            "protocols": ["chat_completions"],
            "pricing": {"inputRublesPerMillion": 0, "outputRublesPerMillion": 0},
        },
        {"id": "synthetic/unknown", "protocols": ["chat_completions"]},
    ]}
    data = assert_success(harness.post("/api/models", {
        "provider": "simpleclaw", "base_url": "https://simple-claw.ru/v1",
    }))
    assert data["currency"] == "RUB"
    assert data["price_unit"] == "RUB / 1M tokens"
    by_id = {model["id"]: model for model in data["models"]}
    assert by_id["synthetic/priced"]["name"] == "Название модели"
    assert by_id["synthetic/priced"]["input_price"] == 87.5
    assert by_id["synthetic/priced"]["output_price"] == 437.5
    assert by_id["synthetic/free"]["input_price"] == 0
    assert by_id["synthetic/free"]["output_price"] == 0
    assert by_id["synthetic/unknown"]["input_price"] is None


def test_simpleclaw_catalog_does_not_claim_key_was_verified(harness, selection):
    data = assert_success(harness.post("/api/test-connection", {
        **selection, "provider": "simpleclaw", "base_url": "https://simple-claw.ru/v1",
    }))
    assert data["status"] == "warning"
    assert "ключ" in data["message"].lower() or "токен" in data["message"].lower()
    assert all(path == "/models" for _, path, _ in harness.transport.calls)


def test_simpleclaw_catalog_excludes_nontext_protocols(harness):
    harness.transport.models = {"data": [
        {"id": "synthetic/chat", "protocols": ["chat_completions"]},
        {"id": "synthetic/messages", "protocols": ["messages"]},
        {"id": "synthetic/responses", "protocols": ["responses"]},
        {"id": "synthetic/image", "protocols": ["images"]},
        {"id": "synthetic/embedding", "protocols": ["embeddings"]},
        {"id": "synthetic/audio", "protocols": ["audio"]},
    ]}
    data = assert_success(harness.post("/api/models", {
        "provider": "simpleclaw", "base_url": "https://simple-claw.ru/v1",
    }))
    assert {model["id"] for model in data["models"]} == {"synthetic/chat"}


def test_connection_verifies_openrouter_key_without_generating(harness, selection):
    data = assert_success(harness.post("/api/test-connection", selection))
    assert data["status"] == "ok"
    assert data["checked_at"]
    assert "генерац" in data["message"].lower()
    assert data["model_available"] is True
    calls = harness.transport.calls
    assert any(path == "/key" and token == FAKE_TOKEN for _, path, token in calls)
    assert all(path in ("/key", "/models") for _, path, _ in calls)
    assert harness.vault.entries == {}


def test_absent_selected_model_is_warning_not_verified_generation(harness, selection):
    data = assert_success(harness.post("/api/test-connection", {
        **selection, "model_id": "synthetic/not-in-catalog",
    }))
    assert data["status"] == "warning"
    assert data["model_available"] is False


def test_saved_key_is_reused_only_for_its_scope(harness, selection):
    assert_success(harness.post("/api/settings", selection))
    payload = {key: value for key, value in selection.items() if key != "token"}
    assert_success(harness.post("/api/test-connection", payload))
    assert any(path == "/key" and token == FAKE_TOKEN
               for _, path, token in harness.transport.calls)


@pytest.mark.parametrize("code,status", [
    ("authentication_failed", 401), ("rate_limited", 429), ("network_error", 502),
])
def test_transport_errors_are_safe_and_distinct(harness, selection, code, status):
    harness.transport.error = AppError(code, "Безопасное сообщение", status_code=status)
    response = harness.post("/api/test-connection", selection)
    assert response.status_code == status
    result = response.json()
    assert result["success"] is False
    assert result["error"]["code"] == code
    assert FAKE_TOKEN not in response.text


def test_manual_model_id_can_be_saved_without_catalog(harness, selection):
    harness.transport.error = AppError("network_error", "Нет сети", status_code=502)
    data = assert_success(harness.post("/api/settings", {
        **selection, "model_id": "synthetic/manually-entered",
    }))
    assert data["settings"]["model_id"] == "synthetic/manually-entered"
    assert harness.transport.calls == ()


def test_openai_catalog_excludes_nontext_generation_and_moderation_models(harness):
    harness.transport.models = {"data": [
        {"id": "gpt-6-mini"}, {"id": "omni-moderation-latest"}, {"id": "sora-2"},
        {"id": "gpt-realtime"}, {"id": "gpt-audio"},
    ]}
    data = assert_success(harness.post("/api/models", {
        "provider": "openai", "base_url": "https://api.openai.com/v1", "token": FAKE_TOKEN,
    }))
    assert {model["id"] for model in data["models"]} == {"gpt-6-mini"}
