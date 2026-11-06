"""Legacy wire compatibility for installed Claude Code and Codex clients."""

import json
import selectors
import subprocess
from pathlib import Path

import pytest


def receive(process):
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=15):
            pytest.fail("MCP server did not respond to the legacy handshake")
        return json.loads(process.stdout.readline())
    finally:
        selector.close()


@pytest.mark.parametrize("version", ["2024-11-05", "2025-03-26", "2025-06-18"])
def test_legacy_handshake_and_readonly_tools(version, tmp_path):
    launcher = Path(__file__).parents[1] / "scripts" / "mcp-server.command"
    process = subprocess.Popen(
        [str(launcher), "--data-dir", str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        cwd=tmp_path,
    )

    def send(payload):
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "legacy-client-check", "version": "1"},
                },
            }
        )
        initialized = receive(process)
        assert initialized["result"]["protocolVersion"] == version
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listing = receive(process)
        assert listing["id"] == 2
        assert {tool["name"] for tool in listing["result"]["tools"]} == {
            "search_memory",
            "get_record",
            "get_context_brief",
            "get_sync_status",
            "get_evidence_bundle",
        }
        assert all(tool["annotations"]["readOnlyHint"] for tool in listing["result"]["tools"])
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
