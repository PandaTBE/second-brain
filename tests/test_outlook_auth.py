"""Bounded OAuth device sessions and one-use account tickets, all synthetic."""

import json
import threading

import pytest

from brain_settings.errors import AppError
from brain_settings.outlook_auth import OutlookAuthManager

CLIENT_ID = "11111111-2222-4333-8444-555555555555"


class Clock:
    now = 100

    def __call__(self):
        return self.now


class OAuth:
    def __init__(self):
        self.polls = 0
        self.result = {"status": "pending"}

    def start(self, client_id):
        return {"device_code": "PRIVATE_DEVICE", "user_code": "DEMO-CODE",
                "verification_uri": "https://microsoft.com/devicelogin",
                "expires_in": 900, "interval": 5}

    def poll(self, client_id, device_code):
        self.polls += 1
        return self.result


class Graph:
    def profile(self, access_token):
        return {"id": "demo-account", "username": "demo@example.com", "display_name": "Demo"}


def setup_manager():
    clock, oauth = Clock(), OAuth()
    return OutlookAuthManager(oauth, Graph(), clock=clock), clock, oauth


def completed(oauth):
    oauth.result = {"status": "completed", "tokens": {
        "access_token": "PRIVATE_ACCESS", "refresh_token": "PRIVATE_REFRESH",
        "expires_at": 2000000000}}


def test_public_flow_never_contains_device_or_access_credentials():
    manager, clock, oauth = setup_manager()
    started = manager.start(CLIENT_ID)
    assert "PRIVATE_DEVICE" not in json.dumps(started)
    assert started["interval"] == 5 and started["user_code"] == "DEMO-CODE"
    completed(oauth)
    clock.now += 5
    result = manager.poll(started["flow_id"])
    assert result["status"] == "completed" and result["account"]["username"] == "demo@example.com"
    assert "PRIVATE_" not in json.dumps(result)
    credentials = manager.take(result["auth_id"])
    assert credentials["access_token"] == "PRIVATE_ACCESS"
    assert manager.is_claim_active(result["auth_id"])
    manager.finish(result["auth_id"])
    assert not manager.is_claim_active(result["auth_id"])
    with pytest.raises(AppError):
        manager.take(result["auth_id"])


def test_poll_interval_and_slowdown_never_issue_duplicate_early_requests():
    manager, clock, oauth = setup_manager()
    flow = manager.start(CLIENT_ID)
    manager.poll(flow["flow_id"])
    assert oauth.polls == 0
    clock.now += 5
    oauth.result = {"status": "slow_down"}
    result = manager.poll(flow["flow_id"])
    assert result["interval"] == 10 and oauth.polls == 1
    clock.now += 9
    manager.poll(flow["flow_id"])
    assert oauth.polls == 1
    clock.now += 1
    manager.poll(flow["flow_id"])
    assert oauth.polls == 2


def test_cancelled_late_completion_cannot_create_a_ticket():
    manager, clock, oauth = setup_manager()
    flow = manager.start(CLIENT_ID)
    entered, release = threading.Event(), threading.Event()
    completed(oauth)
    original = oauth.poll
    def delayed(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)
    oauth.poll = delayed
    clock.now += 5
    failures = []
    def worker():
        try:
            manager.poll(flow["flow_id"])
        except AppError as error:
            failures.append(error.code)
    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(2)
    manager.cancel(flow["flow_id"])
    release.set()
    thread.join(2)
    assert failures and not thread.is_alive()
    assert not manager._tickets


def test_pending_flows_expire_and_flow_count_is_bounded():
    manager, clock, _ = setup_manager()
    flows = [manager.start(CLIENT_ID) for _ in range(8)]
    with pytest.raises(AppError):
        manager.start(CLIENT_ID)
    clock.now += 901
    with pytest.raises(AppError):
        manager.poll(flows[0]["flow_id"])
    assert manager.start(CLIENT_ID)


def test_failed_save_restores_one_use_ticket_but_cancel_or_expiry_prevents_restore():
    manager, clock, oauth = setup_manager()
    flow = manager.start(CLIENT_ID)
    completed(oauth)
    clock.now += 5
    result = manager.poll(flow["flow_id"])
    credentials = manager.take(result["auth_id"])
    assert manager.restore(result["auth_id"], credentials)
    assert manager.take(result["auth_id"]) == credentials
    manager.cancel(flow["flow_id"])
    assert not manager.restore(result["auth_id"], credentials)


def test_completed_and_claimed_tickets_expire_without_retaining_secrets():
    manager, clock, oauth = setup_manager()
    flow = manager.start(CLIENT_ID)
    completed(oauth)
    clock.now += 5
    result = manager.poll(flow["flow_id"])
    credentials = manager.take(result["auth_id"])
    clock.now += 901
    assert not manager.restore(result["auth_id"], credentials)
    assert not manager.is_claim_active(result["auth_id"])
    assert not manager._tickets


def test_concurrent_poll_is_single_flight():
    manager, clock, oauth = setup_manager()
    flow = manager.start(CLIENT_ID)
    entered, release = threading.Event(), threading.Event()
    original = oauth.poll
    def delayed(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)
    oauth.poll = delayed
    clock.now += 5
    thread = threading.Thread(target=lambda: manager.poll(flow["flow_id"]))
    thread.start()
    assert entered.wait(2)
    assert manager.poll(flow["flow_id"])["status"] == "pending"
    release.set()
    thread.join(2)
    assert oauth.polls == 1 and not thread.is_alive()


def test_terminal_failure_drops_device_session_and_sanitizes_unknown_errors():
    manager, clock, oauth = setup_manager()
    flow = manager.start(CLIENT_ID)
    def failed(*args):
        raise RuntimeError("PRIVATE_DEVICE")
    oauth.poll = failed
    clock.now += 5
    with pytest.raises(AppError) as caught:
        manager.poll(flow["flow_id"])
    assert "PRIVATE_DEVICE" not in caught.value.message
    with pytest.raises(AppError):
        manager.poll(flow["flow_id"])


def test_completed_poll_is_idempotent_and_modified_restore_is_rejected():
    manager, clock, oauth = setup_manager()
    flow = manager.start(CLIENT_ID)
    completed(oauth)
    clock.now += 5
    first = manager.poll(flow["flow_id"])
    assert manager.poll(flow["flow_id"]) == first and oauth.polls == 1
    credentials = manager.take(first["auth_id"])
    assert not manager.restore(first["auth_id"], {**credentials, "access_token": "other"})
    assert manager.restore(first["auth_id"], credentials)
    manager.cancel(flow["flow_id"])
    assert not manager._tickets


def test_start_reserves_capacity_before_upstream_requests_and_releases_failed_slots():
    manager, clock, oauth = setup_manager()
    entered, release = threading.Semaphore(0), threading.Event()
    original = oauth.start
    def delayed(client_id):
        entered.release()
        assert release.wait(3)
        return original(client_id)
    oauth.start = delayed
    threads = [threading.Thread(target=lambda: manager.start(CLIENT_ID)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for _ in threads:
        assert entered.acquire(timeout=2)
    with pytest.raises(AppError) as caught:
        manager.start(CLIENT_ID)
    assert caught.value.code == "outlook_flow_limit"
    release.set()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    clock.now += 901
    def failed(client_id):
        raise RuntimeError("PRIVATE_DEVICE")
    oauth.start = failed
    with pytest.raises(AppError) as caught:
        manager.start(CLIENT_ID)
    assert "PRIVATE_DEVICE" not in caught.value.message
    assert not manager._flows


def test_failed_start_attempts_are_rate_limited_before_upstream_requests():
    manager, clock, oauth = setup_manager()
    calls = []
    def failed(client_id):
        calls.append(client_id)
        raise RuntimeError("unavailable")
    oauth.start = failed
    for _ in range(8):
        with pytest.raises(AppError):
            manager.start(CLIENT_ID)
    with pytest.raises(AppError) as caught:
        manager.start(CLIENT_ID)
    assert caught.value.code == "outlook_start_limit" and len(calls) == 8
    clock.now += 61
    with pytest.raises(AppError):
        manager.start(CLIENT_ID)
    assert len(calls) == 9


def test_idle_cleanup_and_close_remove_pending_credentials():
    manager, clock, oauth = setup_manager()
    flow = manager.start(CLIENT_ID)
    completed(oauth)
    clock.now += 5
    manager.poll(flow["flow_id"])
    assert manager._tickets
    clock.now += 301
    manager.cleanup()
    assert not manager._flows and not manager._tickets
    manager.start(CLIENT_ID)
    manager.close()
    assert not manager._flows and not manager._tickets and not manager._claims


@pytest.mark.parametrize("profile", [{"id": "demo-account"},
                                     {"id": "demo", "username": "bad\nprofile"}, None])
def test_profile_must_be_verified_before_creating_account_ticket(profile):
    manager, clock, oauth = setup_manager()
    flow = manager.start(CLIENT_ID)
    completed(oauth)
    manager.graph_client.profile = lambda token: profile
    clock.now += 5
    with pytest.raises(AppError):
        manager.poll(flow["flow_id"])
    assert not manager._tickets
