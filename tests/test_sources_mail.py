import json
from dataclasses import dataclass

import pytest
from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient
from test_settings_api import assert_success

from brain_settings.app import create_app
from brain_settings.errors import AppError

SYNTHETIC_PASSWORD = "synthetic-imap-app-password-not-real"


class FakeMailTester:
    def __init__(self, count=7, error=None):
        self.count = count
        self.error = error
        self.calls = ()

    def test(self, *, host, username, password, port=993, mailbox="INBOX"):
        self.calls = (*self.calls, {
            "host": host, "username": username, "password": password,
            "port": port, "mailbox": mailbox,
        })
        if self.error is not None:
            raise self.error
        return self.count


@dataclass(frozen=True)
class MailHarness:
    client: TestClient
    vault: MemoryVault
    tester: FakeMailTester
    data_dir: object

    @property
    def headers(self):
        csrf = self.client.get("/api/bootstrap").json()["data"]["csrf_token"]
        return {"Origin": ORIGIN, "X-Brain-CSRF": csrf}

    def post(self, path, payload):
        return self.client.post(path, json=payload, headers=self.headers)


@pytest.fixture
def mail_harness(tmp_path):
    vault, tester = MemoryVault(), FakeMailTester()
    app = create_app(data_dir=tmp_path, vault=vault, transport=FakeTransport(), mail_tester=tester)
    with TestClient(app, base_url=ORIGIN) as client:
        yield MailHarness(client, vault, tester, tmp_path)


@pytest.fixture
def mail_selection():
    return {
        "kind": "imap", "name": "Тестовая почта", "host": "mail.example.com",
        "username": "reader@example.com", "password": SYNTHETIC_PASSWORD,
        "port": 993, "mailbox": "INBOX",
    }


def test_mail_configuration_saves_only_scoped_keychain_password(mail_harness, mail_selection):
    response = mail_harness.post("/api/sources", mail_selection)
    source = assert_success(response)["source"]
    assert source["kind"] == "imap"
    assert source["credential_saved"] is True
    assert source["status"] == "not_checked"
    assert source["item_count"] is None
    assert mail_harness.tester.calls == ()
    assert tuple(mail_harness.vault.entries.values()) == (SYNTHETIC_PASSWORD,)
    assert SYNTHETIC_PASSWORD not in response.text
    for path in mail_harness.data_dir.rglob("*"):
        if path.is_file():
            assert SYNTHETIC_PASSWORD.encode() not in path.read_bytes()
    sources = assert_success(mail_harness.client.get("/api/sources"))["sources"]
    assert SYNTHETIC_PASSWORD not in json.dumps(sources)
    assert sources[0]["username"] == "reader@example.com"


def test_mail_check_uses_saved_credentials_only_after_user_action(mail_harness, mail_selection):
    source = assert_success(mail_harness.post("/api/sources", mail_selection))["source"]
    response = mail_harness.post(f"/api/sources/{source['id']}/test", {})
    checked = assert_success(response)["source"]
    assert checked["status"] == "ok"
    assert checked["item_count"] == 7
    assert checked["last_checked_at"]
    assert mail_harness.tester.calls == ({
        "host": "mail.example.com", "username": "reader@example.com",
        "password": SYNTHETIC_PASSWORD, "port": 993, "mailbox": "INBOX",
    },)
    assert SYNTHETIC_PASSWORD not in response.text


def test_mail_credentials_are_independent_even_for_same_host_and_username(
    mail_harness, mail_selection,
):
    first = assert_success(mail_harness.post("/api/sources", mail_selection))["source"]
    second_password = "synthetic-second-source-password"
    second = assert_success(mail_harness.post("/api/sources", {
        **mail_selection, "name": "Вторая почта", "password": second_password,
    }))["source"]
    assert first["id"] != second["id"]
    assert len(mail_harness.vault.entries) == 2
    response = mail_harness.client.request(
        "DELETE", f"/api/sources/{first['id']}", json={}, headers=mail_harness.headers,
    )
    assert assert_success(response)["deleted"] is True
    assert tuple(mail_harness.vault.entries.values()) == (second_password,)
    remaining = assert_success(mail_harness.client.get("/api/sources"))["sources"]
    assert remaining[0]["id"] == second["id"]


@pytest.mark.parametrize("changes", [
    {"password": None}, {"password": ""}, {"password": "line1\r\nline2"},
    {"port": 143}, {"mailbox": "INBOX\r\nFETCH"}, {"host": "127.0.0.1"},
    {"host": "::1"}, {"host": "169.254.169.254"}, {"host": "https://mail.example.com"},
    {"username": ""},
    {"username": "reader other"}, {"username": 'reader"other'},
    {"username": "reader\\other"}, {"username": "reader%other"},
    {"username": "reader(other"}, {"username": "reader\x7fother"},
    {"password": "   "}, {"password": "synthetic\x7fpassword"},
    {"host": "localhost"}, {"host": "mail.internal"},
])
def test_unsafe_mail_configuration_is_rejected_without_connecting(
    mail_harness, mail_selection, changes,
):
    response = mail_harness.post("/api/sources", {**mail_selection, **changes})
    assert response.status_code in (400, 422)
    assert response.json()["success"] is False
    assert mail_harness.tester.calls == ()
    assert mail_harness.vault.entries == {}
    assert SYNTHETIC_PASSWORD not in response.text


def test_mail_auth_failure_is_visible_and_persisted_without_exposing_credentials(
    mail_harness, mail_selection,
):
    source = assert_success(mail_harness.post("/api/sources", mail_selection))["source"]
    mail_harness.tester.error = AppError("mail_auth_failed", "Не удалось войти в почту.", 401)
    response = mail_harness.post(f"/api/sources/{source['id']}/test", {})
    assert response.status_code >= 400
    assert response.json()["success"] is False
    assert SYNTHETIC_PASSWORD not in response.text
    saved = assert_success(mail_harness.client.get("/api/sources"))["sources"][0]
    assert saved["status"] == "error"
    assert saved["message"]
    assert saved["last_checked_at"]


def test_unexpected_mail_failure_does_not_expose_raw_server_text(mail_harness, mail_selection):
    source = assert_success(mail_harness.post("/api/sources", mail_selection))["source"]
    mail_harness.tester.error = RuntimeError("Synthetic server echoed " + SYNTHETIC_PASSWORD)
    response = mail_harness.post(f"/api/sources/{source['id']}/test", {})
    assert response.status_code >= 400
    assert SYNTHETIC_PASSWORD not in response.text
    assert response.json()["success"] is False


def test_mail_password_save_failure_does_not_create_connection(tmp_path, mail_selection):
    class FailingVault(MemoryVault):
        def set(self, scope, token):
            raise RuntimeError("Synthetic vault error " + SYNTHETIC_PASSWORD)

    tester = FakeMailTester()
    app = create_app(
        data_dir=tmp_path, vault=FailingVault(), transport=FakeTransport(), mail_tester=tester,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        response = client.post("/api/sources", json=mail_selection, headers={
            "Origin": ORIGIN, "X-Brain-CSRF": csrf,
        })
        assert response.status_code == 503
        assert SYNTHETIC_PASSWORD not in response.text
        assert assert_success(client.get("/api/sources"))["sources"] == []
    assert tester.calls == ()


def test_mail_password_delete_failure_keeps_connection_for_retry(tmp_path, mail_selection):
    class FailingDeleteVault(MemoryVault):
        def delete(self, scope):
            raise RuntimeError("Synthetic vault error " + SYNTHETIC_PASSWORD)

    vault = FailingDeleteVault()
    app = create_app(
        data_dir=tmp_path, vault=vault, transport=FakeTransport(), mail_tester=FakeMailTester(),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        headers = {"Origin": ORIGIN, "X-Brain-CSRF": csrf}
        source = assert_success(client.post(
            "/api/sources", json=mail_selection, headers=headers,
        ))["source"]
        response = client.request(
            "DELETE", f"/api/sources/{source['id']}", json={}, headers=headers,
        )
        assert response.status_code == 503
        assert SYNTHETIC_PASSWORD not in response.text
        sources = assert_success(client.get("/api/sources"))["sources"]
        assert len(sources) == 1
        assert sources[0]["id"] == source["id"]
    assert tuple(vault.entries.values()) == (SYNTHETIC_PASSWORD,)
