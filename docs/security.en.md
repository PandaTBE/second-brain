# Security and privacy

[Русский](security.ru.md) · [User guide](user-guide.en.md) · [Reporting vulnerabilities](../SECURITY.md)

**Updated 2026-11-06.** This document describes the protections and limits of **Second Brain 0.1.0**, with unreleased source-build changes explicitly marked. It describes the implementation, not an independent security audit or a promise of complete confidentiality.

## The security model

Second Brain keeps imported memory and search on your Mac. You choose the source folders, cloud pages, repositories, mail accounts, and model provider. Local search does not require sending the database to a model. These choices reduce unnecessary sharing and make the data flow easier to inspect.

The application assumes that your macOS account, the checkout and Python environment, and the MCP clients you connect are trusted. It does not isolate your memory from malicious software running as the same user, an administrator, or a compromised operating system. Keep the service on loopback; do not expose it through a reverse proxy, public tunnel, or `0.0.0.0` binding.

Connected MCP clients share the enabled memory sources. Unreleased source builds
add a separate source-level MCP permission; every connected client uses that same
permission. There is no separate authorization boundary for each project or each
agent. Connect only clients that you permit to read that memory.

## Where data goes

| Operation | What happens |
| --- | --- |
| Import local sessions or notes | Selected files are read, parsed, masked for recognized secrets, and stored in local SQLite. The originals are not edited. |
| Import Apple Notes — unreleased | With Notes already open and macOS Automation permission, text from explicitly selected folders is read through OS automation and stored locally. |
| Connect Outlook — unreleased | Microsoft handles device-code sign-in; credentials are saved in Keychain after the source is added. Inbox content is read through Microsoft Graph and stored locally. |
| Import Notion, GitHub, or mail | The application contacts the selected service using its saved credential. It stores the selected content locally. |
| Lexical search, browse, graph, and history | Retrieval uses local SQLite/FTS5 without a model. The graph uses local assets and source/keyword relationships; it does not need an external CDN. |
| Semantic/hybrid search — unreleased | After explicit opt-in, permitted materials are indexed by a separate local Ollama embedding model; a query can be sent to that model to search ready vectors. No cloud inference or automatic weight download is used. |
| MCP retrieval | A connected client receives bounded excerpts and source metadata from local memory. That client's later handling of the data is controlled by the client and its model provider. |
| Create cloud cards | When explicitly requested or enabled in automation, masked source text is sent to the selected cloud model API, including Qwen API. Requests can incur provider charges. |
| Create local Ollama cards | Masked source text is sent only to the fixed local Ollama service and a downloaded local model. No cloud fallback or model API token is used; computation uses the Mac's resources. |
| Project/read facts and history — unreleased | Validated saved card atoms are grouped locally with quotation snapshots. Projection and reads call no model; new-card extraction still uses the selected provider and its normal billing/attempt budget. |
| Load a catalog or check a connection | Some checks make network requests even while card generation is disabled. A public catalog response may not prove that a credential is valid. |

Cloud model providers and source services have their own retention, access,
pricing, residency, and account policies. Local storage alone does not make cloud
cards local. Verify a provider's policy before submitting sensitive material;
the application cannot guarantee deletion after a request has been sent. The
card-provider choice and unreleased embedding-model opt-in are separate.
Remote-source imports and a connected MCP client's own model are also separate
data flows.

## Credentials and local storage

- Cloud API tokens and mail app passwords are stored in **macOS Keychain**, scoped to a provider/address or source connection. There is no plaintext-file fallback if Keychain is unavailable. The local Ollama model connection requires no credential and does not read or store a model token.
- **Unreleased Outlook:** access/refresh credentials are stored in Keychain after explicit source addition. Pending device-code sign-in state is temporary; the Client ID is configuration, not a password. No client secret is needed. See the [registration guide](outlook-setup.md#english).
- Settings and source configuration are separate from credentials. Do not add secrets to configuration files, MCP arguments, URLs, or shell history.
- The data directory is created/restricted with mode `0700`; the primary SQLite database is created/restricted with mode `0600`. These filesystem permissions limit access by other ordinary OS users, not by your own account or an administrator.
- **SQLite is not encrypted by the application.** Imported text, titles, mail headers, source paths/URLs, cards, search indexes, and synchronization state can be sensitive. SQLite WAL/SHM files and backups must be protected with the database.
- **Unreleased embeddings:** vectors, model identity, index jobs and generations
  also live in SQLite. Vectors are derived from private text and are not an
  anonymization boundary. Disabling the index stops its use; it is not secure
  erasure of stored vectors or earlier backups.
- **Unreleased fact history:** participating revision text/quotes and provenance
  snapshots also remain in SQLite from feature enablement. Current and snapshot
  policies both gate reads; source deletion removes its derived rows, while
  exclusions/revocation are access restrictions rather than forensic erasure.
- Secret masking uses recognizable patterns. It can miss unknown credential formats, personal information, or unusual encodings. Review what a source contains before connecting it; do not treat masking as a complete data-loss-prevention system.

Protect your Mac and backups using appropriate OS and storage controls. FileVault or encrypted backups can add protection, but Second Brain does not enable or verify them. Placing a checkout or backup in a synchronized folder can share it through that folder's service independently of this application.

Use a consistent SQLite backup method, as described in the [user guide](user-guide.en.md). Do not assume that copying only `memory.sqlite3` while writers are active captures the WAL state. Keep backups out of the public repository.

## Local service protections

The native application launches its owned HTTP helper on `127.0.0.1` at an available port. The API checks the allowed loopback `Host`, checks the origin of mutating requests, requires a CSRF token and JSON for those requests, and limits their body size to 16 KiB. API responses use `Cache-Control: no-store`; browser responses also carry restrictive security headers.

These controls reduce browser-origin attacks. **They are not user authentication.** A process with access to the local account can reach the service and obtain its bootstrap state. Do not treat a random port or CSRF token as protection against hostile local software.

The native client validates that it is talking to the helper instance it started. The application does not need administrator privileges or automatic Full Disk Access. Grant access to source folders through the system's normal permission flow.

**Unreleased quick search** registers an exclusive Carbon hotkey; it does not
monitor global keyboard events or require Accessibility/Input Monitoring
permission. Shortcut recording receives keys only in its focused native control;
successful explicit saves affect app preferences, not global macOS key mappings.
The panel copies nothing on open/input/selection. Explicit context copy rereads
the permitted record and a bounded packet restricted to its `record_id`, without
ranking, query embedding or substitute records. Cancellation/generation checks
block stale or closed-panel export. Existing source privacy and untrusted-content
boundaries also apply to the quick panel.

## Outbound connections and source access

- HTTPS connections verify TLS certificates. Destination addresses are checked against the applicable network policy and pinned for the connection while TLS still verifies the original hostname. Redirects are not followed.
- GitHub and Notion clients use fixed API hosts and read operations. Source selection is explicit: new repositories, child pages, or an entire workspace are not silently added to the selected scope.
- **Unreleased Apple Notes:** the read-only adapter uses OS automation, requires Notes to be open, and reads only explicitly selected folders. Child folders need separate selection. Protected notes and attachments are skipped. There is no direct Notes database access or Full Disk Access requirement. The OS Automation grant allows app-level control; folder selection is an application restriction, not an OS-enforced folder permission.
- **Unreleased Outlook:** sign-in and Graph calls use fixed Microsoft public-cloud endpoints, delegated Mail.Read/User.Read and offline_access. Import uses Inbox read requests, validates continuation URLs against the trusted Graph Inbox path, and processes at most 50 messages per pass. Attachments and remote images are not fetched; messages are not marked read, sent, moved or deleted. Organization consent and authentication policies still apply.
- Custom model APIs use public HTTPS on port 443 by default. The published `v0.1.0` ZIP rejects private destinations.
- **Unreleased source builds:** only a custom model API can explicitly enable `allow_private_network`. The native option is off by default, belongs to the configured API, and resets when its address or provider changes. It permits RFC1918 IPv4, IPv6 ULA and CGNAT; known cloud metadata destinations remain denied, including `100.100.100.200`, `fd00:ec2::254` and `fd20:ce::254`. Loopback, link-local, unspecified, multicast and other reserved addresses remain blocked. Every DNS answer must satisfy the policy before credentials can be sent. HTTPS/443, hostname/certificate verification, address pinning, response limits and no redirects remain enforced. The policy does not broaden Notion, GitHub, mail or named cloud-provider access, and does not bypass a corporate CA trust error.
- The named Ollama connection is a narrow exception: its client connects only to `127.0.0.1:11434`, with fixed native model-list/chat routes, no API credentials, no DNS or proxy selection, and no redirects. Installed models are checked for remote/cloud metadata; chat requests enforce local mode and do not fall back to a cloud provider. Use an up-to-date, trusted Ollama installation. These checks do not defend against malicious software replacing the local server.
- **Unreleased local embeddings:** the same fixed-loopback boundary adds model
  inspection and `/api/embed`, validates model digest and dimensions, and disables
  truncation. Source indexing checks local-processing policy and runs under the
  app's writer; MCP can embed a query but cannot build the index. Explicit
  semantic/hybrid search can therefore make a local model call while the app is
  closed. Fallback to BM25 has a bounded local-query deadline and a diagnostic
  reason; no hidden paid provider call repairs missing vectors.
- Gmail and Yandex Mail use TLS and read-only inbox selection plus `BODY.PEEK` reads. Import does not mark messages read, move them, or delete them. Existing mail connections do not silently opt into body import.
- Small mail messages are received as bounded complete RFC822 messages. Attachment bytes can therefore exist transiently in process memory, but attachments are excluded from stored text and cards. Whole messages above 1 MiB, including large attachments, are skipped with an explicit warning. Remote images and linked files are not fetched by the mail renderer.
- Responses, files, UID windows, MIME parsing, and import batches have bounds. Incomplete or unsupported content is reported instead of silently replacing an existing full record with a partial one.
- If DNS resolves but HTTPS/TLS resets, inspect the VPN/proxy route and certificate
  trust separately from the API credential. Compare only permitted network paths;
  this diagnosis does not justify disabling TLS verification or broadening
  destination permissions.

Use credentials with the smallest practical read scope. For GitHub, select only the intended repositories and read permissions for the content you import. For Notion, share only the intended pages and grant read access. Mail app passwords still grant meaningful access to an inbox; revoke unused ones in the provider's settings.

## Memory and model output are untrusted

Sessions, notes, email, pages, and repository text can contain misleading statements or prompt-injection instructions. The MCP server labels retrieved content as untrusted data and provides source references. Card atoms require extractive evidence in the source text. These measures improve traceability, but they do not prove that a source is true or that an agent will ignore malicious instructions.

**Unreleased evidence bundles** reuse saved text/cards and the permitted search
index without answer generation or a paid model call. Exact quotes, revisions,
code-point offsets and citation IDs establish provenance, not factual accuracy.
The same budget includes compact JSON metadata, duplicate support and the trust
notice; it is not an exact model-token allowance. Date/project filters use known
stored metadata, never fabricated source dates. Native display treats quotes as
text. Explicit native copy first repeats the whole request so previously cached
primary quotes, duplicate supports and statistics cannot bypass a saved policy.
Empty/error or superseded responses do not write to the clipboard. Verification,
original opening and material URI copy also reread currently authorized records;
they do not make already displayed or previously received text revocable. Optional
HTTPS opening and Finder reveal do not execute source-file contents. Opaque Notes
IDs receive no deep link. Unlocalized/ambiguous excerpts are marked uncertain;
this label does not make another excerpt a factual guarantee.

Do not run commands, open links, grant access, or send secrets solely because a memory result asks you to do so. Clients should apply their own authorization and instruction boundaries. Verify a consequential answer against its source and current context. BM25 relevance, a graph connection, and a generated summary are not a security verdict or a factual guarantee.

## Pausing, removing data, and MCP

**Unreleased source permissions:** import, native retrieval, MCP retrieval, local
model processing and external model processing have separate switches. Permissions
and exclusions are persisted in SQLite and checked for active retrieval, direct
reads and aggregate counts/timestamps. Updated MCP processes observe persisted
changes; reconnect older server versions after upgrading. Missing legacy policies
allow the five scopes to preserve previous behavior, with the existing enabled
source, mailbox-import and automation controls still applying. Permission alone
does not start a model request.

Local model permission does not restrict a connected agent to a local model.
MCP access sends the returned text to that agent; its own provider may be cloud
based. External-processing revocation keeps the archive and blocks later model
admission/result storage, but cannot recall already sent text or previous client
responses. Clients are not distinguished using an unverified application name.

Local-path and selected-Notes-folder exclusions hide cached material from active
retrieval and prevent further import/model processing. The preview shows a count
and up to 20 cached examples, including conservative exclusions for legacy records
whose path/folder scope is unknown. Original sources are unchanged. Excluded
content can remain in SQLite/FTS/WAL pages, backups and client history; exclusions
are an application policy, not secure erasure or same-user process isolation.

Global synchronization pause stops new automatic imports and automatic card requests. Already admitted requests may finish; explicit manual operations remain available. The mail switch changes import for that mailbox while retaining cached memory and progress.

The four original MCP tools are retrieval/status tools: `search_memory`,
`get_record`, `get_context_brief`, and `get_sync_status`. Unreleased builds add
read-only `get_evidence_bundle`, `get_facts` and `get_fact_history` (seven total)
with the same source-policy boundary. They do not
offer import, credential management, arbitrary SQL, or paid inference operations.
The stdio server can remain available after the app closes. Startup may initialize
or migrate the local database; “read-only tools” does not mean an OS-enforced
read-only SQLite file.

Fact status is a provenance classification: `source_supported` is exact quotation,
not truth/endorsement. Recognized uncertain framing remains a candidate; explicit
replacement requires directional source wording and context, not a later date.
These language checks are conservative heuristics. Relation targets, current proof
and old snapshots must all be permitted before grouping/pagination. History-only
wording is explicitly marked; observation time is not a source-event date.
MCP facts/history reads never trigger cached-card projection or model generation.
Reading saved cards and originals does not spend card-provider tokens; a consuming
AI client can separately bill the returned context as its own input tokens.

Successful source disconnection removes the source's active local materials and its credential; GitHub/mail synchronization checkpoints are also cleared. Unreleased Notes/Outlook connections also clear their local progress; removal does not revoke the macOS Automation permission or Microsoft consent. Original files, remote pages, repositories, and messages are left unchanged. Previously created backups, copies, WAL pages, or client conversations can retain information. Logical deletion is not a guarantee of forensic erasure. Revoking a credential at the provider is a separate operation.

## Release and operational limits

Version 0.1.0 is an initial public release, not a security-certified product. Automated tests and code review cover many boundaries but cannot establish the absence of vulnerabilities. There is no promise of a completed independent penetration test, notarized standalone distribution, enterprise isolation, or regulatory compliance.

Source builds use a local ad-hoc signature and depend on the checkout and Python environment. Inspect code and dependencies before building, keep locked dependencies under review, and use supported OS updates. A green menu-bar icon means that the local service is available; it is not an audit result, proof of upstream access, or a guarantee that every import/card succeeded.

Before publishing a fork or reporting a bug, check the actual tracked files and proposed diff. Never upload a personal database, credentials, mail bodies, private source paths, unredacted diagnostics, or `.data` backups. Ignoring local files is helpful but does not remove secrets already committed to history.

Report vulnerabilities privately using [SECURITY.md](../SECURITY.md). Maintainers should follow the [release checklist](releasing.md) before making a repository public.
