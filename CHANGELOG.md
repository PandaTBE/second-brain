# Changelog

Changes are recorded by release. Versions follow semantic versioning; while the
project is below 1.0, interfaces and database formats may still change. Back up
personal memory before upgrading. Binary assets and their signing status are
listed in the release notes; preparing local artifacts does not publish a release.

## [Unreleased]

### Added

- Read-only `get_evidence_bundle` MCP and shared HTTP/native evidence preview:
  exact quotes, stable code-point citation IDs, current revisions, bounded
  duplicate support and compact JSON context budgets of 2,000–32,000 characters.
  Source/project/UTC-date filters apply before ranking; unknown legacy dates and
  projects remain unknown. Native context copying is explicit, current-record
  verification is optional; copying refreshes the complete request, including
  duplicate support/counts, and cancels on empty or stale responses. Original
  opening and URI copying reread currently permitted materials; unlocalized or
  ambiguous excerpts are marked uncertain. Originals use checked HTTPS/Finder
  targets rather than invented Notes links. Extraction creates no answer or paid model request;
  the four original MCP required-argument contracts remain compatible.
- Opt-in local semantic and hybrid retrieval with a separate installed Ollama
  embedding model, SQLite index generations and bounded writer-owned indexing.
  Native settings expose the model, progress and search modes; HTTP/MCP share
  lexical fallback and partial-coverage diagnostics. Oversized prepared text is
  reported rather than truncated and remains available to BM25. No model download
  or paid inference is started by retrieval. Real-model relevance improvement and
  cold/query-embedding timings remain unverified; a separate synthetic 10,000-vector
  scan report measures only precomputed retrieval. Published `v0.1.0` is unchanged.
- Independent source permissions for import, native retrieval, MCP retrieval,
  local processing and external processing, persisted in SQLite. Source-build
  UI includes local-path/selected-Notes-folder exclusions and a bounded preview.
  Legacy defaults preserve enabled-source and mailbox behavior. Policy checks
  cover retrieval and model admission/result storage; a source can be hidden
  from MCP independently of local processing. Published `v0.1.0` is unchanged.
- Explicit, disabled-by-default internal-network access for a custom HTTPS model
  API. The permission is saved for that API, resets when its address or provider
  changes, and applies to catalog checks and card generation. TLS verification,
  DNS pinning and metadata/loopback restrictions remain enforced. This is an
  unreleased source-build change; published `v0.1.0` assets are unchanged.
- Apple Notes source with explicit folder selection, macOS Automation permission,
  bounded background reconciliation and manual import. Reads note text only;
  protected notes and attachments are skipped. Notes must already be open.
- Outlook.com/Microsoft 365 Inbox import through Microsoft Graph and public-client
  device-code sign-in, using a user-registered Client ID and Keychain tokens.
  Optional 30/90/365-day or all-history import, bounded paging, manual import and
  background reconciliation. Includes bilingual Entra registration instructions.
  Apple Notes and Outlook are source-build changes, not in published `v0.1.0` ZIPs;
  restart running MCP clients after adding either new source kind.

## [0.1.0] — 2026-11-04

Initial public-source release of Second Brain: a native macOS application and a
shared local memory server for AI assistants.

### Added

- Native SwiftUI settings, model selection and source connections, a Dock icon,
  and a menu-bar status indicator. The model catalogue scrolls independently
  inside the settings page. Closing the window keeps automation running;
  quitting stops the application-owned helper.
- Shared SQLite memory with FTS5/BM25 search, source citations, deterministic
  chunk identities, content revisions and import checkpoints.
- Read-only MCP tools: `search_memory`, `get_record`, `get_context_brief` and
  `get_sync_status`, usable by Codex, Claude Code and compatible clients.
- Codex, Claude Code and Qwen Code JSONL session import, plus Markdown, TXT and JSONL note
  folders. Session watching uses completed-turn markers or a 90-second quiet
  period, with a 30-second polling interval. Qwen Code uses the quiet period
  and reads only chat logs, excluding tool, system and thinking payloads.
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
- Qwen API cloud connection and a separate credential-free local Ollama connection
  for downloaded Qwen models. Local cards retain exact evidence validation, use
  one effective generation request at a time and have no cloud fallback. Ollama
  and model weights are separate installs; the saved concurrency target is retained.
- Durable card retry budgets: an initial attempt and two automatic retries,
  followed by an explicit user retry when needed. The memory screen shows which
  errors can retry automatically and which need user action; Retry all errors
  requeues eligible failures with a fresh budget while preserving pauses.
- Clickable memory counters and paginated browsing, plus a local HTML graph and
  history view. Graph connections reflect sources and shared card keywords.
  Batched SVG paths, reusable nodes, indexed filtering and frame-coalesced camera
  updates keep dense graphs responsive while retaining every relationship.
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
  Redaction is heuristic, and optional cloud card generation sends text to the
  selected model provider. Local generation requires a separate Ollama installation.
- Notion does not crawl whole workspaces; GitHub does not import code, comments,
  review discussions or diffs. Email imports only Inbox, and whole messages over
  1 MiB, including their attachments, are skipped with a visible warning.
- Source deletion in a remote service does not automatically purge the local
  archive. Removing a source in Second Brain removes its local memory.

---

## Русский

### Ещё не выпущено

- `get_evidence_bundle` и общий HTTP/нативный просмотр: точные цитаты, устойчивые
  ID с диапазонами codepoints, ревизии, дополнительные подтверждения дублей и
  JSON-бюджет 2 000–32 000 символов. Фильтры источника/проекта/UTC-даты применяются
  до ранжирования; неизвестные старые даты и проекты не выдумываются. Контекст
  копируется явно после обновления всего пакета; пустой/устаревший ответ отменяет
  запись в буфер. Цитату можно перепроверить, неоднозначные фрагменты помечаются;
  перед открытием/копированием URI проверяется текущий доступ. Оригиналы открываются через
  проверенные HTTPS/Finder, без выдуманных Notes-ссылок. Ответ/платная генерация
  не запускаются; обязательные аргументы четырёх прежних MCP сохраняются.
- Явно включаемые локальные embeddings и гибридный поиск: отдельная установленная
  модель Ollama, поколения SQLite, фоновая индексация, нативные настройки и выбор
  режима. HTTP/MCP показывают возврат к BM25 и неполное покрытие; слишком длинный
  текст получает ошибку без усечения. Модели не скачиваются, платной генерации
  при чтении нет. Цель качества и время настоящей модели пока не проверены;
  отчёт по 10 000 готовых синтетических векторов измеряет только их поиск.
- Раздельные разрешения источника: импорт, поиск в приложении, MCP, локальная
  и внешняя обработка; исключения локальных путей и выбранных папок Notes
  с предпросмотром. Старые настройки сохраняют поведение; политики проверяются
  при чтении, допуске модели и сохранении результата. Это изменение исходников,
  опубликованный `v0.1.0` не меняется.
- Apple Заметки: явный выбор папок, разрешение «Автоматизация», ручной импорт и
  фоновая сверка с ограничением размера прохода. Notes должны быть открыты;
  защищённые заметки и вложения пропускаются.
- Outlook.com / Microsoft 365: вход Microsoft по коду с собственным Client ID,
  токены в Keychain, чтение входящих через Graph. Импорт включается явно для
  30/90/365 дней или всей истории; добавлена двуязычная инструкция регистрации.
- Новые источники доступны только в сборке из исходников, не в ZIP `v0.1.0`.
  После их добавления перезапустите уже работающие MCP-клиенты.
- Сохраняется неопубликованное разрешение внутренней сети для собственного API,
  описанное в английском разделе выше.

### 0.1.0 — 2026-11-04

Первая публичная версия исходного кода: нативное приложение macOS и общая
локальная память для ИИ-ассистентов.

- SQLite/FTS5/BM25, ссылки на источники, ревизии и чтение через MCP.
- Импорт Codex, Claude Code, Qwen Code, заметок, выбранных страниц Notion, материалов GitHub
  и входящих Gmail / Яндекс Почты.
- Облачная Qwen API и локальная Qwen через отдельно установленную Ollama: без
  токена модели для локального режима, без перехода в облако и с точной проверкой цитат.
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
База не шифруется приложением; облачные карточки передают текст выбранному
провайдеру, локальные используют отдельно установленную Ollama. Перед обновлением рекомендуется согласованная резервная копия SQLite.
