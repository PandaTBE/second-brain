import json
from unittest.mock import Mock

import pytest

from brain_settings.mcp_config import install_project_configs
from brain_settings.mcp_server import MemoryTools, build_server, main
from brain_settings.source_storage import Source, SourcesStore

SOURCE_ID = "00000000-0000-4000-8000-000000000001"


def fake_store():
    record = {
        "id": "record-1",
        "source_id": SOURCE_ID,
        "title": "Title",
        "text": "body",
        "summary": "summary",
        "secret_future_column": "do-not-expose-extra",
        "card": {"summary": "x" * 10000, "atoms": ["huge"]},
        "score": 1,
        "revision": 2,
        "content_hash": "synthetic-hash",
    }
    store = Mock()
    store.search.return_value = [record]
    store.get_record.return_value = record
    store.context_brief.return_value = {"records": [record]}
    store.status.return_value = {
        "by_source": {SOURCE_ID: {"documents": 1, "ready": 1}},
        "last_updated_at": "2026-11-04",
    }
    return store


def memory_tools(store, data_dir=None):
    """Keep these metadata-boundary unit fakes independent of SQLite vector internals."""
    tools = MemoryTools(store, data_dir)
    tools.retrieval.vectors = Mock()
    tools.retrieval.vectors.config.return_value = {"enabled": False, "generation": None}
    tools.retrieval.vectors.coverage.side_effect = lambda **kwargs: {
        "eligible": int(kwargs.get("allowed_source_ids") != frozenset()),
        "ready": 0,
        "partial": True,
    }
    return tools


def test_fixed_fields_and_bounded_context_never_forward_extra_columns():
    tools = memory_tools(fake_store())
    for result in [
        tools.search_memory("body"),
        tools.get_record("record-1"),
        tools.get_context_brief(),
        tools.get_sync_status(),
    ]:
        assert "do-not-expose-extra" not in json.dumps(result)
    assert len(tools.get_record("record-1")["record"]["card"]["summary"]) == 1500


@pytest.mark.parametrize(
    "query,limit,code",
    [
        ("x" * 1001, 8, "invalid_query"),
        ("text", 0, "invalid_limit"),
        ("text", True, "invalid_limit"),
        ("text", 21, "invalid_limit"),
    ],
)
def test_search_input_bounds(query, limit, code):
    assert MemoryTools(fake_store()).search_memory(query, limit)["error"]["code"] == code


@pytest.mark.parametrize(
    "record_id,offset,max_chars",
    [("", 0, 1), ("x" * 129, 0, 1), ("x", True, 1), ("x", 0, 0), ("x", 0, True)],
)
def test_record_input_bounds(record_id, offset, max_chars):
    assert "error" in MemoryTools(fake_store()).get_record(record_id, offset, max_chars)


def test_search_query_boundary_matches_store_contract():
    store = fake_store()
    tools = memory_tools(store)
    assert "results" in tools.search_memory("x" * 512)
    store.search.reset_mock()
    assert tools.search_memory("x" * 513)["error"]["code"] == "invalid_query"
    store.search.assert_not_called()


def test_oversized_source_identifier_is_rejected_before_query():
    store = fake_store()
    assert "error" in MemoryTools(store).search_memory("x", source_id="s" * 129)
    store.search.assert_not_called()


def test_all_store_errors_are_redacted():
    store = fake_store()
    for name in ("search", "get_record", "context_brief", "status"):
        getattr(store, name).side_effect = RuntimeError("sensitive internal provider response")
    tools = memory_tools(store)
    for result in (
        tools.search_memory("x"),
        tools.get_record("x"),
        tools.get_context_brief(),
        tools.get_sync_status(),
    ):
        assert "error" in result
        assert "sensitive" not in json.dumps(result)


def test_disconnected_source_fails_closed_in_all_tools(tmp_path):
    store = fake_store()
    source_store = SourcesStore(tmp_path)
    source_store.save((Source(id=SOURCE_ID, name="Test", kind="codex"),))
    tools = memory_tools(store, tmp_path)
    assert tools.search_memory("x")["results"]
    store.search.assert_called_with(
        "x", limit=20, source_id=None, allowed_source_ids=frozenset([SOURCE_ID]), visibility="mcp",
        project=None, date_from=None, date_to=None,
    )
    source_store.save(())
    assert tools.search_memory("x")["results"] == []
    assert tools.get_context_brief()["records"] == []
    assert "error" in tools.get_record("record-1")
    assert tools.get_sync_status()["memory"]["documents"] == 0
    source_store.path.write_text("broken config")
    assert "error" in tools.search_memory("x")


def test_bad_existing_config_is_not_overwritten(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text('{"mcpServers":[]}')
    with pytest.raises(ValueError):
        install_project_configs(tmp_path)
    assert path.read_text() == '{"mcpServers":[]}'


def test_sdk_server_registration_and_cli_select_stdio(tmp_path, monkeypatch):
    assert build_server(tmp_path) is not None
    import brain_settings.mcp_server as module

    fake = Mock()
    builder = Mock(return_value=fake)
    monkeypatch.setattr(module, "build_server", builder)
    monkeypatch.setattr("sys.argv", ["mcp-server", "--data-dir", str(tmp_path)])
    main()
    builder.assert_called_once_with(tmp_path.resolve())
    fake.run.assert_called_once_with(transport="stdio")
