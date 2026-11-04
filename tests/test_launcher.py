"""Launcher recognizes only its own application, even on an occupied port."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from launch import is_running


@pytest.mark.parametrize("payload", [[], None, {"data": None}, {"data": []}, {"data": {}}])
def test_other_service_health_is_not_our_application(payload):
    response = Mock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    with patch("urllib.request.build_opener", return_value=opener):
        assert is_running("http://127.0.0.1:8765") is False


def test_valid_health_recognizes_application():
    response = Mock()
    response.read.return_value = b'{"data":{"app_id":"second-brain-settings"}}'
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    with patch("urllib.request.build_opener", return_value=opener):
        assert is_running("http://127.0.0.1:8765") is True


def test_relaunch_retries_dependency_sync_even_after_partial_install(tmp_path):
    shutil.copy(Path(__file__).parents[1] / "start.command", tmp_path / "start.command")
    executable = tmp_path / ".venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    tools = tmp_path / "tools"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text("#!/bin/sh\nexit 17\n")
    uv.chmod(0o700)
    result = subprocess.run(
        ["/bin/zsh", str(tmp_path / "start.command")],
        env={**os.environ, "PATH": f"{tools}:/usr/bin:/bin"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 17
