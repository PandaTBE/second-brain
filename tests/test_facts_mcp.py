import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_fact_projection import legacy_ready

from brain_settings.mcp_server import MemoryTools
from brain_settings.memory_store import MemoryStore
from brain_settings.source_storage import Source, SourcesStore

SOURCES = tuple(f"00000000-0000-4000-8000-{index:012d}" for index in (1, 2, 3))


def seed(directory, *, project=True):
    store = MemoryStore(directory)
    sources = tuple(Source(id=source, name="Synthetic", kind="folder", path=str(directory))
                    for source in SOURCES)
    SourcesStore(directory).save(sources)
    for source in SOURCES:
        legacy_ready(store, source=source)
    if project:
        store.project_facts()
    return store


def test_mcp_fact_reads_do_not_project_existing_cards_or_write_state(tmp_path):
    store = seed(tmp_path, project=False)
    tools = MemoryTools(store, tmp_path)
    with sqlite3.connect(store.path) as observer:
        version = observer.execute("PRAGMA data_version").fetchone()[0]
        assert tools.get_facts()["facts"] == []
        assert tools.get_fact_history("unavailable")["error"]["code"] == "fact_unavailable"
        assert observer.execute("PRAGMA data_version").fetchone()[0] == version
    assert store.project_facts()["projected"] == 3


def test_two_stdio_fact_clients_share_grouped_proofs_and_recheck_policy_revision_delete(tmp_path):
    store = seed(tmp_path)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).parents[1] / "brain_settings" / "mcp_server.py"),
              "--data-dir", str(tmp_path)],
    )

    async def call(client, tool="get_facts", arguments=None):
        result = await client.call_tool(tool, arguments or {})
        assert not result.is_error
        return result.structured_content or json.loads(result.content[0].text)

    async def verify():
        async with (stdio_client(parameters) as (read_a, write_a),
                    stdio_client(parameters) as (read_b, write_b)):
            async with (ClientSession(read_a, write_a) as first,
                        ClientSession(read_b, write_b) as second):
                await first.initialize()
                await second.initialize()
                one, two = await call(first), await call(second)
                assert one == two
                entity = one["facts"][0]
                assert len(one["facts"]) == 1
                assert len(entity["evidence"]) == 3
                store.set_source_policy(SOURCES[0], {"mcp_enabled": False})
                for client in (first, second):
                    packet = await call(client)
                    assert len(packet["facts"][0]["evidence"]) == 2
                    history = await call(client, "get_fact_history", {"fact_id": entity["id"]})
                    assert len(history["events"]) == 2
                    assert SOURCES[0] not in {event["source_id"] for event in history["events"]}
                SourcesStore(tmp_path).save(tuple(
                    source.model_copy(update={"enabled": False}) if source.id == SOURCES[1]
                    else source for source in SourcesStore(tmp_path).load()
                ))
                store.upsert_document(
                    source_id=SOURCES[2], source_kind="folder", source_object_id="one",
                    title="Changed", text="Use Redis.", source_uri="synthetic:changed",
                )
                assert (await call(first))["facts"] == []
                historic = await call(first, arguments={"include_history": True})
                assert historic["include_history"] is True
                assert historic["facts"][0]["historical_only"] is True
                history = await call(second, "get_fact_history", {"fact_id": entity["id"]})
                assert len(history["events"]) == 1 and not history["events"][0]["active"]
                store.delete_source(SOURCES[2])
                for client in (first, second):
                    assert (await call(client))["facts"] == []
                    hidden = await call(client, "get_fact_history", {"fact_id": entity["id"]})
                    assert hidden["error"]["code"] == "fact_unavailable"

    asyncio.run(verify())
