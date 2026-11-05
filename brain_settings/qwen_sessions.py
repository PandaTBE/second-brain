"""Read-only Qwen Code transcript locations and visible dialogue projection."""

from pathlib import Path


def is_qwen_session(root: Path, relative: Path) -> bool:
    """Accept chats/*.jsonl and chats/archive/*.jsonl, never profile/config logs."""
    if (
        relative.is_absolute()
        or relative.suffix.lower() != ".jsonl"
        or relative.stem.casefold()
        in {"auth", "credentials", "secrets", "passwords", "tools", "system", "system-prompt"}
    ):
        return False
    if any(part.startswith(".") for part in relative.parts):
        return False
    parents = relative.parts[:-1]
    if parents and parents[-1] == "archive":
        parents = parents[:-1]
    if root.name == "archive" and root.parent.name == "chats":
        return not relative.parts[:-1]
    return (
        (root.name == "chats" and not parents)
        or parents == ("chats",)
        or (root.name == "projects" and len(parents) == 2 and parents[1] == "chats")
        or (len(parents) == 3 and parents[0] == "projects" and parents[2] == "chats")
    )


def qwen_dialogue(row: dict) -> tuple[str, str]:
    """Keep authored user/model text; omit tool, thought, and runtime envelopes."""
    role = row.get("type")
    if role not in ("user", "assistant"):
        return "", ""
    if row.get("subtype") not in (None, "mid_turn_user_message"):
        return "", ""
    if row.get("subtype") == "mid_turn_user_message" and role != "user":
        return "", ""
    if row.get("provenance") not in (None, "real_user", "assistant_output"):
        return "", ""
    message = row.get("message")
    expected = "model" if role == "assistant" else "user"
    if not isinstance(message, dict) or message.get("role") != expected:
        return "", ""
    parts = message.get("parts")
    if not isinstance(parts, list):
        return "", ""
    text = "\n".join(
        part["text"]
        for part in parts
        if isinstance(part, dict)
        and isinstance(part.get("text"), str)
        and not part.get("thought")
        and not (set(part) - {"text", "thought", "thoughtSignature"})
    )
    return role, text
