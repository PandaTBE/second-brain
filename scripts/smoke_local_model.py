#!/usr/bin/env python3
"""Test the frozen local-card path against a synthetic Ollama, never a real LLM."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
from smoke_release import data

MODEL = "qwen-smoke:8b"
TEXT = "Synthetic local memory: SQLite keeps searchable notes."


class SyntheticOllama(BaseHTTPRequestHandler):
    chat_calls = 0

    def log_message(self, *_arguments):
        pass

    def reply(self, payload, status=200):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path != "/api/tags" or self.headers.get("Authorization"):
            return self.reply({"error": "Unexpected request"}, 400)
        self.reply({"models": [
            {"name": MODEL, "model": MODEL, "size": 1024},
            {"name": "synthetic-cloud:cloud", "size": 1024,
             "remote_host": "https://ollama.com", "remote_model": "synthetic-cloud"},
        ]})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if self.path != "/api/chat" or not 0 < size <= 131072:
            return self.reply({"error": "Unexpected request"}, 400)
        payload = json.loads(self.rfile.read(size))
        valid = (not self.headers.get("Authorization")
                 and payload.get("model") == MODEL + ":local"
                 and payload.get("stream") is False and payload.get("think") is False
                 and payload.get("format") == "json")
        if not valid:
            return self.reply({"error": "Local-source contract failed"}, 400)
        card = {"title": "Synthetic local card", "summary": "A local synthetic note.",
                "keywords": ["SQLite"], "questions": ["Where are notes stored?"],
                "atoms": [{"kind": "fact", "text": "SQLite keeps searchable notes.",
                           "evidence": TEXT}]}
        type(self).chat_calls += 1
        self.reply({"model": MODEL, "message": {"role": "assistant",
                                                "content": json.dumps(card)},
                    "done": True, "done_reason": "stop", "prompt_eval_count": 20,
                    "eval_count": 40})


def exercise(client, process):
    deadline = time.monotonic() + 25
    while True:
        try:
            data(client.get("/api/health"))
            break
        except httpx.HTTPError:
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Frozen helper did not start") from None
            time.sleep(0.1)
    bootstrap = data(client.get("/api/bootstrap"))
    assert bootstrap["settings"]["provider"] == "ollama" and not bootstrap["token_saved"]
    client.headers.update({"Origin": str(client.base_url).rstrip("/"),
                           "X-Brain-CSRF": bootstrap["csrf_token"]})
    selection = {"provider": "ollama", "base_url": "http://127.0.0.1:11434/v1"}
    models = data(client.post("/api/models", json=selection))["models"]
    assert [model["id"] for model in models] == [MODEL]
    data(client.post("/api/settings", json={**selection, "model_id": MODEL,
                                       "model_name": "Synthetic local model"}))
    assert data(client.get("/api/memory/status"))["provider_backoff"]["effective_concurrency"] == 1
    job_id = data(client.post("/api/memory/cards", json={"limit": 1}))["job_id"]
    deadline = time.monotonic() + 15
    while True:
        job = data(client.get("/api/memory/jobs/" + job_id))["job"]
        if job["status"] not in ("queued", "running"):
            assert job["status"] == "completed" and job["stats"]["ready"] == 1
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Synthetic local-card job did not finish")
        time.sleep(0.05)
    status = data(client.get("/api/memory/status"))
    assert status["cards_ready"] == 1 and status["cards_error"] == 0
    assert SyntheticOllama.chat_calls == 1


def smoke(app):
    # Refuse to replace a running Ollama or share its port with real models.
    with ThreadingHTTPServer(("127.0.0.1", 11434), SyntheticOllama) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="second-brain-local-smoke-") as temporary:
                directory = Path(temporary)
                moved = directory / "Moved Applications" / "Second Brain.app"
                shutil.copytree(app, moved, symlinks=True)
                helper = moved / "Contents/Resources/Backend/SecondBrainHelper"
                user_home = directory / "Fresh User"
                user_home.mkdir()
                storage = user_home / "Library/Application Support/Second Brain"
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
                from brain_settings.memory_store import MemoryStore
                from brain_settings.providers import Settings
                from brain_settings.source_storage import Source, SourcesStore
                from brain_settings.storage import SettingsStore

                source = Source(id=str(uuid4()), name="Synthetic smoke", kind="folder",
                                path=str(user_home))
                SourcesStore(storage).save((source,))
                SettingsStore(storage).save(Settings(provider="ollama",
                    base_url="http://127.0.0.1:11434/v1", model_id=MODEL))
                MemoryStore(storage).upsert_document(
                    source_id=source.id, source_kind="folder", source_object_id="note",
                    title="Synthetic note", text=TEXT, source_uri="synthetic:note",
                )
                environment = {key: value for key, value in os.environ.items()
                               if not key.startswith(("PYTHON", "SECOND_BRAIN", "DYLD", "_PYI"))}
                environment.update({"HOME": str(user_home), "PATH": "/usr/bin:/bin"})
                with socket.socket() as connection:
                    connection.bind(("127.0.0.1", 0))
                    port = connection.getsockname()[1]
                with (directory / "helper.stderr").open("w") as errors:
                    process = subprocess.Popen([str(helper), "serve", "--port", str(port)],
                                               env=environment, cwd=directory,
                                               stdout=subprocess.DEVNULL, stderr=errors)
                    try:
                        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3,
                                          trust_env=False) as client:
                            exercise(client, process)
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
        finally:
            server.shutdown()
            thread.join(timeout=3)
    return {"synthetic_local_inference": True, "forced_local_source": True,
            "credentials_required": False, "ready_cards": 1, "real_llm_tested": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(smoke(arguments.app.resolve())))


if __name__ == "__main__":
    main()
