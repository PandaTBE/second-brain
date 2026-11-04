"""Importing the server must not open a user's database or credentials."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock

import uvicorn
from conftest import MemoryVault
from fastapi.testclient import TestClient

import launch
from brain_settings.app import create_app


def test_app_import_does_not_construct_resources(tmp_path):
    package_root = Path(__file__).resolve().parents[1]
    script = """
from unittest.mock import patch

def forbidden(*args, **kwargs):
    raise AssertionError('Import attempted to construct application resources')

with patch('brain_settings.service.SettingsService', forbidden), \\
     patch('brain_settings.sources.SourceService', forbidden), \\
     patch('brain_settings.memory_service.MemoryService', forbidden), \\
     patch('brain_settings.memory_store.MemoryStore', forbidden):
    import brain_settings.app
    assert callable(brain_settings.app.create_app)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(package_root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_launcher_uses_explicit_factory(monkeypatch):
    monkeypatch.setenv("SECOND_BRAIN_PORT", "18765")
    monkeypatch.setattr(launch, "is_running", lambda _url: False)
    monkeypatch.setattr(launch.socket, "socket", MagicMock())
    monkeypatch.setattr(launch.threading, "Thread", Mock())
    run = Mock()
    monkeypatch.setattr(launch.uvicorn, "run", run)
    launch.main()
    assert run.call_args.args == ("brain_settings.app:create_app",)
    assert run.call_args.kwargs["factory"] is True
    assert run.call_args.kwargs["host"] == "127.0.0.1"


def test_uvicorn_factory_loads_a_synthetic_application(tmp_path, monkeypatch):
    import brain_settings.app as server_module

    directory = tmp_path / "owned-data"
    monkeypatch.setattr(
        server_module, "create_app", lambda: create_app(data_dir=directory, vault=MemoryVault())
    )
    config = uvicorn.Config("brain_settings.app:create_app", factory=True, log_config=None)
    config.load()
    with TestClient(config.loaded_app, base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data"]["app_id"] == "second-brain-settings"
    assert (directory / "memory.sqlite3").exists()
