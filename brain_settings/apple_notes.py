"""Bounded, read-only Apple Notes automation; identifiers are data, never script code."""

import json
import os
import re
import selectors
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .errors import AppError
from .runtime import asset_directory

MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_NOTE_BYTES = 1024 * 1024
MAX_FOLDERS = 1000
MAX_NOTES_PER_FOLDER = 10_000
MAX_SECONDS = 25
BRIDGE_WARNINGS = frozenset(
    {
        "Защищённые заметки пропущены.",
        "Заметки вне выбранной папки пропущены.",
        "Заметки больше 1 МиБ пропущены; прежние материалы сохранены.",
        "Часть заметок недоступна; прежние материалы сохранены.",
    }
)


def normalize_notes_folder_ids(values) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 50:
        raise AppError("apple_notes_scope", "Выберите от 1 до 50 папок Apple «Заметок».")
    result = tuple(dict.fromkeys(_identifier(value) for value in values))
    return result


def _identifier(value) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or any(ord(char) < 32 for char in value)
    ):
        raise AppError(
            "apple_notes_response", "Apple «Заметки» вернули недопустимый идентификатор."
        )
    return value


def _text(value, maximum: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > maximum
        or (not empty and not value.strip())
        or "\x00" in value
    ):
        raise AppError("apple_notes_response", "Apple «Заметки» вернули недопустимые данные.")
    return value


def _timestamp(value) -> str:
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise AppError("apple_notes_response", "Не удалось прочитать дату заметки.") from exc
    return value


def _cursor(value, folder_count: int):
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"folder_index", "offset"}
        or type(value["folder_index"]) is not int
        or type(value["offset"]) is not int
        or not 0 <= value["folder_index"] < folder_count
        or not 0 <= value["offset"] <= MAX_NOTES_PER_FOLDER
    ):
        raise AppError("apple_notes_response", "Не удалось прочитать продолжение импорта заметок.")
    return {"folder_index": value["folder_index"], "offset": value["offset"]}


def _bounded_process(command: list[str], payload: bytes, *, timeout=MAX_SECONDS) -> bytes:
    process = None
    chunks, total, stderr = [], 0, bytearray()
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        offset = 0
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdin.fileno(), False)
            if payload:
                selector.register(process.stdin, selectors.EVENT_WRITE, "input")
            else:
                process.stdin.close()
            for stream, label in ((process.stdout, "output"), (process.stderr, "error")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, label)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AppError(
                        "apple_notes_timeout",
                        "Apple «Заметки» не ответили вовремя. "
                        "Откройте приложение и повторите проверку.",
                        504,
                    )
                for key, _ in selector.select(min(remaining, 0.1)):
                    if key.data == "input":
                        try:
                            offset += os.write(key.fd, payload[offset : offset + 65_536])
                        except BlockingIOError:
                            # Readiness can be stale; the outer loop still enforces the deadline.
                            continue
                        except BrokenPipeError:
                            offset = len(payload)
                        if offset >= len(payload):
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                        continue
                    try:
                        raw = os.read(key.fd, 65_536)
                    except BlockingIOError:
                        continue
                    if not raw:
                        selector.unregister(key.fileobj)
                    elif key.data == "output":
                        total += len(raw)
                        if total > MAX_OUTPUT_BYTES:
                            raise AppError(
                                "apple_notes_limit",
                                "Ответ Apple «Заметок» слишком большой. Выберите меньше папок.",
                            )
                        chunks.append(raw)
                    elif len(stderr) < 16_384:
                        stderr.extend(raw[: 16_384 - len(stderr)])
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        process.wait(timeout=remaining)
        if process.returncode:
            if re.search(rb"(?<!\d)-1743(?!\d)", stderr):
                raise AppError(
                    "apple_notes_permission",
                    "Разрешите Second Brain доступ к Apple "
                    "«Заметкам» в настройках macOS → Конфиденциальность → Автоматизация.",
                )
            raise AppError(
                "apple_notes_unavailable",
                "Не удалось прочитать Apple «Заметки». "
                "Откройте приложение и проверьте разрешение автоматизации.",
                503,
            )
        return b"".join(chunks)
    except subprocess.TimeoutExpired as exc:
        raise AppError(
            "apple_notes_timeout",
            "Apple «Заметки» не ответили вовремя. Откройте приложение и повторите проверку.",
            504,
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppError(
            "apple_notes_unavailable", "Не удалось запустить чтение Apple «Заметок».", 503
        ) from exc
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


def run_notes_script(request: dict, *, script_path: Path | None = None) -> bytes:
    if sys.platform != "darwin":
        raise AppError(
            "apple_notes_platform", "Apple «Заметки» доступны только в приложении macOS."
        )
    script = script_path or asset_directory() / "brain_settings/resources/apple_notes.js"
    if not script.is_file() or script.is_symlink():
        raise AppError(
            "apple_notes_unavailable",
            "Компонент чтения Apple «Заметок» не найден. Обновите или пересоберите приложение.",
            503,
        )
    payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
    if len(payload) > 32_768:
        raise AppError("apple_notes_scope", "Выберите меньше папок Apple «Заметок».")
    return _bounded_process(["/usr/bin/osascript", "-l", "JavaScript", str(script)], payload)


def _decode(value) -> dict:
    try:
        if isinstance(value, (str, bytes)):
            if len(value.encode("utf-8") if isinstance(value, str) else value) > MAX_OUTPUT_BYTES:
                raise ValueError
            value = json.loads(value)
        if not isinstance(value, dict) or value.get("complete", True) is not True:
            raise ValueError
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AppError(
            "apple_notes_response", "Не удалось прочитать полный ответ Apple «Заметок»."
        ) from exc
    if "error" in value:
        code, message = {
            "permission_denied": (
                "apple_notes_permission",
                "Разрешите Second Brain доступ к Apple "
                "«Заметкам» в настройках macOS → Конфиденциальность → Автоматизация.",
            ),
            "not_running": (
                "apple_notes_not_running",
                "Откройте Apple «Заметки» и повторите проверку.",
            ),
            "scope_changed": (
                "apple_notes_scope",
                "Выбранная папка заметок недоступна. Выберите папки заново.",
            ),
            "limit": (
                "apple_notes_limit",
                "Достигнут предел чтения Apple «Заметок». Выберите меньше папок.",
            ),
        }.get(
            value["error"] if isinstance(value["error"], str) else "",
            ("apple_notes_unavailable", "Не удалось прочитать Apple «Заметки»."),
        )
        raise AppError(code, message)
    return value


def validate_notes_page(value, folder_ids: tuple[str, ...], limit: int = 50) -> dict:
    value = _decode(value)
    notes = value.get("notes")
    if not isinstance(notes, list) or len(notes) > limit or "next_cursor" not in value:
        raise AppError("apple_notes_response", "Apple «Заметки» вернули неполный список заметок.")
    normalized, seen = [], set()
    for item in notes:
        if not isinstance(item, dict) or item.get("folder_id") not in folder_ids:
            raise AppError("apple_notes_scope", "Заметка не принадлежит выбранной папке.")
        identifier = _identifier(item.get("id"))
        if identifier in seen or item.get("password_protected", False) is not False:
            raise AppError("apple_notes_response", "Получен недопустимый список заметок.")
        seen.add(identifier)
        normalized.append(
            {
                "id": identifier,
                "folder_id": item["folder_id"],
                "title": _text(item.get("title"), 2048, empty=True),
                "text": _text(item.get("text"), MAX_NOTE_BYTES, empty=True),
                "created_at": _timestamp(item.get("created_at")),
                "updated_at": _timestamp(item.get("updated_at")),
            }
        )
    skipped = value.get("skipped", 0)
    warnings = value.get("warnings", [])
    if (
        type(skipped) is not int
        or not 0 <= skipped <= limit
        or len(notes) + skipped > limit
        or not isinstance(warnings, list)
        or len(warnings) > 100
    ):
        raise AppError("apple_notes_response", "Получены недопустимые счётчики импорта заметок.")
    # Only bridge-defined warnings are displayed; note titles and OS error text never become logs.
    clean_warnings = []
    for warning in warnings:
        _text(warning, 512)
        safe = (
            warning
            if warning in BRIDGE_WARNINGS
            else ("Часть заметок недоступна; прежние материалы сохранены.")
        )
        if safe not in clean_warnings:
            clean_warnings.append(safe)
    return {
        "notes": normalized,
        "next_cursor": _cursor(value["next_cursor"], len(folder_ids)),
        "warnings": clean_warnings,
        "skipped": skipped,
    }


class AppleNotesClient:
    def __init__(self, executor=None):
        self.executor = executor if executor is not None else run_notes_script

    def list_folders(self) -> dict:
        value = _decode(self.executor({"operation": "folders"}))
        folders = value.get("folders")
        if not isinstance(folders, list) or len(folders) > MAX_FOLDERS:
            raise AppError(
                "apple_notes_response", "Не удалось прочитать полный список папок заметок."
            )
        normalized, seen = [], set()
        for item in folders:
            if not isinstance(item, dict):
                raise AppError(
                    "apple_notes_response", "Получены недопустимые данные папки заметок."
                )
            identifier, count = _identifier(item.get("id")), item.get("note_count")
            if identifier in seen or type(count) is not int or not 0 <= count <= 1_000_000:
                raise AppError(
                    "apple_notes_response", "Получены недопустимые данные папки заметок."
                )
            seen.add(identifier)
            normalized.append(
                {
                    "id": identifier,
                    "name": _text(item.get("name"), 2048),
                    "account": _text(item.get("account"), 2048),
                    "path": _text(item.get("path"), 4096),
                    "note_count": count,
                }
            )
        return {"folders": normalized}

    def read_page(self, folder_ids, cursor=None, limit=50) -> dict:
        selected = normalize_notes_folder_ids(folder_ids)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise AppError("apple_notes_scope", "За один проход можно прочитать до 50 заметок.")
        continuation = _cursor(cursor, len(selected))
        request = {
            "operation": "notes",
            "folder_ids": list(selected),
            "cursor": continuation,
            "limit": limit,
        }
        return validate_notes_page(self.executor(request), selected, limit)
