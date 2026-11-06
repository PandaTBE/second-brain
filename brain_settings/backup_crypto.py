"""Bounded standard-age passphrase transport isolated in a helper process."""

import argparse
import base64
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from .errors import AppError

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_ARCHIVE_BYTES + 1024 * 1024
MAX_HEADER_BYTES = 8192
MAX_STDIN_BYTES = 8192
MAX_WORK_FACTOR = 20
WORKER_TIMEOUT = 90
_HEADER = re.compile(
    rb"age-encryption.org/v1\n-> scrypt ([A-Za-z0-9+/]{22}) ([0-9]{1,2})\n"
    rb"([A-Za-z0-9+/]{43})\n--- ([A-Za-z0-9+/]{43})\n"
)


def _invalid_archive():
    return AppError("backup_ciphertext_invalid", "Неподдерживаемый или повреждённый age-архив.")


def validate_age_header(payload):
    """Validate only the supported standard-age header; pyrage authenticates the file."""
    if not isinstance(payload, bytes):
        raise _invalid_archive()
    boundary = payload.find(b"\n--- ", 0, MAX_HEADER_BYTES)
    end = payload.find(b"\n", boundary + 5, MAX_HEADER_BYTES + 1) if boundary >= 0 else -1
    if end < 0 or end + 1 > MAX_HEADER_BYTES:
        raise _invalid_archive()
    match = _HEADER.fullmatch(payload[: end + 1])
    if match is None:
        raise _invalid_archive()
    factor = int(match[2])
    if not 1 <= factor <= MAX_WORK_FACTOR:
        raise AppError("backup_work_factor", "Архив превышает допустимую сложность расшифрования.")
    try:
        for encoded, size in ((match[1], 16), (match[3], 32), (match[4], 32)):
            if len(base64.b64decode(encoded + b"=" * (-len(encoded) % 4), validate=True)) != size:
                raise ValueError
    except ValueError:
        raise _invalid_archive() from None
    return factor


def worker_command(operation, source, destination, *, frozen=None, executable=None):
    if operation not in ("encrypt", "decrypt"):
        raise AppError("backup_crypto_operation", "Неизвестная операция шифрования.")
    executable = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    prefix = (
        [str(executable), "backup-crypto"]
        if frozen
        else [str(executable), "-m", "brain_settings.backup_crypto"]
    )
    return [*prefix, operation, str(source), str(destination)]


def apply_worker_limits():
    """Bound CPU/output/core dumps, preserving macOS dyld virtual address mappings."""
    import resource

    for kind, limit in (
        (resource.RLIMIT_CPU, 60),
        (resource.RLIMIT_FSIZE, MAX_CIPHERTEXT_BYTES),
        (resource.RLIMIT_CORE, 0),
    ):
        _, hard = resource.getrlimit(kind)
        ceiling = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
        resource.setrlimit(kind, (ceiling, ceiling))


def _passphrase(value, *, encrypt=False):
    try:
        valid = (
            isinstance(value, str)
            and (8 if encrypt else 1) <= len(value) <= 1024
            and len(value.encode("utf-8")) <= 4096
        )
    except UnicodeError:
        valid = False
    if not valid:
        raise AppError(
            "backup_passphrase_invalid",
            "Укажите пароль длиной от 8 до 1024 символов." if encrypt else "Введите пароль архива.",
        )
    return value


def _open_source(source, maximum):
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AppError("backup_crypto_path", "Выберите обычный файл архива.")
        if metadata.st_size > maximum:
            raise AppError("backup_too_large", "Архив превышает безопасный предел 128 МиБ.")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _destination(destination):
    if not destination.is_absolute() or not destination.parent.is_dir():
        raise AppError("backup_crypto_path", "Выберите полный путь к новому файлу архива.")
    if destination.exists() or destination.is_symlink():
        raise AppError("backup_destination_exists", "Файл назначения уже существует.", 409)


def _read_source(source, operation, *, whole=False):
    maximum = MAX_ARCHIVE_BYTES if operation == "encrypt" else MAX_CIPHERTEXT_BYTES
    if not source.is_absolute():
        raise AppError("backup_crypto_path", "Выберите файл по полному пути.")
    with _open_source(source, maximum) as stream:
        payload = stream.read(maximum + 1 if whole else MAX_HEADER_BYTES + 1)
        if whole and len(payload) > maximum:
            raise AppError("backup_too_large", "Архив превышает безопасный предел 128 МиБ.")
    if operation == "decrypt":
        validate_age_header(payload)
    return payload


def _write_result(destination, payload):
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _transform(operation, source, destination, passphrase):
    from pyrage import passphrase as age_passphrase

    _destination(destination)
    payload = _read_source(source, operation, whole=True)
    try:
        transformed = (
            age_passphrase.encrypt(payload, passphrase)
            if operation == "encrypt"
            else age_passphrase.decrypt(payload, passphrase)
        )
    except Exception:
        raise AppError(
            "backup_password_invalid", "Неверный пароль или повреждённый архив."
        ) from None
    maximum = MAX_CIPHERTEXT_BYTES if operation == "encrypt" else MAX_ARCHIVE_BYTES
    if len(transformed) > maximum:
        raise AppError("backup_too_large", "Архив превышает безопасный предел 128 МиБ.")
    if operation == "encrypt":
        validate_age_header(transformed)
    _write_result(destination, transformed)


def worker_main(operation, source, destination):
    """The dedicated frozen-helper subcommand reads its only secret from stdin."""
    try:
        apply_worker_limits()
        payload = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        if len(payload) > MAX_STDIN_BYTES:
            raise ValueError
        request = json.loads(payload)
        if not isinstance(request, dict) or set(request) != {"passphrase"}:
            raise ValueError
        value = _passphrase(request["passphrase"], encrypt=operation == "encrypt")
        _transform(operation, Path(source), Path(destination), value)
    except Exception as error:
        code = error.code if isinstance(error, AppError) else "backup_crypto_failed"
        print(json.dumps({"error": code}), file=sys.stderr)
        raise SystemExit(1) from None


class AgeCrypto:
    def __init__(self, *, executable=None, frozen=None):
        self.executable, self.frozen = executable, frozen

    def encrypt(self, source, destination, passphrase):
        self._run("encrypt", source, destination, passphrase)

    def decrypt(self, source, destination, passphrase):
        self._run("decrypt", source, destination, passphrase)

    def _run(self, operation, source, destination, passphrase):
        source, destination = Path(source), Path(destination)
        value = _passphrase(passphrase, encrypt=operation == "encrypt")
        _destination(destination)
        try:
            _read_source(source, operation)
            # Cleanup is confined to this owned directory. The caller's path may be
            # created by another operation after preflight and must never be removed.
            with tempfile.TemporaryDirectory(prefix=".age-worker-", dir=destination.parent) as name:
                staged = Path(name) / "result"
                response = subprocess.run(
                    worker_command(
                        operation, source, staged, executable=self.executable, frozen=self.frozen
                    ),
                    input=json.dumps({"passphrase": value}, ensure_ascii=False).encode("utf-8"),
                    capture_output=True,
                    timeout=WORKER_TIMEOUT,
                    check=False,
                    cwd=Path(__file__).resolve().parent.parent,
                )
                if response.returncode != 0:
                    raise AppError(
                        "backup_crypto_failed", "Не удалось обработать зашифрованный архив."
                    )
                _read_source(staged, "decrypt" if operation == "encrypt" else "encrypt")
                # A hard link publishes atomically and fails if any file appeared
                # at the destination. TemporaryDirectory removes only our staging.
                os.link(staged, destination, follow_symlinks=False)
        except FileExistsError:
            raise AppError(
                "backup_destination_exists", "Файл назначения уже существует.", 409
            ) from None
        except (OSError, subprocess.SubprocessError) as error:
            raise AppError(
                "backup_crypto_failed", "Не удалось обработать зашифрованный архив."
            ) from error


def main(argv=None):
    parser = argparse.ArgumentParser(prog="second-brain-backup-crypto")
    parser.add_argument("operation", choices=("encrypt", "decrypt"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args(argv)
    worker_main(arguments.operation, arguments.source, arguments.destination)


if __name__ == "__main__":
    main()
