#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h:h}"
cd -- "$project_dir"

if [[ ! -x .venv/bin/python ]]; then
  print -u2 "Сначала установите зависимости: uv sync --group dev"
  exit 1
fi

swift build --package-path native -c release
binary_dir="$(swift build --package-path native -c release --show-bin-path)"
staging_dir="$project_dir/native/.build/app-staging"
staged_app="$staging_dir/Second Brain.app"
app_dir="$project_dir/Second Brain.app"

rm -rf -- "$staging_dir"
mkdir -p "$staged_app/Contents/MacOS" "$staged_app/Contents/Resources"
cp "$binary_dir/SecondBrain" "$staged_app/Contents/MacOS/SecondBrain"
printf '%s\n' "$project_dir" > "$staged_app/Contents/Resources/project-location.txt"

.venv/bin/python - "$staged_app/Contents/Info.plist" <<'PY'
import plistlib
import sys
from pathlib import Path

metadata = {
    "CFBundleDevelopmentRegion": "ru",
    "CFBundleIdentifier": "local.secondbrain.desktop",
    "CFBundleName": "Second Brain",
    "CFBundleDisplayName": "Second Brain",
    "CFBundleExecutable": "SecondBrain",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": "0.1.0",
    "CFBundleVersion": "1",
    "CFBundleIconFile": "AppIcon",
    "LSMinimumSystemVersion": "14.0",
    "LSUIElement": False,
    "NSHighResolutionCapable": True,
    "NSPrincipalClass": "NSApplication",
    "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    "NSDocumentsFolderUsageDescription": "Чтение выбранных вами папок сессий и заметок.",
    "NSDesktopFolderUsageDescription": "Чтение выбранной вами папки заметок.",
    "NSDownloadsFolderUsageDescription": "Чтение выбранной вами папки заметок.",
    "NSAppleEventsUsageDescription": "Чтение заметок только из выбранных вами папок Apple Заметок.",
}
Path(sys.argv[1]).write_bytes(plistlib.dumps(metadata))
PY

swift scripts/make-icon.swift "$staging_dir/AppIcon.iconset"
iconutil --convert icns "$staging_dir/AppIcon.iconset" \
  --output "$staged_app/Contents/Resources/AppIcon.icns"
codesign --force --sign - "$staged_app"
codesign --verify --strict "$staged_app"

.venv/bin/python - "$staged_app" "$app_dir" <<'PY'
import plistlib
import shutil
import sys
from pathlib import Path

source, destination = map(Path, sys.argv[1:])
if destination.exists():
    metadata = plistlib.loads((destination / "Contents" / "Info.plist").read_bytes())
    if metadata.get("CFBundleIdentifier") != "local.secondbrain.desktop":
        raise SystemExit("Не заменяю приложение с другим идентификатором.")
    shutil.rmtree(destination)
shutil.move(str(source), str(destination))
PY

print "Готово: $app_dir"
