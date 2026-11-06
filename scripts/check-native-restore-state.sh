#!/bin/bash
set -euo pipefail
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
swift build --package-path "$repo_dir/native" --product SecondBrain >/dev/null
product_dir="$(swift build --package-path "$repo_dir/native" --show-bin-path)"
shopt -s nullglob
if [[ -f "$product_dir/SecondBrainCore.o" ]]; then
    core_objects=("$product_dir/SecondBrainCore.o")
else
    core_objects=("$product_dir/SecondBrainCore.build/"*.o)
fi
if [[ ${#core_objects[@]} -eq 0 ]]; then
    echo "SecondBrainCore build objects were not found." >&2
    exit 1
fi
mkdir -p "$repo_dir/outputs/native-state-checks"
app_sources=()
for source_file in "$repo_dir/native/Sources/SecondBrain/"*.swift; do
    if [[ "$(basename "$source_file")" != "SecondBrainApp.swift" ]]; then
        app_sources+=("$source_file")
    fi
done
swiftc -parse-as-library -I "$product_dir" -I "$product_dir/Modules" \
    "${core_objects[@]}" "${app_sources[@]}" \
    "$repo_dir/native/Tests/NativeStateChecks/RestoreStateChecks.swift" \
    -framework AppKit -framework SwiftUI -framework Carbon \
    -o "$repo_dir/outputs/native-state-checks/restore-state-checks"
"$repo_dir/outputs/native-state-checks/restore-state-checks"
