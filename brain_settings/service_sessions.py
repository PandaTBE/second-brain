"""Recognize Second Brain's own card-generation transcripts without running a CLI."""

import json
import posixpath
import re

from .qwen_sessions import qwen_dialogue

LEGACY_CARD_PROMPT = "Generate the requested search card from standard input. Do not use tools."
CARD_PROMPT = "[SECOND_BRAIN_INTERNAL_CARD_GENERATION_V1]\n" + LEGACY_CARD_PROMPT


def _service_workspace(value) -> bool:
    if (not isinstance(value, str) or not value.startswith("/") or len(value) > 4096
            or any(ord(character) < 32 for character in value)):
        return False
    parent, name = posixpath.split(posixpath.normpath(value))
    temporary_parent = parent in {"/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp"}
    mac_temporary_parent = re.fullmatch(
        r"/(?:private/)?var/folders/[A-Za-z0-9]{2}/[A-Za-z0-9_-]+/T", parent,
    ) is not None
    return ((temporary_parent or mac_temporary_parent)
            and re.fullmatch(r"second-brain-cli-[A-Za-z0-9_-]+", name) is not None)


def _request_envelope(text: str) -> bool:
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError):
        return False
    if not isinstance(payload, dict) or set(payload) != {"messages"}:
        return False
    messages = payload["messages"]
    return (isinstance(messages, list) and len(messages) == 2
            and all(isinstance(row, dict) and row.get("role") == role
                    and isinstance(row.get("content"), str)
                    for row, role in zip(messages, ("system", "user"), strict=True)))


def _service_prompt(text: str) -> bool:
    text = text.strip()
    # The reserved new prompt identifies future sessions even if a stored chunk
    # ends before the JSON input. A quoted marker in ordinary prose is not enough.
    if text == CARD_PROMPT or text.startswith(CARD_PROMPT + "\n"):
        return True
    if text == LEGACY_CARD_PROMPT:
        return True
    if text.startswith(LEGACY_CARD_PROMPT + "\n"):
        return _request_envelope(text[len(LEGACY_CARD_PROMPT):].strip())
    if text.endswith("\n" + CARD_PROMPT):
        return _request_envelope(text[:-len(CARD_PROMPT)].strip())
    if text.endswith("\n" + LEGACY_CARD_PROMPT):
        return _request_envelope(text[:-len(LEGACY_CARD_PROMPT)].strip())
    return False


def is_service_session_row(row, kind: str) -> bool:
    """Recognize only Qwen/GigaCode runtime metadata or authored user prompts."""
    if kind != "qwen" or not isinstance(row, dict):
        return False
    if _service_workspace(row.get("cwd")):
        return True
    role, text = qwen_dialogue(row)
    return role == "user" and _service_prompt(text)


def is_service_record(record: dict) -> bool:
    """Block recognizable old queued chunks before a source is fully reimported."""
    if record.get("source_kind") != "qwen":
        return False
    if _service_workspace(record.get("project")):
        return True
    text = record.get("text")
    if not isinstance(text, str) or not text.startswith("Пользователь: "):
        return False
    user_text = text[len("Пользователь: "):].split("\n\nАссистент: ", 1)[0]
    return _service_prompt(user_text)
