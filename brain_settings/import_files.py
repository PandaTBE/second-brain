"""Read only explicitly selected regular files beneath a directory descriptor."""

import os as os
from pathlib import Path

from .local_scan import _open_directory

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 10_000
SKIP_NAMES = frozenset(
    {
        "auth.jsonl",
        "credentials.jsonl",
        "secrets.txt",
        "passwords.txt",
        "agents.md",
        "claude.md",
        "system.md",
        "system-prompt.md",
    }
)
SKIP_NAMES = SKIP_NAMES | frozenset(
    stem + suffix
    for stem in ("auth", "credentials", "secrets", "passwords", "tools", "system", "system-prompt")
    for suffix in (".md", ".txt", ".jsonl")
)


def open_source_directory(path: str) -> int:
    return _open_directory(path)


def read_source_files(root: Path, kind: str, eligible_paths=None, *, known_files=None):
    """Yield bounded file events; legacy relative/raw/mtime tuple unpacking is supported."""
    from .import_batch import read_file_events

    yield from read_file_events(root, kind, eligible_paths, known_files)
