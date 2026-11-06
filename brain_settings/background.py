"""Opt-in scheduling and bounded, descriptor-anchored session change detection."""

import asyncio
import fcntl
import json
import logging
import os
import stat
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from .errors import AppError
from .local_scan import _open_directory
from .maintenance import guarded_store
from .qwen_sessions import is_qwen_session

logger = logging.getLogger(__name__)
EXCLUDED = frozenset({"node_modules", "vendor", "venv", "__pycache__"})


def acquire_writer_lease(directory: Path) -> int:
    descriptor = None
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            directory / "memory-worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BlockingIOError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise AppError(
            "memory_busy", "Другой экземпляр приложения уже обрабатывает память.", 409
        ) from exc
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise AppError(
            "memory_lock_failed", "Не удалось открыть службу обработки памяти.", 503
        ) from exc


def release_writer_lease(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: StrictBool = False
    interval_minutes: StrictInt = Field(default=15, ge=5, le=1440)
    process_cards: StrictBool = False
    batch_limit: StrictInt = Field(default=10, ge=1, le=50)
    card_concurrency: StrictInt = Field(default=10, ge=1)
    auto_import_enabled: StrictBool = True
    paused: StrictBool = False


class ScheduleStore:
    def __init__(self, directory: Path):
        self.path = directory / "memory_schedule.json"

    @guarded_store
    def load(self) -> ScheduleConfig:
        if not self.path.exists():
            return ScheduleConfig()
        try:
            if self.path.stat().st_size > 16_384:
                raise ValueError
            return ScheduleConfig.model_validate_json(self.path.read_text("utf-8"))
        except (OSError, ValueError, ValidationError) as exc:
            raise AppError(
                "schedule_unreadable", "Не удалось прочитать расписание памяти.", 503
            ) from exc

    @guarded_store
    def save(self, config: ScheduleConfig) -> None:
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.path.parent.chmod(0o700)
            descriptor, filename = tempfile.mkstemp(prefix=".schedule-", dir=self.path.parent)
            temporary = Path(filename)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(config.model_dump_json(indent=2) + "\n")
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
        except OSError as exc:
            raise AppError(
                "schedule_write_failed", "Не удалось сохранить расписание памяти.", 503
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove temporary schedule preferences")


@dataclass(frozen=True)
class WatchBatch:
    source_id: str
    paths: frozenset[str]
    signatures: tuple[tuple[str, tuple[int, int, int]], ...] = ()


def _metadata_entries(root_fd: int, max_entries: int, allowed_path=None):
    visited = 0
    deadline = time.monotonic() + 8

    def walk(directory_fd, relative, depth):
        nonlocal visited
        if depth > 32:
            raise AppError("watch_limit", "Автоимпорт ограничен глубиной 32 папки.")
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                visited += 1
                if visited > max_entries or time.monotonic() > deadline:
                    raise AppError(
                        "watch_limit",
                        "Автоимпорт ограничен 10 000 объектами "
                        "и временем проверки. Выберите папку меньшего размера.",
                    )
                if entry.name.startswith("."):
                    continue
                if allowed_path is not None and not allowed_path(relative / entry.name):
                    continue
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode) and entry.name not in EXCLUDED:
                    child = os.open(
                        entry.name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    try:
                        yield from walk(child, relative / entry.name, depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(metadata.st_mode) and entry.name.lower().endswith(".jsonl"):
                    yield relative / entry.name, metadata, directory_fd, entry.name

    yield from walk(root_fd, Path(), 0)


def _completed_turn(descriptor: int, size: int, kind: str) -> bool:
    """Recognize a completed turn, not proof that the whole session is closed."""
    if kind == "qwen":
        return False  # No transcript marker is assumed to mean the session is idle.
    try:
        os.lseek(descriptor, max(0, size - 65_536), os.SEEK_SET)
        tail = os.read(descriptor, 65_536)
        if not tail.endswith(b"\n"):
            return False
        lines = tail.split(b"\n")[:-1]
        if not lines:
            return False
        event = json.loads(lines[-1])
        if not isinstance(event, dict):
            return False
        payload = event.get("payload")
        if kind == "codex":
            return (
                event.get("type") == "event_msg"
                and isinstance(payload, dict)
                and (payload.get("type") in ("task_complete", "turn_complete"))
            )
        message = event.get("message")
        return (event.get("type") == "system" and event.get("subtype") == "turn_duration") or (
            event.get("type") == "assistant"
            and isinstance(message, dict)
            and message.get("stop_reason") == "end_turn"
        )
    except (OSError, ValueError, UnicodeError):
        return False


class SessionWatcher:
    def __init__(self, quiet_seconds: int = 90, max_entries: int = 10_000):
        self.quiet_seconds = quiet_seconds
        self.max_entries = max_entries
        self._imported = {}
        self._lock = threading.Lock()

    def collect(self, source: dict, now: float | None = None, *, allowed_path=None) -> WatchBatch:
        source_id = source["id"]
        empty = WatchBatch(source_id, frozenset())
        if source.get("kind") not in ("codex", "claude", "qwen") or not source.get("enabled", True):
            return empty
        if allowed_path is not None and not allowed_path(Path()):
            return empty
        raw_path = source.get("path")
        if not raw_path or not Path(raw_path).is_absolute():
            raise AppError("watch_source_unavailable", "Выберите папку локального источника.")
        root = Path(raw_path)
        now = time.time() if now is None else now
        candidates = ()
        descriptor = None
        try:
            descriptor = _open_directory(str(root))
            for relative, metadata, directory_fd, name in _metadata_entries(
                descriptor,
                self.max_entries,
                allowed_path,
            ):
                try:
                    if allowed_path is not None and not allowed_path(relative):
                        continue
                    if source["kind"] == "qwen" and not is_qwen_session(root, relative):
                        continue
                    path = str(root / relative)
                    signature = (metadata.st_ino, metadata.st_mtime_ns, metadata.st_size)
                    with self._lock:
                        already = self._imported.get((source_id, path)) == signature
                    if already:
                        continue
                    ready = now - metadata.st_mtime >= self.quiet_seconds
                    if not ready:
                        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                        try:
                            ready = _completed_turn(file_fd, metadata.st_size, source["kind"])
                        finally:
                            os.close(file_fd)
                    if ready:
                        candidates = (*candidates, (path, signature))
                except OSError:
                    continue
            return WatchBatch(source_id, frozenset(path for path, _ in candidates), candidates)
        except OSError as exc:
            raise AppError(
                "watch_source_unavailable", "Не удалось проверить изменения папки источника.", 503
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def mark_imported(self, batch: WatchBatch, paths: frozenset[str] | None = None) -> None:
        with self._lock:
            self._imported = {
                **self._imported,
                **{
                    (batch.source_id, path): signature
                    for path, signature in batch.signatures
                    if paths is None or path in paths
                },
            }


class BackgroundRunner:
    """No thread or task is started until the ASGI lifespan explicitly calls start."""

    def __init__(self, tick, poll_seconds: float = 30):
        self.tick = tick
        self.poll_seconds = poll_seconds
        self._task = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.poll_seconds)
            try:
                await asyncio.to_thread(self.tick)
            except AppError as error:
                logger.warning("Memory scheduler deferred (%s)", error.code)
            except Exception as error:
                logger.error("Memory scheduler failed (%s)", type(error).__name__)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
