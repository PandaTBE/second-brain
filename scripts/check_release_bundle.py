#!/usr/bin/env python3
"""Inspect a portable app, including compressed Python, without executing its code.

Run with the build environment that produced the helper (the marshal format must
match). Findings contain category and hashed location only, never matched values.
This heuristic gate complements code review; it cannot prove absence of secrets.
"""

import argparse
import hashlib
import json
import marshal
import re
import struct
import types
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

TOKEN = re.compile(rb"\b(?:sk-|sc_|gsk_|gh[pousr]_|github_pat_|ntn_|secret_)[A-Za-z0-9_-]{20,}\b")
PRIVATE_KEY = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")
PERSONAL_PATH = re.compile(rb"(?:/Users/|/home/)([A-Za-z0-9_.-]+)/")
PLACEHOLDERS = frozenset({
    b"build", b"runner", b"source", b"your_user", b"your_username", b"username",
    b"user", b"example", b"test-user", b"you",
})
PRIVATE_PARTS = frozenset({
    ".data", ".git", ".codex", ".claude", ".serena", ".secrets", "sessions",
})


@dataclass(frozen=True)
class Scan:
    findings: tuple = ()
    files: int = 0
    code: int = 0
    archives: int = 0
    unreadable: int = 0

    def add(self, other):
        return Scan(self.findings + other.findings, self.files + other.files,
                    self.code + other.code, self.archives + other.archives,
                    self.unreadable + other.unreadable)


def finding(location, category):
    identifier = hashlib.sha256(location.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return Scan(findings=((identifier, category),))


def _native_section(raw, offset, endian, segment):
    section = struct.unpack_from(endian + "16s16sQQIIIIIIII", raw, offset)
    name, parent, address, size, file_offset, _, reloc_offset, reloc_count, flags, *_ = section
    seg_name, vm_address, vm_size, seg_offset, seg_size, _, protection, *_ = segment
    if not vm_address <= address <= address + size <= vm_address + vm_size:
        return False, False
    file_backed = flags & 0xFF not in (1, 0xC, 0x12)
    if file_backed:  # Zero-fill sections have no file payload.
        if not seg_offset <= file_offset <= file_offset + size <= seg_offset + seg_size:
            return False, False
    if reloc_count and reloc_offset + reloc_count * 8 > len(raw):
        return False, False
    instructions = (file_backed and seg_name.rstrip(b"\0") == b"__TEXT"
                    and parent.rstrip(b"\0") == b"__TEXT"
                    and name.rstrip(b"\0") == b"__text" and size >= 4
                    and bool(protection & 4) and bool(flags & 0x80000400))
    return True, instructions


def _native_segment(raw, offset, command_size, endian):
    if command_size < 72:
        return False, False
    segment = struct.unpack_from(endian + "16sQQQQIIII", raw, offset + 8)
    _, vm_address, vm_size, file_offset, file_size, maximum, protection, count, _ = segment
    if (command_size != 72 + count * 80 or file_offset + file_size > len(raw)
            or file_size > vm_size or vm_address + vm_size >= 1 << 64
            or maximum & ~7 or protection & ~maximum):
        return False, False
    meaningful = False
    for index in range(count):
        valid, instructions = _native_section(raw, offset + 72 + index * 80, endian, segment)
        if not valid:
            return False, False
        meaningful |= instructions
    return True, meaningful


def _native_macho(raw):
    """Recognize bounded thin 64-bit native images without executing scanned code."""
    endian = {b"\xcf\xfa\xed\xfe": "<", b"\xfe\xed\xfa\xcf": ">"}.get(raw[:4])
    if endian is None or len(raw) < 32:
        return False
    _, cpu, _, kind, count, size, _, reserved = struct.unpack_from(endian + "8I", raw)
    if (cpu not in (0x1000007, 0x100000C) or kind not in (2, 6, 8) or reserved
            or not 1 <= count <= 4096 or size < count * 8 or size % 8 or 32 + size > len(raw)):
        return False
    position, end, meaningful = 32, 32 + size, False
    for _ in range(count):
        if position + 8 > end:
            return False
        command, command_size = struct.unpack_from(endian + "II", raw, position)
        if command_size < 8 or command_size % 8 or command_size > end - position:
            return False
        if command == 0x19:  # LC_SEGMENT_64: validate all mapped sections.
            valid, instructions = _native_segment(raw, position, command_size, endian)
            if not valid:
                return False
            meaningful |= instructions
        position += command_size
    return position == end and meaningful


def _unsafe_private_key(raw, native):
    for match in PRIVATE_KEY.finditer(raw):
        matching_end = match.group().replace(b"-----BEGIN ", b"-----END ", 1)
        if not native or (raw[match.end():match.end() + 1] != b"\0"
                          and not raw.startswith(matching_end, match.end())):
            return True
    return False


def scan_bytes(raw, location, markers, *, native=False):
    result = Scan()
    if TOKEN.search(raw) or _unsafe_private_key(raw, native):
        result = result.add(finding(location, "credential_shape"))
    if (any(match[1] not in PLACEHOLDERS for match in PERSONAL_PATH.finditer(raw))
            or b"/private/var/folders/" in raw):
        result = result.add(finding(location, "personal_path"))
    if any(marker and marker in raw for marker in markers):
        result = result.add(finding(location, "owner_marker"))
    return result


def scan_code(code, location, markers):
    if not isinstance(code, types.CodeType):
        raise ValueError("Unsupported bytecode object")
    result = Scan(code=1).add(scan_bytes(code.co_filename.encode(), location, markers))
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            result = result.add(scan_code(value, location, markers))
        elif isinstance(value, str):
            result = result.add(scan_bytes(value.encode("utf-8", "surrogatepass"),
                                           location, markers))
        elif isinstance(value, bytes):
            result = result.add(scan_bytes(value, location, markers))
        elif isinstance(value, (tuple, frozenset)):
            result = result.add(scan_constants(value, location, markers))
    return result


def scan_constants(values, location, markers):
    result = Scan()
    for value in values:
        if isinstance(value, str):
            result = result.add(scan_bytes(value.encode("utf-8", "surrogatepass"),
                                           location, markers))
        elif isinstance(value, bytes):
            result = result.add(scan_bytes(value, location, markers))
        elif isinstance(value, (tuple, frozenset)):
            result = result.add(scan_constants(value, location, markers))
    return result


def unreadable(location):
    return Scan(unreadable=1).add(finding(location, "unreadable"))


def scan_pyc(raw, location, markers):
    try:
        return scan_code(marshal.loads(raw[16:]), location, markers)
    except (ValueError, TypeError, EOFError, RecursionError):
        return unreadable(location)


def scan_zip(path, location, markers):
    result = Scan(archives=1)
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                member_location = location + "/" + member.filename
                result = result.add(scan_bytes(member_location.encode("utf-8", "surrogatepass"),
                                               member_location, markers))
                raw = archive.read(member)
                result = result.add(scan_bytes(raw, member_location, markers))
                if member.filename.endswith(".pyc"):
                    result = result.add(scan_pyc(raw, member_location, markers))
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, zlib.error):
        result = result.add(unreadable(location))
    return result


def archive_reader():
    # PyInstaller is a build-only dependency, not needed by application tests.
    from PyInstaller.archive.readers import CArchiveReader

    return CArchiveReader


def scan_carchive(path, location, markers):
    result = Scan(archives=1)
    try:
        archive = archive_reader()(str(path))
        for name, entry in archive.toc.items():
            member_location = location + "/" + name
            result = result.add(scan_bytes(member_location.encode("utf-8", "surrogatepass"),
                                           member_location, markers))
            kind = entry[-1]
            if kind == "z":
                pyz = archive.open_embedded_archive(name)
                result = result.add(Scan(archives=1))
                for module in pyz.toc:
                    module_location = member_location + "/" + module
                    result = result.add(scan_bytes(module_location.encode("utf-8", "surrogatepass"),
                                                   module_location, markers))
                    value = pyz.extract(module)
                    if isinstance(value, types.CodeType):
                        result = result.add(scan_code(value, member_location + "/" + module,
                                                      markers))
                    elif isinstance(value, bytes):
                        result = result.add(scan_bytes(value, member_location, markers))
            elif kind in {"s", "m", "M"}:
                result = result.add(scan_code(marshal.loads(archive.extract(name)),
                                              member_location, markers))
            elif kind not in {"o", "n"}:
                raw = archive.extract(name)
                if isinstance(raw, bytes):
                    result = result.add(scan_bytes(raw, member_location, markers))
    except (ImportError, OSError, ValueError, TypeError, EOFError, KeyError,
            RuntimeError, RecursionError, zlib.error):
        result = result.add(unreadable(location))
    return result


def is_private_file(relative):
    name = relative.name.lower()
    return (any(part.lower() in PRIVATE_PARTS for part in relative.parts)
            or name in {"project-location.txt", "settings.json", "sources.json",
                        "memory_schedule.json", "memory-worker.lock", ".mcp.json"}
            or name.startswith(".env")
            or bool(re.search(r"\.(?:sqlite3?|db)(?:-[a-z]+)?$", name))
            or name.endswith((".jsonl", ".p12", ".pfx", ".p8", ".key")))


def scan_file(path, relative, markers):
    location = relative.as_posix()
    result = Scan(files=1)
    if is_private_file(relative):
        result = result.add(finding(location, "private_file"))
    result = result.add(scan_bytes(location.encode(), location, markers))
    try:
        raw = path.read_bytes()
    except OSError:
        return result.add(unreadable(location))
    result = result.add(scan_bytes(raw, location, markers, native=_native_macho(raw)))
    if path.name == "SecondBrainHelper":
        result = result.add(scan_carchive(path, location, markers))
    elif path.suffix == ".zip":
        result = result.add(scan_zip(path, location, markers))
    elif path.suffix == ".pyc":
        result = result.add(scan_pyc(raw, location, markers))
    return result


def scan_bundle(app, owner_markers=()):
    app = Path(app).resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise ValueError("Specify an existing application bundle")
    markers = tuple(value.encode("utf-8") for value in owner_markers if value)
    result = Scan()
    for path in sorted(app.rglob("*")):
        relative = path.relative_to(app)
        if path.is_symlink():
            try:
                if not path.resolve(strict=True).is_relative_to(app):
                    result = result.add(finding(relative.as_posix(), "external_symlink"))
            except (OSError, RuntimeError):
                result = result.add(unreadable(relative.as_posix()))
        elif path.is_file():
            result = result.add(scan_file(path, relative, markers))
    findings = tuple(sorted(set(result.findings)))
    return {
        "passed": not findings and result.unreadable == 0,
        "files_scanned": result.files,
        "python_code_objects": result.code,
        "python_archives": result.archives,
        "unreadable_count": result.unreadable,
        "findings": [{"location_hash": location, "category": category}
                     for location, category in findings],
        "scope": "Explicit application bundle; heuristic scan without executing bundled code",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--owner-marker", action="append", default=[])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.report and args.report.resolve().is_relative_to(args.app.resolve()):
        raise ValueError("Write the audit report outside the scanned bundle")
    report = scan_bundle(args.app, args.owner_marker)
    serialized = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
