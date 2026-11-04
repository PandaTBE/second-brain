"""Bounded metadata-only checks anchored to directory file descriptors."""

import os
import stat
import time
from pathlib import Path

from .errors import AppError

MAX_ENTRIES = 10_000
MAX_DEPTH = 32
SKIP_DIRECTORIES = frozenset({"node_modules", "venv", "__pycache__", "dist", "build"})
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def validate_local_path(value: str | None) -> str:
    if not value or not Path(value).is_absolute():
        raise AppError("invalid_source_path", "Выберите существующую папку по полному пути.")
    path = Path(value)
    try:
        if path.is_symlink() or not path.is_dir():
            raise ValueError
        return str(path.resolve(strict=True))
    except (OSError, ValueError, RuntimeError) as exc:
        raise AppError("invalid_source_path", "Папка недоступна или является ссылкой. "
                       "Выберите существующую папку.") from exc


def _open_directory(path: str) -> int:
    directory = os.open("/", DIRECTORY_FLAGS)
    try:
        for component in Path(path).parts[1:]:
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=directory)
            os.close(directory)
            directory = child
        return directory
    except OSError:
        os.close(directory)
        raise


def scan_local(path: str, kind: str) -> int:
    """Count eligible readable files without opening content or following symlinks."""
    extensions = frozenset({".jsonl"}) if kind in ("codex", "claude") else frozenset(
        {".md", ".txt", ".jsonl"})
    visited = 0
    deadline = time.monotonic() + 8

    def walk(directory: int, depth: int) -> int:
        nonlocal visited
        count = 0
        if depth > MAX_DEPTH:
            raise AppError("scan_limit", "Достигнуто ограничение глубины проверки папки.")
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > MAX_ENTRIES or time.monotonic() > deadline:
                    raise AppError("scan_limit", "Проверка ограничена 10 000 объектами "
                                   "и временем выполнения. Выберите папку меньшего размера.")
                if entry.name.startswith("."):
                    continue
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode) and entry.name not in SKIP_DIRECTORIES:
                    child = os.open(entry.name, DIRECTORY_FLAGS, dir_fd=directory)
                    try:
                        count += walk(child, depth + 1)
                    finally:
                        os.close(child)
                elif (stat.S_ISREG(metadata.st_mode)
                      and Path(entry.name).suffix.lower() in extensions):
                    if not os.access(entry.name, os.R_OK, dir_fd=directory,
                                     follow_symlinks=False):
                        raise PermissionError
                    count += 1
        return count

    try:
        directory = _open_directory(path)
        try:
            return walk(directory, 0)
        finally:
            os.close(directory)
    except OSError as exc:
        raise AppError("source_unavailable", "Не удалось проверить папку. "
                       "Проверьте, что она существует и доступна приложению.", 403) from exc
