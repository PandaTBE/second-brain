"""Carry only explicit bounded project metadata; never inspect a working directory."""

import posixpath

from .memory_text import redact_text


def session_project(row: dict, kind: str, previous: str | None = None) -> str | None:
    payload = row.get("payload")
    metadata = (
        payload
        if kind == "codex"
        and row.get("type") in ("session_meta", "turn_context")
        and isinstance(payload, dict)
        else row
    )
    value = metadata.get("cwd")
    cwd = isinstance(value, str)
    if value is None:
        value = metadata.get("project")
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        return previous
    if any(ord(character) < 32 for character in value):
        return previous
    try:
        value.encode("utf-8")
    except UnicodeError:
        return previous
    if cwd:
        if not value.startswith("/"):
            return previous
        value = posixpath.normpath(value)
    return redact_text(value.strip())
