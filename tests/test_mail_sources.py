"""Provider presets and opt-in mail memory, using synthetic accounts only."""

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime

import pytest
from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient
from test_memory_service_api import wait_job
from test_settings_api import assert_success
from test_sources_mail import FakeMailTester

from brain_settings.app import create_app
from brain_settings.background import ScheduleConfig
from brain_settings.errors import AppError
from brain_settings.mcp_server import MemoryTools

PASSWORD = "abcdefgh" + "ijklmnop"


@dataclass(frozen=True)
class ProviderHarness:
    client: TestClient
    headers: dict
    vault: MemoryVault
    tester: FakeMailTester
    app: object
    directory: object

    def add(self, provider="gmail", **changes):
        return self.client.post("/api/sources", headers=self.headers, json={
            "kind": "imap", "name": "Synthetic inbox", "mail_provider": provider,
            "username": " Reader@Example.com ", "password": PASSWORD,
            "mail_import_enabled": True, "mail_lookback_days": 30, **changes,
        })


@pytest.fixture
def providers(tmp_path):
    vault, tester = MemoryVault(), FakeMailTester()
    app = create_app(data_dir=tmp_path, vault=vault, transport=FakeTransport(),
                     mail_tester=tester)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = assert_success(client.get("/api/bootstrap"))["csrf_token"]
        yield ProviderHarness(client, {"Origin": ORIGIN, "X-Brain-CSRF": csrf},
                              vault, tester, app, tmp_path)


@pytest.mark.parametrize(("provider", "host"), [
    ("gmail", "imap.gmail.com"), ("yandex", "imap.yandex.ru"),
])
def test_provider_preset_saves_fixed_server_and_keychain_only(providers, provider, host):
    response = providers.add(provider)
    source = assert_success(response)["source"]
    assert source["kind"] == "imap" and source["mail_provider"] == provider
    assert source["host"] == host
    assert source["username"] == "reader@example.com"
    assert source["port"] == 993 and source["mailbox"] == "INBOX"
    assert source["mail_import_enabled"] is True and source["mail_lookback_days"] == 30
    assert source["credential_saved"] is True and source["status"] == "not_checked"
    assert providers.tester.calls == ()
    assert tuple(providers.vault.entries.values()) == (PASSWORD,)
    assert PASSWORD not in response.text
    assert PASSWORD not in json.dumps(assert_success(providers.client.get("/api/sources")))
    for path in providers.directory.rglob("*"):
        if path.is_file():
            assert PASSWORD.encode() not in path.read_bytes()


def test_gmail_grouped_application_password_is_normalized_before_keychain(providers):
    assert_success(providers.add(password="abcd efgh " + "ijkl mnop"))
    assert tuple(providers.vault.entries.values()) == (PASSWORD,)


@pytest.mark.parametrize("provider", ["gmail", "yandex"])
def test_provider_supports_full_custom_domain_email(providers, provider):
    source = assert_success(providers.add(provider,
                                          username="team+notes@company.example"))["source"]
    assert source["username"] == "team+notes@company.example"


@pytest.mark.parametrize("changes", [
    {"mail_provider": "unknown"}, {"username": "missing-domain"},
    {"username": "reader@"}, {"username": "@example.com"},
    {"username": "Reader <reader@example.com>"}, {"username": "reader\r\n@example.com"},
    {"host": "127.0.0.1"}, {"host": "https://imap.gmail.com"},
    {"host": "imap.yandex.ru"}, {"port": 143}, {"mailbox": "Sent"},
    {"password": None}, {"password": ""}, {"password": "abcd\r\nefgh"},
    {"mail_import_enabled": "yes"}, {"mail_import_enabled": 1},
    {"mail_lookback_days": -1}, {"mail_lookback_days": 15},
    {"mail_lookback_days": False}, {"mail_lookback_days": 30.0},
    {"mail_lookback_days": "30"},
])
def test_invalid_provider_request_never_connects_or_saves(providers, changes):
    response = providers.add(**changes)
    assert response.status_code in (400, 422)
    assert response.json()["success"] is False
    assert not providers.vault.entries and providers.tester.calls == ()
    assert PASSWORD not in response.text


@pytest.mark.parametrize("days", [0, 30, 90, 365])
def test_import_window_and_explicit_optout_survive_reload(providers, days):
    source = assert_success(providers.add(mail_lookback_days=days,
                                          mail_import_enabled=False))["source"]
    loaded = assert_success(providers.client.get("/api/sources"))["sources"][0]
    assert loaded["id"] == source["id"]
    assert loaded["mail_lookback_days"] == days and loaded["mail_import_enabled"] is False
    response = providers.client.post("/api/memory/import", headers=providers.headers,
                                     json={"source_id": source["id"]})
    assert response.status_code == 404
    assert providers.tester.calls == ()


def test_provider_connection_test_uses_preset_without_importing_bodies(providers):
    source = assert_success(providers.add("yandex"))["source"]
    checked = assert_success(providers.client.post(
        f"/api/sources/{source['id']}/test", json={}, headers=providers.headers,
    ))["source"]
    assert checked["status"] == "ok" and checked["item_count"] == 7
    assert providers.tester.calls == ({
        "host": "imap.yandex.ru", "username": "reader@example.com", "password": PASSWORD,
        "port": 993, "mailbox": "INBOX",
    },)
    assert assert_success(providers.client.get("/api/memory/status"))["records"] == 0


def test_legacy_imap_is_preserved_and_never_silently_imported(providers):
    source = assert_success(providers.client.post("/api/sources", headers=providers.headers, json={
        "kind": "imap", "name": "Legacy custom", "host": "mail.example.com",
        "username": "LegacyReader", "password": PASSWORD,
    }))["source"]
    assert source["host"] == "mail.example.com" and source["username"] == "LegacyReader"
    assert source["mail_import_enabled"] is False
    service = providers.app.state.memory_service
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False))
    service.tick(now=10**12)
    assert service.status()["jobs"] == [] and providers.tester.calls == ()
    assert_success(providers.client.post(f"/api/sources/{source['id']}/test",
                                        json={}, headers=providers.headers))
    assert providers.tester.calls[0]["username"] == "LegacyReader"


def test_provider_credentials_are_independent_and_deleted_without_message_changes(providers):
    first = assert_success(providers.add())["source"]
    second = assert_success(providers.add())["source"]
    assert len(providers.vault.entries) == 2
    assert_success(providers.client.request("DELETE", f"/api/sources/{first['id']}",
                                           json={}, headers=providers.headers))
    assert len(providers.vault.entries) == 1 and providers.tester.calls == ()
    remaining = assert_success(providers.client.get("/api/sources"))["sources"]
    assert [item["id"] for item in remaining] == [second["id"]]


def test_source_creation_requires_csrf_even_for_ready_presets(providers):
    response = providers.client.post("/api/sources", json={
        "kind": "imap", "name": "Synthetic Gmail", "mail_provider": "gmail",
        "username": "reader@example.com", "password": PASSWORD,
    }, headers={"Origin": ORIGIN})
    assert response.status_code == 403 and providers.vault.entries == {}


def message(uid, *, attachment=False):
    email = EmailMessage()
    email["From"] = "Synthetic Sender <sender@example.test>"
    email["To"] = "reader@example.com"
    email["Subject"] = f"SQLite mail decision {uid}"
    email["Date"] = format_datetime(datetime.now(UTC))
    email["Message-ID"] = f"<synthetic-{uid}@example.test>"
    email.set_content(f"Synthetic email {uid}: use SQLite for shared memory.")
    if attachment:
        email.add_attachment(b"ATTACHMENT_SHOULD_NOT_ENTER_MEMORY", maintype="text",
                             subtype="plain", filename="private.txt")
    return email.as_bytes()


class FakeMailClient:
    def __init__(self):
        self.messages = {1: message(1, attachment=True), 2: message(2)}
        self.calls = ()
        self.uid_validity = 123
        self.before_fetch = None
        self.fetch_error = None

    @contextmanager
    def open(self, source, password, check_stop=lambda: None):
        check_stop()
        self.calls = (*self.calls, ("open", source["host"], password))
        remote = self

        class Session:
            uid_validity = remote.uid_validity
            uid_next = max(remote.messages, default=0) + 1

            def search(self, start_uid, end_uid, since=None):
                check_stop()
                remote.calls = (*remote.calls, ("search", start_uid, end_uid, since))
                return tuple(uid for uid in sorted(remote.messages)
                             if start_uid <= uid <= end_uid)

            def fetch(self, uid):
                check_stop()
                remote.calls = (*remote.calls, ("fetch", uid))
                if remote.before_fetch:
                    callback, remote.before_fetch = remote.before_fetch, None
                    callback()
                if remote.fetch_error:
                    raise remote.fetch_error
                return remote.messages[uid]

        try:
            yield Session()
        finally:
            self.calls = (*self.calls, ("close",))


@pytest.fixture
def mailbox(tmp_path):
    vault, tester, remote = MemoryVault(), FakeMailTester(), FakeMailClient()
    app = create_app(data_dir=tmp_path, vault=vault, transport=FakeTransport(),
                     mail_tester=tester, mail_client=remote)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = assert_success(client.get("/api/bootstrap"))["csrf_token"]
        harness = ProviderHarness(client, {"Origin": ORIGIN, "X-Brain-CSRF": csrf},
                                  vault, tester, app, tmp_path)
        source = assert_success(harness.add(mail_lookback_days=0))["source"]
        yield harness, remote, source


def import_job(harness, source):
    job_id = assert_success(harness.client.post("/api/memory/import", headers=harness.headers,
                                                json={"source_id": source["id"]}))["job_id"]
    return wait_job(harness.client, job_id)


def test_mail_import_deduplicates_new_arrivals_searches_mcp_and_disconnects(mailbox):
    harness, remote, source = mailbox
    assert remote.calls == ()  # Saving and checking do not start content reads.
    first = import_job(harness, source)
    assert first["status"] == "completed", first
    assert first["stats"]["imported"] == 2
    service = harness.app.state.memory_service
    hits = service.search("SQLite")["results"]
    assert len(hits) == 2 and all(row["source_kind"] == "imap" for row in hits)
    for row in hits:
        record = assert_success(harness.client.get(
            f"/api/memory/records/{row['id']}"
        ))["record"]
        assert "ATTACHMENT_SHOULD_NOT_ENTER_MEMORY" not in record["text"]
    tools = MemoryTools(service.store, harness.directory)
    assert len(tools.search_memory("SQLite")["results"]) == 2
    fetched = len([call for call in remote.calls if call[0] == "fetch"])
    again = import_job(harness, source)
    assert again["status"] == "completed" and again["stats"]["imported"] == 0
    assert len([call for call in remote.calls if call[0] == "fetch"]) == fetched
    remote.messages = {**remote.messages, 3: message(3)}
    assert import_job(harness, source)["stats"]["imported"] == 1
    assert len(tools.search_memory("SQLite")["results"]) == 3
    assert_success(harness.client.request("DELETE", f"/api/sources/{source['id']}",
                                         json={}, headers=harness.headers))
    assert tools.search_memory("SQLite")["results"] == [] and not harness.vault.entries
    with service.store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM mail_sync_progress WHERE source_id=?",
                                  (source["id"],)).fetchone()[0] == 0


def test_mail_periodic_sync_respects_pause_and_manual_import_works_while_paused(mailbox):
    harness, remote, source = mailbox
    service = harness.app.state.memory_service
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                           process_cards=False, paused=True))
    service.tick(now=10**12)
    assert remote.calls == ()
    service.set_sync_paused(False)
    service.tick(now=10**12)
    job = wait_job(harness.client, service.status()["jobs"][0]["id"])
    assert job["status"] == "completed" and job["stats"]["imported"] == 2
    service.set_sync_paused(True)
    remote.messages = {**remote.messages, 3: message(3)}
    assert import_job(harness, source)["stats"]["imported"] == 1


def test_automatic_pause_before_commit_preserves_uncommitted_mail_for_manual_resume(mailbox):
    harness, remote, source = mailbox
    service = harness.app.state.memory_service
    service.update_schedule(ScheduleConfig(enabled=True, auto_import_enabled=False,
                                           process_cards=False))
    remote.before_fetch = lambda: service.set_sync_paused(True)
    service.tick(now=10**12)
    job = wait_job(harness.client, service.status()["jobs"][0]["id"])
    assert job["status"] == "error" and service.status()["records"] == 0
    resumed = import_job(harness, source)
    assert resumed["status"] == "completed" and resumed["stats"]["imported"] == 2


def test_network_failure_does_not_skip_messages_on_next_import(mailbox):
    harness, remote, source = mailbox
    remote.fetch_error = AppError("mail_network_error", "Не удалось прочитать письмо.", 502)
    failed = import_job(harness, source)
    assert failed["status"] == "error" or failed["stats"]["errors"]
    assert harness.app.state.memory_service.status()["records"] == 0
    remote.fetch_error = None
    retried = import_job(harness, source)
    assert retried["status"] == "completed" and retried["stats"]["imported"] == 2


def test_missing_password_never_opens_mail_connection(mailbox):
    harness, remote, source = mailbox
    harness.vault.entries = {}
    failed = import_job(harness, source)
    assert failed["status"] == "error" and remote.calls == ()
    assert PASSWORD not in json.dumps(failed)


def test_mail_backfill_is_bounded_and_new_arrivals_are_not_lost(mailbox):
    harness, remote, source = mailbox
    remote.messages = {uid: message(uid) for uid in range(1, 52)}
    first = import_job(harness, source)
    assert first["status"] == "completed" and first["stats"]["imported"] == 50
    assert first["stats"]["has_more"] is True
    assert first["stats"]["files_deferred"] >= 1
    remote.messages = {**remote.messages, 52: message(52)}
    second = import_job(harness, source)
    assert second["status"] == "completed" and second["stats"]["imported"] == 2
    assert harness.app.state.memory_service.status()["records"] == 52
    assert import_job(harness, source)["stats"]["imported"] == 0
    fetched = [call[1] for call in remote.calls if call[0] == "fetch"]
    assert len(fetched) == len(set(fetched)) == 52


def test_disabling_mail_import_keeps_cached_memory_but_blocks_new_reads(mailbox):
    harness, remote, source = mailbox
    assert import_job(harness, source)["status"] == "completed"
    service = harness.app.state.memory_service
    with service.store._connection() as connection:
        progress = connection.execute(
            "SELECT state FROM mail_sync_progress WHERE source_id=?", (source["id"],)
        ).fetchone()[0]
    calls, credentials = remote.calls, dict(harness.vault.entries)
    disabled = assert_success(harness.client.post(
        f"/api/sources/{source['id']}/mail-import",
        json={"enabled": False}, headers=harness.headers,
    ))["source"]
    assert disabled["mail_import_enabled"] is False
    response = harness.client.post("/api/memory/import", headers=harness.headers,
                                   json={"source_id": source["id"]})
    assert response.status_code == 404 and remote.calls == calls
    assert harness.vault.entries == credentials
    assert len(service.search("SQLite")["results"]) == 2
    tools = MemoryTools(service.store, harness.directory)
    assert len(tools.search_memory("SQLite")["results"]) == 2
    enabled = assert_success(harness.client.post(
        f"/api/sources/{source['id']}/mail-import",
        json={"enabled": True}, headers=harness.headers,
    ))["source"]
    assert enabled["mail_import_enabled"] is True and remote.calls == calls
    with service.store._connection() as connection:
        assert connection.execute(
            "SELECT state FROM mail_sync_progress WHERE source_id=?", (source["id"],)
        ).fetchone()[0] == progress
    remote.messages = {**remote.messages, 3: message(3)}
    resumed = import_job(harness, source)
    assert resumed["status"] == "completed" and resumed["stats"]["imported"] == 1
    assert len(tools.search_memory("SQLite")["results"]) == 3


def test_disconnection_during_import_never_recreates_records_or_progress(mailbox):
    harness, remote, source = mailbox
    service = harness.app.state.memory_service
    remote.before_fetch = lambda: service.disconnect_source(source["id"])
    job = import_job(harness, source)
    assert job["status"] == "error" and service.status()["records"] == 0
    assert assert_success(harness.client.get("/api/sources"))["sources"] == []
    assert not harness.vault.entries
    with service.store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM mail_sync_progress WHERE source_id=?",
                                  (source["id"],)).fetchone()[0] == 0


@pytest.mark.parametrize("enabled", [None, "false", "true", 0, 1, [], {}])
def test_mail_import_toggle_rejects_non_boolean_values_without_changing_source(providers, enabled):
    source = assert_success(providers.add())["source"]
    response = providers.client.post(f"/api/sources/{source['id']}/mail-import",
                                     json={"enabled": enabled}, headers=providers.headers)
    assert response.status_code == 422 and providers.tester.calls == ()
    saved = assert_success(providers.client.get("/api/sources"))["sources"][0]
    assert saved["mail_import_enabled"] is True


def test_legacy_mail_import_can_be_enabled_explicitly_without_reentering_password(providers):
    source = assert_success(providers.client.post("/api/sources", headers=providers.headers, json={
        "kind": "imap", "name": "Legacy custom", "host": "mail.example.com",
        "username": "LegacyReader", "password": PASSWORD,
    }))["source"]
    credentials = dict(providers.vault.entries)
    enabled = assert_success(providers.client.post(
        f"/api/sources/{source['id']}/mail-import",
        json={"enabled": True}, headers=providers.headers,
    ))["source"]
    assert enabled["id"] == source["id"] and enabled["mail_import_enabled"] is True
    assert enabled["host"] == source["host"] and enabled["username"] == source["username"]
    assert providers.vault.entries == credentials and providers.tester.calls == ()


def test_mail_import_toggle_rejects_nonmail_and_missing_sources(providers):
    folder = assert_success(providers.client.post("/api/sources", headers=providers.headers, json={
        "kind": "folder", "name": "Synthetic notes", "path": str(providers.directory),
    }))["source"]
    for source_id in (folder["id"], "00000000-0000-0000-0000-000000000000"):
        response = providers.client.post(f"/api/sources/{source_id}/mail-import",
                                         json={"enabled": True}, headers=providers.headers)
        assert response.status_code in (400, 404)
    assert providers.tester.calls == ()


def test_mail_import_toggle_requires_csrf_without_mutating_settings(providers):
    source = assert_success(providers.add())["source"]
    response = providers.client.post(f"/api/sources/{source['id']}/mail-import",
                                     json={"enabled": False}, headers={"Origin": ORIGIN})
    assert response.status_code == 403
    saved = assert_success(providers.client.get("/api/sources"))["sources"][0]
    assert saved["mail_import_enabled"] is True and providers.tester.calls == ()
