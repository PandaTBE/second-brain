"""The CLI adapter is exercised with synthetic local executables only."""

import json
import os
import sys
import time

import pytest

from brain_settings.errors import AppError
from brain_settings.gigacode_cli import GigaCodeCLI

HELP = "--safe-mode --prompt --output-format text json stream-json --model"
PAYLOAD = {"messages": [{"role": "system", "content": "Create a card"},
                        {"role": "user", "content": "synthetic private source"}]}
RESULT = [{"type": "result", "subtype": "success", "is_error": False,
           "result": "card text", "usage": {"input_tokens": 12, "output_tokens": 3,
                                              "total_tokens": 15}}]


def executable(tmp_path, code, name="gigacode"):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n" + code)
    path.chmod(0o700)
    return path


def ready(tmp_path, code=""):
    return executable(tmp_path, "import sys, json, os\n"
                      f"if '--help' in sys.argv:\n print({HELP!r}); sys.exit(0)\n" + code)


def test_check_only_runs_help_without_input_or_model_inference(tmp_path):
    marker = tmp_path / "inference"
    path = ready(tmp_path, f"open({str(marker)!r},'w').write('unexpected')\n")
    assert GigaCodeCLI(str(path)).check()["compatible"] is True
    assert not marker.exists()


def test_generation_keeps_prompt_off_argv_and_uses_private_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGACODE_TEST_AUTH", "present")
    marker = tmp_path / "captured.json"
    path = ready(tmp_path, f"open({str(marker)!r},'w').write(json.dumps({{"
                 "'argv':sys.argv,'stdin':sys.stdin.read(),'cwd':os.getcwd(),"
                 "'auth':os.getenv('GIGACODE_TEST_AUTH'),'mode':os.stat('.').st_mode & 0o777}))\n"
                 f"print({json.dumps(RESULT)!r})\n")
    admissions = []
    result = GigaCodeCLI(str(path)).generate(PAYLOAD, model_id="model-name", before_request=lambda:
                                            admissions.append(True))
    captured = json.loads(marker.read_text())
    assert result["choices"][0]["message"]["content"] == "card text"
    assert result["usage"]["total_tokens"] == 15
    assert json.loads(captured["stdin"])["messages"] == PAYLOAD["messages"]
    assert "synthetic private source" not in " ".join(captured["argv"])
    assert captured["argv"][1:] == ["--safe-mode", "-p",
        "Generate the requested search card from standard input. Do not use tools.",
        "--output-format", "json", "--model", "model-name"]
    assert captured["auth"] == "present" and captured["mode"] == 0o700
    assert not os.path.exists(captured["cwd"])
    assert admissions == [True]


def test_default_model_uses_cli_configuration(tmp_path):
    path = ready(tmp_path, "assert '--model' not in sys.argv\n"
                 f"print({json.dumps(RESULT)!r})\n")
    assert GigaCodeCLI(str(path)).generate(PAYLOAD)["choices"]


def test_frozen_helper_environment_does_not_contaminate_external_runtime(monkeypatch):
    from brain_settings.gigacode_cli import _environment

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/synthetic/bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/synthetic/user-library")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/synthetic/bundle")
    monkeypatch.delenv("DYLD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "synthetic-helper")
    monkeypatch.setenv("GIGACODE_TEST_AUTH", "present")
    environment = _environment()
    assert environment["LD_LIBRARY_PATH"] == "/synthetic/user-library"
    assert "LD_LIBRARY_PATH_ORIG" not in environment
    assert "DYLD_LIBRARY_PATH" not in environment
    assert "_PYI_ARCHIVE_FILE" not in environment
    assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert environment["GIGACODE_TEST_AUTH"] == "present"
    assert os.environ["LD_LIBRARY_PATH"] == "/synthetic/bundle"


@pytest.mark.parametrize("path", ["relative/gigacode", "~/gigacode", "/missing/gigacode"])
def test_bad_paths_fail_without_inference(path):
    with pytest.raises(AppError) as caught:
        GigaCodeCLI(path).check()
    assert caught.value.request_sent is False


def test_path_lookup_without_shell(tmp_path, monkeypatch):
    path = ready(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert GigaCodeCLI().check()["compatible"] is True
    path.chmod(0o600)
    with pytest.raises(AppError):
        GigaCodeCLI().check()


@pytest.mark.parametrize("help_text", ["", "--safe-mode --prompt --output-format text",
                                        "--prompt --output-format json", "--safe-mode json"])
def test_missing_capabilities_fail_without_version_requirement(tmp_path, help_text):
    path = executable(tmp_path, f"print({help_text!r})\n")
    with pytest.raises(AppError) as caught:
        GigaCodeCLI(str(path)).check()
    assert caught.value.code == "gigacode_cli_unsupported"
    assert caught.value.request_sent is False


def test_exit_error_does_not_reflect_sensitive_stdout_or_stderr(tmp_path):
    path = ready(tmp_path, "print('synthetic-secret');print('synthetic-secret',file=sys.stderr);"
                 "sys.exit(7)\n")
    with pytest.raises(AppError) as caught:
        GigaCodeCLI(str(path)).generate(PAYLOAD)
    assert "synthetic-secret" not in str(caught.value)
    assert caught.value.request_sent is True


def test_invalid_json_is_a_sent_request(tmp_path):
    path = ready(tmp_path, "print('not JSON')\n")
    with pytest.raises(AppError) as caught:
        GigaCodeCLI(str(path)).generate(PAYLOAD)
    assert caught.value.code == "gigacode_response_invalid"
    assert caught.value.request_sent is True


@pytest.mark.parametrize("stream,size", [("stdout", 1_048_577), ("stderr", 65_537)])
def test_output_is_bounded_and_fails_promptly(tmp_path, stream, size):
    path = ready(tmp_path, f"sys.{stream}.write('x'*{size});sys.{stream}.flush()\n"
                 "import time;time.sleep(10)\n")
    started = time.monotonic()
    with pytest.raises(AppError) as caught:
        GigaCodeCLI(str(path)).generate(PAYLOAD, timeout=2)
    assert caught.value.code == "gigacode_cli_output_limit"
    assert time.monotonic() - started < 3


def test_timeout_kills_child_group_before_private_directory_is_removed(tmp_path):
    marker = tmp_path / "child-did-run"
    script = f"import time;time.sleep(0.7);open({str(marker)!r},'w').write('unexpected')"
    path = ready(tmp_path, "import subprocess,time\n"
                 f"subprocess.Popen([{sys.executable!r},'-c',{script!r}])\n"
                 "time.sleep(10)\n")
    with pytest.raises(AppError) as caught:
        GigaCodeCLI(str(path)).generate(PAYLOAD, timeout=0.15)
    assert caught.value.code == "gigacode_cli_timeout"
    assert caught.value.request_sent is True
    time.sleep(0.8)
    assert not marker.exists()


def test_child_holding_pipe_cannot_outlive_successful_parent(tmp_path):
    marker = tmp_path / "child-did-run"
    script = f"import time;time.sleep(0.7);open({str(marker)!r},'w').write('unexpected')"
    path = ready(tmp_path, "import subprocess\n"
                 f"subprocess.Popen([{sys.executable!r},'-c',{script!r}])\n"
                 f"print({json.dumps(RESULT)!r})\n")
    started = time.monotonic()
    assert GigaCodeCLI(str(path)).generate(PAYLOAD, timeout=2)["choices"]
    assert time.monotonic() - started < 3
    time.sleep(0.8)
    assert not marker.exists()


def test_failed_admission_never_starts_generation(tmp_path):
    marker = tmp_path / "inference"
    path = ready(tmp_path, f"open({str(marker)!r},'w').write('unexpected')\n")
    def deny():
        raise AppError("source_disabled", "Source disabled")
    with pytest.raises(AppError) as caught:
        GigaCodeCLI(str(path)).generate(PAYLOAD, before_request=deny)
    assert caught.value.code == "source_disabled"
    assert caught.value.request_sent is False
    assert not marker.exists()


def test_unexpected_admission_failure_is_safe_and_not_sent(tmp_path):
    marker = tmp_path / "called"
    path = ready(tmp_path, f"open({str(marker)!r},'w').write('unexpected')\n")
    def deny():
        raise RuntimeError("synthetic-sensitive-detail")
    with pytest.raises(AppError) as caught:
        GigaCodeCLI(str(path)).generate(PAYLOAD, before_request=deny)
    assert caught.value.request_sent is False
    assert caught.value.code == "gigacode_admission_failed"
    assert "synthetic-sensitive-detail" not in str(caught.value)
    assert not marker.exists()


def test_cannot_start_process_has_safe_unsent_error(tmp_path, monkeypatch):
    path = ready(tmp_path)
    def fail(*args, **kwargs):
        raise OSError("synthetic-sensitive-detail")
    monkeypatch.setattr("brain_settings.gigacode_cli.subprocess.Popen", fail)
    with pytest.raises(AppError) as caught:
        GigaCodeCLI(str(path)).generate(PAYLOAD)
    assert caught.value.request_sent is False
    assert caught.value.code == "gigacode_cli_start_failed"
    assert "synthetic-sensitive-detail" not in str(caught.value)


def test_os_pipe_error_is_safe_and_conservatively_sent(tmp_path, monkeypatch):
    path = ready(tmp_path, f"print({json.dumps(RESULT)!r})\n")
    def fail(*args, **kwargs):
        raise OSError("synthetic-sensitive-detail")
    monkeypatch.setattr("brain_settings.gigacode_cli._pump", fail)
    with pytest.raises(AppError) as caught:
        GigaCodeCLI(str(path)).generate(PAYLOAD)
    assert caught.value.request_sent is True
    assert caught.value.code == "gigacode_cli_io_failed"
    assert "synthetic-sensitive-detail" not in str(caught.value)


@pytest.mark.parametrize("operation", ["check", "generate"])
def test_busy_runner_rejects_immediately_without_stale_queued_admission(tmp_path, operation):
    from brain_settings.gigacode_cli import _LOCK

    marker = tmp_path / "called"
    path = executable(tmp_path, f"open({str(marker)!r},'w').write('unexpected')\n")
    admissions = []
    started = time.monotonic()
    with _LOCK:
        with pytest.raises(AppError) as caught:
            client = GigaCodeCLI(str(path))
            if operation == "check":
                client.check()
            else:
                client.generate(PAYLOAD, before_request=lambda: admissions.append(True))
    assert time.monotonic() - started < 0.2
    assert caught.value.code == "gigacode_cli_busy"
    assert caught.value.request_sent is False
    assert not marker.exists() and admissions == []


@pytest.mark.parametrize("payload,model", [(None, "default"), ({}, "default"),
                                           (PAYLOAD, "--bad"), (PAYLOAD, "bad\nmodel")])
def test_invalid_input_never_starts_cli(tmp_path, payload, model):
    marker = tmp_path / "called"
    path = executable(tmp_path, f"open({str(marker)!r},'w').write('unexpected')\n")
    with pytest.raises(AppError) as caught:
        GigaCodeCLI(str(path)).generate(payload, model_id=model)
    assert caught.value.request_sent is False
    assert not marker.exists()
