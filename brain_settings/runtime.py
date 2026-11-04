"""Side-effect-free locations for source checkouts and portable app bundles."""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_directory(explicit: Path | str | None = None) -> Path:
    selected = explicit if explicit is not None else os.environ.get("SECOND_BRAIN_DATA_DIR")
    if selected:
        return Path(selected).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path.home() / "Library" / "Application Support" / "Second Brain"
    return PROJECT_ROOT / ".data"


def asset_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return PROJECT_ROOT


def mcp_command(data_dir: Path | str | None = None) -> tuple[str, list[str]]:
    directory = str(data_directory(data_dir))
    if getattr(sys, "frozen", False):
        return sys.executable, ["mcp", "--data-dir", directory]
    return str(PROJECT_ROOT / "scripts" / "mcp-server.command"), ["--data-dir", directory]
