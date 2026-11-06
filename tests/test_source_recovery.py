"""Restored sources require explicit local repair and fresh credential consent."""

import pytest
from conftest import ORIGIN, FakeTransport, MemoryVault
from fastapi.testclient import TestClient

from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore
from brain_settings.providers import EndpointRequest, SaveRequest, SelectionRequest, Settings
from brain_settings.service import SettingsService
from brain_settings.source_storage import Source, SourcesStore
from brain_settings.sources import SourceService, source_credential_scope

SID = "00000000-0000-4000-8000-000000000066"


class CountingVault(MemoryVault):
    def __init__(self):
        super().__init__()
        self.reads = []

    def get(self, scope):
        self.reads.append(scope)
        return super().get(scope)


def restored_source(kind="github", **changes):
    return Source(
        id=SID, name="Restored synthetic", kind=kind, requires_reauthentication=True, **changes
    )


@pytest.mark.parametrize(
    "kind,method,changes",
    [
        ("github", "github_token", {"github_repositories": ("synthetic/project",)}),
        ("notion", "notion_token", {"notion_page_ids": ("00000000-0000-4000-8000-000000000067",)}),
        (
            "imap",
            "mail_password",
            {"host": "imap.synthetic.example", "username": "user@example.com"},
        ),
        (
            "outlook",
            "outlook_credentials",
            {
                "outlook_account_id": "synthetic-account",
                "outlook_client_id": "00000000-0000-4000-8000-000000000068",
            },
        ),
    ],
)
def test_restored_remote_surfaces_never_read_existing_keychain(tmp_path, kind, method, changes):
    vault = CountingVault()
    source = restored_source(kind, **changes)
    vault.set(source_credential_scope(source), "synthetic old credential")
    service = SourceService(tmp_path, vault)
    service.store.save((source,))
    assert service.list_sources()["sources"][0]["credential_saved"] is False
    with pytest.raises(AppError) as caught:
        getattr(service, method)(SID)
    assert caught.value.code == "source_reauthentication_required"
    assert vault.reads == []


def test_restored_model_status_and_calls_do_not_read_keychain(tmp_path):
    vault = CountingVault()
    service = SettingsService(tmp_path, vault=vault, transport=FakeTransport())
    service.store.save(Settings(model_id="synthetic/model", requires_reauthentication=True))
    assert service.bootstrap("synthetic-csrf")["token_saved"] is False
    request = EndpointRequest(provider="openrouter", base_url="https://openrouter.ai/api/v1")
    assert service.credential_status(request)["token_saved"] is False
    with pytest.raises(AppError) as caught:
        service._token(
            SelectionRequest(
                provider=request.provider, base_url=request.base_url, model_id="synthetic/model"
            ),
            "synthetic-scope",
        )
    assert caught.value.code == "model_reauthentication_required"
    assert vault.reads == []


def test_model_reauth_cannot_be_cleared_by_saving_without_new_token(tmp_path):
    vault = CountingVault()
    service = SettingsService(tmp_path, vault=vault, transport=FakeTransport())
    service.store.save(Settings(requires_reauthentication=True))
    request = SaveRequest(
        provider="openrouter", base_url="https://openrouter.ai/api/v1", model_id="synthetic/model"
    )
    with pytest.raises(AppError) as caught:
        service.save(request)
    assert caught.value.code == "model_reauthentication_required"
    assert vault.reads == []
    saved = service.save(
        SaveRequest.model_validate(request.model_dump() | {"token": "synthetic-new-token"})
    )
    assert saved["settings"]["requires_reauthentication"] is False


def test_source_reauth_preserves_identity_and_clears_marker_after_explicit_token(tmp_path):
    from brain_settings.source_relocation import SourceReauthenticationRequest

    service = SourceService(tmp_path, CountingVault())
    source = restored_source(github_repositories=("synthetic/project",))
    service.store.save((source,))
    synthetic_fixture = "synthetic-new-token"
    result = service.reauthenticate(SID, SourceReauthenticationRequest(token=synthetic_fixture))
    assert result["source"]["id"] == SID
    assert result["source"]["requires_reauthentication"] is False
    assert service.github_token(SID) == synthetic_fixture


def test_relocation_uses_new_selected_root_and_preserves_historical_uri(tmp_path):
    from brain_settings.source_relocation import SourceRelocationRequest

    directory = tmp_path / "data"
    service = SourceService(directory, MemoryVault())
    store = MemoryStore(directory)
    service.memory_store = store
    selected = tmp_path / "selected"
    selected.mkdir()
    note = selected / "note.md"
    note.write_text("Synthetic local note")
    source = Source(
        id=SID, name="Restored", kind="folder", path="/synthetic/old", requires_relocation=True
    )
    service.store.save((source,))
    record = {
        "source_id": SID,
        "source_kind": "folder",
        "source_object_id": "note.md::chunk:0",
        "source_uri": "file:///synthetic/old/note.md",
    }
    assert service.open_target(record) is None
    service.relocate(SID, SourceRelocationRequest(path=str(selected)))
    assert service.open_target(record) == note.as_uri()
    assert record["source_uri"] == "file:///synthetic/old/note.md"
    assert service.open_target({**record, "source_object_id": "../outside::chunk:0"}) is None
    assert service.open_target({**record, "source_object_id": "[REDACTED]::file:opaque"}) is None


def test_relocation_rejects_symlink_roots_and_opaque_notes_do_not_open(tmp_path):
    from brain_settings.source_relocation import SourceRelocationRequest

    service = SourceService(tmp_path / "data", MemoryVault())
    root = tmp_path / "root"
    root.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(root, target_is_directory=True)
    service.store.save((Source(id=SID, name="Restored", kind="folder", requires_relocation=True),))
    with pytest.raises(AppError):
        service.relocate(SID, SourceRelocationRequest(path=str(link)))
    assert service.store.load()[0].requires_relocation is True
    assert (
        service.open_target(
            {"source_id": SID, "source_kind": "apple_notes", "source_uri": "opaque-note-id"}
        )
        is None
    )


def test_import_policy_cannot_enable_before_source_repair(tmp_path):
    from brain_settings.app import create_app
    from brain_settings.source_policy import SourcePolicy

    root = tmp_path / "data"
    SourcesStore(root).save((restored_source(github_repositories=("synthetic/project",)),))
    app = create_app(data_dir=root, vault=MemoryVault(), transport=FakeTransport())
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        response = client.put(
            f"/api/sources/{SID}/policy",
            json=SourcePolicy().model_dump(mode="json"),
            headers={"Origin": ORIGIN, "X-Brain-CSRF": csrf},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "source_reauthentication_required"


@pytest.mark.parametrize("matching", [True, False])
def test_outlook_reauth_requires_matching_fresh_ticket_and_preserves_source_id(tmp_path, matching):
    import time

    from brain_settings.source_relocation import SourceReauthenticationRequest

    client_id = "00000000-0000-4000-8000-000000000068"
    credentials = {
        "client_id": client_id,
        "account_id": "original" if matching else "different",
        "username": "user@example.com",
        "access_token": "synthetic-access",
        "refresh_token": "synthetic-refresh",
        "expires_at": time.time() + 3600,
    }

    class Tickets:
        finished = False
        restored = False

        def take(self, auth_id):
            return credentials

        def is_claim_active(self, auth_id):
            return True

        def finish(self, auth_id):
            self.finished = True
            return True

        def restore(self, auth_id, value):
            self.restored = value == credentials
            return True

    auth, vault = Tickets(), CountingVault()
    service = SourceService(tmp_path, vault, outlook_auth=auth)
    service.store.save(
        (
            restored_source(
                "outlook",
                outlook_client_id=client_id,
                outlook_account_id="original",
                username="user@example.com",
            ),
        )
    )
    request = SourceReauthenticationRequest(outlook_auth_id="synthetic-ticket-0000000000000000")
    if matching:
        result = service.reauthenticate(SID, request)
        assert result["source"]["id"] == SID
        assert result["source"]["requires_reauthentication"] is False and auth.finished
        assert len(vault.entries) == 1
    else:
        with pytest.raises(AppError):
            service.reauthenticate(SID, request)
        assert auth.restored and not auth.finished
        assert service.store.load()[0].requires_reauthentication is True
        assert vault.entries == {} and vault.reads == []


def test_restored_scope_test_does_not_scan_old_machine_path(tmp_path, monkeypatch):
    import brain_settings.sources as module

    service = SourceService(tmp_path, MemoryVault())
    service.store.save(
        (
            Source(
                id=SID,
                kind="folder",
                name="Restored",
                path="/synthetic/old",
                requires_relocation=True,
            ),
        )
    )
    monkeypatch.setattr(module, "scan_local", lambda *a: pytest.fail("Scanned old machine path"))
    with pytest.raises(AppError) as caught:
        service.test(SID)
    assert caught.value.code == "source_relocation_required"


def test_model_generator_reauth_guard_precedes_keychain_or_network(tmp_path):
    from brain_settings.llm_cards import LLMCardGenerator

    vault = CountingVault()
    service = SettingsService(tmp_path, vault=vault, transport=FakeTransport())
    service.store.save(Settings(model_id="synthetic/model", requires_reauthentication=True))
    with pytest.raises(AppError) as caught:
        LLMCardGenerator(service)._configuration()
    assert caught.value.code == "model_reauthentication_required"
    assert vault.reads == []


@pytest.mark.parametrize("code", ["memory_maintenance", "restore_recovery_required"])
def test_bootstrap_does_not_fallback_to_keychain_during_maintenance(tmp_path, monkeypatch, code):
    vault = CountingVault()
    service = SettingsService(tmp_path, vault=vault, transport=FakeTransport())

    def blocked():
        raise AppError(code, "Synthetic blocked read", 503)

    monkeypatch.setattr(service.store, "load", blocked)
    with pytest.raises(AppError) as caught:
        service.bootstrap("synthetic-csrf")
    assert caught.value.code == code
    assert vault.reads == []


@pytest.mark.parametrize("write_fails", [False, True])
def test_deleting_unreauthenticated_restored_source_never_touches_coincident_key(
    tmp_path, monkeypatch, write_fails
):
    vault = CountingVault()
    service = SourceService(tmp_path, vault)
    source = restored_source(github_repositories=("synthetic/project",))
    scope = source_credential_scope(source)
    vault.set(scope, "synthetic shared credential")
    service.store.save((source,))
    if write_fails:

        def blocked(*args):
            raise AppError("sources_write_failed", "Synthetic source failure", 503)

        monkeypatch.setattr(service.store, "save", blocked)
        with pytest.raises(AppError):
            service.delete(SID)
    else:
        assert service.delete(SID)["deleted"] is True
    assert vault.reads == []
    assert vault.entries[scope] == "synthetic shared credential"


def test_relocation_refuses_active_writer_and_clears_only_selected_source_progress(tmp_path):
    from brain_settings.background import acquire_writer_lease, release_writer_lease
    from brain_settings.source_relocation import SourceRelocationRequest

    root = tmp_path / "data"
    service = SourceService(root, MemoryVault())
    store = MemoryStore(root)
    service.memory_store = store
    service.store.save((Source(id=SID, kind="folder", name="Restored", requires_relocation=True),))
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    from brain_settings.apple_notes_import import AppleNotesProgress
    from brain_settings.import_progress import ImportProgress

    ImportProgress(store, SID)
    AppleNotesProgress(store, SID)
    with store._connection(write=True) as connection:
        for identifier in (SID, "other"):
            connection.execute(
                "INSERT INTO import_checkpoints VALUES(?,?,?,?,?,?,?)",
                (identifier, "old", "[]", "synthetic", 1, "[]", "done"),
            )
            connection.execute(
                "INSERT INTO apple_notes_sync_progress VALUES(?,?,?)", (identifier, "old", "{}")
            )
    lease = acquire_writer_lease(root)
    try:
        with pytest.raises(AppError) as caught:
            service.relocate(SID, SourceRelocationRequest(path=str(chosen)))
        assert caught.value.code == "memory_busy"
        assert service.store.load()[0].requires_relocation is True
    finally:
        release_writer_lease(lease)
    service.relocate(SID, SourceRelocationRequest(path=str(chosen)))
    with store._connection() as connection:
        for table in ("import_checkpoints", "apple_notes_sync_progress"):
            assert [row[0] for row in connection.execute(f"SELECT source_id FROM {table}")] == [
                "other"
            ]


def test_notes_policy_retains_historical_exclusions_and_rejects_invented_ids(tmp_path):
    from brain_settings.app import create_app
    from brain_settings.source_policy import SourcePolicy

    root = tmp_path / "data"
    source = Source(
        id=SID, kind="apple_notes", name="Repaired Notes", notes_folder_ids=("new-folder",)
    )
    SourcesStore(root).save((source,))
    app = create_app(data_dir=root, vault=MemoryVault(), transport=FakeTransport())
    app.state.memory_service.store.set_source_policy(
        SID, SourcePolicy(import_enabled=False, excluded_notes_folder_ids=("old-private-folder",))
    )
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/bootstrap").json()["data"]["csrf_token"]
        headers = {"Origin": ORIGIN, "X-Brain-CSRF": csrf}
        candidate = SourcePolicy(excluded_notes_folder_ids=("old-private-folder", "new-folder"))
        response = client.put(
            f"/api/sources/{SID}/policy", json=candidate.model_dump(mode="json"), headers=headers
        )
        assert response.status_code == 200
        invented = candidate.model_copy(update={"excluded_notes_folder_ids": ("invented",)})
        denied = client.put(
            f"/api/sources/{SID}/policy", json=invented.model_dump(mode="json"), headers=headers
        )
        assert denied.status_code == 400
