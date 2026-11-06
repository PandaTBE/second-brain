# Development

Second Brain 0.1.0 is a source-distributed macOS application with a local Python service and a separate read-only MCP process. It is not a published Python SDK or a standalone app bundle with an embedded Python runtime.

## Tools and setup

- macOS 14+ for the app; Swift tools 6.0+ and the Apple SDK/Command Line Tools.
- Python 3.11+ and `uv`; the CI backend matrix covers 3.11 and 3.12.
- Node.js 22+ for the dependency-free static JavaScript tests.
- On Linux, `zsh` is needed by the launcher regression test. Native SwiftUI/AppKit code requires macOS.

```sh
uv sync --frozen --group dev
```

No environment file or real provider token is required for tests. Application credentials are entered through the native UI and stored in the Keychain. Do not add a `.env` containing real secrets or configure a CI secret for routine tests.

## Layout

| Path | Responsibility |
| --- | --- |
| `native/Sources/SecondBrain/` | SwiftUI views and app lifecycle |
| `native/Sources/SecondBrainCore/` | Shared models, service startup, API client, and state helpers |
| `native/Tests/SecondBrainCoreTests/` | Executable CoreChecks target |
| `brain_settings/app.py` | FastAPI factory; importing it must not create the personal database |
| `brain_settings/memory_service.py` | Bounded import/card jobs and automation |
| `brain_settings/memory_store.py` | SQLite, revisions, FTS5, and source deletion guards |
| `brain_settings/source_policy.py`, `source_policy_routes.py` | Unreleased independent permissions/exclusions, SQLite predicates and preview API |
| `brain_settings/mcp_server.py` | Read-only stdio memory tools |
| `brain_settings/*_client.py`, `*_import.py` | Source-specific read-only access and ingestion |
| `static/` | Local memory atlas and optional browser settings interface |
| `tests/` | Synthetic Python and JavaScript regression tests |
| `scripts/build-app.sh` | Build and ad-hoc sign the local macOS bundle |
| `scripts/mcp-server.command` | Stable stdio launcher using this checkout's `.venv` |

## Verification

Run from the repository root:

```sh
uv run --frozen python scripts/check_public_release.py
uv run --frozen ruff check brain_settings tests scripts launch.py
uv run --frozen pytest --cov=brain_settings --cov-report=term-missing
node --test tests/test-model-utils.mjs tests/test-memory-atlas.mjs
swift run --package-path native SecondBrainCoreChecks
./scripts/build-app.sh
```

`scripts/check_public_release.py` checks the proposed public files in the Git index. On a clone, tracked files are already indexed; for a release candidate with local changes, stage only reviewed public files first. The guard complements manual review and a secret scan; it does not inspect ignored personal data or establish that every possible secret has been removed.

The coverage threshold is configured in `pyproject.toml`. Python tests use temporary directories, synthetic credentials, and fake transports. Do not replace their fakes with real accounts or a production `.data` directory. Native CoreChecks is the test command for this package; `swift test` has no XCTest target to run.

`./scripts/build-app.sh` builds and replaces the generated `Second Brain.app` inside the checkout. Avoid rebuilding while relying on the running app for work. The generated bundle records this checkout's absolute path and needs its `.venv`, sources, and static files. Moving the project requires rebuilding the bundle and updating absolute MCP paths in clients.

To launch the local app after building:

```sh
open "Second Brain.app"
```

`./start.command` can install runtime dependencies and build a missing bundle. It uses the frozen uv environment when uv is present, or the generated `requirements.txt` fallback otherwise. Development and CI should use the explicit locked setup above.

## Dependency and action updates

```sh
uv lock --check
uv export --frozen --no-dev --no-hashes --no-emit-project --format requirements-txt --output-file requirements.txt
```

Use `uv lock` deliberately when changing requirements. Review both the lock and exported runtime file. Dependency resolution is not proof of runtime API compatibility; rerun MCP protocol and source/provider suites after relevant updates.

CI uses SHA-pinned official GitHub actions, read-only repository permissions, no real source credentials, and no upload of databases or generated app bundles. A successful CI run verifies its listed tests and build; it does not establish that a real account works, that a model is free, or that the app is notarized.

## Designing changes

Preserve the explicit separation between import, local search, and paid model processing. Keep network destinations bounded, return safe errors, and retain source provenance. Imported material is untrusted data. Sources must not issue writes to mail, GitHub, or Notion as a side effect of ingestion. Review schema changes for legacy configuration and MCP-process compatibility, and test cancellation, retries, and deletion with synthetic data.

Source-policy changes must use the shared SQLite predicates for the correct
audience: native retrieval and MCP retrieval are independent, as are local and
external model processing. Missing legacy policies permit existing scopes without
enabling automation; retain outer source/mailbox gates. Check permissions before
model admission and in the transaction that commits a result. Test live policy
changes using independent stores/processes, hidden-source aggregate timestamps,
and exclusions on already imported materials. Do not use a real user's database
to construct migration fixtures. See [search contracts](search-contracts.md) and
[the offline benchmark](search-contracts.md#offline-evaluation).

The native UI is currently Russian. The public README and user guides are bilingual; documentation translation does not imply that every native view is localized into English.

See [CONTRIBUTING.md](../CONTRIBUTING.md), [SECURITY.md](../SECURITY.md), and the [release checklist](releasing.md).
