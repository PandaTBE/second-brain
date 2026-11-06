"""Synthetic Outlook archives retain scoped cursors and source deletion boundaries."""

from uuid import uuid4

import pytest

from brain_settings.errors import AppError
from brain_settings.mcp_server import MemoryTools
from brain_settings.memory_store import MemoryStore
from brain_settings.outlook_import import import_outlook
from brain_settings.outlook_progress import OutlookProgress


def email(identifier="CaseSensitive", **updates):
    return {"id": identifier, "subject": "Synthetic astronomy", "text": "Read about astronomy.",
            "source_uri": "https://outlook.office.com/mail/inbox/id/demo",
            "created_at": "2026-11-01T12:00:00Z", "updated_at": "2026-11-02T12:00:00Z",
            **updates}


class Client:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def messages(self, token, lookback_days, next_link=None):
        self.calls.append((token, lookback_days, next_link))
        return self.pages.pop(0)


def page(messages=(), next_link=None):
    return {"messages": list(messages), "next_link": next_link, "skipped": 0, "warnings": []}


@pytest.fixture
def setup(tmp_path):
    return {"id": str(uuid4()), "kind": "outlook", "mail_import_enabled": True,
            "mail_lookback_days": 30}, MemoryStore(tmp_path)


def credentials():
    return {"access_token": "demo"}


def test_import_search_mcp_and_repeated_reconciliation_are_idempotent(setup):
    source, store = setup
    client = Client(page([email()]), page([email()]))
    assert import_outlook(source, store, client, credentials)["imported"] == 1
    assert import_outlook(source, store, client, credentials)["unchanged"] == 1
    result = MemoryTools(store).search_memory("astronomy")
    assert result["results"][0]["source_kind"] == "outlook"
    assert not OutlookProgress(store, source["id"]).load()["next_link"]


def test_each_pass_reads_one_page_and_next_pass_resumes_after_restart(setup):
    source, store = setup
    link = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skiptoken=demo"
    first = Client(page([email(str(i)) for i in range(50)], link))
    result = import_outlook(source, store, first, credentials)
    assert result["imported"] == 50 and result["has_more"] and len(first.calls) == 1
    second = Client(page([email("CaseSensitive")]))
    result = import_outlook(source, store, second, credentials)
    assert second.calls[0][2] == link and result["imported"] == 1 and not result["has_more"]
    assert store.status()["documents"] == 51


def test_lookback_change_resets_old_cursor(setup):
    source, store = setup
    link = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skip=50"
    import_outlook(source, store, Client(page([email()], link)), credentials)
    updated = {**source, "mail_lookback_days": 90}
    client = Client(page())
    import_outlook(updated, store, client, credentials)
    assert client.calls[0][1:] == (90, None)


def test_message_update_replaces_prior_chunks_and_preserves_case_sensitive_ids(setup):
    source, store = setup
    import_outlook(source, store, Client(page([email("ABC"), email("abc")])), credentials)
    assert store.status()["documents"] == 2
    client = Client(page([email("ABC", text="New astronomy decision")]))
    result = import_outlook(source, store, client, credentials)
    assert result["updated"] == 1 and store.status()["documents"] == 2


def test_redaction_runs_before_chunking(setup):
    source, store = setup
    raw = "synthetic-" + "password"
    row = email(text="password: " + raw + "\nAstronomy details.")
    import_outlook(source, store, Client(page([row])), credentials)
    assert raw not in store.search("astronomy")[0]["text"]


def test_disabled_or_deleted_source_never_reads_credentials_or_network(setup):
    source, store = setup
    client = Client()
    def forbidden():
        raise AssertionError("Credentials must not be read")
    with pytest.raises(AppError):
        import_outlook({**source, "mail_import_enabled": False}, store, client, forbidden)
    store.delete_source(source["id"])
    with pytest.raises(AppError):
        import_outlook(source, store, client, forbidden)
    assert not client.calls


def test_pause_after_fetch_keeps_old_content_and_cursor(setup):
    source, store = setup
    import_outlook(source, store, Client(page([email()])), credentials)
    checks = []
    def stop():
        checks.append(1)
        if len(checks) == 3:
            raise AppError("sync_paused", "Пауза")
    with pytest.raises(AppError):
        import_outlook(source, store, Client(page([email(text="Replacement")])), credentials, stop)
    assert "astronomy" in store.search("astronomy")[0]["text"]


def test_tampered_persisted_cursor_fails_before_credentials_or_request(setup):
    source, store = setup
    progress = OutlookProgress(store, source["id"])
    with store._connection(write=True) as connection:
        connection.execute("INSERT INTO outlook_sync_progress VALUES(?,?)", (
            source["id"], '{"version":1,"lookback":30,"next_link":"https://unrelated.invalid/collect"}'))
    with pytest.raises(AppError):
        import_outlook(source, store, Client(), lambda: pytest.fail("No credentials"))
    with pytest.raises(AppError):
        progress.save({"version": 1, "lookback": 30, "next_link": "x" * 20_000})


def test_deletion_during_fetch_prevents_records_and_progress_resurrection(setup):
    source, store = setup
    class DeletingClient:
        def messages(self, *args, **kwargs):
            store.delete_source(source["id"])
            return page([email()])
    with pytest.raises(AppError):
        import_outlook(source, store, DeletingClient(), credentials)
    assert store.status()["documents"] == 0
