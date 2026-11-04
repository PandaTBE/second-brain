"""Public-release checks inspect index blobs, not uncommitted local replacements."""

import subprocess

import pytest

from scripts.check_public_release import inspect_index, main


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True).stdout


@pytest.fixture
def release_repo(tmp_path):
    git(tmp_path, "init", "-q")
    files = {
        "README.md": "# Project\n[Guide](docs/README.md)\n",
        "LICENSE": "MIT License\n",
        "SECURITY.md": "# Security\n",
        "CONTRIBUTING.md": "# Contributing\n",
        "CHANGELOG.md": "# Changelog\n## [0.1.0] — 2026-11-04\n",
        "docs/README.md": "# Documentation\n[Home](../README.md)\n",
        "pyproject.toml": '[project]\nname="synthetic"\nversion="0.1.0"\n',
        "scripts/build-app.sh": '"CFBundleShortVersionString": "0.1.0",\n',
    }
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(tmp_path, "add", ".")
    return tmp_path


def test_anonymized_build_paths_are_public_but_personal_paths_still_fail(release_repo):
    path = release_repo / "build-example.md"
    path.write_text("/Users/" + "build/sdk/include")
    git(release_repo, "add", ".")
    assert inspect_index(release_repo) == ()
    path.write_text("/Users/" + "personal-account/sdk/include")
    git(release_repo, "add", ".")
    assert any(row.category == "personal-absolute-path" for row in inspect_index(release_repo))


def test_valid_public_tree_and_explicit_synthetic_fixture_pass(release_repo):
    path = release_repo / "tests" / "fixture.py"
    path.parent.mkdir()
    marker = "ntn_SYNTHETIC_" + "A" * 30
    header = "-----BEGIN " + "PRIVATE KEY-----"
    path.write_text(f'TOKEN = "{marker}"\n'
                    f'KEY = "{header}\\nSYNTHETIC-MATERIAL"\n')
    git(release_repo, "add", ".")
    assert inspect_index(release_repo) == ()


def test_existing_redaction_fixture_marker_is_narrow_and_not_a_blanket_test_exemption(release_repo):
    folder = release_repo / "tests"
    folder.mkdir()
    marked = "sk-test" + "SYNTHETIC" + "A" * 30
    (folder / "synthetic.py").write_text('KEY = "' + marked + '"\n')
    git(release_repo, "add", "tests")
    assert inspect_index(release_repo) == ()
    real_shape = "sk-" + "B6c7D8e9" * 5
    (folder / "unmarked.py").write_text('KEY = "' + real_shape + '"\n')
    git(release_repo, "add", "tests")
    assert any(row.path == "tests/unmarked.py" and row.category == "credential-shaped-text"
               for row in inspect_index(release_repo))


@pytest.mark.parametrize("relative", [
    ".data/memory.sqlite3", "work/report.txt", ".codex/config.toml", ".mcp.json",
    ".env", "credentials.json", "private.key", "native/.build/app", "Cache.app/code",
    "docs/database.db", "service-account-test.json", "secrets/value.txt",
    ".aws/config", ".ssh/id_ed25519", "exports/mail.txt",
])
def test_private_paths_cannot_be_force_added(release_repo, relative):
    path = release_repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("private synthetic fixture")
    (release_repo / ".gitignore").write_text(relative + "\n")
    git(release_repo, "add", "-f", relative)
    assert any(row.category == "private-or-generated-path" and row.path == relative
               for row in inspect_index(release_repo))


def test_scans_staged_secret_even_after_worktree_has_been_cleaned(release_repo, capsys):
    path = release_repo / "example.py"
    secret = "ghp_" + "A4b5C6d7" * 5
    path.write_text('TOKEN = "' + secret + '"\n')
    git(release_repo, "add", "example.py")
    marker = "not-a-secret"
    path.write_text(f'TOKEN = "{marker}"\n')
    findings = inspect_index(release_repo)
    assert any(row.path == "example.py" and row.category == "credential-shaped-text"
               and row.line == 1 for row in findings)
    assert main(["--root", str(release_repo)]) == 1
    output = capsys.readouterr()
    assert secret not in output.out + output.err


def test_credential_shaped_filename_is_rejected_without_echoing_credential(release_repo, capsys):
    secret = "ghp_" + "C8d9E0f1" * 5
    filename = secret + ".txt"
    (release_repo / filename).write_text("No secret in this blob")
    git(release_repo, "add", "--", filename)
    assert any(row.path == filename and row.category == "credential-shaped-filename"
               for row in inspect_index(release_repo))
    assert main(["--root", str(release_repo)]) == 1
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert "REDACTED" in output.out


def test_diagnostics_escape_control_and_directional_filename_characters(release_repo, capsys):
    filename = "forged\n\x1b[31m\u202ereport.txt"
    (release_repo / filename).write_text("Plain public text")
    git(release_repo, "add", "--", filename)
    assert any(row.path == filename and row.category == "unsafe-filename"
               for row in inspect_index(release_repo))
    assert main(["--root", str(release_repo)]) == 1
    output = capsys.readouterr()
    assert filename not in output.out
    assert "\x1b" not in output.out and "\u202e" not in output.out
    assert "\\n" in output.out and "\\u001b" in output.out and "\\u202e" in output.out


def test_personal_filename_path_is_redacted_in_diagnostics(release_repo, capsys):
    relative = "notes/Users/" + "private-person/project/readme.txt"
    path = release_repo / relative
    path.parent.mkdir(parents=True)
    path.write_text("Not private content")
    git(release_repo, "add", "--", relative)
    assert main(["--root", str(release_repo)]) == 1
    output = capsys.readouterr()
    assert "private-person" not in output.out


def test_private_key_in_nonfixture_is_not_exempted_by_synthetic_nearby(release_repo):
    path = release_repo / "key.txt"
    header = "-----BEGIN " + "PRIVATE KEY-----"
    path.write_text(header + "\nSYNTHETIC-MATERIAL\n")
    git(release_repo, "add", "key.txt")
    assert any(row.category == "private-key-material" for row in inspect_index(release_repo))


def test_documentation_links_resolve_against_the_index(release_repo):
    (release_repo / "README.md").write_text(
        "[Missing](docs/missing.md)\n[Web](https://example.com/page)\n[Anchor](#topic)\n"
    )
    git(release_repo, "add", "README.md")
    assert [row.category for row in inspect_index(release_repo)] == ["broken-relative-link"]
    (release_repo / "docs" / "missing.md").write_text("Local only, not staged")
    assert any(row.category == "broken-relative-link" for row in inspect_index(release_repo))


def test_version_and_changelog_must_match_metadata(release_repo):
    (release_repo / "pyproject.toml").write_text('[project]\nversion="0.2.0"\n')
    git(release_repo, "add", "pyproject.toml")
    assert {row.category for row in inspect_index(release_repo)} == {
        "version-mismatch", "missing-changelog-release",
    }


def test_symlinks_and_personal_paths_are_rejected(release_repo):
    (release_repo / "shortcut").symlink_to("README.md")
    user_path = "/Users/" + "personal-account/private/project"
    (release_repo / "notes.md").write_text(user_path)
    git(release_repo, "add", "shortcut", "notes.md")
    assert {row.category for row in inspect_index(release_repo)} == {
        "symbolic-link", "personal-absolute-path",
    }


def test_empty_index_and_missing_required_files_fail(release_repo, tmp_path):
    git(release_repo, "rm", "--cached", "SECURITY.md")
    assert any(row.category == "required-file-missing" and row.path == "SECURITY.md"
               for row in inspect_index(release_repo))
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    assert inspect_index(empty)[0].category == "empty-index"
