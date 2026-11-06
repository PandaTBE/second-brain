"""In-place SQLite restore with an fsynced rollback journal and startup recovery."""

import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing, contextmanager

from .background import acquire_writer_lease, release_writer_lease
from .backup_archive import CONFIG_FILES, LIVE_CONFIG_NAMES, copy_database, validate_configs
from .backup_files import atomic_bytes, bounded_read, checksum, fsync_directory, json_bytes
from .errors import AppError
from .maintenance import RECOVERY_DIRECTORY, exclusive, legacy_sqlite_handles
from .source_policy import load_policy, save_policy


@contextmanager
def isolated_database(directory, *, finalized_error=None):
    import logging

    connection = sqlite3.connect(directory / "memory.sqlite3", timeout=0.2, isolation_level=None)
    body_failed = False
    try:
        connection.execute("PRAGMA busy_timeout=200")
        connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        if connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
            raise AppError(
                "backup_legacy_readers", "Закройте старые клиенты с открытой базой.", 409
            )
        if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
            raise AppError(
                "backup_isolation_unavailable", "Не удалось изолировать базу памяти.", 409
            )
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute("COMMIT")
        yield connection
    except sqlite3.Error as error:
        body_failed = True
        raise AppError(
            "backup_isolation_unavailable",
            "База занята. Закройте старые клиенты памяти и повторите.",
            409,
        ) from error
    except BaseException:
        body_failed = True
        raise
    finally:
        finalization_error = None
        try:
            if connection.in_transaction:
                connection.rollback()
            connection.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error as error:
            finalization_error = error
        finally:
            connection.close()
        if finalization_error is not None:
            if body_failed or (finalized_error is not None and finalized_error()):
                logging.getLogger(__name__).warning("Database WAL transition deferred")
            else:
                raise AppError(
                    "backup_finalization_failed",
                    "Не удалось завершить обслуживание базы памяти.",
                    503,
                ) from finalization_error


def _prepare(directory, target):
    pending = directory / RECOVERY_DIRECTORY
    with tempfile.TemporaryDirectory(prefix=".restore-prepare-", dir=directory) as temporary:
        from pathlib import Path

        staging = Path(temporary)
        with closing(sqlite3.connect(staging / "memory.sqlite3")) as rollback:
            copy_database(target, rollback)
            rollback.execute("PRAGMA journal_mode=DELETE")
        (staging / "memory.sqlite3").chmod(0o600)
        files = {"memory.sqlite3": checksum(staging / "memory.sqlite3")}
        for name in CONFIG_FILES:
            current = directory / LIVE_CONFIG_NAMES.get(name, name)
            if current.exists() or current.is_symlink():
                atomic_bytes(staging / name, bounded_read(current, 1_048_576))
                files[name] = checksum(staging / name)
            else:
                files[name] = None
        atomic_bytes(
            staging / "journal.json",
            json_bytes({"version": 1, "phase": "prepared", "files": files}),
        )
        fsync_directory(staging)
        os.replace(staging, pending)
        fsync_directory(directory)
    return pending


def _verify_journal(pending):
    if pending.is_symlink() or not pending.is_dir():
        raise ValueError("Unsafe recovery directory")
    manifest = json.loads(bounded_read(pending / "journal.json", 16384))
    if (
        set(manifest) != {"version", "phase", "files"}
        or manifest["version"] != 1
        or manifest["phase"] != "prepared"
        or set(manifest["files"]) != {"memory.sqlite3", *CONFIG_FILES}
    ):
        raise ValueError("Invalid recovery journal")
    expected = {"journal.json"}
    for name, metadata in manifest["files"].items():
        if metadata is not None:
            bounded_read(pending / name, 128 * 1024 * 1024)
            if checksum(pending / name) != metadata:
                raise ValueError("Recovery checksum mismatch")
            expected.add(name)
    if {path.name for path in pending.iterdir()} != expected:
        raise ValueError("Unexpected recovery files")
    if manifest["files"]["memory.sqlite3"] is None:
        raise ValueError("Missing recovery database")
    return manifest


def _rollback(directory, target, pending):
    manifest = _verify_journal(pending)
    with closing(
        sqlite3.connect((pending / "memory.sqlite3").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as rollback:
        copy_database(rollback, target)
    for name in CONFIG_FILES:
        if manifest["files"][name] is None:
            (directory / LIVE_CONFIG_NAMES.get(name, name)).unlink(missing_ok=True)
        else:
            atomic_bytes(
                directory / LIVE_CONFIG_NAMES.get(name, name),
                bounded_read(pending / name, 1_048_576),
            )
    fsync_directory(directory)


def _finish(directory, pending):
    import logging
    from uuid import uuid4

    _verify_journal(pending)
    retired = directory / (".restore-retired-" + str(uuid4()))
    pending.rename(retired)
    try:
        fsync_directory(directory)
    except OSError:
        retired.rename(pending)
        raise
    # The durable rename commits the complete restore/rollback. Cleanup cannot undo it.
    try:
        shutil.rmtree(retired)
        fsync_directory(directory)
        return True
    except OSError:
        logging.getLogger(__name__).warning("Restore snapshot cleanup deferred")
        return False


def _safe_state(target, sources, previous_generation, foreign):
    target.row_factory = sqlite3.Row
    target.execute("BEGIN IMMEDIATE")
    try:
        target.execute("UPDATE documents SET card_claim_token=NULL,card_claim_until=NULL")
        target.execute("UPDATE embedding_settings SET enabled=0 WHERE singleton=1")
        target.execute(
            "UPDATE memory_index_generation SET generation=max(generation,?)+1 WHERE id=1",
            (previous_generation,),
        )
        if foreign:
            for source in sources:
                policy = load_policy(target, source.id).model_copy(update={"import_enabled": False})
                save_policy(target, source.id, policy)
        target.commit()
    except Exception:
        target.rollback()
        raise


def _restored_configs(staged, foreign):
    settings, sources, schedule = validate_configs(staged)
    if foreign:
        settings = settings.model_copy(
            update={
                "model_id": "",
                "model_name": "",
                "allow_private_network": False,
                "requires_reauthentication": True,
            }
        )
        sources = tuple(
            source.model_copy(
                update={
                    "credential_saved": False,
                    "requires_relocation": source.kind
                    in ("folder", "codex", "claude", "qwen", "apple_notes"),
                    "requires_reauthentication": source.kind
                    in ("imap", "notion", "github", "outlook"),
                }
            )
            for source in sources
        )
    schedule = schedule.model_copy(update={"paused": True, "process_cards": False})
    payloads = {
        "settings.json": json_bytes(settings.model_dump(mode="json")),
        "sources.json": json_bytes({"sources": [s.model_dump(mode="json") for s in sources]}),
        "schedule.json": json_bytes(schedule.model_dump(mode="json")),
    }
    return sources, payloads


def restore(directory, staged, *, foreign, handles=legacy_sqlite_handles, fault=lambda _: None):
    committed, finalization_warning = False, False

    def finalized_error():
        nonlocal finalization_warning
        finalization_warning = committed
        return committed

    lease = acquire_writer_lease(directory)
    try:
        with exclusive(directory):
            if handles():
                raise AppError(
                    "backup_legacy_readers", "Закройте старые клиенты с открытой базой.", 409
                )
            with isolated_database(directory, finalized_error=finalized_error) as target:
                generation = target.execute(
                    "SELECT generation FROM memory_index_generation WHERE id=1"
                ).fetchone()[0]
                pending = _prepare(directory, target)
                try:
                    fault("after_prepared")
                    sources, payloads = _restored_configs(staged, foreign)
                    with closing(
                        sqlite3.connect(
                            (staged / "memory.sqlite3").as_uri() + "?mode=ro&immutable=1", uri=True
                        )
                    ) as incoming:
                        copy_database(incoming, target)
                    _safe_state(target, sources, generation, foreign)
                    fault("after_database")
                    for name, payload in payloads.items():
                        atomic_bytes(directory / LIVE_CONFIG_NAMES.get(name, name), payload)
                    fault("after_json")
                    cleanup_complete = _finish(directory, pending)
                    committed = True
                except Exception as error:
                    try:
                        _rollback(directory, target, pending)
                        _finish(directory, pending)
                    except Exception as recovery_error:
                        raise AppError(
                            "restore_recovery_required",
                            "Восстановление прервано. Освободите место и повторите запуск.",
                            503,
                        ) from recovery_error
                    raise AppError(
                        "backup_restore_failed",
                        "Восстановление отменено; прежняя память сохранена.",
                    ) from error
        return {"cleanup_complete": cleanup_complete and not finalization_warning}
    finally:
        release_writer_lease(lease)


def recover(directory):
    lease = acquire_writer_lease(directory)
    try:
        with exclusive(directory, recovery=True):
            pending = directory / RECOVERY_DIRECTORY
            _verify_journal(pending)
            if legacy_sqlite_handles(directory / "memory.sqlite3"):
                raise ValueError("Legacy SQLite handles block recovery")
            with isolated_database(directory) as target:
                _rollback(directory, target, pending)
                _finish(directory, pending)
    except Exception as error:
        raise AppError(
            "restore_recovery_required",
            "Не удалось завершить восстановление. "
            "Проверьте свободное место и закройте старые клиенты.",
            503,
        ) from error
    finally:
        release_writer_lease(lease)
