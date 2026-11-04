# Changelog

Changes are recorded by release. Versions follow semantic versioning; while the
project is below 1.0, interfaces and database formats may still change. Back up
personal memory before upgrading. Binary assets and their signing status are
listed in the release notes; preparing local artifacts does not publish a release.

## [Unreleased]

No unreleased changes recorded.

## [0.1.0] — 2026-11-04

Initial public-source release of Second Brain: a native macOS application and a
shared local memory server for AI assistants.

### Added

- Native SwiftUI settings, model selection and source connections, a Dock icon,
  and a menu-bar status indicator. Closing the window keeps automation running;
  quitting stops the application-owned helper.
- Shared SQLite memory with FTS5/BM25 search, source citations, deterministic
  chunk identities, content revisions and import checkpoints.
- Read-only MCP tools: `search_memory`, `get_record`, `get_context_brief` and
  `get_sync_status`, usable by Codex, Claude Code and compatible clients.
- Codex and Claude Code JSONL session import, plus Markdown, TXT and JSONL note
  folders. Session watching uses completed-turn markers or a 90-second quiet
  period, with a 30-second polling interval.
- Notion integration tokens and explicit selection of up to 100 pages. Selected
  pages are imported as source-backed text; child pages are selected separately.
- GitHub repository selection, up to 20 repositories per source, with optional
  Markdown documentation, issue bodies and pull-request bodies. Bounded passes
  preserve their continuation state in SQLite.
- Guided Gmail and Yandex Mail connections using application passwords, fixed
  server presets and an explicit email-import switch. Inbox import supports
  30/90/365-day initial windows or all history, preserves read flags and stores
  text without attachments. Each pass processes up to 50 messages.
- Optional model-generated cards with evidence validation, API usage metadata,
  a separate batch limit and configurable concurrency: any positive whole number,
  defaulting to 10 requests. Actual parallel work is bounded by the current batch.
- Durable card retry budgets: an initial attempt and two automatic retries,
  followed by an explicit user retry when needed. The memory screen shows which
  errors can retry automatically and which need user action; Retry all errors
  requeues eligible failures with a fresh budget while preserving pauses.
- Clickable memory counters and paginated browsing, plus a local HTML graph and
  history view. Graph connections reflect sources and shared card keywords.
- Global synchronization pause and per-mailbox import pause, retaining access
  to cached memory and MCP search.
- macOS Keychain credential storage, heuristic secret redaction, loopback API
  protections, bounded network reads and read-only external source operations.
- Portable Apple Silicon ZIP packaging with an embedded Python service, shared
  per-user Application Support storage, bundled MCP support, checksums and a
  GitHub Actions workflow that creates a release draft after validation.
- Release privacy checks inspect compressed Python code and archive metadata;
  Python build paths are anonymized during packaging.
- English and Russian documentation, an MIT license, a security policy,
  contribution guidance, issue templates and CI checks for the public repository.

### Limits to understand

- Portable ZIP assets bundle their runtime and require Apple Silicon and macOS
  14 or later. They currently use an ad-hoc signature without Apple notarization.
  The separate developer build still depends on its checkout and Python environment.
  This project is not a published PyPI SDK.
- The SQLite memory and its backups are not encrypted by the application.
  Redaction is heuristic, and optional card generation sends text to the selected
  model provider.
- Notion does not crawl whole workspaces; GitHub does not import code, comments,
  review discussions or diffs. Email imports only Inbox, and whole messages over
  1 MiB, including their attachments, are skipped with a visible warning.
- Source deletion in a remote service does not automatically purge the local
  archive. Removing a source in Second Brain removes its local memory.

---

## Русский

### 0.1.0 — 2026-11-04

Первая публичная версия исходного кода: нативное приложение macOS и общая
локальная память для ИИ-ассистентов.

- SQLite/FTS5/BM25, ссылки на источники, ревизии и чтение через MCP.
- Импорт Codex, Claude Code, заметок, выбранных страниц Notion, материалов GitHub
  и входящих Gmail / Яндекс Почты.
- Карточки через выбранную модель, ограниченные партии, параллельная обработка
  и два автоматических повтора после первой неудачи.
- Фоновая синхронизация, глобальная пауза, пауза почтовых источников, постраничный
  список памяти и локальный граф с историей.
- Связка ключей macOS, маскирование известных форматов секретов и ограничения
  локального API. Границы этих мер описаны в [политике безопасности](SECURITY.md).
- Руководства на двух языках, лицензия MIT и файлы для совместной разработки.

Переносимый ZIP для Apple Silicon и macOS 14+ включает Python-сервис и не требует
Xcode, Python или uv у пользователя. В сборке пока используется локальная подпись
без нотарификации Apple. Отдельная сборка для разработки зависит от папки проекта.
База не шифруется приложением; включённое создание карточек передаёт текст выбранному
провайдеру. Перед обновлением рекомендуется согласованная резервная копия SQLite.
