"""Apple Notes adapter tests use synthetic JSON and never invoke Notes or TCC."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from brain_settings.apple_notes import AppleNotesClient, run_notes_script
from brain_settings.errors import AppError


def folder(identifier="folder-1"):
    return {
        "id": identifier,
        "name": "Ideas",
        "account": "Demo",
        "path": "Demo / Ideas",
        "note_count": 1,
    }


def note(identifier="note-1", folder_id="folder-1", text="Synthetic astronomy decision"):
    return {
        "id": identifier,
        "folder_id": folder_id,
        "title": "Astronomy",
        "text": text,
        "created_at": "2026-11-01T10:00:00Z",
        "updated_at": "2026-11-02T10:00:00Z",
    }


def test_folder_discovery_uses_structured_request_and_validates_metadata():
    calls = []
    client = AppleNotesClient(
        executor=lambda request: calls.append(request) or {"folders": [folder()]}
    )
    assert client.list_folders()["folders"] == [folder()]
    assert calls == [{"operation": "folders"}]


def test_page_is_scoped_bounded_and_has_opaque_continuation():
    calls = []
    response = {
        "notes": [note()],
        "next_cursor": {"folder_index": 0, "offset": 50},
        "warnings": [],
        "skipped": 0,
    }
    client = AppleNotesClient(executor=lambda request: calls.append(request) or response)
    result = client.read_page(("folder-1",), limit=50)
    assert result == response
    assert calls == [
        {"operation": "notes", "folder_ids": ["folder-1"], "cursor": None, "limit": 50}
    ]


@pytest.mark.parametrize(
    "folders", [(), ("",), ("x\nno",), ("x" * 513,), tuple(str(n) for n in range(51))]
)
def test_invalid_selection_never_starts_executor(folders):
    client = AppleNotesClient(executor=lambda request: pytest.fail("must not invoke Notes"))
    with pytest.raises(AppError):
        client.read_page(folders)


@pytest.mark.parametrize(
    "response",
    [
        {"notes": [note(folder_id="outside")], "next_cursor": None},
        {"notes": [note(text="x" * (1024 * 1024 + 1))], "next_cursor": None},
        {"notes": [note()], "next_cursor": {"folder_index": 8, "offset": 0}},
        {"notes": [note()], "next_cursor": {"folder_index": 0, "offset": -1}},
        {"notes": [note()], "next_cursor": {"folder_index": 0, "offset": True}},
        {"notes": [note()], "next_cursor": None, "skipped": True},
        {"notes": [note()], "next_cursor": None, "complete": False},
        {"notes": "not a list", "next_cursor": None},
    ],
)
def test_untrusted_or_partial_page_is_rejected(response):
    with pytest.raises(AppError):
        AppleNotesClient(executor=lambda request: response).read_page(("folder-1",))


@pytest.mark.parametrize(
    "code, expected",
    [
        ("permission_denied", "apple_notes_permission"),
        ("not_running", "apple_notes_not_running"),
        ("scope_changed", "apple_notes_scope"),
        ("limit", "apple_notes_limit"),
        ("other", "apple_notes_unavailable"),
    ],
)
def test_script_errors_are_sanitized(code, expected):
    client = AppleNotesClient(executor=lambda request: {"error": code, "message": "PRIVATE_BODY"})
    with pytest.raises(AppError) as error:
        client.list_folders()
    assert error.value.code == expected and "PRIVATE_BODY" not in error.value.message


def test_json_output_must_be_complete_and_bounded():
    for raw in (b'{"folders":', b"[]", b"bad", b"x" * (8 * 1024 * 1024 + 1)):
        with pytest.raises(AppError):
            AppleNotesClient(executor=lambda request, data=raw: data).list_folders()
    assert AppleNotesClient(
        executor=lambda request: json.dumps({"folders": [folder()]})
    ).list_folders()["folders"]


def test_runner_has_no_shell_interpolation_and_stdin_is_private(tmp_path, monkeypatch):
    import brain_settings.apple_notes as module

    monkeypatch.setattr(module.sys, "platform", "darwin")
    script = tmp_path / "bridge.js"
    script.write_text("function run() { return '{}'; }")
    captured = {}

    class Process:
        returncode = 0

    def fake_run(command, payload, **kwargs):
        captured.update(command=command, payload=payload, kwargs=kwargs)
        return b'{"folders":[]}'

    monkeypatch.setattr(module, "_bounded_process", fake_run)
    request = {"operation": "notes", "folder_ids": ["quoted'\"$folder"]}
    assert run_notes_script(request, script_path=script) == b'{"folders":[]}'
    assert captured["command"] == ["/usr/bin/osascript", "-l", "JavaScript", str(script)]
    assert json.loads(captured["payload"]) == request
    assert "quoted" not in " ".join(captured["command"])
    assert module.MAX_SECONDS == 25  # Leave time to return through the native 30s request.


def test_runner_rejects_unsupported_platform_before_launch(monkeypatch):
    import brain_settings.apple_notes as module

    monkeypatch.setattr(module.sys, "platform", "linux")
    with pytest.raises(AppError) as error:
        run_notes_script({"operation": "folders"})
    assert error.value.code == "apple_notes_platform"


def test_bounded_runner_reaps_timed_out_child_that_never_reads_stdin(monkeypatch):
    import brain_settings.apple_notes as module

    real_popen, children = subprocess.Popen, []

    def spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    with pytest.raises(AppError) as error:
        module._bounded_process(
            [sys.executable, "-c", "import time; time.sleep(10)"], b"x" * 32_768, timeout=0.1
        )
    assert error.value.code == "apple_notes_timeout"
    assert children and children[0].poll() is not None


@pytest.mark.parametrize("operation", ["read", "write"])
def test_bounded_runner_retries_transient_nonblocking_io(monkeypatch, operation):
    import brain_settings.apple_notes as module

    original = getattr(module.os, operation)
    transient_failures = []

    def transient(*args, **kwargs):
        # Popen also uses os.read internally for its exec-error pipe; inject only bridge I/O.
        bridge_call = operation == "write" or args[1] == 65_536
        if bridge_call and not transient_failures:
            transient_failures.append(True)
            raise BlockingIOError(35, "Resource temporarily unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(module.os, operation, transient)
    program = "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'{}')"
    assert module._bounded_process([sys.executable, "-c", program], b"{}", timeout=3) == b"{}"
    assert transient_failures == [True]


def test_bounded_runner_caps_stdout_and_reaps_child(monkeypatch):
    import brain_settings.apple_notes as module

    real_popen, children = subprocess.Popen, []

    def spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(module, "MAX_OUTPUT_BYTES", 1024)
    program = "import os,time; os.write(1,b'x'*4096); time.sleep(10)"
    with pytest.raises(AppError) as error:
        module._bounded_process([sys.executable, "-c", program], b"{}", timeout=2)
    assert error.value.code == "apple_notes_limit"
    assert children and children[0].poll() is not None


def test_bounded_runner_drains_stderr_without_returning_private_error_text():
    import brain_settings.apple_notes as module

    program = "import os; os.write(2,b'x'*200000); os.write(1,b'{}')"
    assert module._bounded_process([sys.executable, "-c", program], b"{}", timeout=3) == b"{}"
    program = "import os,sys; os.write(2,b'PRIVATE_NOTE_CONTENT (-1743)'); sys.exit(1)"
    with pytest.raises(AppError) as error:
        module._bounded_process([sys.executable, "-c", program], b"{}", timeout=3)
    assert error.value.code == "apple_notes_permission"
    assert "PRIVATE_NOTE_CONTENT" not in error.value.message


def test_bounded_runner_non_utf8_and_process_failure_are_sanitized():
    import brain_settings.apple_notes as module

    def execute(request):
        return module._bounded_process(
            [sys.executable, "-c", "import os; os.write(1,bytes([255]))"], b"{}", timeout=3
        )

    with pytest.raises(AppError) as error:
        AppleNotesClient(executor=execute).list_folders()
    assert error.value.code == "apple_notes_response"
    with pytest.raises(AppError) as error:
        module._bounded_process(
            [sys.executable, "-c", "raise RuntimeError('PRIVATE_NOTE_CONTENT')"], b"{}", timeout=3
        )
    assert "PRIVATE_NOTE_CONTENT" not in error.value.message


def test_arbitrary_warning_contents_and_false_like_complete_are_not_trusted():
    response = {
        "notes": [note()],
        "next_cursor": None,
        "warnings": ["PRIVATE_NOTE_BODY"],
        "skipped": 0,
    }
    result = AppleNotesClient(executor=lambda request: response).read_page(("folder-1",))
    assert "PRIVATE_NOTE_BODY" not in " ".join(result["warnings"])
    for malformed in (0, "false", None):
        with pytest.raises(AppError):
            AppleNotesClient(
                executor=lambda request, marker=malformed: {"folders": [], "complete": marker}
            ).list_folders()


def test_bridge_source_is_bundled_and_read_only():
    script = Path("brain_settings/resources/apple_notes.js").read_text()
    assert "passwordProtected" in script and ".plaintext()" in script
    assert "NSRunningApplication" in script and "readDataToEndOfFile" in script
    for forbidden in (".activate(", ".launch(", ".delete(", ".make(", "System Events", ".body("):
        assert forbidden not in script
