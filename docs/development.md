# Development

Second Brain is a macOS application with a local Python service and a separate
read-only MCP process. The published 0.1.0 Apple Silicon bundle embeds its Python
runtime; development builds use the checkout's environment. Unreleased features
documented below require current source builds. This is not a published Python SDK.

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
| Native `QuickSearch*` / `QuickShortcutSettings` | Unreleased independent panel requests, explicit selected-record actions and exclusive Carbon shortcut registration |
| `native/Tests/SecondBrainCoreTests/` | Executable CoreChecks target |
| `brain_settings/app.py` | FastAPI factory; importing it must not create the personal database |
| `brain_settings/memory_service.py` | Bounded import/card jobs and automation |
| `brain_settings/memory_store.py` | SQLite, revisions, FTS5, and source deletion guards |
| `brain_settings/source_policy.py`, `source_policy_routes.py` | Unreleased independent permissions/exclusions, SQLite predicates and preview API |
| `brain_settings/embedding_store.py`, `embeddings.py`, `retrieval.py` | Unreleased local index generations, background batches, bounded exact scans and shared RRF retrieval |
| `brain_settings/evidence*.py`, `retrieval_filters.py`, `source_dates.py`, `session_metadata.py` | Unreleased extractive packets, pre-ranking filters and explicit date/project provenance |
| `brain_settings/memory_facts.py`, `fact_schema.py`, `fact_queries.py`, `fact_relations.py`, `facts_service.py` | Unreleased exact card projection, revision snapshots, conservative relations and scoped facts/history reads |
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

Evidence tests must verify exact saved-text code-point slices, revision and policy
changes, source diversity/deduplication, and the whole compact JSON character
budget rather than quote length alone. Keep HTTP and MCP on `EvidenceService`,
with their own native/MCP source permissions. Filters must precede candidate
limits in lexical and vector paths; use known project metadata and explicit UTC
source dates. Synthetic migrations leave legacy source dates/import time unknown;
test unchanged reimports filling metadata without increasing the text revision.
Native checks compare Unicode scalars for exact evidence, retain structured trust
metadata during copy, and reject source schemes that could execute content.

Native export/open actions must refresh the complete request, including duplicate
support and counts, before using cached evidence. After each await, guard the
request generation/parameters and active sheet; verify original targets against a
currently authorized record. Empty/error/stale results must not touch the clipboard.
Exercise policy revocation while a packet is visible, using an isolated native
build and synthetic sources. Already returned/rendered text is not revocable.

Facts/history must remain read-only: no projection or model request on MCP reads.
Project old ready cards through the app's existing writer with bounded jobs;
explicit local projection remains available while automatic work is paused.
Schema/prompt 3 extends the normal card request, not a second extraction call.
Retain its provider selection and attempt budgets. Test exact wording grouping,
heuristic candidate framing, directed explicit replacement, revision revalidation,
and policy filtering of both current records and historical snapshots/targets.
History begins at feature enablement; never synthesize missing older versions.
Exercise `include_history`, past-only badges, stable event IDs/cursor progress and
32,000-byte compact UTF-8 limits, including relation/metadata truncation. Relevant
synthetic suites include `test_memory_facts.py`, `test_fact_cards.py`,
`test_fact_projection.py` and `test_facts_mcp.py`; native checks cover stale sheets
and active versus historical material actions.

Quick-search checks must cover independent query/action generations, the
300 ms debounce, eight-result bound, IME composition and ordinary navigation keys.
Use a fake registrar/preferences domain for shortcut conflicts; the replacement
must register before the old binding is removed. Do not modify real user
shortcuts/preferences during development. Native QA should invoke the panel from
another app, select/open/copy explicitly, close with Esc, and verify stale requests
and changed source permissions cannot export. Selected context uses evidence
`record_id` with a 6,000-character budget, not a wider query or embedding call.
AX labels/announcement code alone do not prove full live VoiceOver speech; record
the actual accessibility checks performed. The feature needs no global event tap
or extra Accessibility/Input Monitoring permission.

The native UI is currently Russian. The public README and user guides are bilingual; documentation translation does not imply that every native view is localized into English.

## Retrieval evaluation

The offline BM25 benchmark and precomputed-vector scan use disposable synthetic
databases and need no model or private configuration:

```sh
uv run --frozen python scripts/benchmark_search.py
uv run --frozen python scripts/benchmark_vectors.py --count 10000
uv run --frozen python scripts/benchmark_evidence.py
```

See [search contracts](search-contracts.md) for the fixture, metrics, timing scopes
and measured reports. Integration fakes do not establish semantic quality. The
evidence benchmark is a separate 10-case bilingual synthetic passage-qrels
regression slice with zero model calls; its fixture precision/recall is not a
claim about arbitrary sources, semantic models or answer correctness. The
real-model evaluator is separate and requires both `--model` and
`--allow-local-model`; it only uses the synthetic corpus, never downloads weights,
and must not silently replace a missing model. It compares the unchanged fixture
against the committed BM25 baseline and reports whether the +0.15 paraphrase
Recall@5/no exact-MRR regression gate passed. That real-model gate, query latency
and controlled cold-start timing have not been verified. Server-reported load
duration is not a controlled cold-start measurement.

See [CONTRIBUTING.md](../CONTRIBUTING.md), [SECURITY.md](../SECURITY.md), and the [release checklist](releasing.md).
