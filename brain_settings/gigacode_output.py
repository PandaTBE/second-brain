"""Normalize the observed GigaCode JSON envelope, without executing the CLI.

Reported tool activity is rejected after parsing. This is a response integrity
check, not a sandbox or a guarantee that tools were disabled before execution.
"""

import json

from .errors import AppError

MAX_OUTPUT_BYTES = 1_048_576
MAX_EVENTS = 128
MAX_CONTENT_CHARACTERS = 32_000
SAFE_MODE_WARNING = (
    "⚠ SAFE MODE - all customizations disabled (hooks, extensions, skills, MCP servers, "
    "GIGACODE.md and compatible GIGACODE.md). Restart without --safe-mode to resume "
    "normal operation."
)
_TOOL_TYPES = frozenset({"tool_use", "tool_call", "tool_result", "function_call"})


def _invalid():
    raise AppError("gigacode_response_invalid", "GigaCode CLI вернул неподдерживаемый "
                   "ответ. Исходный текст сохранён.", 502) from None


def _unique_object(pairs):
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("Duplicate keys")
    return dict(pairs)


def _reject_constant(_value):
    raise ValueError("Non-finite JSON value")


def _load_events(stdout):
    if not isinstance(stdout, (str, bytes)) or len(stdout) > MAX_OUTPUT_BYTES:
        _invalid()
    try:
        raw = stdout.encode("utf-8") if isinstance(stdout, str) else stdout
        if len(raw) > MAX_OUTPUT_BYTES:
            _invalid()
        content = raw.decode("utf-8").strip()
        first, separator, rest = content.partition("\n")
        if separator and first.rstrip("\r") == SAFE_MODE_WARNING:
            content = rest
        events = json.loads(content, object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        _invalid()
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        _invalid()
    return events


def _check_node(value, depth=0):
    if depth > 64:
        _invalid()
    if isinstance(value, dict):
        kind = value.get("type")
        if ((isinstance(kind, str) and kind in _TOOL_TYPES)
                or value.get("role") in ("tool", "function")
                or value.get("tool_calls") or value.get("function_call")):
            _invalid()
        for child in value.values():
            _check_node(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_node(child, depth + 1)


def _nonnegative_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_activity(result):
    if "permission_denials" in result and result["permission_denials"] != []:
        _invalid()
    stats = result.get("stats", {})
    if not isinstance(stats, dict):
        _invalid()
    for kind in ("tools", "skills"):
        if kind not in stats:
            continue
        activity = stats[kind]
        if not isinstance(activity, dict) or activity.get("byName", {}) != {}:
            _invalid()
        for key, value in activity.items():
            if key == "totalDecisions" and isinstance(value, dict):
                if (not value.keys() <= {"accept", "reject", "modify", "auto_accept"}
                        or any(not _nonnegative_integer(count) or count != 0
                               for count in value.values())):
                    _invalid()
                continue
            if key.startswith("total") and (not _nonnegative_integer(value) or value != 0):
                _invalid()


def _usage(result):
    if "usage" not in result:
        return {}
    usage = result["usage"]
    if not isinstance(usage, dict):
        _invalid()
    names = {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens",
             "total_tokens": "total_tokens"}
    if any(not _nonnegative_integer(usage[key]) for key in names if key in usage):
        _invalid()
    return {"usage": {target: usage[key] for key, target in names.items() if key in usage}}


def normalize_gigacode_output(stdout: str | bytes) -> dict:
    """Extract one final successful result from the bounded observed JSON array."""
    events = _load_events(stdout)
    for event in events:
        if (not isinstance(event, dict)
                or event.get("type") not in ("system", "assistant", "result")):
            _invalid()
        _check_node(event)
    if sum(event["type"] == "result" for event in events) != 1:
        _invalid()
    result = events[-1]
    if (result["type"] != "result" or result.get("subtype") != "success"
            or result.get("is_error") is not False):
        _invalid()
    content = result.get("result")
    if (not isinstance(content, str) or not content.strip()
            or len(content) > MAX_CONTENT_CHARACTERS):
        _invalid()
    _check_activity(result)
    return {"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": content}}], **_usage(result)}
