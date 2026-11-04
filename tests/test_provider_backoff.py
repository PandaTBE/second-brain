"""Provider throttling is durable, scoped and contains no source material."""

import pytest

from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore
from brain_settings.provider_backoff import ProviderBackoff


def limited(*, retry_after=60, requires_user_action=False):
    error = AppError("rate_limited", "untrusted provider details", 429)
    error.retry_after = retry_after
    error.requires_user_action = requires_user_action
    return error


def test_hold_survives_restart_and_isolated_from_other_models(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    store = MemoryStore(tmp_path)
    gate = ProviderBackoff(store)
    gate.limit("model-a", limited(retry_after=120))
    restarted = ProviderBackoff(store)
    state = restarted.status("model-a", 10)
    assert state["blocked"] and state["retry_at"] == "1970-01-01T00:18:40+00:00"
    assert state["effective_concurrency"] == 1
    assert state["configured_concurrency"] == 10
    assert not restarted.status("model-b", 10)["blocked"]
    with pytest.raises(AppError, match="Провайдер") as caught:
        restarted.ensure_ready("model-a")
    assert caught.value.code == "provider_paused" and not caught.value.request_sent
    clock[0] = 1120
    assert not restarted.status("model-a", 10)["blocked"]
    restarted.success("model-a", 10)
    assert restarted.status("model-a", 10)["effective_concurrency"] == 2


def test_later_limit_cannot_shorten_hold_or_success_clear_it(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    gate = ProviderBackoff(MemoryStore(tmp_path))
    gate.limit("model", limited(retry_after=120))
    clock[0] += 5
    gate.limit("model", limited(retry_after=1))
    gate.success("model", 10)
    state = gate.status("model", 10)
    assert state["retry_at"] == "1970-01-01T00:18:40+00:00"
    assert state["effective_concurrency"] == 1
    assert "untrusted" not in state["message"]


def test_balance_hold_requires_explicit_resume_without_resetting_probe(tmp_path, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1000)
    gate = ProviderBackoff(MemoryStore(tmp_path))
    gate.limit("model", limited(requires_user_action=True))
    monkeypatch.setattr("time.time", lambda: 10**10)
    state = gate.status("model", 10)
    assert state["blocked"] and state["requires_user_action"]
    assert state["retry_at"] is None
    gate.resume("model")
    state = gate.status("model", 10)
    assert not state["blocked"] and not state["requires_user_action"]
    assert state["effective_concurrency"] == 1


@pytest.mark.parametrize("retry_after", [None, float("nan"), float("inf"), -1, "private"])
def test_invalid_retry_hint_uses_safe_default(tmp_path, monkeypatch, retry_after):
    monkeypatch.setattr("time.time", lambda: 1000)
    gate = ProviderBackoff(MemoryStore(tmp_path))
    gate.limit("model", limited(retry_after=retry_after))
    assert gate.status("model", 10)["retry_at"] == "1970-01-01T00:17:40+00:00"
