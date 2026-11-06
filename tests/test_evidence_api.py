import asyncio
import inspect
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_settings_api import FakeTransport, MemoryVault

from brain_settings.app import create_app
from brain_settings.mcp_server import MemoryTools
from brain_settings.memory_store import MemoryStore
from brain_settings.source_policy import SourcePolicy
from brain_settings.source_storage import Source, SourcesStore

SOURCE = "00000000-0000-4000-8000-000000000001"
ORIGIN = "http://127.0.0.1:8765"


def seed(directory):
    store = MemoryStore(directory)
    SourcesStore(directory).save(
        (Source(id=SOURCE, name="Synthetic", kind="folder", path=str(directory)),)
    )
    saved = store.upsert_document(
        source_id=SOURCE,
        source_kind="folder",
        source_object_id="decision",
        title="Retry budget",
        text="The retry budget is three attempts.",
        source_uri="file:///synthetic/decision.md",
        project="memory",
        message_at="2026-09-03T12:00:00+03:00",
    )
    return store, saved


def test_http_and_mcp_share_packet_and_old_brief_signature(tmp_path):
    store, _ = seed(tmp_path)
    app = create_app(data_dir=tmp_path, vault=MemoryVault(), transport=FakeTransport())
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get(
            "/api/memory/evidence",
            params={
                "q": "retry",
                "project": "memory",
                "date_from": "2026-09-03",
                "date_to": "2026-09-03",
                "mode": "lexical",
                "budget_chars": 3000,
            },
        )
        assert response.status_code == 200
        http = response.json()["data"]
        mcp = MemoryTools(store, tmp_path).get_evidence_bundle(
            "retry",
            project="memory",
            date_from="2026-09-03",
            date_to="2026-09-03",
            mode="lexical",
            budget_chars=3000,
        )
        assert http == mcp
        assert mcp["citations"][0]["dates"]["message_at"] == "2026-09-03T09:00:00+00:00"
        assert list(inspect.signature(MemoryTools.get_context_brief).parameters) == ["self"]
        assert MemoryTools(store).get_evidence_bundle("", mode="lexical")["error"]["code"] == (
            "invalid_query"
        )
        assert (
            client.get("/api/memory/evidence", params={"q": "retry", "budget_chars": 1}).status_code
            == 422
        )


def test_two_independent_stdio_clients_share_evidence_and_observe_policy_revision(tmp_path):
    store, saved = seed(tmp_path)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            str(Path(__file__).parents[1] / "brain_settings" / "mcp_server.py"),
            "--data-dir",
            str(tmp_path),
        ],
    )

    async def read(client):
        result = await client.call_tool(
            "get_evidence_bundle", {"query": "retry", "mode": "lexical"}
        )
        assert not result.is_error
        return result.structured_content or json.loads(result.content[0].text)

    async def verify():
        async with (
            stdio_client(parameters) as (read_a, write_a),
            stdio_client(parameters) as (read_b, write_b),
        ):
            async with (
                ClientSession(read_a, write_a) as first,
                ClientSession(read_b, write_b) as second,
            ):
                await first.initialize()
                await second.initialize()
                packet_a, packet_b = await read(first), await read(second)
                assert packet_a == packet_b
                citation = packet_a["citations"][0]
                assert citation["record_id"] == saved["id"]
                store.set_source_policy(SOURCE, SourcePolicy(mcp_enabled=False))
                assert (await read(first))["citations"] == []
                assert (await read(second))["citations"] == []
                store.set_source_policy(SOURCE, SourcePolicy())
                store.upsert_document(
                    source_id=SOURCE,
                    source_kind="folder",
                    source_object_id="decision",
                    title="Retry budget",
                    text="The retry budget is five attempts.",
                    source_uri="file:///synthetic/decision.md",
                )
                assert (await read(first))["citations"][0]["revision"] == citation["revision"] + 1
                store.delete_source(SOURCE)
                assert (await read(second))["citations"] == []

    asyncio.run(verify())
