#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h:h}"
cd -- "$project_dir"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  print -u2 "Portable releases currently require an Apple Silicon macOS builder."
  exit 1
fi

uv sync --frozen --group build
build_dir="$project_dir/build/release"
app_dir="$build_dir/app/Second Brain.app"
if [[ -e "$app_dir" ]]; then
  print -u2 "Release staging already exists. Move build/release/app aside before rebuilding."
  exit 1
fi
mkdir -p "$build_dir" "$project_dir/dist"

swift_options=(--package-path "$project_dir/native" --scratch-path "$build_dir/swift"
  -c release --triple arm64-apple-macosx14.0
  -Xswiftc -file-prefix-map -Xswiftc "$project_dir=/src/second-brain"
  -Xswiftc -debug-prefix-map -Xswiftc "$project_dir=/src/second-brain"
  -Xswiftc -file-prefix-map -Xswiftc "$HOME=/Users/build"
  -Xswiftc -debug-prefix-map -Xswiftc "$HOME=/Users/build")
export MACOSX_DEPLOYMENT_TARGET=14.0
swift build "${swift_options[@]}"
binary_dir="$(swift build "${swift_options[@]}" --show-bin-path)"

.venv/bin/python -m PyInstaller --noconfirm --clean --onedir --console \
  --name SecondBrainHelper --target-architecture arm64 --distpath "$build_dir/helper-dist" \
  --workpath "$build_dir/pyinstaller" --specpath "$build_dir" \
  --paths "$project_dir" --add-data "$project_dir/static:static" \
  --add-data "$project_dir/brain_settings/resources:brain_settings/resources" \
  --additional-hooks-dir "$project_dir/scripts/pyinstaller-hooks" \
  --hidden-import keyring.backends.macOS --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.asyncio --hidden-import uvicorn.protocols.http.h11_impl \
  --hidden-import uvicorn.lifespan.on --collect-data certifi \
  "$project_dir/scripts/frozen-helper.py"

swift "$project_dir/scripts/make-icon.swift" "$build_dir/AppIcon.iconset"
iconutil --convert icns "$build_dir/AppIcon.iconset" --output "$build_dir/AppIcon.icns"
.venv/bin/python scripts/release_package.py --root "$project_dir" \
  --native "$binary_dir/SecondBrain" --helper "$build_dir/helper-dist/SecondBrainHelper" \
  --icon "$build_dir/AppIcon.icns" --notices "$build_dir/notices" \
  --app "$app_dir" --output "$project_dir/dist"
