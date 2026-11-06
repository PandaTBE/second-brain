"""Assemble an explicitly allowlisted, portable macOS application release."""

import argparse
import hashlib
import importlib.metadata
import platform
import plistlib
import re
import shutil
import subprocess
import sysconfig
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

PRIVATE_NAMES = frozenset({
    ".data", ".git", ".venv", "work", "sessions", "exports", "project-location.txt",
})
MACHO_MAGIC = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
               b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def release_name(version: str, architecture: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("Invalid release version")
    if architecture != "arm64":
        raise ValueError("This release builder currently supports arm64 only")
    return f"Second-Brain-{version}-macos-{architecture}.zip"


def validate_tree(root: Path) -> None:
    resolved = root.resolve()
    for entry in root.rglob("*"):
        if entry.name in PRIVATE_NAMES or entry.suffix in {".sqlite", ".sqlite3", ".db"}:
            raise ValueError("Refusing a private file in the release inputs")
        if entry.is_symlink() and not entry.resolve().is_relative_to(resolved):
            raise ValueError("Release symlink escapes its bundle")


def bundle_metadata(version: str) -> dict:
    release_name(version, "arm64")
    return {
        "CFBundleDevelopmentRegion": "ru", "CFBundleIdentifier": "local.secondbrain.desktop",
        "CFBundleName": "Second Brain", "CFBundleDisplayName": "Second Brain",
        "CFBundleExecutable": "SecondBrain", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version, "CFBundleVersion": "2",
        "CFBundleIconFile": "AppIcon", "LSMinimumSystemVersion": "14.0",
        "LSUIElement": False, "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "NSDocumentsFolderUsageDescription": "Чтение выбранных папок сессий и заметок.",
        "NSDesktopFolderUsageDescription": "Чтение выбранной папки заметок.",
        "NSDownloadsFolderUsageDescription": "Чтение выбранной папки заметок.",
        "NSAppleEventsUsageDescription": (
            "Чтение заметок только из выбранных вами папок Apple Заметок."
        ),
    }


def stage_bundle(root, native, helper, icon, notices, app, version):
    validate_tree(helper)
    if not (helper / "SecondBrainHelper").is_file():
        raise ValueError("Bundled helper executable is missing")
    if app.exists():
        raise ValueError("Release staging destination already exists")
    macos = app / "Contents/MacOS"
    resources = app / "Contents/Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    shutil.copy2(native, macos / "SecondBrain")
    shutil.copytree(helper, resources / "Backend", symlinks=True)
    shutil.copy2(icon, resources / "AppIcon.icns")
    shutil.copy2(root / "LICENSE", resources / "LICENSE")
    license_dir = resources / "Licenses"
    license_dir.mkdir()
    shutil.copy2(notices / "THIRD_PARTY_NOTICES.txt", license_dir / "THIRD_PARTY_NOTICES.txt")
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(bundle_metadata(version)))
    validate_tree(app)


def write_notices(root: Path, destination: Path) -> None:
    """Ship installed runtime dependency licenses, never checkout or environment contents."""
    config = tomllib.loads((root / "pyproject.toml").read_text())
    pending = [Requirement(value).name for value in config["project"]["dependencies"]]
    pending.append("pyinstaller")  # Its bundled bootloader has an explicit exception.
    visited = set()
    sections = []
    destination.mkdir(parents=True, exist_ok=True)
    while pending:
        name = pending.pop()
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        if normalized in visited:
            continue
        visited.add(normalized)
        distribution = importlib.metadata.distribution(name)
        sections.append(_distribution_notice(distribution))
        if normalized == "pyinstaller":
            continue  # Build tools are not installed into the user's runtime.
        for value in distribution.requires or []:
            requirement = Requirement(value)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    sections.append("Python runtime license\n" + python_license.read_text("utf-8"))
    (destination / "THIRD_PARTY_NOTICES.txt").write_text("\n\n".join(sections), "utf-8")


def _distribution_notice(distribution) -> str:
    metadata = distribution.metadata
    lines = [f"{metadata['Name']} {distribution.version}"]
    license_name = metadata.get("License-Expression") or metadata.get("License")
    if license_name:
        lines.append(license_name)
    for entry in distribution.files or []:
        parts = entry.parts
        if not any(part.endswith(".dist-info") for part in parts):
            continue
        if not ("licenses" in parts or re.match(r"(?i)(license|copying|notice)", entry.name)):
            continue
        path = Path(distribution.locate_file(entry))
        if path.is_file():
            lines.append(path.read_text("utf-8"))
    if len(lines) < 2:
        raise ValueError(f"No redistributable license metadata found for {metadata['Name']}")
    return "\n".join(lines)


def _run(*arguments):
    return subprocess.check_output(arguments, text=True, stderr=subprocess.STDOUT)


def _macho_files(app):
    files = []
    for path in app.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as handle:
                if handle.read(4) in MACHO_MAGIC:
                    files.append(path)
    return files


def _system_path(value):
    return value.startswith(("@rpath/", "@loader_path/", "@executable_path/",
                             "/usr/lib/", "/System/Library/"))


def verify_macho(path: Path) -> None:
    architecture = _run("lipo", "-archs", str(path)).split()
    if architecture != ["arm64"]:
        raise ValueError("Release contains a non-arm64 Mach-O")
    load_commands = _run("otool", "-l", str(path))
    minimums = re.findall(r"\bminos (\d+(?:\.\d+)*)", load_commands)
    minimums += re.findall(r"cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\S+)",
                           load_commands)
    if not minimums or any(tuple(map(int, value.split(".")[:2])) > (14, 0)
                           for value in minimums):
        raise ValueError("Mach-O requires macOS newer than the advertised macOS 14")
    rpaths = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset", load_commands)
    for value in rpaths:
        if value.startswith("/Library/Developer/"):
            _run("install_name_tool", "-delete_rpath", value, str(path))
        elif not _system_path(value.rstrip("/") + "/"):
            raise ValueError("Release contains a nonportable library search path")
    dependencies = _run("otool", "-L", str(path)).splitlines()[1:]
    for line in dependencies:
        value = line.strip().split(" (compatibility version", 1)[0]
        if value and not _system_path(value):
            raise ValueError("Release contains a nonportable linked library")


def sign_bundle(app: Path) -> None:
    native = app / "Contents/MacOS/SecondBrain"
    _run("strip", "-S", str(native))
    machos = _macho_files(app)
    for path in machos:
        verify_macho(path)
    for path in sorted(machos, key=lambda item: len(item.parts), reverse=True):
        _run("codesign", "--force", "--sign", "-", str(path))
    frameworks = sorted(app.rglob("*.framework"), key=lambda item: len(item.parts), reverse=True)
    for path in frameworks:
        if not path.is_symlink():
            _run("codesign", "--force", "--sign", "-", str(path))
    _run("codesign", "--force", "--sign", "-", str(app))
    _run("codesign", "--verify", "--deep", "--strict", str(app))


def archive_bundle(app: Path, output: Path, version: str) -> Path:
    validate_tree(app)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / release_name(version, platform.machine())
    _run("ditto", "-c", "-k", "--norsrc", "--noextattr", "--keepParent", str(app), str(archive))
    with archive.open("rb") as handle:
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()
    (output / "SHA256SUMS").write_text(f"{checksum}  {archive.name}\n", "ascii")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--native", type=Path)
    parser.add_argument("--helper", type=Path)
    parser.add_argument("--icon", type=Path)
    parser.add_argument("--notices", type=Path, required=True)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--notices-only", action="store_true")
    args = parser.parse_args()
    write_notices(args.root, args.notices)
    if args.notices_only:
        return
    if any(value is None for value in (args.native, args.helper, args.icon, args.app, args.output)):
        parser.error("Building a release requires native, helper, icon, app and output paths")
    version = tomllib.loads((args.root / "pyproject.toml").read_text())["project"]["version"]
    release_name(version, platform.machine())
    stage_bundle(args.root, args.native, args.helper, args.icon, args.notices, args.app, version)
    sign_bundle(args.app)
    archive = archive_bundle(args.app, args.output, version)
    print(f"Portable release created: {archive.name}")


if __name__ == "__main__":
    main()
