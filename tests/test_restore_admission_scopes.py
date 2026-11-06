"""Restore boundaries cover configuration and admission before a writer starts."""

import pytest
from test_restore_read_scopes import setup_scope

from brain_settings.errors import AppError


def test_embedding_probe_cannot_reenable_index_across_restore(tmp_path):
    memory, backups, inspection, _, _ = setup_scope(tmp_path)

    class SyntheticClient:
        def embedding_catalog(self):
            return [{"id": "synthetic-embed", "digest": "synthetic-digest"}]

        def embed(self, *args, **kwargs):
            backups.restore(inspection)
            return {"embeddings": [[1.0, 0.0]]}

    memory.embeddings.client = SyntheticClient()
    with pytest.raises(AppError) as caught:
        memory.configure_embeddings(True, "synthetic-embed")
    assert caught.value.code == "maintenance_lock_upgrade"
    backups.restore(inspection)
    assert memory.embedding_settings()["enabled"] is False


@pytest.mark.parametrize(
    ("surface", "hook"),
    [
        ("import", "_import_sources"),
        ("cards", "_provider_scope"),
        ("embeddings", "_check_embedding_work"),
        ("facts", "_check_stop"),
        ("retry", "_retry_processing_state"),
        ("resume", "_provider_scope"),
        ("disconnect", "_find"),
        ("tick", "_import_sources"),
    ],
)
def test_configuration_to_admission_is_one_maintenance_scope(
    tmp_path, monkeypatch, surface, hook
):
    memory, backups, inspection, _, _ = setup_scope(tmp_path)
    target = memory.source_service if surface == "disconnect" else memory
    original, fired = getattr(target, hook), False

    def interleave_restore(*args, **kwargs):
        nonlocal fired
        result = original(*args, **kwargs)
        if not fired:
            fired = True
            backups.restore(inspection)
        return result

    # An unguarded admission must fail before it can start synthetic background work.
    monkeypatch.setattr(memory, "_begin", lambda *args: None)
    monkeypatch.setattr(target, hook, interleave_restore)
    calls = {
        "import": memory.start_import,
        "cards": memory.start_cards,
        "embeddings": memory.start_embedding_index,
        "facts": memory.start_fact_projection,
        "retry": memory.retry_all_card_errors,
        "resume": memory.resume_provider,
        "disconnect": lambda: memory.disconnect_source(memory._sources()[0]["id"]),
        "tick": memory.tick,
    }
    with pytest.raises(AppError) as caught:
        calls[surface]()
    assert caught.value.code == "maintenance_lock_upgrade"
    assert fired
    backups.restore(inspection)
    assert memory.source_service.store.load()[0].enabled is False
