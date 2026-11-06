import hashlib
import json
import sqlite3

import pytest
from test_embeddings import FakeEmbedding

from brain_settings.embedding_store import VectorStore
from brain_settings.errors import AppError
from brain_settings.evidence import EvidenceService, encoded_size
from brain_settings.memory_store import SCHEMA, MemoryStore
from brain_settings.retrieval import Retrieval
from brain_settings.source_dates import source_date
from brain_settings.source_policy import SourcePolicy


def document(store, key, *, project="other", date="2026-05-01T00:00:00+00:00"):
    item = store.upsert_document(
        source_id="synthetic",
        source_kind="folder",
        source_object_id=key,
        title="Retry policy",
        text="Retry budget is three attempts.",
        source_uri=f"file:///synthetic/{key}.md",
        project=project,
        message_at=date,
    )
    return store.get_record(item["id"])


def test_semantic_filters_run_before_top_candidate_heap_and_coverage(tmp_path):
    store = MemoryStore(tmp_path)
    vectors = VectorStore(store)
    state = vectors.configure("embed:small", "a" * 64, 2)
    for index in range(60):
        item = document(store, f"noise-{index}")
        assert vectors.save(item, state["generation"], [1, 0])
    target = document(store, "target", project="memory", date="2026-09-03T00:00:00+00:00")
    assert vectors.save(target, state["generation"], [0.8, 0.6])
    retrieval = Retrieval(store, client=FakeEmbedding())
    result = retrieval.search(
        "unrelated words",
        mode="semantic",
        project="memory",
        date_from="2026-09-01",
        date_to="2026-09-30",
        limit=1,
    )
    assert [row["id"] for row in result["results"]] == [target["id"]]
    assert result["search"]["eligible"] == result["search"]["ready"] == 1


def test_policy_changed_after_retrieval_hides_citations_and_aggregate_counts(tmp_path):
    store = MemoryStore(tmp_path)
    document(store, "target")

    class Revoke:
        def search(self, *args, **kwargs):
            result = Retrieval(store).search(*args, **kwargs)
            store.set_source_policy("synthetic", SourcePolicy(mcp_enabled=False))
            return result

    result = EvidenceService(store, retrieval=Revoke()).bundle(
        "retry", visibility="mcp", mode="lexical"
    )
    assert result["citations"] == []
    assert result["search"]["eligible"] == result["search"]["ready"] == 0


def test_escaped_request_metadata_cannot_exceed_small_total_budget(tmp_path):
    store = MemoryStore(tmp_path)
    with pytest.raises(AppError, match="метаданных"):
        EvidenceService(store).bundle("\0" * 512, budget_chars=2000, mode="lexical")
    for budget in (2000, 3000, 12000, 32000):
        packet = EvidenceService(store).bundle("retry", budget_chars=budget, mode="lexical")
        assert packet["used_chars"] == encoded_size(packet) <= budget


@pytest.mark.parametrize(
    "changes",
    [
        {"query": "\ud800"},
        {"source_id": "\ud800"},
        {"project": "\ud800"},
    ],
)
def test_lone_surrogate_request_fields_are_rejected(tmp_path, changes):
    arguments = {"query": "retry", "mode": "lexical"} | changes
    with pytest.raises(AppError):
        EvidenceService(MemoryStore(tmp_path)).bundle(**arguments)


def test_same_instant_inclusive_date_filters_and_utc_day_bounds(tmp_path):
    store = MemoryStore(tmp_path)
    document(store, "known", date="2026-09-03T12:00:00+03:00")
    service = EvidenceService(store)
    same_instant = service.bundle(
        "retry", mode="lexical", date_from="2026-09-03T09:00:00Z", date_to="2026-09-03T09:00:00Z"
    )
    assert same_instant["citations"]
    assert service.bundle("retry", mode="lexical", date_to="2026-09-02")["citations"] == []


@pytest.mark.parametrize(
    "value", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00", "2026-01-01T00:00:00"]
)
def test_unrepresentable_or_ambiguous_source_date_remains_unknown(value):
    assert source_date(value) is None


def test_old_schema_dates_stay_unknown_until_explicit_same_text_reimport(tmp_path):
    title, text = "Retry policy", "Retry budget is three attempts."
    digest = hashlib.sha256((title + "\0" + text).encode()).hexdigest()
    with sqlite3.connect(tmp_path / "memory.sqlite3") as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO documents(id,source_id,source_kind,source_object_id,title,text,source_uri,"
            "content_hash,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy-record",
                "synthetic",
                "folder",
                "legacy",
                title,
                text,
                "file:///synthetic/legacy.md",
                digest,
                1,
                "2026-01-01",
                "2026-01-02",
            ),
        )
    store = MemoryStore(tmp_path)
    before = store.get_record("legacy-record")
    assert all(
        before[name] is None
        for name in ("message_at", "source_created_at", "source_modified_at", "imported_at")
    )
    updated = store.upsert_document(
        source_id="synthetic",
        source_kind="folder",
        source_object_id="legacy",
        title=title,
        text=text,
        source_uri="file:///synthetic/legacy.md",
        project="memory",
        message_at="2026-09-03T12:00:00+03:00",
    )
    after = store.get_record("legacy-record")
    assert updated["changed"] is False and after["revision"] == 1
    assert after["message_at"] == "2026-09-03T09:00:00+00:00"
    assert after["project"] == "memory"
    assert after["imported_at"] is None
    # A caller that supplies no metadata does not erase verified provenance.
    store.upsert_document(
        source_id="synthetic",
        source_kind="folder",
        source_object_id="legacy",
        title=title,
        text=text,
        source_uri="file:///synthetic/legacy.md",
    )
    assert store.get_record("legacy-record")["message_at"] == after["message_at"]


@pytest.mark.parametrize("kind", ["codex", "claude", "qwen"])
@pytest.mark.parametrize("streamed", [False, True])
def test_explicit_session_project_survives_actual_import(tmp_path, monkeypatch, kind, streamed):
    from brain_settings import import_files
    from brain_settings.importers import import_source

    root = tmp_path / ("chats" if kind == "qwen" else "sessions")
    root.mkdir()
    project = "/synthetic/work/memory"
    if kind == "codex":
        rows = [
            {"type": "session_meta", "payload": {"cwd": project}},
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": "Retry budget is three."},
            },
        ]
    else:
        rows = [
            {
                "type": "user",
                "cwd": project,
                "message": {"role": "user", "content": "Retry budget is three."},
            }
        ]
    if kind == "qwen":
        rows = [
            {
                "type": "user",
                "cwd": project,
                "message": {"role": "user", "parts": [{"text": "Retry budget is three."}]},
            }
        ]
    (root / "session.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    if streamed:
        monkeypatch.setattr(import_files, "MAX_FILE_BYTES", 1)
    # Qwen import paths are scoped by the connector's actual filename convention.
    if kind == "qwen":
        (root / "session.jsonl").rename(root / "session-synthetic.jsonl")
    store = MemoryStore(tmp_path / "data")
    result = import_source({"id": "synthetic", "kind": kind, "path": str(root)}, store)
    assert result["imported"] == 1
    found = EvidenceService(store).bundle("retry", project=project, mode="lexical")
    assert found["citations"][0]["project"] == project
    assert (
        EvidenceService(store).bundle("retry", project="other", mode="lexical")["citations"] == []
    )


def test_mail_unknown_timezone_does_not_become_verified_message_date():
    from brain_settings.mail_message import parse_message

    for date in ("Fri, 06 Nov 2026 10:00:00 -0000", "Fri, 06 Nov 2026 10:00:00"):
        parsed = parse_message(
            f"Subject: Synthetic\nDate: {date}\n\nRetry budget is three.".encode()
        )
        assert parsed["timestamp"] is None


def test_github_file_dates_do_not_inherit_repository_activity(tmp_path):
    from test_github_import import FakeGitHub

    from brain_settings.github_import import import_github

    store = MemoryStore(tmp_path)
    source = {
        "id": "synthetic",
        "kind": "github",
        "github_repositories": ["owner/project"],
        "github_content": ["docs", "issues"],
    }
    import_github(source, store, FakeGitHub(documents=1, issues=1), "synthetic")
    records = store.search("SQLite")
    document = next(row for row in records if "::doc:" in row["source_object_id"])
    issue = next(row for row in records if "::issue:" in row["source_object_id"])
    assert document["source_created_at"] is document["source_modified_at"] is None
    assert issue["source_modified_at"] == "2026-11-01T00:00:00+00:00"


def test_outlook_received_fallback_does_not_claim_known_modification_date(tmp_path):
    from test_outlook_client import message

    from brain_settings.outlook_client import _message
    from brain_settings.outlook_import import _commit

    missing_modified = _message(message(lastModifiedDateTime=None))
    assert missing_modified["source_modified_at"] is None
    store = MemoryStore(tmp_path)
    _, saved = _commit(store, "synthetic", missing_modified)
    record = store.get_record(saved[0]["id"])
    assert record["source_modified_at"] is None
    assert record["message_at"] == "2026-11-01T12:00:00+00:00"


@pytest.mark.parametrize(
    "metadata",
    [
        {"cwd": "relative/project"},
        {"cwd": "/synthetic/\nproject"},
        {"cwd": "/\ud800"},
        {"cwd": "/" + "x" * 256},
        {"project": {"path": "/synthetic"}},
    ],
)
def test_untrusted_project_metadata_cannot_override_known_scope(metadata):
    from brain_settings.session_metadata import session_project

    assert session_project(metadata, "claude", "/synthetic/known") == "/synthetic/known"


def test_project_metadata_is_explicit_and_normalizes_without_filesystem_lookup():
    from brain_settings.session_metadata import session_project

    assert session_project({"cwd": "/synthetic/other/../memory"}, "claude") == "/synthetic/memory"
    assert session_project({"project": "Memory project"}, "qwen") == "Memory project"
    assert session_project({"title": "memory", "path": "/guessed"}, "claude") is None
