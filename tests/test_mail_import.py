"""Read-only synthetic sessions into temporary SQLite with durable cursors."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from test_mail_message import message

from brain_settings.errors import AppError
from brain_settings.mail_import import import_mail
from brain_settings.mail_progress import MailProgress
from brain_settings.memory_store import MemoryStore


class Session:
    uid_validity = 1

    def __init__(self, count=3):
        self.messages = {uid: message(f"SQLite synthetic decision {uid}").as_bytes()
                         for uid in range(1, count + 1)}
        self.uid_next = count + 1
        self.calls = ()
        self.error = None

    def search(self, start_uid, end_uid, since=None):
        self.calls = (*self.calls, ("search", start_uid, end_uid, since))
        return tuple(uid for uid in sorted(self.messages) if start_uid <= uid <= end_uid)

    def fetch(self, uid):
        self.calls = (*self.calls, ("fetch", uid))
        if self.error is not None:
            raise self.error
        return self.messages[uid]


class FakeMail:
    def __init__(self, count=3):
        self.session = Session(count)
        self.calls = ()

    @contextmanager
    def open(self, source, password, check_stop=lambda: None):
        check_stop()
        self.calls = (*self.calls, (source["id"], password))
        yield self.session


@pytest.fixture
def setup(tmp_path):
    source = {"id": str(uuid4()), "kind": "imap", "host": "imap.gmail.com",
              "username": "reader@example.com", "mail_provider": "gmail",
              "mail_import_enabled": True, "mail_lookback_days": 30}
    return source, MemoryStore(tmp_path), FakeMail()


def test_import_deduplicates_and_finds_email_in_memory(setup):
    source, store, client = setup
    result = import_mail(source, store, client, "synthetic")
    assert result["imported"] == 3 and not result["has_more"]
    assert len(store.search("SQLite")) == 3
    assert import_mail(source, store, client, "synthetic")["imported"] == 0
    assert len([call for call in client.session.calls if call[0] == "fetch"]) == 3


def test_large_inbox_is_bounded_and_continues_after_restart(setup):
    source, store, _ = setup
    client = FakeMail(101)
    first = import_mail(source, store, client, "synthetic")
    assert first["files_processed"] == 50 and first["has_more"]
    second = import_mail(source, MemoryStore(store.path.parent), client, "synthetic")
    assert second["files_processed"] == 50 and second["has_more"]
    third = import_mail(source, store, client, "synthetic")
    assert third["files_processed"] == 1 and not third["has_more"]


def test_new_mail_takes_priority_over_large_backfill(setup):
    source, store, _ = setup
    client = FakeMail(100)
    import_mail(source, store, client, "synthetic")
    client.session.messages[101] = message("Brand new arrival").as_bytes()
    client.session.uid_next = 102
    import_mail(source, store, client, "synthetic")
    fetches = [call[1] for call in client.session.calls if call[0] == "fetch"]
    assert fetches[50] == 101
    assert store.search("arrival")


def test_empty_sparse_windows_advance_and_remain_bounded(setup):
    source, store, client = setup
    client.session.messages = {}
    client.session.uid_next = 1_000_001
    result = import_mail(source, store, client, "synthetic")
    searches = [call for call in client.session.calls if call[0] == "search"]
    assert len(searches) <= 4
    assert all(end - start < 2000 for _, start, end, _ in searches)
    assert result["has_more"]
    assert MailProgress(store, source["id"]).load()["backfill_uid"] < 1_000_000


def test_uidvalidity_change_does_not_overwrite_different_message(setup):
    source, store, client = setup
    import_mail(source, store, client, "synthetic")
    client.session.uid_validity = 2
    client.session.messages = {1: message("New UID mapping different content").as_bytes()}
    client.session.uid_next = 2
    import_mail(source, store, client, "synthetic")
    assert len(store.browse(limit=50)["results"]) == 4


def test_transient_fetch_failure_preserves_cursor_for_retry(setup):
    source, store, client = setup
    client.session.error = AppError("mail_network_error", "Сеть недоступна.", 502)
    result = import_mail(source, store, client, "synthetic")
    assert result["errors"] and result["has_more"]
    client.session.error = None
    assert import_mail(source, store, client, "synthetic")["imported"] == 3


@pytest.mark.parametrize("code", ["sync_paused", "source_deleted", "memory_cancelled"])
def test_stop_callbacks_propagate_before_any_network(setup, code):
    source, store, client = setup
    def stop():
        raise AppError(code, "Остановлено.", 409)
    with pytest.raises(AppError) as caught:
        import_mail(source, store, client, "synthetic", check_stop=stop)
    assert caught.value.code == code and not client.calls


def test_optin_required_and_progress_cannot_resurrect_deleted_source(setup):
    source, store, client = setup
    with pytest.raises(AppError):
        import_mail({**source, "mail_import_enabled": False}, store, client, "synthetic")
    progress = MailProgress(store, source["id"])
    store.delete_source(source["id"])
    with pytest.raises(AppError):
        progress.save({"uid_validity": 1, "live_uid": 2, "backfill_uid": 1})


@pytest.mark.parametrize("bad", ["[]", "invalid", '{"x":' + '"x"' * 2000 + "}"])
def test_corrupt_progress_is_visible_error(setup, bad):
    source, store, _ = setup
    progress = MailProgress(store, source["id"])
    with store._connection(write=True) as connection:
        connection.execute("INSERT INTO mail_sync_progress VALUES(?,?)", (source["id"], bad))
    with pytest.raises(AppError) as caught:
        progress.load()
    assert caught.value.code == "mail_progress"


def test_progress_size_bound_and_unicode_roundtrip(setup):
    source, store, _ = setup
    progress = MailProgress(store, source["id"])
    progress.save({"note": "почта" * 100})
    assert progress.load() == {"note": "почта" * 100}
    with pytest.raises(AppError):
        progress.save({"too_large": "x" * 5000})


def test_identical_message_after_uidvalidity_reset_is_deduplicated(setup):
    source, store, client = setup
    import_mail(source, store, client, "synthetic")
    client.session.uid_validity = 2
    result = import_mail(source, store, client, "synthetic")
    assert result["imported"] == 0 and result["unchanged"] == 3
    assert len(store.search("SQLite")) == 3


def test_large_message_is_explicit_skip_and_does_not_block_other_mail(setup):
    source, store, client = setup
    original = client.session.fetch
    def fetch(uid):
        if uid == 3:
            raise AppError("mail_message_large", "Письмо слишком большое.", 422)
        return original(uid)
    client.session.fetch = fetch
    result = import_mail(source, store, client, "synthetic")
    assert result["imported"] == 2 and result["skipped"] == 1 and result["errors"]
    assert not result["has_more"]
