"""Bounded headless GigaCode execution; safe mode is not an OS sandbox.

The selected executable inherits the user's login environment for CLI-owned
authentication. Built-in agent tools may remain available. Callers must admit
only explicitly requested manual card jobs under external-model source policy.
"""

import json
import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import AppError
from .gigacode_output import normalize_gigacode_output

MAX_STDOUT = 1_048_576
MAX_STDERR = 65_536
MAX_INPUT = 131_072
PROMPT = "Generate the requested search card from standard input. Do not use tools."
_LOCK = threading.Lock()


@contextmanager
def _claimed():
    if not _LOCK.acquire(blocking=False):
        raise _error("gigacode_cli_busy", "GigaCode CLI уже выполняет запрос. "
                     "Дождитесь завершения и повторите ручной запуск.")
    try:
        yield
    finally:
        _LOCK.release()


def _error(code, message, *, sent=False):
    error = AppError(code, message, 502 if sent else 400)
    error.request_sent = sent
    error.requires_user_action = True
    return error


def _executable(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise _error("gigacode_cli_path_invalid", "Укажите полный путь к GigaCode CLI.")
    selected = value or shutil.which("gigacode")
    if not selected:
        raise _error("gigacode_cli_missing", "GigaCode CLI не найден. Укажите полный путь к нему.")
    path = Path(selected)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise _error("gigacode_cli_path_invalid", "Проверьте полный путь и права запуска "
                     "GigaCode CLI.")
    return str(path)


def _environment():
    environment = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    if getattr(sys, "frozen", False):
        # External programs must use their own libraries, not the frozen helper's.
        for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            original = environment.pop(name + "_ORIG", None)
            if original is None:
                environment.pop(name, None)
            else:
                environment[name] = original
        environment = {key: value for key, value in environment.items()
                       if not key.startswith("_PYI_")}
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _duration(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise _error("gigacode_cli_input_invalid", "Проверьте параметры запроса GigaCode CLI.")
    return min(180, max(0.05, value))


def _pump(process, content, deadline, *, sent):
    """Write stdin and drain both output pipes without unbounded communicate()."""
    output = {"stdout": bytearray(), "stderr": bytearray()}
    offset = 0
    with selectors.DefaultSelector() as selector:
        for name in output:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        if content:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            process.stdin.close()
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _error("gigacode_cli_timeout", "GigaCode CLI не ответил вовремя. "
                             "Повторите ручной запуск позже.", sent=sent)
            if process.poll() is not None:
                _kill_group(process)
            for key, _ in selector.select(min(remaining, 0.05)):
                if key.data == "stdin":
                    try:
                        offset += os.write(key.fd, content[offset:offset + 65536])
                    except BrokenPipeError:
                        offset = len(content)
                    if offset == len(content):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                block = os.read(key.fd, 65536)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                limit = MAX_STDOUT if key.data == "stdout" else MAX_STDERR
                if len(output[key.data]) + len(block) > limit:
                    raise _error("gigacode_cli_output_limit", "Ответ GigaCode CLI превысил лимит. "
                                 "Сократите материал для карточки.", sent=sent)
                output[key.data].extend(block)
    try:
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        raise _error("gigacode_cli_timeout", "GigaCode CLI не завершил запрос вовремя.",
                     sent=sent) from None
    if process.returncode:
        raise _error("gigacode_cli_failed", "GigaCode CLI завершился с ошибкой. Проверьте вход "
                     "и подключение в терминале; исходный текст сохранён.", sent=sent)
    return bytes(output["stdout"])


def _run(argv, content=b"", *, timeout, sent=False, before_request=None):
    if os.name != "posix":
        raise _error("gigacode_cli_unsupported", "Этот запуск GigaCode CLI требует "
                     "macOS или Linux.")
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="second-brain-cli-") as work:
            os.chmod(work, 0o700)
            if before_request is not None:
                try:
                    before_request()
                except AppError as error:
                    error.request_sent = False
                    raise
                except Exception:
                    raise _error("gigacode_admission_failed", "Запрос GigaCode CLI не отправлен. "
                                 "Проверьте состояние синхронизации и повторите позже.") from None
            try:
                process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, cwd=work, start_new_session=True,
                                           env=_environment(),
                                           shell=False)
            except OSError:
                raise _error("gigacode_cli_start_failed", "Не удалось запустить GigaCode CLI. "
                             "Проверьте путь к программе и права доступа.") from None
            try:
                return _pump(process, content, time.monotonic() + timeout, sent=sent)
            finally:
                _kill_group(process)
                process.wait()
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
    except OSError:
        raise _error("gigacode_cli_io_failed", "Не удалось завершить обмен с GigaCode CLI. "
                     "Исходный текст сохранён.", sent=sent and process is not None) from None


def _request(payload, model_id):
    if (not isinstance(payload, dict) or not isinstance(payload.get("messages"), list)
            or not 1 <= len(payload["messages"]) <= 8
            or not isinstance(model_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", model_id)):
        raise _error("gigacode_cli_input_invalid", "Проверьте модель и текст для GigaCode CLI.")
    for row in payload["messages"]:
        if (not isinstance(row, dict) or row.get("role") not in ("system", "user")
                or not isinstance(row.get("content"), str)):
            raise _error("gigacode_cli_input_invalid", "Проверьте текст для GigaCode CLI.")
    try:
        content = json.dumps({"messages": payload["messages"]}, ensure_ascii=False).encode("utf-8")
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise _error("gigacode_cli_input_invalid", "Проверьте текст для GigaCode CLI.") from None
    if len(content) > MAX_INPUT:
        raise _error("gigacode_cli_input_invalid", "Сократите материал для карточки GigaCode CLI.")
    return content


class GigaCodeCLI:
    def __init__(self, cli_path=""):
        self.cli_path = cli_path

    def check(self):
        """Check capabilities using help only; this does not verify authentication."""
        with _claimed():
            output = _run([_executable(self.cli_path), "--help"], timeout=10)
        text = output.decode("utf-8", errors="replace")
        if (not all(re.search(r"(?<![\w-])" + flag + r"(?![\w-])", text)
                    for flag in ("--safe-mode", "--prompt", "--output-format", "--model"))
                or not re.search(r"\bjson\b", text)):
            raise _error("gigacode_cli_unsupported", "GigaCode CLI не подтвердил headless JSON "
                         "и safe-mode. Проверьте доступные параметры CLI.")
        return {"status": "ok", "compatible": True, "message": "GigaCode CLI доступен. "
                "Авторизация и генерация не проверены."}

    def generate(self, payload, model_id="default", timeout=180, *, before_request=None):
        content = _request(payload, model_id)
        duration = _duration(timeout)
        executable = _executable(self.cli_path)
        argv = [executable, "--safe-mode", "-p", PROMPT, "--output-format", "json"]
        if model_id != "default":
            argv += ["--model", model_id]
        with _claimed():
            try:
                output = _run(argv, content, timeout=duration, sent=True,
                              before_request=before_request)
            except AppError as error:
                if not hasattr(error, "request_sent"):
                    error.request_sent = False
                raise
            try:
                return normalize_gigacode_output(output)
            except AppError as error:
                error.request_sent = True
                raise
