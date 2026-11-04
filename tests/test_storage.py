import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import FAKE_TOKEN, OPENROUTER, MemoryVault

from brain_settings.errors import AppError
from brain_settings.providers import Settings
from brain_settings.storage import MacKeychain, SettingsStore, credential_scope, vault_operation


def test_scope_changes_with_provider_or_endpoint():
    first = credential_scope("custom", "https://one.example.com/v1")
    assert first == credential_scope("custom", "https://one.example.com/v1")
    assert first != credential_scope("custom", "https://two.example.com/v1")
    assert first != credential_scope("openrouter", "https://one.example.com/v1")


def test_missing_preferences_load_safe_defaults(tmp_path):
    settings = SettingsStore(tmp_path).load()
    assert settings.provider == "openrouter"
    assert settings.base_url == OPENROUTER
    assert settings.model_id == ""


@pytest.mark.parametrize("contents", [
    "not JSON",
    json.dumps({"provider": "unsupported", "base_url": OPENROUTER}),
    json.dumps({"provider": "openrouter", "base_url": OPENROUTER, "token": FAKE_TOKEN}),
    "x" * 16_385,
])
def test_invalid_preferences_fail_safely_instead_of_exposing_contents(tmp_path, contents):
    (tmp_path / "settings.json").write_text(contents)
    with pytest.raises(AppError) as caught:
        SettingsStore(tmp_path).load()
    assert FAKE_TOKEN not in str(caught.value)
    assert caught.value.status_code == 503


def test_atomic_save_failure_preserves_previous_file_and_cleans_temporary(tmp_path, monkeypatch):
    from pathlib import Path

    store = SettingsStore(tmp_path)
    store.save(Settings(model_id="synthetic/original"))
    previous = store.path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("Synthetic write failure " + FAKE_TOKEN)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(AppError) as caught:
        store.save(Settings(model_id="synthetic/replacement"))
    assert store.path.read_bytes() == previous
    assert list(tmp_path.glob(".settings-*")) == []
    assert FAKE_TOKEN not in str(caught.value)


def test_no_keychain_fallback_on_non_macos(monkeypatch):
    import brain_settings.storage as storage

    monkeypatch.setattr(storage.sys, "platform", "linux")
    vault = MacKeychain()
    assert vault.available is False
    for operation, args in [("get", ("scope",)), ("set", ("scope", FAKE_TOKEN)),
                            ("delete", ("scope",))]:
        with pytest.raises(AppError) as caught:
            getattr(vault, operation)(*args)
        assert FAKE_TOKEN not in str(caught.value)


def test_keychain_operations_use_only_injected_secure_backend(monkeypatch):
    import brain_settings.storage as storage

    backend = Mock()
    backend.get_password.return_value = FAKE_TOKEN
    monkeypatch.setattr(storage.sys, "platform", "darwin")
    monkeypatch.setitem(
        sys.modules, "keyring.backends.macOS", SimpleNamespace(Keyring=lambda: backend)
    )
    vault = MacKeychain()
    assert vault.available is True
    assert vault.get("scope") == FAKE_TOKEN
    vault.set("scope", FAKE_TOKEN)
    backend.set_password.assert_called_once_with(vault.service, "scope", FAKE_TOKEN)
    vault.delete("scope")
    backend.delete_password.assert_called_once_with(vault.service, "scope")


def test_deleting_missing_key_is_idempotent(monkeypatch):
    import brain_settings.storage as storage

    backend = Mock()
    backend.get_password.return_value = None
    monkeypatch.setattr(storage.sys, "platform", "darwin")
    monkeypatch.setitem(
        sys.modules, "keyring.backends.macOS", SimpleNamespace(Keyring=lambda: backend)
    )
    MacKeychain().delete("scope")
    backend.delete_password.assert_not_called()


def test_vault_wrapper_sanitizes_failure_and_rejects_unavailable_storage():
    vault = Mock(available=True)
    vault.get.side_effect = RuntimeError("Synthetic error " + FAKE_TOKEN)
    with pytest.raises(AppError) as caught:
        vault_operation(vault, "get", "scope")
    assert caught.value.status_code == 503
    assert FAKE_TOKEN not in str(caught.value)
    with pytest.raises(AppError):
        vault_operation(Mock(available=False), "get", "scope")
    memory = MemoryVault()
    vault_operation(memory, "set", "scope", FAKE_TOKEN)
    assert vault_operation(memory, "get", "scope") == FAKE_TOKEN
