"""Portable packaging only accepts explicit public build inputs."""

import importlib.util
import plistlib
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release_package", Path(__file__).parents[1] / "scripts" / "release_package.py",
)
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


def inputs(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "LICENSE").write_text("Synthetic project license")
    (root / ".data").mkdir()
    (root / ".data" / "personal.sqlite3").write_text("private")
    (root / "project-location.txt").write_text("private checkout location")
    native = tmp_path / "SecondBrain"
    native.write_bytes(b"synthetic executable")
    helper = tmp_path / "helper"
    helper.mkdir()
    (helper / "SecondBrainHelper").write_bytes(b"synthetic helper")
    icon = tmp_path / "AppIcon.icns"
    icon.write_bytes(b"synthetic icon")
    notices = tmp_path / "notices"
    notices.mkdir()
    (notices / "THIRD_PARTY_NOTICES.txt").write_text("Synthetic notices")
    return root, native, helper, icon, notices


def test_stage_uses_allowlisted_inputs_and_no_source_location(tmp_path):
    root, native, helper, icon, notices = inputs(tmp_path)
    app = tmp_path / "output" / "Second Brain.app"
    packaging.stage_bundle(root, native, helper, icon, notices, app, "0.1.0")
    assert (app / "Contents/MacOS/SecondBrain").read_bytes() == native.read_bytes()
    assert (app / "Contents/Resources/Backend/SecondBrainHelper").is_file()
    metadata = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert metadata["CFBundleShortVersionString"] == "0.1.0"
    assert metadata["LSMinimumSystemVersion"] == "14.0"
    assert not list(app.rglob("*.sqlite3"))
    assert not list(app.rglob("project-location.txt"))
    assert not list(app.rglob(".data"))


def test_stage_copies_only_the_generated_license_notice(tmp_path):
    root, native, helper, icon, notices = inputs(tmp_path)
    (notices / "stale-private-notes.txt").write_text("must not be distributed")
    app = tmp_path / "Second Brain.app"
    packaging.stage_bundle(root, native, helper, icon, notices, app, "0.1.0")
    copied = app / "Contents/Resources/Licenses"
    assert {path.name for path in copied.iterdir()} == {"THIRD_PARTY_NOTICES.txt"}


def test_license_notices_refresh_existing_directory_without_copying_stale_files(
    tmp_path, monkeypatch,
):
    root = tmp_path / "source"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\ndependencies = []\n')
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    (stdlib / "LICENSE.txt").write_text("Synthetic Python license")
    destination = tmp_path / "notices"
    destination.mkdir()
    generated = destination / "THIRD_PARTY_NOTICES.txt"
    generated.write_text("stale generated contents")
    retained = destination / "unrelated.txt"
    retained.write_text("preserve unrelated file")
    monkeypatch.setattr(packaging.importlib.metadata, "distribution", lambda _: object())
    monkeypatch.setattr(packaging, "_distribution_notice", lambda _: "Synthetic dependency license")
    monkeypatch.setattr(packaging.sysconfig, "get_path", lambda _: str(stdlib))
    packaging.write_notices(root, destination)
    first = generated.read_text()
    packaging.write_notices(root, destination)
    assert generated.read_text() == first
    assert "Synthetic dependency license" in first and "Synthetic Python license" in first
    assert "stale generated contents" not in first
    assert retained.read_text() == "preserve unrelated file"


def test_external_helper_symlink_is_rejected(tmp_path):
    root, native, helper, icon, notices = inputs(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("not distributable")
    (helper / "outside").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        packaging.stage_bundle(root, native, helper, icon, notices,
                               tmp_path / "Second Brain.app", "0.1.0")


def test_internal_framework_symlink_is_preserved(tmp_path):
    root, native, helper, icon, notices = inputs(tmp_path)
    (helper / "actual.dylib").write_bytes(b"synthetic library")
    (helper / "alias.dylib").symlink_to("actual.dylib")
    app = tmp_path / "Second Brain.app"
    packaging.stage_bundle(root, native, helper, icon, notices, app, "0.1.0")
    alias = app / "Contents/Resources/Backend/alias.dylib"
    assert alias.is_symlink() and alias.read_bytes() == b"synthetic library"


@pytest.mark.parametrize("entry", [".data", ".git", "project-location.txt", "memory.sqlite3"])
def test_helper_private_entries_are_rejected(tmp_path, entry):
    root, native, helper, icon, notices = inputs(tmp_path)
    (helper / entry).write_text("must not ship")
    with pytest.raises(ValueError, match="private"):
        packaging.stage_bundle(root, native, helper, icon, notices,
                               tmp_path / "Second Brain.app", "0.1.0")


@pytest.mark.parametrize("version", ["", "../unsafe", "0.1.0\nother"])
def test_release_name_rejects_invalid_versions(version):
    with pytest.raises(ValueError, match="version"):
        packaging.release_name(version, "arm64")


def test_release_name_is_truthful_about_architecture():
    assert packaging.release_name("0.1.0", "arm64") == "Second-Brain-0.1.0-macos-arm64.zip"
    with pytest.raises(ValueError, match="arm64"):
        packaging.release_name("0.1.0", "x86_64")


@pytest.mark.parametrize("minimum,architecture,dependency,error", [
    ("27.0", "arm64", "@rpath/libpython.dylib", "newer"),
    ("14.0", "x86_64 arm64", "@rpath/libpython.dylib", "non-arm64"),
    ("14.0", "arm64", "/opt/local/libpython.dylib", "nonportable linked"),
])
def test_macho_verification_rejects_nonportable_builds(
    monkeypatch, minimum, architecture, dependency, error,
):
    def run(*args):
        if args[0] == "lipo":
            return architecture
        if args[1] == "-l":
            return f"cmd LC_BUILD_VERSION\ncmdsize 32\nplatform 1\nminos {minimum}\n"
        return "Synthetic:\n" + dependency + " (compatibility version 1.0.0)\n"

    monkeypatch.setattr(packaging, "_run", run)
    with pytest.raises(ValueError, match=error):
        packaging.verify_macho(Path("synthetic executable"))


def test_macho_verification_removes_developer_toolchain_rpath(monkeypatch):
    removed = []

    def run(*args):
        if args[0] == "lipo":
            return "arm64"
        if args[0] == "install_name_tool":
            removed.append(args[2])
            return ""
        if args[1] == "-l":
            return ("cmd LC_BUILD_VERSION\ncmdsize 32\nplatform 1\nminos 14.0\n"
                    "cmd LC_RPATH\ncmdsize 80\n"
                    "path /Library/Developer/CommandLineTools/usr/lib/swift (offset 12)\n")
        return "Synthetic:\n/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"

    monkeypatch.setattr(packaging, "_run", run)
    packaging.verify_macho(Path("synthetic executable"))
    assert removed == ["/Library/Developer/CommandLineTools/usr/lib/swift"]
