import asyncio
import json
import sys
import tomllib
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from brain_settings.mcp_config import connection_info, install_project_configs
from brain_settings.mcp_server import MemoryTools
from brain_settings.memory_store import MemoryStore
from brain_settings.source_storage import Source, SourcesStore


def add_record(store, text="Исправили проблему с авторизацией клубного бота"):
    return store.upsert_document(
        source_id="00000000-0000-4000-8000-000000000001",
        source_kind="codex",
        source_object_id="session/chunk-0",
        title="Решение проблемы бота",
        text=text,
        source_uri="file:///synthetic/session.jsonl#L1-L2",
    )


def test_connection_info_quotes_paths_and_has_no_secret(tmp_path):
    info = connection_info(tmp_path / "data with spaces")
    claude = json.loads(info["claude_json"])
    codex = tomllib.loads(info["codex_toml"])
    assert claude["mcpServers"]["second-brain"]["command"] == info["command"]
    assert codex["mcp_servers"]["second-brain"]["args"] == info["args"]
    assert info["args"] == ["--data-dir", str(tmp_path / "data with spaces")]
    assert "token" not in info["claude_json"].lower()


def test_config_install_preserves_other_servers_and_settings(tmp_path):
    (tmp_path / ".mcp.json").write_text('{"mcpServers":{"other":{"command":"other"}},"custom":1}')
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        'model = "existing"\n[mcp_servers.other]\ncommand = "other"\n'
    )
    install_project_configs(tmp_path, tmp_path / "data")
    install_project_configs(tmp_path, tmp_path / "data")
    claude = json.loads((tmp_path / ".mcp.json").read_text())
    codex = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text())
    assert claude["custom"] == 1
    assert claude["mcpServers"]["other"]["command"] == "other"
    assert codex["model"] == "existing"
    assert codex["mcp_servers"]["other"]["command"] == "other"
    assert "second-brain" in codex["mcp_servers"]


def test_tools_bound_results_and_expose_provenance(tmp_path):
    store = MemoryStore(tmp_path)
    item = add_record(store, "Авторизация " + "проверка " * 3000)
    tools = MemoryTools(store)
    result = tools.get_record(item["id"], offset=10, max_chars=999999)
    assert len(result["record"]["text"]) <= 12000
    assert result["record"]["source_uri"].startswith("file:///synthetic/")
    assert result["untrusted_content"] is True
    assert tools.search_memory("", limit=8)["error"]["code"] == "invalid_query"
    assert tools.get_record(item["id"], offset=-1)["error"]["code"] == "invalid_offset"
    assert tools.get_record("not-present")["error"]["code"] == "record_unavailable"
    store.delete_source("00000000-0000-4000-8000-000000000001")
    assert tools.get_record(item["id"])["error"]["code"] == "record_unavailable"


def test_real_stdio_clients_share_database_without_running_app(tmp_path):
    tmp_path = tmp_path / "shared database with spaces"
    store = MemoryStore(tmp_path)
    item = add_record(store)
    SourcesStore(tmp_path).save(
        (
            Source(
                id="00000000-0000-4000-8000-000000000001",
                name="Test",
                kind="codex",
                path=str(tmp_path),
            ),
        )
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            str(Path(__file__).parents[1] / "brain_settings" / "mcp_server.py"),
            "--data-dir",
            str(tmp_path),
        ],
    )

    async def verify():
        async with stdio_client(parameters) as (read_a, write_a):
            async with ClientSession(read_a, write_a) as client_a:
                await client_a.initialize()
                listing = await client_a.list_tools()
                assert {tool.name for tool in listing.tools} == {
                    "search_memory",
                    "get_record",
                    "get_context_brief",
                    "get_sync_status",
                    "get_evidence_bundle",
                    "get_facts",
                    "get_fact_history",
                }
                async with stdio_client(parameters) as (read_b, write_b):
                    async with ClientSession(read_b, write_b) as client_b:
                        await client_b.initialize()
                        for client in (client_a, client_b):
                            result = await client.call_tool(
                                "search_memory", {"query": "авторизация бота"}
                            )
                            payload = result.structured_content or json.loads(
                                result.content[0].text
                            )
                            assert not result.is_error
                            assert payload["results"][0]["id"] == item["id"]
                            detail = await client.call_tool("get_record", {"record_id": item["id"]})
                            detail_data = detail.structured_content or json.loads(
                                detail.content[0].text
                            )
                            assert (
                                detail_data["record"]["source_id"]
                                == "00000000-0000-4000-8000-000000000001"
                            )
                        updated = add_record(store, "Уникальноеновоерешение для авторизации бота")
                        for client in (client_a, client_b):
                            fresh = await client.call_tool(
                                "search_memory", {"query": "Уникальноеновоерешение"}
                            )
                            fresh_data = fresh.structured_content or json.loads(
                                fresh.content[0].text
                            )
                            assert fresh_data["results"][0]["revision"] == updated["revision"]
                        SourcesStore(tmp_path).save(())
                        denied = await client_b.call_tool("get_record", {"record_id": item["id"]})
                        denied_data = denied.structured_content or json.loads(
                            denied.content[0].text
                        )
                        assert denied_data["error"]["code"] == "record_unavailable"
                        status = await client_b.call_tool("get_sync_status", {})
                        assert not status.is_error
                        brief = await client_a.call_tool("get_context_brief", {})
                        assert not brief.is_error

    asyncio.run(verify())
