"""The bundle gate inspects bytes and code without executing bundled applications."""

import importlib.util
import json
import marshal
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release_bundle.py"
spec = importlib.util.spec_from_file_location("release_bundle_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)


def app_tree(tmp_path):
    app = tmp_path / "Second Brain.app"
    (app / "Contents" / "Resources").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(b"public metadata")
    return app


def categories(report):
    return {item["category"] for item in report["findings"]}


def test_clean_tree_counts_every_file_without_reading_outside_bundle(tmp_path):
    app = app_tree(tmp_path)
    report = guard.scan_bundle(app)
    assert report["passed"] and report["files_scanned"] == 1
    assert report["unreadable_count"] == 0


@pytest.mark.parametrize("relative", [
    "Contents/Resources/project-location.txt", "Contents/Resources/.data/settings.json",
    "Contents/Resources/memory.sqlite3", "Contents/Resources/personal.sqlite3-wal",
    "Contents/Resources/.env", "Contents/Resources/session.jsonl",
])
def test_rejects_private_artifact_names_without_printing_them(tmp_path, relative):
    app = app_tree(tmp_path)
    target = app / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("private contents")
    report = guard.scan_bundle(app)
    assert not report["passed"] and "private_file" in categories(report)
    assert "private contents" not in json.dumps(report)


def test_symlinks_must_stay_inside_bundle(tmp_path):
    app = app_tree(tmp_path)
    (app / "Contents" / "Resources" / "internal").symlink_to("../Info.plist")
    assert guard.scan_bundle(app)["passed"]
    outside = tmp_path / "outside"
    outside.write_text("do not read me")
    (app / "Contents" / "Resources" / "escape").symlink_to(outside)
    report = guard.scan_bundle(app)
    assert "external_symlink" in categories(report)
    assert report["files_scanned"] == 1
    assert "do not read me" not in json.dumps(report)


def test_detects_owner_home_and_tokens_without_echoing_values(tmp_path):
    app = app_tree(tmp_path)
    credential = "sk-" + "opaque" * 6
    content = "Build /Users/" + "private-owner/project, release-user@example.invalid " + credential
    (app / "Contents" / "Resources" / "metadata").write_text(content)
    report = guard.scan_bundle(app, owner_markers=("release-user@example.invalid",))
    assert categories(report) >= {"personal_path", "owner_marker", "credential_shape"}
    serialized = json.dumps(report)
    assert credential not in serialized and "private-owner" not in serialized
    assert "release-user@example.invalid" not in serialized


def test_placeholder_paths_and_validator_patterns_are_not_credentials(tmp_path):
    app = app_tree(tmp_path)
    sample = "/Users/" + "build/project /Users/" + "runner/work /Users/"
    sample += "your_user/example sk-[A-Z]{20,}"
    (app / "Contents" / "Resources" / "metadata").write_text(sample)
    assert guard.scan_bundle(app)["passed"]


def test_nested_code_constants_and_filenames_are_scanned(tmp_path):
    app = app_tree(tmp_path)
    pyc = app / "Contents" / "Resources" / "unsafe.pyc"
    source = "def nested():\n    return 'owner-sensitive-marker'\n"
    code = compile(source, "/Users/" + "private-owner/source.py", "exec")
    pyc.write_bytes(b"\0" * 16 + marshal.dumps(code))
    report = guard.scan_bundle(app, owner_markers=("owner-sensitive-marker",))
    assert report["python_code_objects"] == 2
    assert categories(report) >= {"personal_path", "owner_marker"}


def test_base_library_zip_is_scanned_after_decompression(tmp_path):
    app = app_tree(tmp_path)
    path = app / "Contents" / "Resources" / "base_library.zip"
    code = compile("value='owner-sensitive-marker'", "public.py", "exec")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("module.pyc", b"\0" * 16 + marshal.dumps(code))
    report = guard.scan_bundle(app, owner_markers=("owner-sensitive-marker",))
    assert report["python_code_objects"] == 1
    assert "owner_marker" in categories(report)
    assert report["unreadable_count"] == 0


def test_corrupt_bytecode_is_a_failure_with_safe_diagnostics(tmp_path):
    app = app_tree(tmp_path)
    (app / "Contents" / "Resources" / "bad.pyc").write_bytes(b"unreadable-code")
    report = guard.scan_bundle(app)
    assert not report["passed"] and report["unreadable_count"] == 1
    assert "unreadable-code" not in json.dumps(report)


def test_carchive_and_nested_pyz_are_audited(monkeypatch, tmp_path):
    app = app_tree(tmp_path)
    helper = app / "Contents" / "Resources" / "Backend" / "SecondBrainHelper"
    helper.parent.mkdir()
    helper.write_bytes(b"fake executable")
    entry_code = compile("value=1", "entry.py", "exec")
    inner_code = compile("value='owner-sensitive-marker'", "inner.py", "exec")

    class FakePyz:
        toc = {"inner": (0, 0, 0), "namespace": (3, 0, 0)}

        def extract(self, name):
            return inner_code if name == "inner" else None

    class FakeArchive:
        def __init__(self, path):
            self.toc = {"entry": (0, 0, 0, 0, "s"), "PYZ.pyz": (0, 0, 0, 0, "z")}

        def extract(self, name):
            return marshal.dumps(entry_code)

        def open_embedded_archive(self, name):
            return FakePyz()

    monkeypatch.setattr(guard, "archive_reader", lambda: FakeArchive)
    report = guard.scan_bundle(app, owner_markers=("owner-sensitive-marker",))
    assert report["python_code_objects"] == 2
    assert report["python_archives"] == 2
    assert "owner_marker" in categories(report)


def test_helper_archive_must_not_be_silently_skipped(monkeypatch, tmp_path):
    app = app_tree(tmp_path)
    helper = app / "Contents" / "Resources" / "Backend" / "SecondBrainHelper"
    helper.parent.mkdir()
    helper.write_bytes(b"fake executable")
    monkeypatch.setattr(guard, "archive_reader", lambda: (_ for _ in ()).throw(ImportError()))
    report = guard.scan_bundle(app)
    assert not report["passed"] and report["unreadable_count"] == 1


def test_folded_tuple_constants_are_not_a_bytecode_blind_spot(tmp_path):
    app = app_tree(tmp_path)
    code = compile("value=('owner-sensitive-marker', ('nested-value',))", "public.py", "exec")
    target = app / "Contents" / "Resources" / "tuple.pyc"
    target.write_bytes(b"\0" * 16 + marshal.dumps(code))
    report = guard.scan_bundle(app, owner_markers=("owner-sensitive-marker",))
    assert "owner_marker" in categories(report)


def test_corrupt_zip_and_broken_symlink_fail_closed(tmp_path):
    app = app_tree(tmp_path)
    (app / "Contents" / "Resources" / "bad.zip").write_bytes(b"not an archive")
    (app / "Contents" / "Resources" / "broken").symlink_to("missing")
    report = guard.scan_bundle(app)
    assert report["unreadable_count"] == 2 and not report["passed"]


@pytest.mark.parametrize("archive_kind", ["zip", "carchive", "pyz"])
def test_archive_member_names_are_scanned(monkeypatch, tmp_path, archive_kind):
    app = app_tree(tmp_path)
    code = compile("value=1", "public.py", "exec")
    member_name = "owner-sensitive-marker"
    if archive_kind == "zip":
        target = app / "Contents" / "Resources" / "base_library.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(member_name + ".pyc", b"\0" * 16 + marshal.dumps(code))
    else:
        target = app / "Contents" / "Resources" / "Backend" / "SecondBrainHelper"
        target.parent.mkdir()
        target.write_bytes(b"fake executable")

        class FakePyz:
            toc = {member_name: (0, 0, 0)}

            def extract(self, name):
                return code

        class FakeArchive:
            def __init__(self, path):
                self.toc = ({member_name: (0, 0, 0, 0, "s")} if archive_kind == "carchive"
                            else {"PYZ.pyz": (0, 0, 0, 0, "z")})

            def extract(self, name):
                return marshal.dumps(code)

            def open_embedded_archive(self, name):
                return FakePyz()

        monkeypatch.setattr(guard, "archive_reader", lambda: FakeArchive)
    report = guard.scan_bundle(app, owner_markers=(member_name,))
    assert "owner_marker" in categories(report)
    assert member_name not in json.dumps(report)


def test_missing_app_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="application bundle"):
        guard.scan_bundle(tmp_path / "missing.app")


def test_cli_safe_report_is_written_outside_scanned_app(tmp_path):
    app = app_tree(tmp_path)
    report = tmp_path / "report.json"
    assert guard.main(["--app", str(app), "--report", str(report)]) == 0
    assert json.loads(report.read_text())["unreadable_count"] == 0
    with pytest.raises(ValueError):
        guard.main(["--app", str(app), "--report", str(app / "report.json")])
