"""Release identity must agree across source, runtime and both bundle formats."""

import re
import tomllib
from pathlib import Path

import pytest

import brain_settings
from brain_settings import helper
from scripts.release_package import bundle_metadata

ROOT = Path(__file__).resolve().parents[1]


def project_version():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_runtime_and_http_identity_follow_project_version(harness):
    version = project_version()
    assert getattr(brain_settings, "__version__", None) == version
    assert helper.VERSION == version
    assert harness.client.app.version == version


def test_helper_version_output_uses_release_identity(monkeypatch, capsys):
    monkeypatch.setattr(helper, "data_directory", lambda *a, **k: pytest.fail("Created app data"))
    with pytest.raises(SystemExit) as caught:
        helper.main(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out.strip() == f"SecondBrainHelper {project_version()}"


def test_lock_and_development_bundle_match_portable_release_metadata():
    version = project_version()
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert next(item for item in lock["package"] if item["name"] == "second-brain-settings")[
        "version"
    ] == version
    script = (ROOT / "scripts/build-app.sh").read_text()
    metadata = bundle_metadata(version)
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        match = re.search(rf'"{key}": "([^"]+)"', script)
        assert match and match[1] == metadata[key]
