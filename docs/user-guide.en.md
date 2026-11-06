# User guide

**Second Brain 0.1.0 · updated 2026-11-06** · [Русский](user-guide.ru.md) · [Documentation index](README.md)

## 1. What it does and when to use it

Second Brain is a native macOS application that brings materials you select into a shared local memory. Sources include Codex, Claude Code and Qwen Code sessions, notes, Notion pages, GitHub materials, and Gmail/Yandex inboxes. It stores text and cards in SQLite, searches with FTS5/BM25, and exposes that memory to AI clients through MCP.

Useful workflows include:

- finding a decision made in another project and checking the original conversation;
- sharing a history of decisions between Codex and Claude Code without copying entire chats;
- searching notes, repository documentation, and selected emails together;
- retaining context that would otherwise stay scattered across tools.

The practical advantages are shared memory across clients, local search without model calls, source and revision references, and explicit control over card-generation work. These are not benchmarks or a promise of better answers to every question. Keyword search can miss paraphrases, small models can make mistakes, and large archives take time to ingest.

This application is not cloud synchronization between computers, a backup service, or a verified user profile. Its graph shows source membership and shared keywords, not proven semantic or causal relationships. The native interface in 0.1.0 uses Russian; this guide includes the corresponding Russian control names where useful.

## 2. Requirements and installation

### Prebuilt release: no developer tools required

For an **Apple Silicon Mac running macOS 14+**, download
`Second-Brain-0.1.0-macos-arm64.zip` from this repository's **Releases → Assets**.
This asset includes the native application and its Python service; the recipient
does not need Xcode, Python, uv or the source checkout. Intel Macs are not covered
by this asset. See the [release notes](releases/0.1.0.md).

1. Compare the ZIP's SHA-256 with `SHA256SUMS` attached to the same release. A
   matching checksum detects a changed file, not whether its publisher is trusted.
2. Extract the ZIP and move **Second Brain.app** to **Applications** before
   registering MCP paths.
3. Open the app. The current package is ad-hoc signed and **not Developer ID
   signed or notarized by Apple**. If macOS blocks it and you trust the release,
   follow [Apple's opening instructions](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac):
   **System Settings → Privacy & Security → Open Anyway**. Do not disable
   Gatekeeper or other system protections.
4. Add your sources and, optionally, model credentials. The release contains no
   preconfigured personal archive. macOS may ask for source-folder or Keychain
   access; review these requests in the system dialog.

Release data is stored in `~/Library/Application Support/Second Brain`, outside
of the app bundle. Moving or replacing the app does not move this archive, but
MCP client paths must be updated if the app's location changes. A source build's
`.data` archive is separate and is not automatically copied or migrated.

Local search works offline after import. Remote source synchronization and cloud
card generation use their respective services.

### Build from source for development

This option requires macOS 14+, Apple tools with **Swift 6.0+**, Python 3.11+, and
[uv](https://docs.astral.sh/uv/getting-started/installation/). Full Xcode is not
required for the project's executable CoreChecks; see
[Apple's developer-tool resources](https://developer.apple.com/xcode/resources/).
Obtain the source and run from the checkout's root:

```sh
xcode-select --install
uv sync --frozen --group dev
./scripts/build-app.sh
open "Second Brain.app"
```

Alternatively, run `./start.command` for the local launcher. The development app
uses the source directory and its `.venv`, and stores its data in `.data` beside
the checkout. Keep the directory in a permanent location; rebuild and update MCP
paths after moving it. This differs from the portable ZIP described above.

## 3. First run without model charges

1. Open **Источники → Добавить источник** (Sources → Add source) and choose a notes or session directory.
2. Use that source's import button. Checking access and importing content are separate operations.
3. Open **Память** (Memory) and search for a phrase you know is in the imported material.
4. Open the result and check its text, source, and revision.
5. Connect an MCP client if needed, as described below.

These steps require no model or model API token. In a fresh installation, automatic card generation is off. Local session watching is on, while periodic reconciliation is off. These are application defaults, not a description of an already configured installation; inspect your current settings in the app.

## 4. Models and cards

In **Модель и API** (Model and API), choose a provider and model, check the connection, and save. Cloud presets, including **Qwen API**, use an API token. **Qwen / Ollama · локально** (local) uses a downloaded local model without an API token. A custom OpenAI-compatible endpoint uses HTTPS and public addresses by default. The published `v0.1.0` ZIP rejects internal addresses; the unreleased source-build option described below allows selected corporate connections.

For a provider configured by URL, select **Свой API · OpenAI-совместимый** (Custom API · OpenAI-compatible). Enter the public base URL, for example `https://api.example.com/v1`, without `/chat/completions`; supply its API token and the exact model ID issued by that provider. The endpoint must accept OpenAI-style `POST /chat/completions` requests. Loading a catalog and checking the connection are optional: a manually entered model ID can be saved even when `/models` is unavailable or does not list it. Check generation with a small batch after saving. In published `v0.1.0`, private addresses are not supported. Plain HTTP and custom ports remain unsupported; the named Ollama connection is the loopback exception.

### Corporate API on an internal network — unreleased

**Updated 2026-11-05. Source builds only; this option is not in the published `v0.1.0` ZIP.** For a trusted corporate API, choose **Свой API · OpenAI-совместимый** and enable **Разрешить внутреннюю сеть для этого API** (Allow the internal network for this API). The option is off by default and resets when the base address or provider changes. Its saved request/settings field is `allow_private_network`; other provider presets remain public-only.

Enter only the base prefix, for example `https://llm.example.internal/api/v2`, plus the token and exact model ID. The app appends `/chat/completions`, producing `/api/v2/chat/completions`. Catalog loading remains optional. Use the corporate network or VPN if your provider requires it; no corporate address or token needs to be included in a public issue.

The opt-in permits RFC1918 IPv4, IPv6 ULA and CGNAT destinations, excluding known cloud metadata endpoints. Loopback, link-local, unspecified, multicast and other reserved destinations remain blocked. HTTPS on port 443, a valid certificate for the original hostname, DNS pinning and no redirects are still required. A corporate certificate not trusted by the app's CA bundle may produce a separate TLS error; this setting does not disable certificate verification.

Cloud tokens are stored in macOS Keychain, separately for each provider and Base URL. The app does not reveal a stored token. If Keychain is unavailable, saving a cloud credential fails rather than falling back to a token file. Ollama model configuration needs no token or model-credential access to Keychain.

A connection check does not generate a card. OpenRouter uses the authenticated `/key` endpoint. Qwen API uses Alibaba Cloud's documented `/api/v1/models` catalog; Ollama checks installed models through `/api/tags`. Other cloud presets use `/models`. SimpleClaw's catalog is public, so loading it does not prove the token is valid. Custom API catalogs may also be public. Use a small manual card batch to check generation with the selected model.

Cards contain a summary, keywords, retrieval questions, and facts supported by excerpts of the imported text. They supplement the archive: full imported text remains searchable when generation fails. Review both model output and its interpretation of the evidence.

Displayed pricing comes from provider catalogs when available; an unknown price does not mean free usage. Models, prices, and data-handling terms can change. The application does not enforce a provider-account spending cap.

To enable automatic cards:

1. Open automation through **Память → Изменить** (Memory → Edit).
2. Enable automatic card creation and periodic reconciliation if appropriate.
3. Choose the batch size and number of simultaneous requests.
4. Read the recurring API-cost warning and save.

The default batch size is 10 materials, with a range of 1–50. Default concurrency is 10 requests. Enter any positive whole number in the concurrency field; there is no application-imposed upper limit. Existing explicitly saved values are preserved. Actual parallel work cannot exceed the current batch size: for example, concurrency 1000 with a batch of 10 can run at most 10 materials simultaneously. This bounds work per run, not money spent. Repeated automatic runs and retries can also incur cloud-provider charges. Local Ollama processing is limited to one effective generation request at a time; the configured target is preserved.

A failed card can receive two automatic retries, approximately 60 and 300 seconds after the respective failures. There are at most three automatic requests per material revision: the initial request and two retries. A request rejected by the local provider-pause guard is not sent and does not consume an attempt; a sent request that receives `429` does. The budget survives restarts and unchanged imports. Once exhausted, the card waits for your action. The retry panel in **Память** shows remaining automatic retries, cards needing manual action, and whether processing is enabled, disabled, paused, or waiting for the provider. **Повторить все ошибки** (Retry all errors) asks for confirmation, queues every eligible failed card and gives each a new initial attempt plus two retries. Ready cards are unchanged. With automatic cards disabled or synchronization paused, queued cards wait; this button changes neither automation settings nor the provider hold. A changed source material gets a new revision and a new budget.

### Qwen API and local Qwen

**Connection guide updated 2026-11-05.** Qwen API is Alibaba Cloud's hosted service. The preset uses the Singapore endpoint `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`; create an API key for the matching region in [Model Studio](https://modelstudio.console.alibabacloud.com/). A Qwen chat account is not a substitute for that API key. Other regional endpoints can be configured as a custom public HTTPS provider. Catalog access does not run generation or prove model availability for your account. Prices remain unknown when the catalog does not establish a usable currency or tier.

To create cards locally:

1. Install the current [Ollama for macOS](https://ollama.com/download/mac) and start it.
2. Download a model, for example `ollama pull qwen3:8b`. The [official model page](https://ollama.com/library/qwen3:8b) lists a roughly 5.2 GB download; working memory and context add to that size. Speed and memory use depend on the Mac and model.
3. In **Model and API**, select the local Ollama connection, refresh the catalog, select the exact downloaded model ID, and save. The token field is not required.
4. Start with a small manual card batch before enabling automatic processing. Existing cloud settings are not switched automatically.

Ollama and model weights are installed separately; the Second Brain ZIP does not include them. This connection uses only the fixed service at `127.0.0.1:11434`, downloaded models, and no cloud fallback. Cloud models and remote aliases are rejected; update Ollama if it cannot enforce the required local mode. Use a trusted local server and OS account. Source imports such as Notion, GitHub and mail still contact their own services.

Local card generation makes one model request at a time while preserving your configured concurrency value. It has no model-provider API charge, but uses your Mac's compute, memory and electricity. The same exact evidence validation, retry budgets, SQLite search and MCP memory apply. The local adapter requests JSON without thinking output, a 16,384-token context, up to 1,024 output tokens, and a bounded generation timeout. These settings are not a quality or speed benchmark.

### Provider pauses and card errors

On `429`, Second Brain stops admitting new card requests for the selected provider, API address, and model. Already admitted requests can finish. The pause survives app restarts. When available, `Retry-After` sets the waiting period; otherwise the delay is 60 seconds. A recognized exhausted-balance or API-key-budget response requires user action instead of repeated automatic requests. Imports, local search, and MCP remain available while this provider pause is active.

**Память** shows **Карточки ожидают провайдера** (Cards are waiting for the provider), the safe reason, and the next allowed retry time or account action. After fixing a balance or budget problem, use **Разрешить повтор** (Allow retry). This removes the provider pause; enabled automatic processing can resume. It does not reset exhausted record budgets or retry all historical errors. Use **Повторить все ошибки** separately after resolving their cause.

After a provider limit, effective concurrency starts at one and increases after successful requests toward your saved value. The configured concurrency is preserved, and the app shows the temporary reduction. A provider pause can delay a retry beyond its per-record retry time. Global synchronization pause remains a separate control.

Card diagnostics distinguish invalid response format or JSON, truncated output, model refusal, unsupported card structure, missing source quotations, and claims outside their quotations. The compact card prompt asks for fewer, shorter fields within the existing output budget. Exact evidence checks still apply; failed source text stays searchable. Diagnostics do not include raw provider responses, and no extra model call silently repairs a failed card.

## 5. Sources

Adding a source saves its configuration. An access check is not an import and does not mean all content is already in memory. Use the source's import button for the initial archive.

<a id="source-permissions"></a>

### Source permissions and exclusions — unreleased

In **Источники** (Sources), use the hand button next to a source to open
**Разрешения источника**. Five independent switches control new imports, search
and browsing in the app, MCP access, local model processing, and external model
processing. Turning off import leaves permitted cached search available; turning
off external processing keeps the local archive. A processing permission does not
start a model or enable an automatic queue.

Legacy sources without a saved policy start with all five permissions allowed,
preserving their previous behavior. The existing source-enabled setting and the
separate mail-import switch still apply: allowing import in this dialog does not
enable a paused mailbox or a disabled source. Existing automation settings and
model selection are preserved.

MCP permission is separate from local model permission. Codex or Claude can send
MCP results to its own cloud model even when Second Brain's external card
processing is disabled. All connected MCP clients use the same source policy;
there is no client-specific identity or permission check.

For session and local-note folders, enter relative file or directory paths, one
per line. A directory excludes its subtree. Absolute paths, `..` and wildcard
patterns are rejected; there are at most 100 paths of 2048 characters each. For
Apple Notes, select exclusions from the folder IDs already chosen for that source.
Other connectors retain their actual page, repository or Inbox scope controls.

After changing exclusions, select **Показать последствия исключений** before
saving. The preview counts affected cached materials and shows up to 20 examples;
it is not a forecast of future imports. Saved exclusions remove materials from
active app/MCP retrieval and block further import and model processing for them.
Original files and notes remain unchanged. Legacy Notes records without a folder
ID, and legacy local records whose path cannot be recovered, are hidden
conservatively when relevant exclusions exist until reimport supplies their scope.

Updated MCP processes observe saved policies without reconnecting. Restart older
server versions when upgrading: they cannot enforce a policy they do not
implement. A policy cannot recall text already returned to a client or sent to a
provider; a revoked model permission blocks later admission and result storage.
Excluded cached text can remain in SQLite pages and older backups; hiding it is
not forensic erasure.

### Codex and Claude Code

Select a session directory using the standard macOS folder picker. Common locations are `~/.codex/sessions` and `~/.claude/projects`; other explicitly selected directories work too. Supported JSONL records are imported. Parsers depend on client formats, so a client format change may produce warnings or skipped records.

The watcher checks for changes roughly every 30 seconds. A recognized completed-turn event can make a file eligible sooner; otherwise it waits about 90 seconds without writes. This is an end-of-turn heuristic, not proof that the entire conversation is finished. Later messages update the archive. An incomplete trailing line is deferred.

The aggregate 128 MiB cap has been removed for session archives. Limits still apply to individual raw JSONL files (64 MiB), individual lines (1 MiB), extracted text, parsing time, and directory traversal. Large logs are streamed and unchanged files use checkpoints. Warnings report limited or unsuccessful passes; removal of the aggregate cap does not mean every possible file can be imported without bounds.

### Qwen Code

Choose **Добавить источник → Qwen Code** and select `~/.qwen/projects`, or the actual `projects` directory when using `QWEN_HOME` / `QWEN_RUNTIME_DIR`. Only session JSONL files in `chats/` and `chats/archive/` are read; a selected `chats` directory also works. This is a local history source, separate from the Qwen API or Ollama model connection; no Qwen token is needed for import.

The importer keeps user and assistant text, skips system events, tool payloads, and thinking parts, and uses the same redaction, SQLite index, and card queue as other sources. Automatic import waits about 90 seconds without writes, checked roughly every 30 seconds. The source must be added, auto-import enabled, and synchronization running. Incomplete lines wait for a later pass. The reader follows the [official Qwen Code recording format](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/services/chatRecordingService.ts); future format changes may need an update.

### Notes directory

Markdown, TXT, and JSONL are supported. Scanning and import do not follow symlinks. A generic notes directory retains limits of 16 MiB per file, 128 MiB per pass, and bounded traversal. Later passes handle deferred files; individually oversized files are skipped with a warning. Select a dedicated directory with relevant notes rather than your entire home directory.

<a id="apple-notes-source"></a>

### Apple Notes — unreleased

**Source builds only; not included in the published `v0.1.0` ZIP.** Open Apple Notes first. In **Источники → Добавить источник → Apple Заметки**, press **Загрузить папки** (Load folders), then allow Second Brain to access Notes when macOS asks. Folder discovery starts only when you press that button. If access was denied, review **System Settings → Privacy & Security → Automation**, as described in [Apple's permission guide](https://support.apple.com/en-ie/guide/mac-help/mchl07817563/27/mac/27).

Select up to 50 folders and choose **Добавить источник**. Selection covers those exact folders; select child folders separately. The importer reads note text, titles, dates and stable note identifiers through Notes automation. It does not open the Notes database, require Full Disk Access, change notes or read attachments. Password-protected notes are skipped even if you have unlocked them in Notes.

Use the source's import button for the initial archive. With periodic source reconciliation enabled, later passes revisit the selected folders while Notes is open. Passes inspect at most 50 notes and preserve continuation; size/time limits and skipped records are reported. Closing Notes or revoking Automation access stops further reads until access is available again. Removing or protecting an original note does not automatically erase a previously imported archive.

### Notion

1. Create an internal Notion integration with **Read content** capability.
2. Grant it access to the desired pages through **Connections** in Notion.
3. Choose **Добавить источник → Notion** in Second Brain and enter the integration token.
4. Search for pages, load more results if needed, and explicitly select up to 100 pages.
5. Save the source and start its import.

Use the integration token, not your Notion account password. Newly granted access may take time to appear in search. Select child pages separately; the importer does not automatically crawl databases or the entire workspace. External attachments are not fetched. Incomplete, truncated, or oversized page responses do not replace a previously complete local copy.

### GitHub

In the **GitHub** form, enter a Personal Access Token, load repositories, and select up to 20. Markdown documentation, issues, and pull requests can be enabled independently. Filtering the loaded list preserves your selection; newly accessible repositories are not connected automatically.

For a fine-grained token, restrict access to the selected repositories: **Metadata: read**, **Contents: read** for documentation, **Issues: read** for issues, and **Pull requests: read** for PRs. An organization may require token approval.

Import covers Markdown files on the default branch and the titles, descriptions, states, and labels of open and closed issues/PRs. Comments, source code, diffs, submodules, and symlinks are excluded. Documentation citations point to commit snapshots. Large archives use bounded batches and durable SQLite progress; after a full pass, another cycle discovers edits to older materials. A remotely deleted object can remain in the local archive.

### Gmail and Yandex Mail

Both forms require the full email address and an **app password**, not the regular account password. Server, TLS, and port settings are automatic. Passwords are stored in Keychain. These Gmail/Yandex forms do not use OAuth in the published 0.1.0 release.

For Gmail, create a separate 16-character app password with 2-Step Verification enabled. Organization rules or Advanced Protection may make this unavailable. Spaces between Google's password groups are removed when saving. See [Google's app-password instructions](https://support.google.com/accounts/answer/185833?hl=en).

For Yandex, enable IMAP and app passwords in mail settings, then create a password of type **Mail / Почта**. The form links to password creation and [Yandex's official configuration guide](https://yandex.ru/support/yandex-360/customers/mail/ru/mail-clients/others).

Choose inbox import and the initial history window: 30 days by default, 90, 365, or all history. The date filter uses the server's received date. Only **INBOX / Входящие** is imported. Durable progress prioritizes new arrivals and then continues the initial archive.

Each pass handles at most 50 messages in bounded UID windows. Messages are not marked as read, moved, or deleted. Memory stores headers and text; HTML becomes readable text, and attachments are neither indexed nor sent to the model. Small messages arrive at the local application in full during reading. **Whole messages above 1 MiB, including attachment size, are currently skipped with a warning.** External images and links are not opened.

The access-check button counts inbox messages without reading their bodies. Each mailbox's enable/pause button retains its password, progress, and existing materials. Existing generic IMAP connections remain available, with content import off until explicitly enabled.

### Outlook / Microsoft 365 — unreleased

**Source builds only; not included in the published `v0.1.0` ZIP.** This connector imports cloud Outlook.com and Microsoft 365 mailboxes through Microsoft Graph. It does not read local Outlook/PST/OST files or an on-premises Exchange mailbox.

1. Register a public-client Microsoft application and obtain its **Application (client) ID** using the [bilingual setup guide](outlook-setup.md#english). No client secret is used.
2. Choose **Добавить источник → Outlook / Microsoft 365**, enter **Client ID приложения**, select whether to import Inbox and choose 30, 90, 365 days or all history.
3. Press **Войти через Microsoft**, then **Открыть страницу Microsoft**. Enter the displayed code on Microsoft's page, sign in and review consent there. Do not enter your Microsoft password in Second Brain or share the device code.
4. Check the returned account identity, then press **Добавить источник**. Cancel or start over if it is the wrong account.
5. Run import from the source row. Later reconciliation can continue the archive when enabled; a mailbox's import switch pauses that source.

The requested delegated permissions are `Mail.Read`, `User.Read` and `offline_access`. `Mail.Read` grants mailbox-wide reading at Microsoft; Second Brain limits its mail requests to **Inbox**. Sign-in does not grant sending, deletion or message-update permissions. Tokens are stored in Keychain after the source is added; the password is handled by Microsoft. Work policies can require administrator approval or block the flow; ask your IT team rather than bypassing those controls.

Each bounded pass reads up to 50 messages and saves its continuation. Bodies are converted to text; attachments and external images are not fetched. Import does not mark messages as read, move or delete them. Existing materials remain searchable when import is paused. Removing an email remotely does not automatically purge its archived copy. If authorization can no longer be renewed, reconnect the source; removing the source clears its local credentials and progress, while revoking consent at Microsoft is a separate action.

## 6. Automation, Dock, and menu bar

The app must be running for automatic import and card generation. Closing the main window leaves it in the Dock and menu bar. Fully quitting stops its owned local service and watching. Automatic launch at macOS login is not configured by this release.

The green icon means the local service is ready. It stays green while idle and while synchronization is paused. It does not certify that an external MCP client is connected or that every source check has passed.

The global stop button pauses automatic imports and automatic cards. Search, MCP, material viewing, and deliberate manual operations remain available. An already started request may finish. Pause survives restart. A mailbox's own pause affects only that mailbox's import.

When enabled, periodic reconciliation defaults to every 15 minutes. It updates generic folders and enabled remote imports, and can generate a bounded card batch when configured. The current native control calls this a full reconciliation of local sources, but it also services connected remote sources.

## 7. Search, cards, graph, and history

In **Память**, choose **Материалы** (Materials), **Карточки** (Cards), **Ожидают** (Pending), or **С ошибкой** (Errors) to filter the list. Search respects that filter. Scrolling loads pages of 30 materials. Background changes offer a refresh while keeping previously loaded pages until you refresh.

FTS5 builds the full-text index and BM25 ranks matches. This is keyword retrieval, not vector search or a model call. If a phrase is missed, try a project name, an exact term, an error message, or alternative wording. Open a result to inspect its text and card evidence.

**Граф и история** (Graph and history) opens a local browser page. The native settings screens do not use a WebView. The graph connects materials to their sources and shared card keywords, with search, filters, and zoom. Its snapshot includes at most 2000 materials and explicitly reports truncation; history is paginated. The app must remain running. History shows dates and current revisions, not a complete log of every older text revision.

<a id="mcp"></a>

## 8. Shared memory through MCP

MCP exposes four tools: `search_memory`, `get_record`, `get_context_brief`, and `get_sync_status`. This server reads local memory; it does not start imports, generate cards, or delete sources. Once separately launched by a client, MCP also works without the application window or application process.

In **Подключение MCP** (MCP connection), copy the Codex or Claude Desktop configuration into the corresponding client. This screen generates configuration; it does not automatically install a global connection or inspect another application's connection state. Confirm success inside the AI client.

For the prebuilt app, first move it to Applications and copy its generated
configuration from **MCP connection**. The command points to
`Second Brain.app/Contents/Resources/Backend/SecondBrainHelper`, the `mcp`
subcommand and an explicit data directory. No system Python is needed.

For a **source build**, if the CLIs are installed, run from the project root:

```sh
project_dir="$(pwd -P)"
codex mcp add second-brain -- "$project_dir/scripts/mcp-server.command" --data-dir "$project_dir/.data"
claude mcp add --scope user --transport stdio second-brain -- "$project_dir/scripts/mcp-server.command" --data-dir "$project_dir/.data"
```

These commands modify the clients' user settings and make this memory available across projects. Do not put model or source tokens in the MCP configuration: SQLite reading does not need them. Preserve other client configuration entries. Claude Code may request trust approval for a project-scoped server.

Reconnect MCP or restart the client after moving the project, upgrading source schemas, or updating the application. Restart already running Codex/Claude MCP clients after adding Apple Notes or Outlook: older server processes do not understand the new source kinds. Clients must use the same absolute `--data-dir` to share memory. The native app's loopback port is unrelated to stdio MCP.

For example, ask an agent: “Find in Second Brain why we chose this authentication approach, and show the original material.” A connected server does not guarantee a memory search on every message. Retrieved source text is untrusted data, not an instruction to execute.

## 9. Data, backup, and removal

The portable release stores data in `~/Library/Application Support/Second Brain`;
a development source build uses `.data` beside the source. Both contain `memory.sqlite3`, `settings.json`, `sources.json`, `memory_schedule.json`, and import checkpoints inside SQLite. Source names, paths, and account addresses can also be private. Secrets are separate in Keychain and are not transferred by copying either data directory.

The application does not encrypt SQLite. Known secret patterns are redacted before text is stored and sent to the model, but this does not guarantee removal of all personal or sensitive information. Enabling cards sends imported text to the selected model provider. Protect your disk and backups according to your threat model; see the [security policy](../SECURITY.md).

Use SQLite's backup API for a consistent database snapshot. Do not copy only an active `.sqlite3` file while ignoring its WAL. This optional example needs Python 3; Python is not required to install the prebuilt app. For a source build, change `source` to the checkout's `.data/memory.sqlite3`:

```sh
python3 - <<'PY'
import sqlite3
from datetime import datetime
from pathlib import Path

source = Path.home() / 'Library' / 'Application Support' / 'Second Brain' / 'memory.sqlite3'
target = Path.home() / 'Library' / 'Application Support' / 'Second Brain Backups'
target.mkdir(mode=0o700, parents=True, exist_ok=True)
target.chmod(0o700)
backup = target / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
    with sqlite3.connect(backup) as dst:
        src.backup(dst)
backup.chmod(0o600)
print(backup)
PY
```

For a matching database-and-settings set, quit the app before taking the snapshot and copying JSON: separate snapshots can disagree if sources are being edited. Keep the JSON settings in the same protected location. They connect records to sources; another Mac may require re-entering credentials. Before restoring, fully quit the app and MCP processes, back up the current state, and restore the database with its matching settings. Do not replace an active database.

Removing a source in the app removes its local materials from search/MCP, related cards, connection secret, and supported source progress. It does not delete original files, pages, repositories, or emails. Disappearance of a remote or local original usually does not remove an imported copy: the database is an archive. Application deletion is not a guarantee of forensic erasure from SQLite, WAL, or older backups.

## 10. Troubleshooting

| Symptom | Check |
| --- | --- |
| Text is searchable but no cards appear | Saved model/token, card-generation setting, global pause, and whether pending materials exist. |
| Catalog loads but generation fails | Model ID, API compatibility, generation permissions, and credits. Public catalogs do not validate tokens. |
| Error 429 / cards are waiting for the provider | Read the provider banner. A temporary limit waits for its delay; a recognized balance or key-budget limit requires fixing the account and choosing Allow retry. Lower concurrency alone does not fix an exhausted budget. |
| Card response or evidence error | Open the material and read the specific reason. Source text remains searchable; resolve the model/output issue before a deliberate retry. |
| Automatic retries have stopped | The three-attempt revision budget may be exhausted. Fix the cause and allow a manual attempt. |
| A new session has not appeared | Running app, correct source, session watching enabled; wait for a completed turn or write quiet time. |
| Notion cannot find a page | Connections access, Read content, and Notion indexing delay; search again. |
| GitHub access fails | Token repository selection, read permissions, organization approval, and repository renames. |
| Apple Notes folders do not load | Open Notes, use **Загрузить папки**, and check the Notes Automation permission. Password-protected notes are intentionally skipped. |
| Outlook sign-in fails | Check the registered Client ID, public-client configuration, consent and organization policy. See the [setup guide](outlook-setup.md#english). Start a new flow if the code expired. |
| Gmail/Yandex login fails | Full address, app password, provider settings, and organization restrictions. New Yandex passwords may not activate immediately. |
| Some emails are missing | INBOX only, selected date window, oversized-message warnings, and deferred work requiring more passes. |
| MCP sees no memory | Reconnect MCP; verify its generated command, shared data directory, client trust and imported content. Source builds also need their `.venv`. |
| App no longer starts after a move | For the portable release, restore the intact bundle and update MCP paths. Source builds also need the original checkout/`.venv` or a rebuild. Review macOS launch and folder permissions. |
| Graph does not open | The app's local service must be running; a standalone MCP process does not serve the graph page. |

For a public issue, provide version, macOS version, affected step, and a minimal synthetic reproduction. Do not attach `.data`, emails, sessions, Keychain contents, full HTTP responses, or screenshots containing private memory. Follow [SECURITY.md](../SECURITY.md) for vulnerabilities.
