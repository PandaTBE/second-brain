"""Service-generated transcripts must not become new generation input."""

import io
import json

import pytest
from test_qwen_source import record, transcript

from brain_settings.dialogue_parsers import parse_session
from brain_settings.importers import import_source
from brain_settings.memory_store import MemoryStore
from brain_settings.session_stream import read_session

LEGACY = "Generate the requested search card from standard input. Do not use tools."
MARKER = "[SECOND_BRAIN_INTERNAL_CARD_GENERATION_V1]"
PROMPT = MARKER + "\n" + LEGACY
PAYLOAD = json.dumps({"messages": [
    {"role": "system", "content": "Synthetic card instructions"},
    {"role": "user", "content": json.dumps({"title": "Synthetic", "text": "source"})},
]})


@pytest.mark.parametrize("prompt", [LEGACY, PROMPT])
@pytest.mark.parametrize("input_first", [False, True])
def test_service_session_is_empty_in_buffered_and_streamed_parsers(prompt, input_first):
    text = PAYLOAD + "\n\n" + prompt if input_first else prompt + "\n\n" + PAYLOAD
    raw = transcript([record("user", text), record("assistant", "Synthetic generated card")])
    turns, complete, _ = parse_session(raw, "qwen")
    streamed = read_session(io.BytesIO(raw.encode()), "qwen", len(raw.encode()))
    assert turns == [] and complete is True
    assert streamed.turns == () and streamed.complete is True
    assert any("служебн" in warning.casefold() for warning in streamed.warnings)


@pytest.mark.parametrize("cwd", [
    "/tmp/second-brain-cli-abc123", "/private/var/tmp/second-brain-cli-123",
])
def test_runtime_service_workspace_discards_even_earlier_assistant_turns(cwd):
    raw = transcript([
        record("assistant", "Do not leak an earlier turn"),
        {"type": "system", "cwd": cwd},
        record("user", "Synthetic payload"),
    ])
    assert parse_session(raw, "qwen")[0] == []
    assert read_session(io.BytesIO(raw.encode()), "qwen", len(raw.encode())).turns == ()


@pytest.mark.parametrize("text", [
    "Explain this prompt: " + LEGACY,
    "Quoted marker: " + MARKER,
    '"' + PROMPT + '"',
    LEGACY + " This is a discussion of the phrase.",
    PAYLOAD,
    "A user's ordinary card-generation conversation",
])
def test_ordinary_chats_are_preserved(text):
    raw = transcript([record("user", text), record("assistant", "Useful ordinary answer")])
    buffered = parse_session(raw, "qwen")[0]
    streamed = read_session(io.BytesIO(raw.encode()), "qwen", len(raw.encode()))
    assert len(buffered) == len(streamed.turns) == 2
    assert "Useful ordinary answer" in buffered[-1].text


@pytest.mark.parametrize("cwd", [
    "second-brain-cli-relative", "/tmp/not-second-brain-cli-abc",
    "/tmp/second-brain-cli-abc/ordinary-project", "",
])
def test_workspace_lookalikes_do_not_hide_chats(cwd):
    raw = transcript([record("user", "Ordinary question", cwd=cwd)])
    assert len(parse_session(raw, "qwen")[0]) == 1


@pytest.mark.parametrize("streamed", [False, True])
def test_reimport_invalidates_old_cache_and_removes_all_service_chunks(
    tmp_path, monkeypatch, streamed,
):
    from brain_settings import import_files, import_progress

    root = tmp_path / "projects"
    path = root / "synthetic" / "chats" / "service.jsonl"
    path.parent.mkdir(parents=True)
    raw = transcript([
        record("user", PAYLOAD + "\n\n" + LEGACY),
        record("assistant", "Legacy assistant-only content " + "x" * 13000),
    ])
    path.write_text(raw)
    source = {"id": "service-test", "kind": "qwen", "path": str(root)}
    store = MemoryStore(tmp_path / "memory")
    with monkeypatch.context() as old:
        # Simulate the published parser/checkpoint before this fix.
        old.setattr(import_progress, "PARSER_VERSION", "bounded-stream-v1")
        import brain_settings.dialogue_parsers as parser

        original = parser.parse_session

        def legacy_parser(raw, kind):
            return original(raw.replace(LEGACY, "legacy unrecognized instruction"), kind)

        old.setattr("brain_settings.importers.parse_session", legacy_parser)
        first = import_source(source, store)
        assert first["imported"] >= 2
    if streamed:
        monkeypatch.setattr(import_files, "MAX_FILE_BYTES", 1)
    second = import_source(source, store)
    assert second["files_processed"] == 1 and not second["errors"]
    assert store.status()["documents"] == 0
    assert store.search("Legacy") == []
    assert path.read_text() == raw
    assert import_source(source, store)["files_unchanged"] == 1


@pytest.mark.parametrize("prompt", [PROMPT, LEGACY])
def test_service_marker_before_partial_tail_remains_complete_empty(prompt):
    raw = transcript([record("user", PAYLOAD + "\n" + prompt)]) + '{"type":"assistant"'
    turns, complete, _ = parse_session(raw, "qwen")
    streamed = read_session(io.BytesIO(raw.encode()), "qwen", len(raw.encode()))
    assert turns == [] and complete is True
    assert streamed.turns == () and streamed.complete is True


@pytest.mark.parametrize("text", [
    LEGACY + "\nnot-json",
    "not-json\n" + LEGACY,
    json.dumps({"messages": "wrong-type"}) + "\n" + LEGACY,
    json.dumps({"messages": [{"role": "assistant", "content": "not user"}]}) + "\n" + LEGACY,
    json.dumps({"messages": [], "unrelated": True}) + "\n" + LEGACY,
    "null\n" + LEGACY,
])
def test_malformed_envelope_lookalikes_do_not_hide_chats(text):
    raw = transcript([record("user", text)])
    assert len(parse_session(raw, "qwen")[0]) == 1


@pytest.mark.parametrize("prompt", [LEGACY, PROMPT])
def test_standalone_service_prompt_excludes_whole_session(prompt):
    raw = transcript([record("user", prompt), record("assistant", "Service output")])
    assert parse_session(raw, "qwen")[0] == []
    assert read_session(io.BytesIO(raw.encode()), "qwen", len(raw.encode())).turns == ()


@pytest.mark.parametrize("cwd", [
    "/work/projects/second-brain-cli-library",
    "/Users/example/projects/second-brain-cli-test",
    "/custom-temp/second-brain-cli-generated",
    "/var/folders/not-two-chars/token/T/second-brain-cli-generated",
])
def test_reserved_basename_in_ordinary_project_is_not_service_evidence(cwd):
    from brain_settings.service_sessions import is_service_record

    raw = transcript([record("user", "Ordinary project question", cwd=cwd)])
    assert len(parse_session(raw, "qwen")[0]) == 1
    assert len(read_session(io.BytesIO(raw.encode()), "qwen", len(raw.encode())).turns) == 1
    assert not is_service_record({"source_kind": "qwen", "project": cwd})


@pytest.mark.parametrize("cwd", [
    "/tmp/second-brain-cli-abc123",
    "/private/tmp/second-brain-cli-abc123",
    "/var/tmp/second-brain-cli-abc123",
    "/private/var/tmp/second-brain-cli-abc123",
    "/var/folders/xy/synthetic-token/T/second-brain-cli-abc123",
    "/private/var/folders/xy/synthetic-token/T/second-brain-cli-abc123",
])
def test_standard_temporary_workspace_is_service_evidence(cwd):
    from brain_settings.service_sessions import is_service_record

    raw = transcript([record("user", "Synthetic payload", cwd=cwd)])
    assert parse_session(raw, "qwen")[0] == []
    assert read_session(io.BytesIO(raw.encode()), "qwen", len(raw.encode())).turns == ()
    assert is_service_record({"source_kind": "qwen", "project": cwd})


def test_record_classifier_is_conservative_and_does_not_read_files():
    from brain_settings.service_sessions import is_service_record, is_service_session_row

    assert is_service_record({"source_kind": "qwen", "text": "Пользователь: " + PROMPT})
    assert is_service_record({"source_kind": "qwen", "project": "/tmp/second-brain-cli-test"})
    assert not is_service_record({"source_kind": "folder", "text": PROMPT})
    assert not is_service_record({"source_kind": "qwen", "text": "Discuss " + PROMPT})
    assert not is_service_session_row(None, "qwen")
    assert not is_service_session_row({"cwd": 123}, "qwen")
    assert not is_service_session_row(record("assistant", PROMPT), "qwen")
