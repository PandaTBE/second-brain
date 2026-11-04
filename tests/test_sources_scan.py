from pathlib import Path

import pytest
from test_settings_api import assert_success
from test_sources_api import add_folder


def check_source(harness, source_id):
    return harness.post(f"/api/sources/{source_id}/test", {})


@pytest.mark.parametrize("kind", ["codex", "claude"])
def test_session_check_counts_only_jsonl_metadata(harness, tmp_path, kind):
    directory = tmp_path / "synthetic-sessions"
    directory.mkdir()
    (directory / "one.jsonl").write_text("This is not valid JSON and must not be parsed")
    (directory / "ignore.md").write_text("Not a session")
    nested = directory / "nested"
    nested.mkdir()
    (nested / "two.jsonl").write_text("Another unread session")
    source = assert_success(add_folder(harness, directory, kind=kind))["source"]
    checked = assert_success(check_source(harness, source["id"]))["source"]
    assert checked["status"] == "ok"
    assert checked["item_count"] == 2
    assert checked["last_checked_at"]
    assert "файл" in checked["message"].lower()
    assert "импорт" not in checked["message"].lower()


def test_folder_check_counts_supported_text_extensions_without_opening_bodies(
    harness, tmp_path, monkeypatch,
):
    directory = tmp_path / "synthetic-notes"
    directory.mkdir()
    for name in ("note.md", "note.txt", "session.jsonl", "ignored.png"):
        (directory / name).write_text("Synthetic content")
    source = assert_success(add_folder(harness, directory, kind="folder"))["source"]
    original_read = Path.read_text

    def guard_read(path, *args, **kwargs):
        if path.is_relative_to(directory):
            raise AssertionError("Metadata check must not read source text")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guard_read)
    checked = assert_success(check_source(harness, source["id"]))["source"]
    assert checked["item_count"] == 3


def test_metadata_scan_ignores_symlinks_hidden_and_dependency_directories(harness, tmp_path):
    directory = tmp_path / "selected"
    directory.mkdir()
    (directory / "allowed.jsonl").write_text("Synthetic")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.jsonl").write_text("Synthetic outside selected directory")
    (directory / "linked-directory").symlink_to(outside, target_is_directory=True)
    (directory / "linked-file.jsonl").symlink_to(outside / "private.jsonl")
    for name in (".git", ".venv", ".hidden", "node_modules"):
        excluded = directory / name
        excluded.mkdir()
        (excluded / "excluded.jsonl").write_text("Synthetic excluded")
    (directory / ".hidden-session.jsonl").write_text("Synthetic hidden file")
    source = assert_success(add_folder(harness, directory))["source"]
    checked = assert_success(check_source(harness, source["id"]))["source"]
    assert checked["item_count"] == 1


def test_empty_folder_check_reports_real_zero(harness, tmp_path):
    directory = tmp_path / "empty"
    directory.mkdir()
    source = assert_success(add_folder(harness, directory))["source"]
    checked = assert_success(check_source(harness, source["id"]))["source"]
    assert checked["status"] == "ok"
    assert checked["item_count"] == 0


def test_disappearing_directory_persists_error_status(harness, tmp_path):
    directory = tmp_path / "gone"
    directory.mkdir()
    source = assert_success(add_folder(harness, directory))["source"]
    directory.rmdir()
    response = check_source(harness, source["id"])
    assert response.status_code >= 400
    assert response.json()["success"] is False
    sources = assert_success(harness.client.get("/api/sources"))["sources"]
    assert sources[0]["status"] == "error"
    assert sources[0]["last_checked_at"]
    assert sources[0]["item_count"] is None


def test_selected_root_replaced_with_symlink_is_not_followed(harness, tmp_path):
    directory = tmp_path / "selected-root"
    directory.mkdir()
    source = assert_success(add_folder(harness, directory))["source"]
    directory.rmdir()
    outside = tmp_path / "outside-root"
    outside.mkdir()
    (outside / "outside.jsonl").write_text("Synthetic unselected content")
    directory.symlink_to(outside, target_is_directory=True)
    response = check_source(harness, source["id"])
    assert response.status_code >= 400
    assert response.json()["success"] is False
    sources = assert_success(harness.client.get("/api/sources"))["sources"]
    assert sources[0]["status"] == "error"
    assert sources[0]["item_count"] is None


def test_failed_recheck_does_not_keep_previous_success_count(harness, tmp_path):
    directory = tmp_path / "rechecked-root"
    directory.mkdir()
    session = directory / "session.jsonl"
    session.write_text("Synthetic")
    source = assert_success(add_folder(harness, directory))["source"]
    first = assert_success(check_source(harness, source["id"]))["source"]
    assert first["item_count"] == 1
    session.unlink()
    directory.rmdir()
    response = check_source(harness, source["id"])
    assert response.status_code >= 400
    updated = assert_success(harness.client.get("/api/sources"))["sources"][0]
    assert updated["status"] == "error"
    assert updated["item_count"] is None


def test_metadata_scan_is_bounded_for_large_directory(harness, tmp_path):
    directory = tmp_path / "many-synthetic-files"
    directory.mkdir()
    for index in range(10_005):
        (directory / f"session-{index}.jsonl").touch()
    source = assert_success(add_folder(harness, directory))["source"]
    response = check_source(harness, source["id"])
    if response.status_code == 200:
        checked = response.json()["data"]["source"]
        assert checked["item_count"] <= 10_000
        assert "лимит" in checked["message"].lower() or "огранич" in checked["message"].lower()
    else:
        assert response.json()["success"] is False
        sources = assert_success(harness.client.get("/api/sources"))["sources"]
        assert sources[0]["status"] == "error"
