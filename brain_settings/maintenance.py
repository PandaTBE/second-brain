"""Reentrant local operation guards and fail-closed restore isolation."""

import fcntl
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import AppError

RECOVERY_DIRECTORY = ".restore-transaction"
LOCK_FILENAME = "memory-maintenance.lock"
_local = threading.local()


def _pending(directory):
    path = directory / RECOVERY_DIRECTORY
    return path.exists() or path.is_symlink()


def _states():
    if not hasattr(_local, "states"):
        _local.states = {}
    return _local.states


@contextmanager
def _guard(directory, *, write=False, recovery=False):
    root = Path(directory).resolve()
    key = str(root)
    state = _states().get(key)
    if state is not None:
        if write and not state["write"]:
            raise AppError(
                "maintenance_lock_upgrade", "Завершите текущее чтение перед обслуживанием.", 409
            )
        yield
        return
    descriptor = None
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(root / LOCK_FILENAME, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + (3 if write else 0)
        while True:
            try:
                fcntl.flock(descriptor, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise AppError(
                        "memory_maintenance", "Память временно занята обслуживанием.", 503
                    ) from None
                time.sleep(0.02)
        if not recovery and _pending(root):
            raise AppError(
                "restore_recovery_required",
                "Необходимо завершить восстановление памяти перед чтением.",
                503,
            )
        _states()[key] = {"write": write, "descriptor": descriptor}
        try:
            yield
        finally:
            _states().pop(key, None)
    except OSError as error:
        raise AppError(
            "maintenance_unavailable", "Не удалось получить доступ к обслуживанию памяти.", 503
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def operation(directory):
    return _guard(directory)


def guarded_store(method):
    """Apply the same boundary to standalone settings/source/scheduler readers and writers."""
    from functools import wraps

    @wraps(method)
    def guarded(self, *args, **kwargs):
        directory = self.directory if hasattr(self, "directory") else self.path.parent
        with operation(directory):
            return method(self, *args, **kwargs)

    return guarded


def guarded_service(method):
    """Fence the complete read/credential/modify/write workflow against restore."""
    from functools import wraps

    @wraps(method)
    def guarded(self, *args, **kwargs):
        with operation(self.store.directory):
            return method(self, *args, **kwargs)

    return guarded


def guarded_memory_tool(method):
    """Keep stdio response envelopes controlled even when the full read fence is unavailable."""
    from functools import wraps

    @wraps(method)
    def guarded(self, *args, **kwargs):
        directory = (
            self.sources.directory
            if self.sources is not None
            else getattr(self.store, "directory", None)
        )
        try:
            if isinstance(directory, (str, Path)):
                with operation(directory):
                    return method(self, *args, **kwargs)
            return method(self, *args, **kwargs)
        except AppError as error:
            return {"error": {"code": error.code, "message": error.message}}

    return guarded


def exclusive(directory, *, recovery=False):
    return _guard(directory, write=True, recovery=recovery)


def recover_pending(directory):
    """Called before creating the database or running migrations."""
    root = Path(directory).resolve()
    if _pending(root):
        from .backup_restore import recover

        recover(root)


def legacy_sqlite_handles(database):
    """Conservative macOS preflight; SQLite's retained EX lock is the final isolation."""
    if sys.platform != "darwin":
        raise AppError(
            "backup_isolation_unavailable", "Проверка открытых баз доступна в macOS.", 503
        )
    path = Path(database)
    candidates = [
        item for item in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")) if item.exists()
    ]
    try:
        response = subprocess.run(
            ["/usr/sbin/lsof", "-t", "--", *(str(item) for item in candidates)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if (
            response.returncode not in (0, 1)
            or response.stderr.strip()
            or len(response.stdout) > 65536
        ):
            raise ValueError("Cannot establish open-handle inventory")
        rows = response.stdout.splitlines()
        if len(rows) > 1024 or any(not row.isdigit() for row in rows):
            raise ValueError("Invalid open-handle inventory")
        return tuple(sorted({int(row) for row in rows}))
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise AppError(
            "backup_isolation_unavailable",
            "Не удалось проверить открытые базы. Закройте старые клиенты памяти.",
            503,
        ) from error
