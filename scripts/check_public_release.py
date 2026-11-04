#!/usr/bin/env python3
"""Check the exact Git index before publishing, without printing sensitive matches.

This is a small release guard, not proof that all secrets have been detected.
It deliberately does not read ignored working files, personal memory, or Keychain.
Run after staging the intended public files: python scripts/check_public_release.py
"""

import argparse
import json
import posixpath
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

REQUIRED = (
    "README.md", "LICENSE", "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md",
    "docs/README.md", "pyproject.toml", "scripts/build-app.sh",
)
PRIVATE_PARTS = {
    ".data", "work", "outputs", ".codex", ".claude", ".serena", ".venv", "venv",
    ".build", ".swiftpm", "__pycache__", ".pytest_cache", ".ruff_cache", "secrets",
    ".secrets", "sessions", "node_modules", "htmlcov", "test-results", ".aws", ".ssh", "exports",
}
PRIVATE_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".p8", ".jks", ".keystore",
                    ".bak", ".backup", ".log", ".pyc", ".dmg", ".pkg"}
MAX_BLOB_BYTES = 4 * 1024 * 1024
TOKEN = re.compile(
    r"\b(?:sk-|sc_|gsk_|gh[pousr]_|github_pat_|ntn_|secret_)[A-Za-z0-9_-]{20,}\b"
)
SYNTHETIC_TOKEN = re.compile(
    r"^(?:sk-|sc_|gsk_|gh[pousr]_|github_pat_|ntn_|secret_)"
    r"(?:SYNTHETIC|TEST[_-]|EXAMPLE[_-]|FAKE[_-]|PLACEHOLDER[_-])", re.I
)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")
PERSONAL_PATH = re.compile(
    r"(?:/Users/|/home/|[A-Za-z]:[\\/]+Users[\\/]+)([A-Za-z0-9_.-]+)[\\/]"
)
PLACEHOLDERS = {"your_user", "your_username", "username", "user", "example", "test-user",
                "you", "build"}
LINK = re.compile(r"!?\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))")


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    category: str


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout


def _private(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    return (
        any(part.lower() in PRIVATE_PARTS or part.lower().endswith(".app") for part in parts)
        or name == ".mcp.json" or name == ".npmrc"
        or (name.startswith(".env") and name != ".env.example")
        or name.startswith((".coverage", "credentials", "client_secret", "service-account"))
        or (name.startswith("token") and name.endswith(".json"))
        or PurePosixPath(name).suffix in PRIVATE_SUFFIXES
        or bool(re.search(r"\.(?:sqlite3?|db)(?:-|$)", name))
    )

def _filename_findings(path: str) -> list[Finding]:
    result = []
    if TOKEN.search(path):
        result.append(Finding(path, 0, "credential-shaped-filename"))
    if any(match[1].lower() not in PLACEHOLDERS for match in PERSONAL_PATH.finditer(path)):
        result.append(Finding(path, 0, "personal-filename-path"))
    if any(ord(char) < 32 or 127 <= ord(char) <= 159
           or ord(char) in (0x061C, 0x200E, 0x200F)
           or 0x202A <= ord(char) <= 0x202E or 0x2066 <= ord(char) <= 0x2069 for char in path):
        result.append(Finding(path, 0, "unsafe-filename"))
    return result


def _display_path(path: str) -> str:
    redacted = TOKEN.sub("[REDACTED]", path)
    redacted = PERSONAL_PATH.sub("[PERSONAL_PATH]/", redacted)
    return json.dumps(redacted, ensure_ascii=True)


def _text_findings(path: str, text: str) -> list[Finding]:
    result = []
    for match in TOKEN.finditer(text):
        fixture = path.startswith("tests/") and "SYNTHETIC" in match[0].upper()
        if not fixture and not SYNTHETIC_TOKEN.match(match[0]):
            result.append(Finding(path, text.count("\n", 0, match.start()) + 1,
                                  "credential-shaped-text"))
    for match in PRIVATE_KEY.finditer(text):
        # Explicitly labelled synthetic key fixtures are necessary for redaction tests.
        fixture = path.startswith("tests/") and "SYNTHETIC-" in text[
            match.end():match.end() + 200
        ]
        if not fixture:
            result.append(Finding(path, text.count("\n", 0, match.start()) + 1,
                                  "private-key-material"))
    for match in PERSONAL_PATH.finditer(text):
        if match[1].lower() not in PLACEHOLDERS:
            result.append(Finding(path, text.count("\n", 0, match.start()) + 1,
                                  "personal-absolute-path"))
    return result


def _metadata(texts: dict[str, str]) -> list[Finding]:
    try:
        version = tomllib.loads(texts.get("pyproject.toml", ""))["project"]["version"]
        if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError
    except (KeyError, ValueError, TypeError):
        return [Finding("pyproject.toml", 0, "invalid-release-version")]
    build = re.search(r'["\']CFBundleShortVersionString["\']\s*:\s*["\']([^"\']+)',
                      texts.get("scripts/build-app.sh", ""))
    result = []
    if build is None or build[1] != version:
        result.append(Finding("scripts/build-app.sh", 0, "version-mismatch"))
    if f"## [{version}]" not in texts.get("CHANGELOG.md", ""):
        result.append(Finding("CHANGELOG.md", 0, "missing-changelog-release"))
    return result


def _links(path: str, text: str, names: frozenset[str]) -> list[Finding]:
    result = []
    for match in LINK.finditer(text):
        value = match[1] or match[2]
        url = urlsplit(value)
        if url.scheme or url.netloc or not url.path:
            continue
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(url.path)))
        if target not in names and not any(name.startswith(target.rstrip("/") + "/")
                                          for name in names):
            result.append(Finding(path, text.count("\n", 0, match.start()) + 1,
                                  "broken-relative-link"))
    return result


def inspect_index(root: Path) -> tuple[Finding, ...]:
    """Return safe diagnostics about staged blobs; never inspect a private working tree."""
    entries = _git(root, "ls-files", "--stage", "-z").split(b"\0")
    result, texts, names = [], {}, set()
    for entry in filter(None, entries):
        metadata, path_bytes = entry.split(b"\t", 1)
        mode, oid, stage = metadata.decode("ascii").split()
        path = path_bytes.decode("utf-8", errors="replace")
        names.add(path)
        result.extend(_filename_findings(path))
        if stage != "0":
            result.append(Finding(path, 0, "unmerged-index-entry"))
            continue
        if _private(path):
            result.append(Finding(path, 0, "private-or-generated-path"))
            continue
        if mode != "100644" and mode != "100755":
            result.append(Finding(path, 0, "symbolic-link" if mode == "120000"
                                  else "unsupported-index-mode"))
            continue
        if int(_git(root, "cat-file", "-s", oid)) > MAX_BLOB_BYTES:
            result.append(Finding(path, 0, "oversized-public-blob"))
            continue
        raw = _git(root, "cat-file", "blob", oid)
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            if PurePosixPath(path).suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif"}:
                result.append(Finding(path, 0, "unexpected-binary-blob"))
            continue
        texts[path] = text
        result.extend(_text_findings(path, text))
    if not names:
        return (Finding("<index>", 0, "empty-index"),)
    result.extend(Finding(name, 0, "required-file-missing")
                  for name in REQUIRED if name not in names)
    result.extend(_metadata(texts))
    for path, text in texts.items():
        if path.endswith(".md"):
            result.extend(_links(path, text, frozenset(names)))
    return tuple(sorted(set(result)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    try:
        root = Path(_git(arguments.root, "rev-parse", "--show-toplevel").decode().strip())
        findings = inspect_index(root)
    except (OSError, subprocess.CalledProcessError, ValueError):
        print("Public release check could not read the Git index.")
        return 2
    for finding in findings:
        print(f"{_display_path(finding.path)}:{finding.line}: {finding.category}")
    if findings:
        print(f"Public release check failed: {len(findings)} finding(s). "
              "Matches are never printed.")
        return 1
    print("Public release checks passed for the Git index. "
          "Heuristic scans are not a security audit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
