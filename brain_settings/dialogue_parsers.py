"""Extract user/assistant dialogue, ignoring tools, snapshots and system context."""

import json
import re
from dataclasses import dataclass
from datetime import datetime

from .memory_text import redact_text
from .qwen_sessions import qwen_dialogue


@dataclass(frozen=True)
class Turn:
    text: str
    first_line: int
    last_line: int
    timestamp: str | None = None


def _content(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            item["text"]
            for item in value
            if isinstance(item, dict)
            and item.get("type") in ("text", "input_text", "output_text")
            and isinstance(item.get("text"), str)
        )
    return ""


def _clean_context(text: str) -> str:
    for tag in ("environment_context", "INSTRUCTIONS", "permissions instructions"):
        text = re.sub(r"<" + tag + r">[\s\S]*?</" + tag + r">", "", text)
    return re.sub(r"(?m)^# AGENTS\.md instructions\s*", "", text).strip()


def parse_session(raw: str, kind: str) -> tuple[list[Turn], bool, int]:
    rows = []
    partial = False
    skipped = 0
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError) as error:
            if not line.endswith(("\n", "\r")) and index == len(lines):
                partial = True
                skipped += 1
                continue
            raise ValueError("Не удалось разобрать завершённую строку JSONL.") from error
        if isinstance(row, dict):
            rows = [*rows, (index, row)]
    response_turns, event_turns = [], []
    for line_number, row in rows:
        role, text = "", ""
        payload = row.get("payload", {})
        payload = payload if isinstance(payload, dict) else {}
        if kind == "codex" and row.get("type") == "response_item":
            if payload.get("type") == "message" and payload.get("role") in ("user", "assistant"):
                role, text = payload["role"], _content(payload.get("content"))
        elif kind == "codex" and row.get("type") == "event_msg":
            event = payload.get("type")
            if event in ("user_message", "agent_message"):
                role = "user" if event == "user_message" else "assistant"
                text = _content(payload.get("message", payload.get("text")))
        elif kind == "claude" and row.get("type") in ("user", "assistant"):
            message = row.get("message", {})
            if isinstance(message, dict) and message.get("role", row["type"]) in (
                "user",
                "assistant",
            ):
                role, text = message.get("role", row["type"]), _content(message.get("content"))
        elif kind == "qwen":
            role, text = qwen_dialogue(row)
        text = _clean_context(text)
        if not role or not text:
            skipped += 1
            continue
        timestamp = row.get("timestamp")
        if isinstance(timestamp, str):
            try:
                datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                timestamp = None
        turn = Turn(
            ("Пользователь: " if role == "user" else "Ассистент: ") + text,
            line_number,
            line_number,
            timestamp if isinstance(timestamp, str) else None,
        )
        if kind == "codex" and row.get("type") == "event_msg":
            event_turns = [*event_turns, turn]
        else:
            response_turns = [*response_turns, turn]
    return response_turns or event_turns, not partial, skipped


def parse_note(raw: str) -> list[Turn]:
    return [
        Turn(line.rstrip("\r\n"), index, index)
        for index, line in enumerate(raw.splitlines(keepends=True), 1)
    ]


def redact_turns(turns: list[Turn]) -> list[Turn]:
    """Mask whole logical text before splitting; retain original turn coordinates."""
    combined = "\n\n".join(turn.text for turn in turns)
    masked = redact_text(combined, preserve_length=True)
    result, offset = [], 0
    for turn in turns:
        text = masked[offset : offset + len(turn.text)].rstrip()
        if text.strip():
            result = [*result, Turn(text, turn.first_line, turn.last_line, turn.timestamp)]
        offset += len(turn.text) + 2
    return result


def chunk_turns(turns: list[Turn], limit: int = 12_000) -> list[Turn]:
    chunks = []
    text, first, last, timestamp = "", 1, 1, None
    for turn in turns:
        remaining = turn.text
        while remaining:
            room = limit - len(text) - (2 if text else 0)
            if room <= 0:
                chunks = [*chunks, Turn(text, first, last, timestamp)]
                text, timestamp = "", None
                continue
            piece, remaining = remaining[:room], remaining[room:]
            if not text:
                first = turn.first_line
            text = text + ("\n\n" if text else "") + piece
            last = turn.last_line
            timestamp = turn.timestamp or timestamp
            if remaining:
                chunks = [*chunks, Turn(text, first, last, timestamp)]
                text, timestamp = "", None
    if text.strip():
        chunks = [*chunks, Turn(text, first, last, timestamp)]
    return chunks
