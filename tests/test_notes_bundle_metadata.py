"""The public app declares Notes automation and bundles only its static reader."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_notes_automation_is_described_in_both_app_variants():
    spec = importlib.util.spec_from_file_location(
        "notes_package_metadata", ROOT / "scripts/release_package.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.bundle_metadata("0.1.0")["NSAppleEventsUsageDescription"]
    assert '"NSAppleEventsUsageDescription"' in (ROOT / "scripts/build-app.sh").read_text()


def test_notes_reader_is_explicitly_added_to_the_frozen_service():
    source = (ROOT / "scripts/build-release.sh").read_text()
    assert '--add-data "$project_dir/brain_settings/resources:brain_settings/resources"' in source
