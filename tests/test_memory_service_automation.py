import json
import os

from test_llm_cards import EVIDENCE
from test_memory_service_api import memory_client as memory_client
from test_memory_service_api import wait_job
from test_settings_api import assert_success


def write_session(path, text=EVIDENCE, timestamp=1000, completed=False):
    message = {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}],
    }}
    content = json.dumps(message, ensure_ascii=False) + "\n"
    if completed:
        content += json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n"
    path.write_text(content)
    os.utime(path, (timestamp, timestamp))


def configure_codex(client, headers, directory):
    directory.mkdir()
    return assert_success(client.post("/api/sources", json={
        "kind": "codex", "name": "Synthetic Codex", "path": str(directory),
    }, headers=headers))["source"]


def latest_job(client):
    jobs = assert_success(client.get("/api/memory/status"))["jobs"]
    assert jobs
    return wait_job(client, jobs[0]["id"])


def test_auto_import_waits_for_quiet_and_never_reads_other_active_session(memory_client, tmp_path):
    client, headers, llm = memory_client
    directory = tmp_path / "sessions"
    configure_codex(client, headers, directory)
    write_session(directory / "quiet.jsonl", timestamp=1000)
    write_session(directory / "active.jsonl", text="Another active session", timestamp=1089)
    service = client.app.state.memory_service
    service.tick(now=1050)
    assert assert_success(client.get("/api/memory/status"))["jobs"] == []
    service.tick(now=1091)
    assert latest_job(client)["status"] == "completed"
    status = assert_success(client.get("/api/memory/status"))
    assert status["records"] == 1
    assert status["cards_pending"] == 1
    assert llm.calls == ()
    service.tick(now=1100)
    assert len(assert_success(client.get("/api/memory/status"))["jobs"]) == 1
    service.tick(now=1180)
    assert latest_job(client)["status"] == "completed"
    assert assert_success(client.get("/api/memory/status"))["records"] == 2
    assert llm.calls == ()


def test_completed_turn_auto_imports_immediately_with_opted_in_card_batch_cap(
    memory_client, tmp_path,
):
    client, headers, llm = memory_client
    directory = tmp_path / "sessions"
    configure_codex(client, headers, directory)
    for index in range(3):
        write_session(directory / f"session-{index}.jsonl", timestamp=1000, completed=True)
    assert_success(client.post("/api/memory/schedule", json={
        "enabled": False, "auto_import_enabled": True, "process_cards": True,
        "batch_limit": 2, "interval_minutes": 15,
    }, headers=headers))
    client.app.state.memory_service.tick(now=1001)
    assert latest_job(client)["status"] == "completed"
    status = assert_success(client.get("/api/memory/status"))
    assert status["records"] == 3
    assert status["cards_ready"] == 2
    assert status["cards_pending"] == 1
    assert len(llm.calls) == 2


def test_pause_stops_auto_import_but_manual_import_never_implicitly_generates(
    memory_client, tmp_path,
):
    client, headers, llm = memory_client
    directory = tmp_path / "sessions"
    source = configure_codex(client, headers, directory)
    write_session(directory / "session.jsonl", timestamp=1000, completed=True)
    assert_success(client.post("/api/memory/schedule", json={
        "enabled": False, "auto_import_enabled": False, "process_cards": True,
        "batch_limit": 2, "interval_minutes": 15,
    }, headers=headers))
    service = client.app.state.memory_service
    service.tick(now=10**12)
    assert assert_success(client.get("/api/memory/status"))["records"] == 0
    job_id = assert_success(client.post("/api/memory/import", json={
        "source_id": source["id"],
    }, headers=headers))["job_id"]
    assert wait_job(client, job_id)["status"] == "completed"
    assert assert_success(client.get("/api/memory/status"))["records"] == 1
    assert llm.calls == ()
