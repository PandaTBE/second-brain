"""The bundle gate inspects bytes and code without executing bundled applications."""

import importlib.util
import json
import marshal
import struct
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


def macho_fixture(payload):
    """A thin arm64 image with mapped __text/__cstring, LC_MAIN and build version."""
    header_size, segment_size = 32, 72 + 2 * 80
    command_size = segment_size + 24 + 24
    code_offset = header_size + command_size
    string_offset = code_offset + 4
    file_size = string_offset + len(payload)
    vm_address = 0x100000000
    vm_size = (file_size + 4095) & ~4095
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x100000C, 0, 2, 3,
                         command_size, 0x200005, 0)
    segment = struct.pack("<II16sQQQQIIII", 0x19, segment_size, b"__TEXT",
                          vm_address, vm_size, 0, file_size, 5, 5, 2, 0)
    text = struct.pack("<16s16sQQIIIIIIII", b"__text", b"__TEXT",
                       vm_address + code_offset, 4, code_offset, 2, 0, 0, 0x80000400, 0, 0, 0)
    strings = struct.pack("<16s16sQQIIIIIIII", b"__cstring", b"__TEXT",
                          vm_address + string_offset, len(payload), string_offset,
                          0, 0, 0, 2, 0, 0, 0)
    entry = struct.pack("<IIQQ", 0x80000028, 24, code_offset, 0)
    version = struct.pack("<IIIIII", 0x32, 24, 1, 14 << 16, 14 << 16, 0)
    return header + segment + text + strings + entry + version + b"\x1f\x20\x03\xd5" + payload


@pytest.mark.parametrize("marker", [
    b"-----BEGIN RSA PRIVATE KEY-----\0",
    b"-----BEGIN OPENSSH PRIVATE KEY----------END OPENSSH PRIVATE KEY-----",
])
def test_native_macho_parser_delimiters_without_payload_are_not_credentials(tmp_path, marker):
    app = app_tree(tmp_path)
    (app / "Contents/Resources/parser.so").write_bytes(macho_fixture(marker))
    assert guard.scan_bundle(app)["passed"]


@pytest.mark.parametrize("context", ["text", "pyc", "zip_text", "filename"])
@pytest.mark.parametrize("marker", [
    b"-----BEGIN RSA PRIVATE KEY-----\0",
    b"-----BEGIN OPENSSH PRIVATE KEY----------END OPENSSH PRIVATE KEY-----",
])
def test_native_parser_delimiters_stay_unsafe_in_other_contexts(tmp_path, context, marker):
    app = app_tree(tmp_path)
    resources = app / "Contents/Resources"
    if context == "text":
        (resources / "parser.txt").write_bytes(marker)
    elif context == "pyc":
        code = compile("value = " + repr(marker), "public_fixture.py", "exec")
        (resources / "parser.pyc").write_bytes(b"\0" * 16 + marshal.dumps(code))
    elif context == "zip_text":
        with zipfile.ZipFile(resources / "parser.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("parser.txt", marker)
    else:
        name = marker.split(b"\0", 1)[0].decode()
        (resources / name).write_bytes(macho_fixture(marker))
    report = guard.scan_bundle(app)
    assert "credential_shape" in categories(report)
    assert "PRIVATE KEY" not in json.dumps(report)


@pytest.mark.parametrize("payload", [
    b"-----BEGIN RSA PRIVATE KEY-----\n" + b"A" * 64 + b"\n-----END RSA PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----\r\n" + b"A" * 64 + b"\r\n-----END RSA PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----" + b"A" * 64 + b"-----END RSA PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----\0" + b"sk-" + b"synthetic" * 4,
    b"-----BEGIN RSA PRIVATE KEY-----\0-----BEGIN PRIVATE KEY-----payload",
])
def test_native_macho_unsafe_private_key_or_token_still_fails(tmp_path, payload):
    app = app_tree(tmp_path)
    (app / "Contents/Resources/parser.so").write_bytes(macho_fixture(payload))
    report = guard.scan_bundle(app)
    assert "credential_shape" in categories(report)
    assert "PRIVATE KEY" not in json.dumps(report) and "synthetic" not in json.dumps(report)


@pytest.mark.parametrize("damage", [
    "magic_only", "truncated_header", "truncated_commands", "oversized_command", "zero_command",
    "zero_commands", "wrong_table_size", "no_instructions", "section_bounds", "segment_bounds",
    "section_count", "unknown_architecture", "relocation_bounds", "zero_fill_instructions",
])
def test_malformed_or_magic_only_macho_never_gets_parser_delimiter_exception(tmp_path, damage):
    marker = b"-----BEGIN RSA PRIVATE KEY-----\0"
    image = bytearray(macho_fixture(marker))
    if damage == "magic_only":
        image = image[:4] + marker
    elif damage == "truncated_header":
        image = image[:24] + marker
    elif damage == "truncated_commands":
        image = image[:104] + marker
    elif damage == "oversized_command":
        struct.pack_into("<I", image, 36, len(image) + 8)
    elif damage == "zero_command":
        struct.pack_into("<I", image, 36, 0)
    elif damage == "zero_commands":
        struct.pack_into("<I", image, 16, 0)
    elif damage == "wrong_table_size":
        struct.pack_into("<I", image, 20, 8)
    elif damage == "no_instructions":
        image[104:120] = b"__const".ljust(16, b"\0")
        struct.pack_into("<I", image, 104 + 64, 0)
    elif damage == "section_bounds":
        struct.pack_into("<I", image, 104 + 48, len(image) + 1)
    elif damage == "segment_bounds":
        struct.pack_into("<Q", image, 32 + 48, len(image) + 1)
    elif damage == "section_count":
        struct.pack_into("<I", image, 32 + 64, 3)
    elif damage == "unknown_architecture":
        struct.pack_into("<I", image, 4, 0)
    elif damage == "zero_fill_instructions":
        struct.pack_into("<I", image, 104 + 64, 0x80000401)
    else:
        struct.pack_into("<II", image, 104 + 56, len(image) + 1, 1)
    app = app_tree(tmp_path)
    (app / "Contents/Resources/parser.so").write_bytes(image)
    assert "credential_shape" in categories(guard.scan_bundle(app))


@pytest.mark.parametrize("container", ["pyc", "zip_text"])
def test_nested_macho_bytes_cannot_enable_a_native_exception_in_container_data(tmp_path, container):
    app = app_tree(tmp_path)
    image = macho_fixture(b"-----BEGIN RSA PRIVATE KEY-----\0")
    if container == "pyc":
        code = compile("value = " + repr(image), "public_fixture.py", "exec")
        (app / "Contents/Resources/parser.pyc").write_bytes(b"\0" * 16 + marshal.dumps(code))
    else:
        with zipfile.ZipFile(app / "Contents/Resources/parser.zip", "w") as archive:
            archive.writestr("parser.txt", image)
    assert "credential_shape" in categories(guard.scan_bundle(app))
