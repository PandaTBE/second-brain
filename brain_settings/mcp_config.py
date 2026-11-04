"""Nonsecret, project-scoped MCP configuration for both supported clients."""

import json
import os
import tempfile
import tomllib
from pathlib import Path

from .runtime import PROJECT_ROOT, data_directory, mcp_command


def connection_info(data_dir: Path | None = None) -> dict:
    command, args = mcp_command(data_directory(data_dir))
    claude = {"mcpServers": {"second-brain": {"command": command, "args": args}}}
    toml = (
        "[mcp_servers.second-brain]\ncommand = "
        + json.dumps(command)
        + "\nargs = "
        + json.dumps(args)
        + "\n"
    )
    return {
        "command": command,
        "args": args,
        "claude_json": json.dumps(claude, ensure_ascii=False, indent=2),
        "codex_toml": toml,
        "instructions": (
            "Добавьте second-brain в настройки MCP Claude Code или Codex. "
            "Скопируйте конфигурацию ниже в настройки выбранного клиента или его проектный файл. "
            "После изменения конфигурации перезапустите клиент или переподключите MCP. "
            "В Claude Code при первом запуске этого проекта подтвердите подключение MCP "
            "(статус Pending approval до подтверждения в клиенте). "
            "Сервер читает общую локальную базу и работает без открытого окна приложения. "
            "Он предоставляет только поиск и чтение: генерация карточек управляется приложением."
        ),
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".mcp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_project_configs(project_dir: Path = PROJECT_ROOT, data_dir: Path | None = None) -> None:
    """Add an absent entry; preserve existing user-owned entries and preferences."""
    info = connection_info(data_dir)
    claude_path = project_dir / ".mcp.json"
    claude = json.loads(claude_path.read_text()) if claude_path.exists() else {}
    servers = claude.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("Existing MCP configuration has an unsupported shape")
    if "second-brain" not in servers:
        updated = {
            **claude,
            "mcpServers": {
                **servers,
                "second-brain": {"command": info["command"], "args": info["args"]},
            },
        }
        _atomic_write(claude_path, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    codex_path = project_dir / ".codex" / "config.toml"
    original = codex_path.read_text() if codex_path.exists() else ""
    parsed = tomllib.loads(original)
    if "second-brain" not in parsed.get("mcp_servers", {}):
        _atomic_write(codex_path, original.rstrip() + "\n\n" + info["codex_toml"])
