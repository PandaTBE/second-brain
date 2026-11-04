import json

import pytest

from brain_settings.importers import import_source, redact_text
from brain_settings.memory_store import MemoryStore


def source(path, kind="folder"):
    return {"id": "synthetic-source", "kind": kind, "path": str(path), "name": "Тест"}


def write_lines(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def codex_message(role, text):
    return {
        "type": "response_item",
        "timestamp": "2026-05-01T10:00:00Z",
        "payload": {
            "type": "message",
            "role": role,
            "content": [
                {"type": "output_text" if role == "assistant" else "input_text", "text": text}
            ],
        },
    }


def test_markdown_import_and_append_are_idempotent_with_stable_first_chunks(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    text = "# Решения\n" + ("Первая заметка про архитектуру. " * 400) + "\n\nВторая тема."
    (root / "note.md").write_text(text)
    store = MemoryStore(tmp_path / "data")
    first = import_source(source(root), store)
    assert first["imported"] >= 2
    records = store.search("", limit=50)
    assert all(len(record["text"]) <= 12_000 for record in records)
    unchanged = import_source(source(root), store)
    assert unchanged["unchanged"] == first["imported"]
    first_record = min(records, key=lambda row: row["source_object_id"])
    with (root / "note.md").open("a") as stream:
        stream.write("\n\nТретья тема после завершения работы.")
    import_source(source(root), store)
    assert store.get_record(first_record["id"])["revision"] == 1
    assert all(
        record["source_uri"].startswith((root / "note.md").as_uri()) for record in store.search("")
    )


def test_codex_prefers_response_messages_and_skips_system_tools_and_partial_tail(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    rows = [
        {"type": "session_meta", "payload": {"base_instructions": "SYSTEM_SECRET_TEXT"}},
        codex_message("system", "SYSTEM_SECRET_TEXT"),
        {"type": "event_msg", "payload": {"type": "user_message", "message": "Повтор вопроса"}},
        codex_message("user", "Как исправить ошибку бота?"),
        codex_message("assistant", "Используй повторные попытки."),
        {
            "type": "response_item",
            "payload": {"type": "function_call", "arguments": "TOOL_SECRET_TEXT"},
        },
    ]
    path = root / "session.jsonl"
    write_lines(path, rows)
    with path.open("a") as stream:
        stream.write('{"type":"response_item","payload":')
    store = MemoryStore(tmp_path / "data")
    stats = import_source(source(root, "codex"), store)
    assert stats["imported"] == 1
    record = store.search("")[0]
    assert "Повтор вопроса" not in record["text"]
    assert "Используй повторные попытки" in record["text"]
    assert "SYSTEM_SECRET_TEXT" not in record["text"]
    assert "TOOL_SECRET_TEXT" not in record["text"]
    assert record["updated_at"].startswith("2026-05-01")


def test_codex_event_fallback_and_claude_nested_content(tmp_path):
    codex = tmp_path / "codex"
    claude = tmp_path / "claude"
    codex.mkdir()
    claude.mkdir()
    write_lines(
        codex / "events.jsonl",
        [
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Вопрос"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "Ответ"}},
        ],
    )
    write_lines(
        claude / "dialogue.jsonl",
        [
            {"type": "user", "message": {"role": "user", "content": "Предпочитаю короткие ответы"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Запомнил предпочтение"},
                        {"type": "tool_use", "input": {"value": "TOOL_ONLY_SECRET"}},
                    ],
                },
            },
            {"type": "file-history-snapshot", "snapshot": {"text": "FILE_SNAPSHOT_SECRET"}},
        ],
    )
    store = MemoryStore(tmp_path / "data")
    assert import_source(source(codex, "codex"), store)["imported"] == 1
    assert import_source({**source(claude, "claude"), "id": "second"}, store)["imported"] == 1
    contents = "\n".join(row["text"] for row in store.search("", limit=50))
    assert "Вопрос" in contents and "Запомнил предпочтение" in contents
    assert "TOOL_ONLY_SECRET" not in contents and "FILE_SNAPSHOT_SECRET" not in contents


def test_allowed_paths_filter_and_no_symlinks_secrets_or_hidden_directories(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "allowed.md").write_text("Разрешённая заметка")
    (root / "active.md").write_text("Активная заметка")
    (root / ".env").write_text("API_KEY=NEVER_READ")
    outside = tmp_path / "outside.md"
    outside.write_text("OUTSIDE_SECRET")
    (root / "link.md").symlink_to(outside)
    hidden = root / ".hidden"
    hidden.mkdir()
    (hidden / "private.md").write_text("HIDDEN_SECRET")
    store = MemoryStore(tmp_path / "data")
    stats = import_source(source(root), store, eligible_paths={str(root / "allowed.md")})
    assert stats["imported"] == 1
    assert store.search("")[0]["text"] == "Разрешённая заметка"


def test_truncated_complete_file_prunes_old_chunks_but_corrupt_file_does_not(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    path = root / "session.jsonl"
    write_lines(path, [codex_message("assistant", "Длинный ответ. " * 1900)])
    store = MemoryStore(tmp_path / "data")
    import_source(source(root, "codex"), store)
    before = store.status()["documents"]
    assert before >= 2
    path.write_text('{"broken":\n')
    result = import_source(source(root, "codex"), store)
    assert result["errors"]
    assert store.status()["documents"] == before
    write_lines(path, [codex_message("assistant", "Короткий ответ")])
    import_source(source(root, "codex"), store)
    assert store.status()["documents"] == 1


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-SYNTHETIC_EXAMPLE_1234567890123",
        "ghp_SYNTHETIC1234567890123456789012345",
        "gsk_SYNTHETIC12345678901234567890",
    ],
)
def test_redaction_handles_keys_authorization_and_environment_values(secret):
    cleaned = redact_text(
        f"OPENAI_API_KEY={secret}\nAuthorization: Bearer {secret}\n"
        'password="' + "synthetic-password-value" + '"\nОбычная заметка'
    )
    assert secret not in cleaned
    assert "synthetic-password-value" not in cleaned
    assert "Обычная заметка" in cleaned


@pytest.mark.parametrize(
    "text,secret",
    [
        ("Authorization: Basic c3ludGhldGljOnBhc3N3b3Jk", "c3ludGhldGljOnBhc3N3b3Jk"),
        ('{"Authorization":"Bearer opaque-synthetic-secret"}', "opaque-synthetic-secret"),
        ("-----BEGIN " + "PRIVATE KEY-----\nSYNTHETIC-PRIVATE-MATERIAL\n",
         "SYNTHETIC-PRIVATE-MATERIAL"),
    ],
)
def test_redaction_handles_all_auth_schemes_and_incomplete_private_blocks(text, secret):
    assert secret not in redact_text(text)


def test_known_key_split_at_chunk_boundary_is_redacted_before_splitting(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    secret = "sk-" + "SYNTHETIC1234567890" * 3
    (root / "key.md").write_text("x" * 11998 + secret)
    store = MemoryStore(tmp_path / "data")
    import_source(source(root), store)
    records = sorted(store.search("", limit=50), key=lambda item: item["source_object_id"])
    assert secret not in "".join(record["text"] for record in records)
    assert not any(
        part in "".join(record["text"] for record in records) for part in ("SYNTHETIC1234567890",)
    )


def test_private_key_block_across_note_lines_is_masked_before_chunking(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "private.md").write_text(
        "Public note\n-----BEGIN " + "PRIVATE KEY-----\n"
        "SYNTHETIC-KEY-MATERIAL\n-----END PRIVATE KEY-----\nEnd"
    )
    store = MemoryStore(tmp_path / "data")
    import_source(source(root), store)
    assert "SYNTHETIC-KEY-MATERIAL" not in "".join(item["text"] for item in store.search(""))


def test_replaced_ancestor_directory_cannot_escape_saved_source_boundary(tmp_path):
    parent = tmp_path / "container"
    selected = parent / "approved"
    selected.mkdir(parents=True)
    original = str(selected)
    parent.rename(tmp_path / "container-old")
    outside = tmp_path / "outside"
    (outside / "approved").mkdir(parents=True)
    (outside / "approved" / "private.md").write_text("OUTSIDE_SCOPE_MARKER")
    parent.symlink_to(outside, target_is_directory=True)
    store = MemoryStore(tmp_path / "data")
    try:
        import_source(source(original), store)
    except Exception:
        pass
    assert store.status()["documents"] == 0


def test_complete_final_json_object_without_newline_is_imported(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    (root / "export.jsonl").write_text(json.dumps(codex_message("assistant", "Готовый ответ")))
    store = MemoryStore(tmp_path / "data")
    stats = import_source(source(root, "codex"), store)
    assert stats["imported"] == 1
    assert "Готовый ответ" in store.search("")[0]["text"]


def test_directory_enumeration_is_bounded_before_sorting(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from brain_settings import import_files
    from brain_settings.errors import AppError

    consumed = 0

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            nonlocal consumed
            for index in range(100):
                consumed += 1
                if consumed > 4:
                    raise AssertionError("Enumeration exceeded cap before import stopped")
                yield SimpleNamespace(name=f"note-{index}.md")

    monkeypatch.setattr(import_files, "MAX_ENTRIES", 3)
    monkeypatch.setattr(import_files.os, "scandir", lambda descriptor: Entries())
    with pytest.raises(AppError) as caught:
        list(import_files.read_source_files(tmp_path, "folder"))
    assert caught.value.code == "import_limit"
    assert consumed == 4


def test_disconnected_source_is_rejected_before_any_file_read(tmp_path, monkeypatch):
    from brain_settings import importers
    from brain_settings.errors import AppError

    store = MemoryStore(tmp_path / "data")
    store.delete_source("synthetic-source")

    def forbidden(*args, **kwargs):
        raise AssertionError("Disconnected source must not read files")

    monkeypatch.setattr(importers, "read_source_files", forbidden)
    with pytest.raises(AppError) as error:
        import_source(source(tmp_path), store)
    assert error.value.code == "source_deleted"


def test_auth_credentials_and_system_prompt_files_are_not_imported(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    for name in ("auth.md", "credentials.txt", "tools.jsonl", "system-prompt.txt", "AGENTS.md"):
        (root / name).write_text("OPAQUE_CREDENTIAL_OR_PROMPT_MATERIAL")
    (root / "useful.md").write_text("Полезная рабочая заметка")
    store = MemoryStore(tmp_path / "data")
    stats = import_source(source(root), store)
    assert stats["imported"] == 1
    assert "OPAQUE_CREDENTIAL_OR_PROMPT_MATERIAL" not in "".join(
        record["text"] for record in store.search("", limit=50)
    )
