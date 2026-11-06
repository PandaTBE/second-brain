"""Bounded standard-age worker checks; all payloads and paths are synthetic."""

import json
import subprocess
from pathlib import Path

import pytest

from brain_settings.errors import AppError


def test_age_crypto_real_roundtrip_and_wrong_password_cleanup(tmp_path):
    from brain_settings.backup_crypto import AgeCrypto

    source, encrypted = tmp_path / "source.zip", tmp_path / "portable.age"
    restored, failed = tmp_path / "restored.zip", tmp_path / "failed.zip"
    source.write_bytes(b"Synthetic backup payload.\x00\xff" * 30)
    crypto = AgeCrypto()
    crypto.encrypt(source, encrypted, "synthetic roundtrip passphrase")
    assert encrypted.read_bytes().startswith(b"age-encryption.org/v1\n-> scrypt ")
    crypto.decrypt(encrypted, restored, "synthetic roundtrip passphrase")
    assert restored.read_bytes() == source.read_bytes()
    with pytest.raises(AppError):
        crypto.decrypt(encrypted, failed, "synthetic incorrect passphrase")
    assert not failed.exists()


def test_age_crypto_never_overwrites_existing_destination(tmp_path):
    from brain_settings.backup_crypto import AgeCrypto

    source, destination = tmp_path / "source.zip", tmp_path / "existing.age"
    source.write_bytes(b"Synthetic plaintext")
    destination.write_bytes(b"Synthetic existing destination")
    with pytest.raises(AppError):
        AgeCrypto().encrypt(source, destination, "synthetic long passphrase")
    assert destination.read_bytes() == b"Synthetic existing destination"


def test_age_crypto_timeout_has_safe_error_and_removes_partial_output(tmp_path, monkeypatch):
    from brain_settings.backup_crypto import AgeCrypto

    source, destination = tmp_path / "source.zip", tmp_path / "partial.age"
    source.write_bytes(b"Synthetic plaintext")

    def timeout(command, **kwargs):
        Path(command[-1]).write_bytes(b"Synthetic partial output")
        raise subprocess.TimeoutExpired(command, 90, stderr=b"synthetic sensitive failure")

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(AppError) as caught:
        AgeCrypto().encrypt(source, destination, "synthetic long passphrase")
    assert "sensitive" not in str(caught.value)
    assert not destination.exists()


def test_invalid_age_work_factor_is_rejected_before_spawning_worker(tmp_path, monkeypatch):
    from brain_settings.backup_crypto import AgeCrypto

    source = tmp_path / "hostile.age"
    source.write_bytes(
        b"age-encryption.org/v1\n-> scrypt AAAAAAAAAAAAAAAAAAAAAA 30\n"
        b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        b"--- AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\nsynthetic"
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Started expensive KDF"))
    with pytest.raises(AppError):
        AgeCrypto().decrypt(source, tmp_path / "restored.zip", "synthetic passphrase")


def test_source_size_limit_is_checked_before_spawning_worker(tmp_path, monkeypatch):
    import brain_settings.backup_crypto as module

    source = tmp_path / "large.zip"
    source.write_bytes(b"Synthetic plaintext beyond a tiny test budget")
    monkeypatch.setattr(module, "MAX_ARCHIVE_BYTES", 16)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Started oversized worker"))
    with pytest.raises(AppError) as caught:
        module.AgeCrypto().encrypt(source, tmp_path / "output.age", "synthetic passphrase")
    assert caught.value.code == "backup_too_large"


def test_crypto_worker_does_not_follow_source_or_output_symlinks(tmp_path):
    from brain_settings.backup_crypto import AgeCrypto

    source = tmp_path / "source.zip"
    source.write_bytes(b"Synthetic plaintext")
    linked_source = tmp_path / "linked.zip"
    linked_source.symlink_to(source)
    with pytest.raises(AppError):
        AgeCrypto().encrypt(linked_source, tmp_path / "out.age", "synthetic passphrase")
    destination = tmp_path / "output.age"
    destination.symlink_to(source)
    with pytest.raises(AppError):
        AgeCrypto().encrypt(source, destination, "synthetic passphrase")
    assert source.read_bytes() == b"Synthetic plaintext"


def test_worker_stdin_is_json_not_an_argument_or_environment(tmp_path, monkeypatch):
    from brain_settings.backup_crypto import AgeCrypto

    source = tmp_path / "source.zip"
    source.write_bytes(b"Synthetic plaintext")
    sent = []

    def capture(command, **kwargs):
        sent.append((command, kwargs))
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"private server detail")

    monkeypatch.setattr(subprocess, "run", capture)
    with pytest.raises(AppError):
        AgeCrypto().encrypt(source, tmp_path / "out.age", "synthetic transport passphrase")
    command, options = sent[0]
    assert json.loads(options["input"])["passphrase"] == "synthetic transport passphrase"
    assert "synthetic transport passphrase" not in str(command)
    assert "synthetic transport passphrase" not in str(options.get("env", {}))


def test_raced_destination_is_preserved_when_worker_fails(tmp_path, monkeypatch):
    from brain_settings.backup_crypto import AgeCrypto

    source, destination = tmp_path / "source.zip", tmp_path / "raced.age"
    source.write_bytes(b"Synthetic plaintext")

    def raced(command, **kwargs):
        destination.write_bytes(b"Synthetic unrelated raced file")
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"synthetic failure")

    monkeypatch.setattr(subprocess, "run", raced)
    with pytest.raises(AppError):
        AgeCrypto().encrypt(source, destination, "synthetic long passphrase")
    assert destination.read_bytes() == b"Synthetic unrelated raced file"


def test_raced_destination_is_preserved_when_exclusive_publication_fails(tmp_path, monkeypatch):
    from pathlib import Path

    from brain_settings.backup_crypto import AgeCrypto

    source, destination = tmp_path / "source.zip", tmp_path / "raced.age"
    source.write_bytes(b"Synthetic plaintext")

    def raced(command, **kwargs):
        output = Path(command[-1])
        output.write_bytes(
            b"age-encryption.org/v1\n-> scrypt AAAAAAAAAAAAAAAAAAAAAA 18\n"
            b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
            b"--- AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\nsynthetic"
        )
        destination.write_bytes(b"Synthetic unrelated raced file")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", raced)
    with pytest.raises(AppError):
        AgeCrypto().encrypt(source, destination, "synthetic long passphrase")
    assert destination.read_bytes() == b"Synthetic unrelated raced file"


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
def test_surrogate_passphrase_is_rejected_before_worker_without_normalization(
    tmp_path, monkeypatch, operation
):
    from brain_settings.backup_crypto import AgeCrypto

    source = tmp_path / "synthetic.input"
    source.write_bytes(b"Synthetic input")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Started malformed request"))
    value = "\ud800synthetic"
    with pytest.raises(AppError) as caught:
        getattr(AgeCrypto(), operation)(source, tmp_path / "output", value)
    assert caught.value.code == "backup_passphrase_invalid"
