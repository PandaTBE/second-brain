"""Synthetic headless transcripts must never become another card request."""

import json

import pytest
from test_gigacode_auto_cards import select_cli
from test_gigacode_cli import PAYLOAD, RESULT, ready
from test_gigacode_manual_cards import enable_automation
from test_memory_service_api import memory_client as memory_client
from test_qwen_source import record, transcript

from brain_settings.gigacode_cli import GigaCodeCLI
from brain_settings.importers import import_source
from brain_settings.memory_store import MemoryStore


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("provenance", ["workspace", "prompt"])
def test_stored_service_record_never_claims_or_calls_provider(
    memory_client, tmp_path, automatic, provenance,
):
    client, headers, llm = memory_client
    root = tmp_path / "projects"
    root.mkdir()
    source = client.post("/api/sources", headers=headers, json={
        "kind": "qwen", "name": "Synthetic chats", "path": str(root),
    }).json()["data"]["source"]
    service = client.app.state.memory_service
    select_cli(service)
    enable_automation(client, headers)
    text = ("Пользователь: [SECOND_BRAIN_INTERNAL_CARD_GENERATION_V1]\n"
            "Generate the requested search card from standard input. Do not use tools.")
    saved = service.store.upsert_document(
        source["id"], "qwen", "demo/chats/service.jsonl::chunk:000000",
        "Synthetic service request", text if provenance == "prompt" else "Assistant output",
        "file:///synthetic/service.jsonl", project=(
            "/tmp/second-brain-cli-test" if provenance == "workspace" else None),
    )
    current = service.store.get_record(saved["id"])
    stats = service._process_card(
        current, expected_scope=service._provider_scope(), manual=not automatic,
        automatic=automatic,
    )
    assert stats["stale"] == 1
    assert llm.calls == ()
    assert service.store.get_record(saved["id"])["card_attempts"] == 0


def test_headless_cli_transcript_cannot_feed_another_import(tmp_path):
    root = tmp_path / "projects"
    chat = root / "service-project" / "chats" / "service.jsonl"
    chat.parent.mkdir(parents=True)
    # Simulate the CLI's documented stdin + appended prompt persistence.
    body = (
        "data=sys.stdin.read()\n"
        "prompt=sys.argv[sys.argv.index('-p')+1]\n"
        "rows=[{'type':'user','message':{'role':'user','parts':[{'text':data+'\\n'+prompt}]}},"
        "{'type':'assistant','message':{'role':'model',"
        "'parts':[{'text':'synthetic card output'}]}}]\n"
        f"open({str(chat)!r},'w').write(''.join(json.dumps(r)+'\\n' for r in rows))\n"
        f"print({json.dumps(RESULT)!r})\n"
    )
    cli = ready(tmp_path, body)
    GigaCodeCLI(str(cli)).generate(PAYLOAD)
    original = chat.read_bytes()
    ordinary = chat.with_name("ordinary.jsonl")
    ordinary.write_text(transcript([record("user", "Ordinary astronomy conversation")]))
    store = MemoryStore(tmp_path / "memory")
    source = {"id": "synthetic-qwen", "kind": "qwen", "path": str(root)}
    first = import_source(source, store)
    assert not first["errors"]
    assert first["imported"] == 1
    assert len(store.search("")) == 1
    assert "astronomy" in store.search("")[0]["text"]
    assert import_source(source, store)["imported"] == 0
    assert chat.read_bytes() == original
