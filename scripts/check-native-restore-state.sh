#!/bin/bash
set -euo pipefail
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
swift build --package-path "$repo_dir/native" --product SecondBrain >/dev/null
product_dir="$(swift build --package-path "$repo_dir/native" --show-bin-path)"
mkdir -p "$repo_dir/outputs/native-state-checks"
app_sources=()
for source_file in "$repo_dir/native/Sources/SecondBrain/"*.swift; do
    if [[ "$(basename "$source_file")" != "SecondBrainApp.swift" ]]; then
        app_sources+=("$source_file")
    fi
done
swiftc -parse-as-library -I "$product_dir" \
    "$product_dir/SecondBrainCore.o" "${app_sources[@]}" \
    "$repo_dir/native/Tests/NativeStateChecks/RestoreStateChecks.swift" \
    -framework AppKit -framework SwiftUI -framework Carbon \
    -o "$repo_dir/outputs/native-state-checks/restore-state-checks"
"$repo_dir/outputs/native-state-checks/restore-state-checks"
