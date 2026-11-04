import pytest
from conftest import FAKE_TOKEN, OPENROUTER


@pytest.mark.parametrize("path", [
    "/api/settings", "/api/models", "/api/test-connection", "/api/credential-status",
])
def test_mutations_without_csrf_are_rejected(harness, selection, path):
    response = harness.client.post(path, json=selection)
    assert response.status_code == 403
    assert response.json()["success"] is False
    assert harness.vault.entries == {}
    assert harness.transport.calls == ()


def test_delete_without_csrf_is_rejected(harness):
    response = harness.client.request("DELETE", "/api/token", json={
        "provider": "openrouter", "base_url": OPENROUTER,
    })
    assert response.status_code == 403


@pytest.mark.parametrize("origin", ["https://unrelated.invalid", "null", "http://127.0.0.1:9999"])
def test_other_origins_are_rejected_even_with_csrf(harness, selection, origin):
    response = harness.client.post("/api/settings", json=selection, headers={
        **harness.headers, "Origin": origin,
    })
    assert response.status_code == 403
    assert harness.vault.entries == {}


def test_untrusted_host_is_rejected(harness):
    response = harness.client.get("/api/bootstrap", headers={"Host": "unrelated.invalid"})
    assert response.status_code in (400, 403)
    assert FAKE_TOKEN not in response.text


def test_wrong_csrf_is_rejected(harness, selection):
    response = harness.client.post("/api/settings", json=selection, headers={
        **harness.headers, "X-Brain-CSRF": "synthetic-wrong-token",
    })
    assert response.status_code == 403


def test_non_json_mutation_is_rejected(harness):
    response = harness.client.post("/api/settings", content="provider=openrouter", headers={
        **harness.headers, "Content-Type": "application/x-www-form-urlencoded",
    })
    assert response.status_code in (400, 415)


def test_oversized_body_rejected_before_upstream_or_vault(harness, selection):
    response = harness.post("/api/settings", {**selection, "model_name": "x" * 17000})
    assert response.status_code == 413
    assert harness.transport.calls == ()
    assert harness.vault.entries == {}


@pytest.mark.parametrize("changes", [
    {"provider": "unsupported"},
    {"provider": "openrouter", "base_url": "https://unrelated.invalid/api/v1"},
    {"model_id": ""},
    {"model_id": None},
    {"token": "line1\nline2"},
    {"token": " "},
])
def test_invalid_settings_fail_without_echoing_secret(harness, selection, changes):
    response = harness.post("/api/settings", {**selection, **changes})
    assert response.status_code in (400, 422)
    assert response.json()["success"] is False
    assert FAKE_TOKEN not in response.text
    assert harness.vault.entries == {}


@pytest.mark.parametrize("endpoint", [
    "http://api.example.com/v1",
    "https://username:password@api.example.com/v1",
    "https://api.example.com/v1?query=1",
    "https://api.example.com/v1#fragment",
    "https://api.example.com:8443/v1",
    "https://127.0.0.1/v1",
    "https://[::1]/v1",
    "https://169.254.169.254/v1",
    "https://10.0.0.1/v1",
])
def test_custom_endpoint_rejects_unsafe_targets_before_request(harness, selection, endpoint):
    response = harness.post("/api/models", {
        **selection, "provider": "custom", "base_url": endpoint,
    })
    assert response.status_code in (400, 422)
    assert harness.transport.calls == ()


def test_validation_failure_has_no_pydantic_input_or_key(harness, selection):
    response = harness.post("/api/settings", {**selection, "provider": [FAKE_TOKEN]})
    assert response.status_code in (400, 422)
    assert response.json()["success"] is False
    assert FAKE_TOKEN not in response.text


def test_api_responses_disallow_caching_and_framing(harness):
    response = harness.client.get("/api/bootstrap")
    assert response.headers["cache-control"] == "no-store"
    policy = response.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in policy
    assert response.headers.get("x-content-type-options") == "nosniff"
