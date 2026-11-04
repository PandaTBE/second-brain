#!/usr/bin/env python3
"""Exercise a moved app's frozen HTTP/MCP helper using only disposable user data."""

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def unpack_result(result):
    if result.is_error:
        raise RuntimeError("Frozen MCP reported a tool error")
    data = result.structured_content
    if data is None:
        data = json.loads(next(block.text for block in result.content if hasattr(block, "text")))
    if "error" in data:
        raise RuntimeError("Frozen MCP reported an application error")
    return data


async def check_mcp(configuration, environment, directory):
    parameters = StdioServerParameters(command=configuration["command"],
                                      args=configuration["args"], env=environment,
                                      cwd=str(directory))
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = unpack_result(await session.call_tool(
                "search_memory", {"query": "portable", "limit": 1},
            ))
            assert len(result["results"]) == 1, "Frozen MCP cannot search the imported note"
            record = unpack_result(await session.call_tool(
                "get_record", {"record_id": result["results"][0]["id"]},
            ))
            assert "Portable release smoke" in record["record"]["text"]


def data(response):
    response.raise_for_status()
    result = response.json()
    assert result["success"], "Frozen HTTP returned an application error"
    return result["data"]


def exercise_http(client, helper, user_home):
    deadline = time.monotonic() + 25
    while True:
        try:
            data(client.get("/api/health"))
            break
        except (httpx.HTTPError, AssertionError):
            if time.monotonic() >= deadline:
                raise RuntimeError("Frozen HTTP helper did not become healthy") from None
            time.sleep(0.1)
    initial = data(client.get("/api/memory/status"))
    assert initial["records"] == 1 and not initial["scheduler"]["process_cards"]
    assert client.get("/").status_code == 200, "Bundled static HTML is missing"
    assert client.get("/static/memory-atlas.mjs").status_code == 200
    hits = data(client.get("/api/memory/search", params={"q": "portable"}))["results"]
    assert len(hits) == 1, "Frozen SQLite FTS5 search failed"
    configuration = data(client.get("/api/memory/mcp"))
    assert Path(configuration["command"]).resolve() == helper.resolve()
    assert configuration["args"][0] == "mcp"
    expected = user_home / "Library" / "Application Support" / "Second Brain"
    assert expected.resolve() == Path(configuration["args"][-1]).resolve()
    assert (expected / "memory.sqlite3").is_file()
    return configuration


def smoke(app):
    with tempfile.TemporaryDirectory(prefix="second-brain-release-smoke-") as temporary:
        directory = Path(temporary)
        moved = directory / "Moved Applications" / "Second Brain.app"
        shutil.copytree(app, moved, symlinks=True)
        helper = moved / "Contents" / "Resources" / "Backend" / "SecondBrainHelper"
        user_home = directory / "Fresh User"
        user_home.mkdir()
        # The test harness seeds only a disposable archive, without reading Keychain.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from brain_settings.memory_store import MemoryStore
        from brain_settings.source_storage import Source, SourcesStore

        notes = user_home / "notes"
        notes.mkdir()
        note = notes / "portable.md"
        text = "Portable release smoke test: SQLite FTS5 memory."
        note.write_text(text)
        storage = user_home / "Library" / "Application Support" / "Second Brain"
        source = Source(id=str(uuid4()), name="Portable smoke", kind="folder", path=str(notes))
        SourcesStore(storage).save((source,))
        MemoryStore(storage).upsert_document(
            source_id=source.id, source_kind="folder", source_object_id="portable.md",
            title="Portable smoke", text=text, source_uri=note.as_uri(),
        )
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(("PYTHON", "SECOND_BRAIN", "DYLD", "_PYI"))}
        environment.update({"HOME": str(user_home), "PATH": "/usr/bin:/bin",
                            "SECOND_BRAIN_PROJECT_DIR": str(directory / "missing-checkout")})
        with socket.socket() as connection:
            connection.bind(("127.0.0.1", 0))
            port = connection.getsockname()[1]
        with (directory / "helper.stderr").open("w") as errors:
            process = subprocess.Popen([str(helper), "serve", "--port", str(port)],
                                       cwd=directory, env=environment,
                                       stdout=subprocess.DEVNULL, stderr=errors)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2,
                                  trust_env=False) as client:
                    configuration = exercise_http(client, helper, user_home)
                asyncio.run(check_mcp(configuration, environment, directory))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    return {"moved_app": True, "external_python_required": False,
            "fresh_user_data": True, "http_fts5": True, "keychain_endpoints_called": False,
            "mcp_search_record": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(smoke(arguments.app.resolve())))


if __name__ == "__main__":
    main()
