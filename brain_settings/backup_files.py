"""Bounded private files used by backup staging and recovery journals."""

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from .errors import AppError


def fsync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_bytes(path, payload):
    path = Path(path)
    descriptor, name = tempfile.mkstemp(prefix=".backup-write-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def regular_file(path, maximum):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path.absolute():
        raise AppError("backup_invalid_path", "Выберите обычный файл без символических ссылок.")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise AppError("backup_too_large", "Архив отсутствует или превышает допустимый размер.")
        return os.fdopen(os.dup(descriptor), "rb")
    finally:
        os.close(descriptor)


def bounded_read(path, maximum):
    with regular_file(path, maximum) as stream:
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise AppError("backup_too_large", "Файл превышает допустимый размер.")
    return payload


def checksum(path):
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
            size += len(chunk)
    return {"size_bytes": size, "sha256": digest.hexdigest()}


def copy_bounded(source, destination, maximum):
    total = 0
    with Path(destination).open("xb") as output:
        os.chmod(destination, 0o600)
        while chunk := source.read(min(65536, maximum - total + 1)):
            total += len(chunk)
            if total > maximum:
                raise AppError("backup_too_large", "Архив превышает допустимый размер.")
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    return total
