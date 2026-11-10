"""Synthetic tokenless CLI integration; no external executable or credentials are used."""

import json

import pytest
from pydantic import ValidationError

from brain_settings.errors import AppError
from brain_settings.llm_cards import LLMCardGenerator
from brain_settings.providers import EndpointRequest, SaveRequest, Settings, normalize_endpoint
from brain_settings.service import SettingsService

BASE = "gigacode://local"
TEXT = "Use SQLite for the archive."


class ForbiddenVault:
    @property
    def available(self):
        raise AssertionError("CLI must not inspect Keychain availability")

    def get(self, *args):
        raise AssertionError("CLI must not read Keychain")

    set = get
    delete = get


class ForbiddenHTTP:
    def request(self, *args, **kwargs):
        raise AssertionError("CLI must not call the HTTP transport")

    post_json = request


def service(tmp_path):
    return SettingsService(tmp_path, vault=ForbiddenVault(), transport=ForbiddenHTTP())


def selection(**updates):
    return SaveRequest(provider="gigacode_cli", base_url=BASE, model_id="default", **updates)


def test_cli_is_tokenless_external_provider_and_never_uses_vault(tmp_path):
    settings = service(tmp_path)
    saved = settings.save(selection(cli_path="/synthetic/bin/gigacode"))
    assert saved["token_saved"] is False
    assert saved["settings"]["cli_path"] == "/synthetic/bin/gigacode"
    data = settings.bootstrap("synthetic-csrf")
    item = next(row for row in data["providers"] if row["id"] == "gigacode_cli")
    assert item["requires_token"] is False
    assert data["storage"]["available"] is False
    assert settings.credential_status(EndpointRequest(provider="gigacode_cli", base_url=BASE)) == {
        "token_saved": False,
    }
    assert settings.delete_token(EndpointRequest(provider="gigacode_cli", base_url=BASE)) == {
        "token_saved": False,
    }


def test_cli_catalog_is_explicitly_not_a_remote_catalog(tmp_path):
    data = service(tmp_path).models(selection())
    assert [row["id"] for row in data["models"]] == ["default"]
    assert data["currency"] is None
    assert data["models"][0]["input_price"] is None
    assert data["models"][0]["output_price"] is None
    assert "CLI" in data["price_unit"]


@pytest.mark.parametrize("model", ["default", "synthetic-explicit-model"])
def test_cli_check_uses_help_only_and_does_not_claim_auth_or_model(tmp_path, monkeypatch, model):
    class CLI:
        def __init__(self, cli_path=""):
            assert cli_path == "/synthetic/gigacode"

        def check(self):
            return {"status": "ok", "compatible": True,
                    "message": "CLI available; authentication and generation not checked"}

    monkeypatch.setattr("brain_settings.service.GigaCodeCLI", CLI)
    result = service(tmp_path).test_connection(SaveRequest(
        provider="gigacode_cli", base_url=BASE, model_id=model, cli_path="/synthetic/gigacode"))
    assert result["compatible"] is True
    assert result["model_available"] is None
    assert "not checked" in result["message"]


@pytest.mark.parametrize("path", ["gigacode", "~/bin/gigacode", "/bin/cli\n--bad", "/bin/cli\0"])
def test_cli_path_rejects_relative_and_control_characters(path):
    with pytest.raises(ValidationError):
        selection(cli_path=path)
    with pytest.raises(ValidationError):
        Settings(provider="gigacode_cli", base_url=BASE, cli_path=path)


def test_cli_rejects_api_token_and_private_network_option(tmp_path):
    with pytest.raises(AppError) as error:
        service(tmp_path).save(selection(token="x" * 24))
    assert error.value.code == "cli_token_forbidden"
    with pytest.raises(ValidationError):
        selection(allow_private_network=True)


def test_cli_endpoint_is_fixed_and_not_an_http_destination():
    assert normalize_endpoint("gigacode_cli", "") == BASE
    assert normalize_endpoint("gigacode_cli", BASE) == BASE
    with pytest.raises(AppError):
        normalize_endpoint("gigacode_cli", "https://unrelated.example.invalid")


def test_cli_generation_reuses_card_validation_usage_and_admission(tmp_path, monkeypatch):
    observed = []
    card = {"title": "Archive", "summary": "SQLite selected.", "keywords": ["SQLite"],
            "questions": ["Which database?"],
            "atoms": [{"kind": "decision", "text": TEXT, "evidence": TEXT}]}

    class CLI:
        def __init__(self, cli_path=""):
            assert cli_path == "/synthetic/gigacode"

        def generate(self, payload, *, model_id, timeout, before_request):
            assert observed == []
            before_request()
            assert model_id == "default"
            assert timeout == 180
            assert observed == ["admitted"]
            assert payload["messages"][1]["content"].find(TEXT) >= 0
            return {"choices": [{"message": {"content": json.dumps(card)},
                                  "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    monkeypatch.setattr("brain_settings.llm_cards.GigaCodeCLI", CLI)
    settings = service(tmp_path)
    settings.save(selection(cli_path="/synthetic/gigacode"))
    generator = LLMCardGenerator(settings, transport=ForbiddenHTTP())
    result = generator.generate({"title": "Archive", "text": TEXT},
                                before_request=lambda: observed.append("admitted"))
    assert result["card"] == card
    assert result["usage"]["total_tokens"] == 15
    assert generator.concurrency_limit() == 1
    assert generator.is_current(result)
    previous_scope = generator.provider_scope()
    settings.save(selection(cli_path="/synthetic/another-gigacode"))
    assert not generator.is_current(result)
    assert generator.provider_scope() != previous_scope


def test_cli_selection_clears_foreign_restore_auth_marker_without_keychain(tmp_path):
    settings = service(tmp_path)
    settings.store.save(Settings(requires_reauthentication=True))
    settings.save(selection())
    assert settings.store.load().requires_reauthentication is False


@pytest.mark.parametrize("model", ["--resume", "model\n--unsafe"])
def test_cli_model_id_is_not_an_option_or_control_string(model):
    with pytest.raises(ValidationError):
        SaveRequest(provider="gigacode_cli", base_url=BASE, model_id=model)


def test_foreign_restore_clears_machine_specific_cli_path_and_pauses_cards(tmp_path, monkeypatch):
    from brain_settings import backup_restore
    from brain_settings.background import ScheduleConfig

    original = Settings(provider="gigacode_cli", base_url=BASE, model_id="default",
                        cli_path="/synthetic/old-machine/gigacode")
    schedule = ScheduleConfig(enabled=True, process_cards=True)
    monkeypatch.setattr(backup_restore, "validate_configs", lambda _: (original, (), schedule))
    _, payloads = backup_restore._restored_configs(tmp_path, foreign=True)
    restored = json.loads(payloads["settings.json"])
    assert restored["cli_path"] == ""
    assert restored["model_id"] == ""
    assert restored["requires_reauthentication"] is True
    assert json.loads(payloads["schedule.json"])["process_cards"] is False
    assert json.loads(payloads["schedule.json"])["paused"] is True
    _, local_payloads = backup_restore._restored_configs(tmp_path, foreign=False)
    assert json.loads(local_payloads["settings.json"])["cli_path"] == original.cli_path


def test_cli_http_settings_save_catalog_and_help_check(harness, monkeypatch):
    checks = []

    class CLI:
        def __init__(self, cli_path=""):
            checks.append(cli_path)

        def check(self):
            return {"status": "ok", "compatible": True, "message": "Help only"}

    monkeypatch.setattr("brain_settings.service.GigaCodeCLI", CLI)
    request = {"provider": "gigacode_cli", "base_url": BASE, "model_id": "default",
               "cli_path": "/synthetic/gigacode"}
    for endpoint in ("/api/settings", "/api/models", "/api/test-connection"):
        response = harness.post(endpoint, request)
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True
    assert checks == ["/synthetic/gigacode"]
    assert harness.transport.calls == ()
    assert harness.vault.entries == {}


def test_cli_invalid_card_preserves_reported_usage(tmp_path, monkeypatch):
    class CLI:
        def __init__(self, cli_path=""):
            pass

        def generate(self, *args, **kwargs):
            return {"choices": [{"message": {"content": "not JSON"}}],
                    "usage": {"total_tokens": 23}}

    monkeypatch.setattr("brain_settings.llm_cards.GigaCodeCLI", CLI)
    settings = service(tmp_path)
    settings.save(selection())
    with pytest.raises(AppError) as error:
        LLMCardGenerator(settings).generate({"text": TEXT})
    assert error.value.code == "card_json_invalid"
    assert error.value.usage["total_tokens"] == 23


def test_cli_prelaunch_error_retains_request_not_sent_metadata(tmp_path, monkeypatch):
    class CLI:
        def __init__(self, cli_path=""):
            pass

        def generate(self, *args, **kwargs):
            error = AppError("gigacode_cli_missing", "CLI is missing", 503)
            error.request_sent = False
            error.requires_user_action = True
            raise error

    monkeypatch.setattr("brain_settings.llm_cards.GigaCodeCLI", CLI)
    settings = service(tmp_path)
    settings.save(selection())
    with pytest.raises(AppError) as error:
        LLMCardGenerator(settings).generate({"text": TEXT})
    assert error.value.code == "gigacode_cli_missing"
    assert error.value.request_sent is False
    assert error.value.requires_user_action is True


def test_real_cli_adapter_generates_validated_card_from_synthetic_executable(tmp_path):
    from test_gigacode_cli import ready

    card = {"title": "Archive", "summary": "SQLite selected.", "keywords": ["SQLite"],
            "questions": ["Which database?"],
            "atoms": [{"kind": "decision", "text": TEXT, "evidence": TEXT}]}
    event = [{"type": "result", "subtype": "success", "is_error": False,
              "result": json.dumps(card),
              "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}]
    executable = ready(tmp_path, "request = json.loads(sys.stdin.read())\n"
                       "assert request['messages'][1]['content']\n"
                       f"print({json.dumps(event)!r})\n")
    settings = service(tmp_path / "data")
    settings.save(selection(cli_path=str(executable)))
    admitted = []
    result = LLMCardGenerator(settings, transport=ForbiddenHTTP()).generate(
        {"text": TEXT}, before_request=lambda: admitted.append(True))
    assert admitted == [True]
    assert result["card"] == card
    assert result["usage"]["total_tokens"] == 15
