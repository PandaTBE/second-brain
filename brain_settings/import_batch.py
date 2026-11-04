"""Budget-aware file events with direct eligible-path access and streaming sessions."""

import os
import stat
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from . import import_files as limits
from .dialogue_parsers import Turn
from .errors import AppError
from .import_progress import file_identity
from .local_scan import DIRECTORY_FLAGS, SKIP_DIRECTORIES
from .session_stream import read_session

MAX_RAW_SESSION_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ImportRead:
    relative: Path
    raw: bytes | None
    modified: float
    signature: tuple[int, int, int] | None
    state: str = "read"
    turns: tuple[Turn, ...] | None = None
    complete: bool = True
    skipped: int = 0
    warnings: tuple[str, ...] = ()
    bytes_read: int = 0
    document_count: int = 0
    cached_state: str = ""
    retryable: bool = False

    def __iter__(self):
        yield self.relative
        yield self.raw
        yield self.modified


def _signature(metadata):
    return metadata.st_ino, metadata.st_mtime_ns, metadata.st_size


def _allowed(relative: Path, kind: str) -> bool:
    extensions = {".jsonl"} if kind in ("codex", "claude") else {".md", ".txt", ".jsonl"}
    return (
        relative.suffix.lower() in extensions
        and not any(part.startswith(".") or part in SKIP_DIRECTORIES for part in relative.parts)
        and relative.name.casefold() not in limits.SKIP_NAMES
    )


def read_file_events(root: Path, kind: str, eligible_paths=None, known_files=None):
    known_files = known_files or {}
    visited = total = 0

    def read_file(directory, relative, metadata):
        nonlocal total
        signature = _signature(metadata)
        cached = known_files.get(file_identity(relative))
        if (
            cached is not None
            and cached["signature"] == signature
            and cached.get("state") != "error"
        ):
            yield ImportRead(
                relative,
                None,
                metadata.st_mtime,
                signature,
                state="unchanged",
                warnings=cached.get("warnings", ()),
                document_count=cached.get("document_count", 0),
                cached_state=cached.get("state", "done"),
            )
            return
        maximum = MAX_RAW_SESSION_BYTES if kind in ("codex", "claude") else limits.MAX_FILE_BYTES
        if metadata.st_size > maximum:
            yield ImportRead(
                relative,
                None,
                metadata.st_mtime,
                signature,
                state="skipped",
                warnings=(
                    "Файл превышает безопасный предел размера; его содержимое не импортировано.",
                ),
            )
            return
        if kind == "folder" and total + metadata.st_size > limits.MAX_TOTAL_BYTES:
            yield ImportRead(relative, None, metadata.st_mtime, signature, state="deferred")
            return
        spent = 0
        try:
            descriptor = os.open(
                relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
            with os.fdopen(descriptor, "rb") as stream:
                try:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        return
                    if _signature(before) != signature:
                        yield ImportRead(
                            relative, None, before.st_mtime, _signature(before), state="deferred"
                        )
                        return
                    if kind in ("codex", "claude") and before.st_size > limits.MAX_FILE_BYTES:
                        session = read_session(stream, kind, before.st_size)
                        event = ImportRead(
                            relative,
                            None,
                            before.st_mtime,
                            signature,
                            turns=session.turns,
                            complete=session.complete,
                            skipped=session.skipped,
                            warnings=session.warnings,
                            bytes_read=session.bytes_read,
                            retryable=session.retryable,
                        )
                    else:
                        raw = stream.read(before.st_size)
                        event = ImportRead(
                            relative, raw, before.st_mtime, signature, bytes_read=len(raw)
                        )
                    after = os.fstat(stream.fileno())
                finally:
                    spent = stream.tell()
            total += spent
            if _signature(after) != signature:
                yield ImportRead(
                    relative,
                    None,
                    after.st_mtime,
                    _signature(after),
                    state="deferred",
                    bytes_read=spent,
                    warnings=("Файл изменился во время чтения и отложен.",),
                )
                return
            yield event
        except (OSError, AppError) as error:
            total += spent
            message = error.message if isinstance(error, AppError) else "Нет доступа к файлу."
            yield ImportRead(
                relative,
                None,
                metadata.st_mtime,
                signature,
                state="error",
                warnings=(message,),
                bytes_read=spent,
                retryable=isinstance(error, OSError),
            )

    def walk(directory, relative, depth):
        nonlocal visited
        if depth > 32:
            raise AppError("import_limit", "Превышена глубина папки импорта.")
        with os.scandir(directory) as entries:
            remaining = max(0, limits.MAX_ENTRIES - visited)
            bounded = tuple(islice(entries, remaining + 1))
            if len(bounded) > remaining:
                raise AppError("import_limit", "Импорт ограничен 10 000 объектами за запуск.")
            ordered = sorted(bounded, key=lambda entry: entry.name)
        for entry in ordered:
            visited += 1
            if entry.name.startswith(".") or entry.name.casefold() in limits.SKIP_NAMES:
                continue
            metadata = entry.stat(follow_symlinks=False)
            child_relative = relative / entry.name
            if stat.S_ISDIR(metadata.st_mode) and entry.name not in SKIP_DIRECTORIES:
                child = os.open(entry.name, DIRECTORY_FLAGS, dir_fd=directory)
                try:
                    yield from walk(child, child_relative, depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode) and _allowed(child_relative, kind):
                yield from read_file(directory, child_relative, metadata)

    def eligible(directory):
        paths = tuple(islice(iter(eligible_paths), limits.MAX_ENTRIES + 1))
        if len(paths) > limits.MAX_ENTRIES:
            raise AppError("import_limit", "Слишком много выбранных файлов импорта.")
        for path in sorted(paths):
            absolute = Path(os.path.abspath(path))
            if not absolute.is_relative_to(root):
                continue
            relative = absolute.relative_to(root)
            if len(relative.parts) > 33 or not _allowed(relative, kind):
                continue
            parent = os.dup(directory)
            try:
                for component in relative.parts[:-1]:
                    child = os.open(component, DIRECTORY_FLAGS, dir_fd=parent)
                    os.close(parent)
                    parent = child
                metadata = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISREG(metadata.st_mode):
                    yield from read_file(parent, relative, metadata)
            except OSError:
                yield ImportRead(
                    relative,
                    None,
                    0,
                    None,
                    state="error",
                    warnings=("Выбранный файл недоступен или является ссылкой.",),
                    retryable=True,
                )
            finally:
                os.close(parent)

    descriptor = limits.open_source_directory(str(root))
    try:
        if eligible_paths is not None:
            yield from eligible(descriptor)
        else:
            yield from walk(descriptor, Path(), 0)
    finally:
        os.close(descriptor)
