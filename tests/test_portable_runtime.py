"""Portable helper tests use temporary homes and never open the user archive."""

import json
import sys
import tomllib
from pathlib import Path
from unittest.mock import Mock

import pytest
from conftest import FakeTransport, MemoryVault
from fastapi.testclient import TestClient

from brain_settings import runtime
from brain_settings.app import create_app
from brain_settings.helper import main
from brain_settings.mcp_config import connection_info


def test_development_defaults_remain_checkout_local(monkeypatch):
    monkeypatch.delenv("SECOND_BRAIN_DATA_DIR", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert runtime.data_directory() == runtime.PROJECT_ROOT / ".data"
    assert runtime.asset_directory() == runtime.PROJECT_ROOT
    command, arguments = runtime.mcp_command()
    assert command == str(runtime.PROJECT_ROOT / "scripts/mcp-server.command")
    assert arguments == ["--data-dir", str(runtime.PROJECT_ROOT / ".data")]


def test_frozen_defaults_use_user_application_support_without_creating_it(tmp_path, monkeypatch):
    monkeypatch.delenv("SECOND_BRAIN_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    expected = tmp_path / "Library/Application Support/Second Brain"
    assert runtime.data_directory() == expected
    assert runtime.asset_directory() == tmp_path / "bundle"
    assert list(tmp_path.iterdir()) == []


def test_explicit_data_and_environment_override_frozen_default(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("SECOND_BRAIN_DATA_DIR", str(tmp_path / "environment"))
    assert runtime.data_directory() == tmp_path / "environment"
    assert runtime.data_directory(tmp_path / "explicit") == tmp_path / "explicit"
    assert list(tmp_path.iterdir()) == []


def test_frozen_mcp_configuration_uses_relocatable_helper(tmp_path, monkeypatch):
    executable = tmp_path / "moved application/SecondBrainHelper"
    directory = tmp_path / "user data"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    info = connection_info(directory)
    assert info["command"] == str(executable)
    assert info["args"] == ["mcp", "--data-dir", str(directory)]
    claude = json.loads(info["claude_json"])["mcpServers"]["second-brain"]
    codex = tomllib.loads(info["codex_toml"])["mcp_servers"]["second-brain"]
    assert claude == {"command": info["command"], "args": info["args"]}
    assert codex == claude


def test_factory_respects_runtime_data_and_bundled_static_assets(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    (assets / "static").mkdir(parents=True)
    (assets / "static/index.html").write_text("Synthetic portable assets")
    (assets / "static/fixture.css").write_text("body{}")
    data = tmp_path / "data"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(assets), raising=False)
    monkeypatch.setenv("SECOND_BRAIN_DATA_DIR", str(data))
    application = create_app(vault=MemoryVault(), transport=FakeTransport())
    assert application.state.memory_service.store.directory == data
    with TestClient(application, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/").text == "Synthetic portable assets"
        assert client.get("/static/fixture.css").text == "body{}"
        assert client.get("/api/health").status_code == 200
    assert (data / "memory.sqlite3").exists()


@pytest.mark.parametrize("arguments", [["--help"], ["--version"], ["serve", "--help"],
                                        ["mcp", "--help"]])
def test_help_and_version_do_not_construct_services(tmp_path, monkeypatch, arguments, capsys):
    monkeypatch.setenv("SECOND_BRAIN_DATA_DIR", str(tmp_path / "untouched"))
    monkeypatch.setattr("brain_settings.app.create_app", Mock(side_effect=AssertionError))
    monkeypatch.setattr("brain_settings.mcp_server.build_server", Mock(side_effect=AssertionError))
    with pytest.raises(SystemExit) as result:
        main(arguments)
    assert result.value.code == 0
    assert "SecondBrainHelper" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_serve_passes_explicit_loopback_and_data_without_reload(tmp_path, monkeypatch):
    application = object()
    factory = Mock(return_value=application)
    runner = Mock()
    monkeypatch.setattr("brain_settings.app.create_app", factory)
    monkeypatch.setattr("uvicorn.run", runner)
    main(["serve", "--port", "8765", "--data-dir", str(tmp_path)])
    factory.assert_called_once_with(data_dir=tmp_path)
    runner.assert_called_once_with(application, host="127.0.0.1", port=8765, loop="asyncio",
                                   http="h11", ws="none", lifespan="on", access_log=False,
                                   log_level="warning")


def test_mcp_launches_only_read_only_stdio_server(tmp_path, monkeypatch):
    server = Mock()
    factory = Mock(return_value=server)
    app_factory = Mock(side_effect=AssertionError)
    monkeypatch.setattr("brain_settings.mcp_server.build_server", factory)
    monkeypatch.setattr("brain_settings.app.create_app", app_factory)
    main(["mcp", "--data-dir", str(tmp_path)])
    factory.assert_called_once_with(tmp_path)
    server.run.assert_called_once_with(transport="stdio")
    app_factory.assert_not_called()


@pytest.mark.parametrize("arguments", [[], ["serve"], ["serve", "--port", "0"],
                                        ["serve", "--port", "65536"],
                                        ["serve", "--port", "word"],
                                        ["serve", "--port", "8765", "--host", "0.0.0.0"]])
def test_invalid_commands_fail_before_touching_data(tmp_path, monkeypatch, arguments):
    monkeypatch.setenv("SECOND_BRAIN_DATA_DIR", str(tmp_path / "untouched"))
    with pytest.raises(SystemExit) as result:
        main(arguments)
    assert result.value.code == 2
    assert list(tmp_path.iterdir()) == []
