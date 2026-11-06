# Release and publication checklist

**Updated 2026-11-06.** These are maintainer instructions. Preparing artifacts does
not create a remote repository, upload files, push commits, or publish a release.

## Release formats

The portable Apple Silicon asset is `Second-Brain-0.2.0-macos-arm64.zip`. It
contains **Second Brain.app**, its frozen Python service and dependencies. The
recipient needs macOS 14+, with no Xcode, Python, uv or source checkout. This is
separate from `scripts/build-app.sh`, whose development app depends on its
checkout and `.venv`. Never ZIP the development app as a portable release.

The current package uses ad-hoc signing. It is not Developer ID signed or Apple
notarized; disclose that in release notes. A checksum is not a signing identity.
Do not claim Intel, universal-binary or clean-machine support without testing the
corresponding artifact. Developer ID signing and notarization require a separate
maintainer-controlled certificate/account workflow; never commit those secrets.

## Build and test

Build from the reviewed source intended for publication, excluding all personal
state. The release builder must collect application files explicitly rather than
copying the working directory wholesale. On the Apple Silicon build Mac, install the development tools and
locked dependencies, then run the project's checks:

```sh
uv sync --frozen --group dev
uv run ruff check brain_settings tests scripts launch.py
uv run pytest --cov=brain_settings --cov-report=term-missing
node --test tests/*.mjs
swift run --package-path native SecondBrainCoreChecks
bash scripts/check-native-restore-state.sh
./scripts/build-release.sh
.venv/bin/python scripts/check_release_bundle.py --app "build/release/app/Second Brain.app"
.venv/bin/python scripts/smoke_release.py --app "build/release/app/Second Brain.app"
.venv/bin/python scripts/smoke_local_model.py --app "build/release/app/Second Brain.app"
```

The local-model smoke uses a synthetic Ollama on `127.0.0.1:11434` and disposable
user data. It refuses to run when that port is occupied; it never replaces an
existing Ollama server, downloads weights, calls a real model, or uses a provider
key. It validates the integration protocol rather than model quality or speed.

The release builder writes artifacts under `dist/`. Keep that generated directory
out of Git. Review the resulting app, checksums and logs before attaching them to
GitHub. Do not infer a successful build or hosted CI run merely from the presence
of these commands or workflow files.

Test an extracted ZIP in a different directory, with an isolated writable data
directory, without depending on the checkout or its `.venv`. Confirm first launch,
blank initial state, static graph assets, loopback HTTP boundary, SQLite/FTS5,
Keychain backend availability and actual stdio MCP initialization/search. Do not
use a live personal database, provider key or mailbox for these checks. A second
Mac with no developer environment gives stronger compatibility evidence.

## Privacy and artifact checks

1. Inspect tracked filenames and the prospective Git index; an ignore rule does
   not remove files already tracked. Exclude `.data`, databases/WAL, credentials,
   source sessions, local MCP config, backups, logs and work/report directories.
2. Scan the **actual public source tree and all history that will be pushed** for
   credential patterns, personal paths, names, emails, private project references
   and filenames. Git author/committer metadata and messages need review too.
3. Use a neutral project identity for public author and committer metadata. If
   private identity or data remains in unpublished local history, and the owner
   explicitly authorizes discarding that history, initialize fresh Git history
   in the existing project directory and create a reviewed initial commit.
   This removes local commits, branches and Git configuration; it must never be
   an automatic cleanup step or a force-push over shared history. A separate
   publication copy is not required for this authorized in-place workflow.
4. Inspect the final app and ZIP separately: embedded code, Python archives,
   Mach-O load paths/build strings, plist metadata, symlinks and archive entries
   can disclose build-machine information even when source files are clean.
5. Confirm that only intended application/runtime files and required licenses are
   present. No personal database, source connections, configured credentials,
   full development environment, diagnostics or code-signing secrets may ship.
6. Verify ZIP extraction, signatures and published SHA-256 checksums against the
   exact files being released. Review scan matches privately; never print a real
   token or attach a private scan report to a public issue or release.

Known-pattern scans are useful but are not a complete security audit or proof of
absence of personal information. Record the checked artifact hashes, scope,
limitations and checks that actually ran. Rotate any real exposed credential;
removing it from a file or a later commit does not revoke it.

After reviewing and staging only intended source files, run the release guard
against that **exact index**:

```sh
git diff --check
git diff --cached --check
git diff --cached --stat
uv run --frozen python scripts/check_public_release.py
```

Keep private reports outside the publication tree. Include the public MIT
license, required dependency notices and accurate English/Russian instructions.

## GitHub repository and draft release

Choose the destination repository explicitly. Before public launch, enable
private vulnerability reporting or provide a working private contact in
[SECURITY.md](../SECURITY.md). Review secret scanning/push protection and branch
rules where available. Local source files cannot enable these account settings.

After authorization to publish, push only the reviewed history to the intended
remote and choose the tested commit for tag `v0.2.0`. Inspect an existing `origin`
before changing it, and never put a token in the remote URL. Pushing, tagging and
publishing are separate actions; preparation does not authorize them by itself.

In GitHub **Releases**, create a **draft**, select the intended tag/commit, paste
[the release notes](releases/0.2.0.md), and attach the portable ZIP plus
`SHA256SUMS`. Offer the source ZIP separately if desired. GitHub's automatic
**Source code** download is not the compiled application. Review the draft and its
attached file hashes before publishing. See [GitHub's release instructions](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).

The release workflow in `.github/workflows/release.yml` prepares a draft when a
version tag is pushed, or when a maintainer starts it manually with a tag. It
builds the portable package, performs its checks and attaches release assets;
the maintainer still reviews the draft and decides when to publish it. Triggering
a remote workflow or pushing a tag changes GitHub state and is separate from
preparing these local files.

Describe the actual architecture/minimum OS, signing/notarization status, data
location and migration limits. Do not claim a successful security audit, hosted
CI run or external-account verification that did not occur.

## Subsequent releases

Update the version and changelog together, regenerate dependency exports when
needed, test schema/runtime compatibility, and repeat source-history and binary
checks. Existing MCP processes may need reconnecting after updates. Preserve user
data outside the app and take a consistent backup before migrations.

## Кратко по-русски

Для пользователей прикладывайте к GitHub Release переносимый ZIP, а не локальный
`.app`, связанный с проектом. Инструменты Xcode/Swift/Python нужны сборщику;
получателю готового архива — нет. Укажите отсутствие Developer ID/нотарификации,
проверенную архитектуру, минимальную ОС, SHA-256 и ограничения переноса данных.

Проверяйте не только исходники, но и всю публикуемую историю Git, метаданные
коммитов, вложенные Python-архивы и бинарные строки готового приложения. Для
публичных коммитов используйте нейтральные проектные данные автора. Только после
явного разрешения владельца неопубликованную личную историю можно заменить новой
историей Git в той же папке; отдельная копия проекта для этого не требуется. Личные
базы, токены, настройки, сессии и приватные отчёты не должны попадать в выпуск.
Подготовка файлов не означает push или публикацию: сначала проверенные артефакты
и черновик Release, затем отдельное решение о публикации.

Автоматический workflow выпуска создаёт черновик по version tag или ручному
запуску с выбранным тегом. Локальная подготовка файлов сама по себе не запускает
workflow, не отправляет теги и не публикует готовый черновик.
