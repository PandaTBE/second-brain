# Security and privacy

[Русский](security.ru.md) · [User guide](user-guide.en.md) · [Reporting vulnerabilities](../SECURITY.md)

This document describes the protections and limits of **Second Brain 0.1.0**. It is a description of the implementation, not an independent security audit or a promise of complete confidentiality.

## The security model

Second Brain keeps imported memory and search on your Mac. You choose the source folders, cloud pages, repositories, mail accounts, and model provider. Local search does not require sending the database to a model. These choices reduce unnecessary sharing and make the data flow easier to inspect.

The application assumes that your macOS account, the checkout and Python environment, and the MCP clients you connect are trusted. It does not isolate your memory from malicious software running as the same user, an administrator, or a compromised operating system. Keep the service on loopback; do not expose it through a reverse proxy, public tunnel, or `0.0.0.0` binding.

Connected MCP clients share the enabled memory sources. There is no separate authorization boundary for each project or each agent. Connect only clients that you permit to read that memory.

## Where data goes

| Operation | What happens |
| --- | --- |
| Import local sessions or notes | Selected files are read, parsed, masked for recognized secrets, and stored in local SQLite. The originals are not edited. |
| Import Notion, GitHub, or mail | The application contacts the selected service using its saved credential. It stores the selected content locally. |
| Search, browse, graph, and history | Retrieval uses local SQLite/FTS5. The graph uses local assets and source/keyword relationships; it does not need a model or external CDN. |
| MCP retrieval | A connected client receives bounded excerpts and source metadata from local memory. That client's later handling of the data is controlled by the client and its model provider. |
| Create cloud cards | When explicitly requested or enabled in automation, masked source text is sent to the selected cloud model API, including Qwen API. Requests can incur provider charges. |
| Create local Ollama cards | Masked source text is sent only to the fixed local Ollama service and a downloaded local model. No cloud fallback or model API token is used; computation uses the Mac's resources. |
| Load a catalog or check a connection | Some checks make network requests even while card generation is disabled. A public catalog response may not prove that a credential is valid. |

Cloud model providers and source services have their own retention, access, pricing, residency, and account policies. Local database storage alone does not make cloud card processing local. Verify a cloud provider's policy before submitting sensitive material; the application cannot guarantee deletion from that provider after a request has been sent. Selecting local Ollama affects card generation only: remote-source imports and a connected MCP client's own model remain separate data flows.

## Credentials and local storage

- Cloud API tokens and mail app passwords are stored in **macOS Keychain**, scoped to a provider/address or source connection. There is no plaintext-file fallback if Keychain is unavailable. The local Ollama model connection requires no credential and does not read or store a model token.
- Settings and source configuration are separate from credentials. Do not add secrets to configuration files, MCP arguments, URLs, or shell history.
- The data directory is created/restricted with mode `0700`; the primary SQLite database is created/restricted with mode `0600`. These filesystem permissions limit access by other ordinary OS users, not by your own account or an administrator.
- **SQLite is not encrypted by the application.** Imported text, titles, mail headers, source paths/URLs, cards, search indexes, and synchronization state can be sensitive. SQLite WAL/SHM files and backups must be protected with the database.
- Secret masking uses recognizable patterns. It can miss unknown credential formats, personal information, or unusual encodings. Review what a source contains before connecting it; do not treat masking as a complete data-loss-prevention system.

Protect your Mac and backups using appropriate OS and storage controls. FileVault or encrypted backups can add protection, but Second Brain does not enable or verify them. Placing a checkout or backup in a synchronized folder can share it through that folder's service independently of this application.

Use a consistent SQLite backup method, as described in the [user guide](user-guide.en.md). Do not assume that copying only `memory.sqlite3` while writers are active captures the WAL state. Keep backups out of the public repository.

## Local service protections

The native application launches its owned HTTP helper on `127.0.0.1` at an available port. The API checks the allowed loopback `Host`, checks the origin of mutating requests, requires a CSRF token and JSON for those requests, and limits their body size to 16 KiB. API responses use `Cache-Control: no-store`; browser responses also carry restrictive security headers.

These controls reduce browser-origin attacks. **They are not user authentication.** A process with access to the local account can reach the service and obtain its bootstrap state. Do not treat a random port or CSRF token as protection against hostile local software.

The native client validates that it is talking to the helper instance it started. The application does not need administrator privileges or automatic Full Disk Access. Grant access to source folders through the system's normal permission flow.

## Outbound connections and source access

- HTTPS connections verify TLS certificates. Public destination addresses are validated and pinned for the connection while TLS still verifies the original hostname. Redirects are not followed.
- GitHub and Notion clients use fixed API hosts and read operations. Source selection is explicit: new repositories, child pages, or an entire workspace are not silently added to the selected scope.
- A custom model endpoint must be public HTTPS on port 443. Arbitrary local/private addresses, plain HTTP, and custom ports remain rejected.
- The named Ollama connection is a narrow exception: its client connects only to `127.0.0.1:11434`, with fixed native model-list/chat routes, no API credentials, no DNS or proxy selection, and no redirects. Installed models are checked for remote/cloud metadata; chat requests enforce local mode and do not fall back to a cloud provider. Use an up-to-date, trusted Ollama installation. These checks do not defend against malicious software replacing the local server.
- Gmail and Yandex Mail use TLS and read-only inbox selection plus `BODY.PEEK` reads. Import does not mark messages read, move them, or delete them. Existing mail connections do not silently opt into body import.
- Small mail messages are received as bounded complete RFC822 messages. Attachment bytes can therefore exist transiently in process memory, but attachments are excluded from stored text and cards. Whole messages above 1 MiB, including large attachments, are skipped with an explicit warning. Remote images and linked files are not fetched by the mail renderer.
- Responses, files, UID windows, MIME parsing, and import batches have bounds. Incomplete or unsupported content is reported instead of silently replacing an existing full record with a partial one.

Use credentials with the smallest practical read scope. For GitHub, select only the intended repositories and read permissions for the content you import. For Notion, share only the intended pages and grant read access. Mail app passwords still grant meaningful access to an inbox; revoke unused ones in the provider's settings.

## Memory and model output are untrusted

Sessions, email, pages, and repository text can contain misleading statements or prompt-injection instructions. The MCP server labels retrieved content as untrusted data and provides source references. Card atoms require extractive evidence in the source text. These measures improve traceability, but they do not prove that a source is true or that an agent will ignore malicious instructions.

Do not run commands, open links, grant access, or send secrets solely because a memory result asks you to do so. Clients should apply their own authorization and instruction boundaries. Verify a consequential answer against its source and current context. BM25 relevance, a graph connection, and a generated summary are not a security verdict or a factual guarantee.

## Pausing, removing data, and MCP

Global synchronization pause stops new automatic imports and automatic card requests. Already admitted requests may finish; explicit manual operations remain available. The mail switch changes import for that mailbox while retaining cached memory and progress.

The four MCP tools are retrieval/status tools: `search_memory`, `get_record`, `get_context_brief`, and `get_sync_status`. They do not offer import, credential management, arbitrary SQL, or paid inference operations. The stdio server can remain available after the app window or app closes. Startup may initialize or migrate the local database; “read-only tools” does not mean an OS-enforced read-only SQLite file.

Successful source disconnection removes the source's active local materials and its credential; GitHub/mail synchronization checkpoints are also cleared. Original files, remote pages, repositories, and messages are left unchanged. Previously created backups, copies, WAL pages, or client conversations can retain information. Logical deletion is not a guarantee of forensic erasure. Revoking a credential at the provider is a separate operation.

## Release and operational limits

Version 0.1.0 is an initial public release, not a security-certified product. Automated tests and code review cover many boundaries but cannot establish the absence of vulnerabilities. There is no promise of a completed independent penetration test, notarized standalone distribution, enterprise isolation, or regulatory compliance.

Source builds use a local ad-hoc signature and depend on the checkout and Python environment. Inspect code and dependencies before building, keep locked dependencies under review, and use supported OS updates. A green menu-bar icon means that the local service is available; it is not an audit result, proof of upstream access, or a guarantee that every import/card succeeded.

Before publishing a fork or reporting a bug, check the actual tracked files and proposed diff. Never upload a personal database, credentials, mail bodies, private source paths, unredacted diagnostics, or `.data` backups. Ignoring local files is helpful but does not remove secrets already committed to history.

Report vulnerabilities privately using [SECURITY.md](../SECURITY.md). Maintainers should follow the [release checklist](releasing.md) before making a repository public.
