"""Bounded JSONL dialogue extraction; large tool/binary lines never enter memory."""

import json
import time
from dataclasses import dataclass

from .dialogue_parsers import Turn, parse_session
from .errors import AppError
from .session_metadata import session_project

MAX_LINE_BYTES = 1024 * 1024
MAX_EXTRACTED_BYTES = 8 * 1024 * 1024
MAX_TURNS = 10_000
MAX_SECONDS = 20


@dataclass(frozen=True)
class StreamedSession:
    turns: tuple[Turn, ...]
    complete: bool
    skipped: int
    warnings: tuple[str, ...]
    bytes_read: int
    retryable: bool = False


def read_session(stream, kind: str, size: int) -> StreamedSession:
    responses, events = (), ()
    project = None
    response_bytes = event_bytes = consumed = skipped = line_number = 0
    complete, warnings = True, ()
    deadline = time.monotonic() + MAX_SECONDS
    while consumed < size:
        if time.monotonic() > deadline:
            warnings = (
                *warnings,
                "Достигнут лимит времени чтения журнала; текст извлечён частично.",
            )
            return StreamedSession(responses or events, False, skipped, warnings, consumed, True)
        raw = stream.readline(min(MAX_LINE_BYTES + 1, size - consumed))
        if not raw:
            break
        consumed += len(raw)
        line_number += 1
        if len(raw) > MAX_LINE_BYTES:
            while not raw.endswith(b"\n") and consumed < size:
                if time.monotonic() > deadline:
                    warning = "Достигнут лимит времени чтения длинной строки; импорт частичный."
                    return StreamedSession(
                        responses or events,
                        False,
                        skipped + 1,
                        (*warnings, warning),
                        consumed,
                        True,
                    )
                raw = stream.readline(min(MAX_LINE_BYTES + 1, size - consumed))
                if not raw:
                    break
                consumed += len(raw)
            complete, skipped = False, skipped + 1
            if len(warnings) < 20:
                warnings = (
                    *warnings,
                    f"Строка {line_number} больше лимита: пропущена "
                    "без сохранения содержимого; импорт файла частичный.",
                )
            continue
        if not raw.strip():
            continue
        try:
            text = raw.decode("utf-8-sig")
            row = json.loads(text)
        except (ValueError, UnicodeError, RecursionError) as error:
            if consumed == size and not raw.endswith(b"\n"):
                complete, skipped = False, skipped + 1
                warnings = (
                    *warnings,
                    "Незавершённая последняя строка отложена до изменения файла.",
                )
                break
            raise AppError(
                "invalid_session", "Не удалось разобрать завершённую строку журнала."
            ) from error
        if not isinstance(row, dict):
            skipped += 1
            continue
        project = session_project(row, kind, project)
        if kind == "codex":
            payload = row.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            candidate = (
                row.get("type") == "response_item"
                and payload.get("type") == "message"
                and payload.get("role") in ("user", "assistant")
            ) or (
                row.get("type") == "event_msg"
                and payload.get("type") in ("user_message", "agent_message")
            )
        else:
            candidate = row.get("type") in ("user", "assistant")
        if not candidate:
            skipped += 1
            continue
        turns, _, ignored = parse_session(text, kind)
        skipped += ignored
        for turn in turns:
            cost = len(turn.text.encode("utf-8"))
            is_event = kind == "codex" and row.get("type") == "event_msg"
            used, items = (event_bytes, events) if is_event else (response_bytes, responses)
            if used + cost > MAX_EXTRACTED_BYTES or len(items) >= MAX_TURNS:
                complete, skipped = False, skipped + 1
                warning = "Достигнут лимит извлечённого текста; журнал импортирован частично."
                if warning not in warnings:
                    warnings = (*warnings, warning)
                continue
            actual = Turn(turn.text, line_number, line_number, turn.timestamp, project)
            if is_event:
                events, event_bytes = (*events, actual), event_bytes + cost
            else:
                responses, response_bytes = (*responses, actual), response_bytes + cost
    return StreamedSession(responses or events, complete, skipped, warnings, consumed)
