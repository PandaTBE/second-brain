"""Synthetic transaction/lifecycle races never touch Keychain or Microsoft."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_notes_outlook_sources import add_outlook, connector_client
from test_outlook_auth import CLIENT_ID, completed, setup_manager

from brain_settings.errors import AppError
from brain_settings.sources import source_credential_scope


def _real_ticket(service):
    manager, clock, oauth = setup_manager()
    service.outlook_auth = manager
    flow = manager.start(CLIENT_ID)
    completed(oauth)
    clock.now += 5
    ticket = manager.poll(flow["flow_id"])
    return manager, clock, flow, ticket


@pytest.mark.parametrize("cancel_during", ["vault", "source_store"])
def test_cancelled_source_creation_rolls_back_credentials_and_source(
        tmp_path, monkeypatch, cancel_during):
    with connector_client(tmp_path) as (client, headers, _, _, _, vault, app):
        service = app.state.memory_service.source_service
        manager, _, flow, ticket = _real_ticket(service)
        restored = []
        original_restore = manager.restore

        def restore(*args):
            result = original_restore(*args)
            restored.append(result)
            return result

        monkeypatch.setattr(manager, "restore", restore)
        target, operation = (vault, "set") if cancel_during == "vault" else (service.store, "save")
        original = getattr(target, operation)

        def cancel_after_write(*args):
            result = original(*args)
            manager.cancel(flow["flow_id"])
            return result

        monkeypatch.setattr(target, operation, cancel_after_write)
        response = add_outlook(client, headers, outlook_auth_id=ticket["auth_id"])
        assert response.status_code == 409
        assert service.store.load() == ()
        assert not vault.entries
        assert restored == [False]
        assert not manager.is_claim_active(ticket["auth_id"])
        with pytest.raises(AppError):
            manager.take(ticket["auth_id"])


def _expired_source(client, headers, vault, service):
    source_id = add_outlook(client, headers).json()["data"]["source"]["id"]
    source = next(source for source in service.store.load() if source.id == source_id)
    scope = source_credential_scope(source)
    credentials = json.loads(vault.get(scope))
    expired = {**credentials, "expires_at": time.time() - 60}
    vault.set(scope, json.dumps(expired))
    return source_id, scope, expired


def _blocking_refresh(monkeypatch, graph):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def refresh(credentials):
        calls.append(dict(credentials))
        entered.set()
        assert release.wait(3), "Synthetic refresh was not released"
        return {**credentials, "access_token": "synthetic-refreshed-access",
                "refresh_token": "synthetic-refreshed-refresh", "expires_at": time.time() + 3600}

    monkeypatch.setattr(graph, "refresh", refresh)
    return entered, release, calls


def test_deleted_source_cannot_regain_credentials_after_inflight_refresh(tmp_path, monkeypatch):
    with connector_client(tmp_path) as (client, headers, _, _, graph, vault, app):
        service = app.state.memory_service.source_service
        source_id, _, _ = _expired_source(client, headers, vault, service)
        entered, release, calls = _blocking_refresh(monkeypatch, graph)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(service.outlook_credentials, source_id)
            try:
                assert entered.wait(3)
                service.delete(source_id)
            finally:
                release.set()
            with pytest.raises(AppError):
                pending.result(timeout=3)
        assert len(calls) == 1
        assert not vault.entries and service.store.load() == ()


def test_refresh_is_singleflight_and_waiters_use_rotated_credentials(tmp_path, monkeypatch):
    with connector_client(tmp_path) as (client, headers, _, _, graph, vault, app):
        service = app.state.memory_service.source_service
        source_id, scope, _ = _expired_source(client, headers, vault, service)
        entered, release, calls = _blocking_refresh(monkeypatch, graph)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(service.outlook_credentials, source_id)
            try:
                assert entered.wait(3)
                second = executor.submit(service.outlook_credentials, source_id)
            finally:
                release.set()
            assert first.result(timeout=3) == second.result(timeout=3)
        assert len(calls) == 1
        assert json.loads(vault.get(scope))["refresh_token"] == "synthetic-refreshed-refresh"


def test_refresh_does_not_overwrite_newer_credential_revision(tmp_path, monkeypatch):
    with connector_client(tmp_path) as (client, headers, _, _, graph, vault, app):
        service = app.state.memory_service.source_service
        source_id, scope, previous = _expired_source(client, headers, vault, service)
        entered, release, _ = _blocking_refresh(monkeypatch, graph)
        newer = {**previous, "access_token": "synthetic-newer-access",
                 "refresh_token": "synthetic-newer-refresh", "expires_at": time.time() + 7200}
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(service.outlook_credentials, source_id)
            try:
                assert entered.wait(3)
                with service._lock:
                    vault.set(scope, json.dumps(newer))
            finally:
                release.set()
            assert pending.result(timeout=3) == newer
        assert json.loads(vault.get(scope)) == newer


def test_paused_sync_still_forgets_expired_oauth_credentials(tmp_path):
    with connector_client(tmp_path) as (*_, app):
        memory = app.state.memory_service
        manager, clock, _, ticket = _real_ticket(memory.source_service)
        memory.schedule_store.save(memory.schedule_store.load().model_copy(update={"paused": True}))
        clock.now += 301
        memory.tick()
        with pytest.raises(AppError):
            manager.take(ticket["auth_id"])
        assert not manager._tickets and not manager._flows


def test_app_shutdown_forgets_unclaimed_oauth_credentials(tmp_path):
    with connector_client(tmp_path) as (*_, app):
        manager, _, _, ticket = _real_ticket(app.state.memory_service.source_service)
        assert manager._tickets
    assert not manager._tickets and not manager._flows
    with pytest.raises(AppError):
        manager.take(ticket["auth_id"])
