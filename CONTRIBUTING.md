# Contributing / Участие в разработке

Thanks for helping improve Second Brain. Issues and pull requests may be written in English or Russian. Describe the user problem, keep changes focused, and use synthetic examples.

Подойдут обсуждения и pull request на русском или английском. Опишите задачу пользователя, ограничьте изменение одной целью и используйте только искусственные тестовые данные.

## Development setup

The native application needs macOS 14+, Swift 6.0+ command-line tools, Python 3.11+, and [uv](https://docs.astral.sh/uv/getting-started/installation/). Node.js 22+ runs the static interface checks. Full Xcode is optional for local development. Linux can run backend and JavaScript checks; install `zsh` for the launcher test. Linux is not a supported native-app platform.

From the repository root:

```sh
uv sync --frozen --group dev
uv run --frozen python scripts/check_public_release.py
uv run --frozen ruff check brain_settings tests scripts launch.py
uv run --frozen pytest --cov=brain_settings --cov-report=term-missing
node --test tests/test-model-utils.mjs tests/test-memory-atlas.mjs
swift run --package-path native SecondBrainCoreChecks
./scripts/build-app.sh
```

The public-release guard examines the Git index: review and stage intended public changes before using it for release approval. It does not scan ignored personal data, replace manual review, or prove the absence of every secret.

See [development notes](docs/development.md) for the project layout and [user guides](docs/README.md) for application behavior. CoreChecks is an executable verification target: this package does not use `swift test` or require an XCTest installation.

## Before opening a pull request

1. Create a branch in your fork and make a small, coherent change.
2. For behavior changes, first reproduce the problem with a meaningful failing test. Do not add tests that only repeat implementation details or superficial text changes.
3. Keep secrets in the macOS Keychain. Never add personal `.data`, account settings, session exports, email messages, or credentials to fixtures.
4. Validate new input and preserve read-only source access. New network calls, model costs, permissions, retry behavior, and stored data need an explicit explanation.
5. Run checks relevant to the change. The Python coverage gate is 80%; CI runs synthetic tests without provider credentials.
6. Update the user guide and changelog when behavior changes. Include actual validation results in the pull request.

Use clear commit messages such as `fix: preserve mail cursor after pause` or `docs: clarify source permissions`. Please do not mix formatting changes with functional changes unless necessary.

## Dependency changes

The reproducible source environment is `pyproject.toml` plus `uv.lock`. The runtime-only pip fallback is generated, not edited by hand:

```sh
uv lock
uv export --frozen --no-dev --no-hashes --no-emit-project --format requirements-txt --output-file requirements.txt
```

Review the lockfile diff, run the relevant suites, and explain the reason for the update. The MCP SDK is tested at the version pinned by the lockfile; do not infer compatibility with every version allowed by a dependency range. GitHub Actions are pinned to commit SHAs; verify upstream ownership and release notes when updating them.

## Using Claude Code or Codex

An assistant can help inspect the repository and implement changes, but its output still needs review and tests. Give it a focused task and use the commands above. Do not paste real API keys, private sessions, mail, or memory exports into issues, prompts, fixtures, or CI. Configuring Second Brain as an MCP source is a separate user action; development does not require access to your personal memory.

## Reporting problems and vulnerabilities

Use the bug report or feature request form with a minimal sanitized example. Include the application version and affected platform/source. Follow [SECURITY.md](SECURITY.md) for vulnerability reporting; do not publish exploit details, credentials, or private data in an issue.

Be respectful, discuss behavior and evidence, and make space for contributors with different levels of experience. Contributions are offered under the repository's [MIT license](LICENSE).
