# Retrieval and evidence contracts

Contracts updated 2026-11-06; Stage 0 implementation baseline `7fe1f49`.
Source policies, local retrieval, evidence, fact/history views and backup/recovery
are included in 0.2.0. The [development plan](agent-memory-plan.ru.md) retains the
historical implementation and verification record.

## Existing compatibility boundary

The following stdio MCP names and required arguments remain stable:

| Tool | Required arguments | Existing response fields |
| --- | --- | --- |
| `search_memory` | `query` | `results`, `untrusted_content`, `notice` |
| `get_record` | `record_id` | `record`, `offset`, `total_chars`, `next_offset`, `untrusted_content`, `notice` |
| `get_context_brief` | none | `records`, `untrusted_content`, `notice`, `message` |
| `get_sync_status` | none | `memory`, `mcp_read_only`, `message` |

Version 0.2.0 adds three read-only tools, for seven total:
`get_evidence_bundle` (required `query`), `get_facts` (no required argument) and
`get_fact_history` (required `fact_id`). They do not change the original four
required-argument contracts or project cards during a read.

`search_memory` keeps optional `limit=8` (1–20) and `source_id=None`, and adds
optional `mode="lexical"` in 0.2.0.
The query must contain 1–512 characters. A result has the existing `id`, `title`,
`source_id`, `source_kind`, `source_uri`, `updated_at`, `revision`, `score`, and
bounded `snippet`, where available. `summary` may also be present. Metadata bounds
are defined in `mcp_server._metadata`; errors keep the existing error envelope.
`get_record` keeps optional `offset=0`, `max_chars=6000`; the current response caps
one text page at 12,000 characters. The brief remains callable without a query and
returns at most eight records. Additions must be optional and additive.

The HTTP application and stdio MCP must call the same retrieval implementation.
MCP reads may run with the application closed. Reads never create card jobs or
invoke a paid provider. A local query embedding is allowed only after explicit
local embedding opt-in; it is not an implicit card-model call.

## Implemented source-policy boundary — Stage 1

`source_policies` and `source_exclusions` in SQLite are authoritative for five
independent scopes: `import_enabled`, `local_search_enabled`, `mcp_enabled`,
`local_model_enabled`, and `external_model_enabled`. A missing policy defaults to
all five allowed, preserving previous behavior; existing source `enabled`,
mail-import and automation gates still apply. The migration does not start jobs
or enable an additional paid operation.

Policy predicates apply before ranking and to direct reads, briefs, graph/history
and scoped aggregate statistics/timestamps. Updated long-lived MCP processes use
persisted policies without retaining a startup snapshot. A revocation cannot
recall earlier responses. Model permission is checked at admission and again
inside result persistence; local permission is distinct from giving text to an
agent through MCP.

The HTTP source API exposes `GET`/`PUT /api/sources/{source_id}/policy` and
`POST /api/sources/{source_id}/policy/preview`. These are application settings,
not new MCP tools. Local exclusions are relative literal paths/subtrees;
Notes exclusions must belong to the source's selected folders. Other connectors
retain their supported scope controls. Preview returns `affected_documents`,
up to 20 `samples`, and `sample_limit`; it describes the current cache and does
not guarantee the number of future imports. Unknown legacy path/folder scope
is hidden conservatively when exclusions require checking it.

Exclusions prevent active retrieval and processing while preserving the original
source. Cached text may remain physically in SQLite, FTS/WAL pages or backups.
Old MCP binaries must be restarted when upgrading; this implementation does not
retrofit enforcement into already running older code.

## Implemented local retrieval — Stage 2

Optional `mode` accepts `lexical` (default), `semantic`, or `hybrid`; HTTP
`GET /api/memory/search` and stdio MCP use `Retrieval`. Lexical retrieval remains
independent of the vector index and makes no model call. After explicit embedding
opt-in, semantic/hybrid can call the fixed-loopback Ollama adapter to embed a
query, including from a standalone MCP process. Query work has a three-second
deadline and no paid/cloud fallback. MCP does not own index writes.

The response adds a `search` object: `requested_mode`, `used_mode`,
`fallback_reason`, `generation`, `query_embedding_ms`, `vector_search_ms`,
`eligible`, `ready`, and `partial`. A disabled index, no ready vectors or a model
error returns lexical results with a diagnostic reason. A partial semantic index
searches ready vectors only; hybrid also retains keyword candidates from all
permitted documents. Coverage is scoped to the requested source/audience, so the
response does not claim complete semantic coverage of an unfinished index.

BM25 and semantic candidates are retrieved independently across all permitted
documents before rank fusion. Hybrid uses equal-weight RRF with `k=60`; BM25 and
cosine scores are not added together. RRF ranks, BM25 scores, cosine similarity, and
freshness factors are ranking signals, never probabilities that a statement is
true. The same policy is checked before candidate selection and before response
formation. Empty permissions produce an empty result, not an unrestricted search.

A vector belongs to a document ID, revision and content hash, with model
ID/digest, dimension and preparation version identified by its generation.
Changing that identity creates a new generation. Dimension or digest mismatch
is an error; incompatible vectors are not compared. A text update, deletion,
source exclusion, or relevant processing-policy change blocks stale/in-flight
writes. Only the app's single background writer indexes materials, respecting
the global pause and local-processing policy. It processes bounded batches, keeps
job errors/retries and continues after restart. Model selection is separate from
cards, starts disabled, probes an installed model using synthetic text, and never
downloads weights.

After bounded embedding retries are exhausted, a successful explicit capability
reprobe/resave of the same model identity deletes only failed jobs in the current
generation, making them pending again. Ready vectors and generation remain;
a failed reprobe does not reset errors. This is separate from paid card retries.

The implementation stores normalized float32 vectors in SQLite, performs exact
scans in batches of 128 rows and keeps a bounded top-candidate heap. It loads no
SQLite extension or separate vector server. Dimensions are limited to 1–4096.
Prepared input is `title + "\n" + text`; more than 12,000 characters or 100,000
UTF-8 bytes produces an explicit indexing error. The adapter uses
`truncate: false`; oversized materials keep their lexical representation.

Embedding settings use `GET`/`POST /api/memory/embeddings`, installed-model lookup
uses `GET /api/memory/embeddings/models`, and
`POST /api/memory/embeddings/index` requests one writer-owned batch of 1–16
materials (default 8). These mutations remain outside MCP. Native semantic/hybrid
search returns at most 20 ranked materials; status-filtered pagination remains
lexical.

## Implemented evidence bundle — Stage 3

Read-only MCP `get_evidence_bundle` requires `query` (1–512 characters) and accepts
`source_id=None`, `project=None`, `date_from=None`, `date_to=None`,
`budget_chars=12000`, `mode="hybrid"`, and optional `record_id=None`. The shared `EvidenceService` also backs
`GET /api/memory/evidence`, where the query parameter is `q`; other parameter names
are identical. Native HTTP retrieval uses local-search permission; MCP uses MCP
permission. The four original tools remain compatible, including a query-free
`get_context_brief()`.

selected-record context uses `record_id` (a nonblank valid-Unicode ID
of 1–128 characters, without control characters). It directly reads only that
current authorized material before any ranking or query embedding, then applies
the same source/project/date filters and final revision/policy snapshot. The
required `query` remains; it helps choose a passage but need not lexically match
the selected record. No other record or duplicate support is substituted when the
selection is missing, hidden, excluded or filtered out.

Selected responses add `filters.record_id`, `search.selection="record_id"` and
`search.used_mode="record"`; `requested_mode` retains the caller's valid
`lexical`/`semantic`/`hybrid` choice. `record` is a diagnostic, not a new accepted
request mode. Query/vector timings and generation are null because this branch
does not invoke embeddings. Coverage counts are restricted to the selected record,
not other materials. An unlocalized source excerpt remains uncertain/incomplete;
partial vector coverage alone does not make a direct record read a semantic-index
failure. The API default of 12,000 characters and range 2,000–32,000 are unchanged;
the quick-panel copy requests 6,000 characters explicitly.

`SearchFilters` applies before candidate retrieval/ranking in BM25 and vector
paths. `project` exactly matches bounded explicit stored metadata (up to 256
characters); it does not guess from titles, source file locations or a filesystem
checkout. Session `cwd`/`project` metadata is carried through parsing/chunking;
unknown project values are null and fail a project filter. Date precedence is
`message_at`, then `source_modified_at`, then `source_created_at`; `imported_at`
never substitutes for a source date. Missing dates fail a date filter. Bounds use
UTC; date-only `date_to` includes the whole named day through an exclusive next-day
boundary, while timezone-aware timestamp bounds include their specified instant.
The response publishes this choice in `filters.date_basis`.

Each primary citation exposes:

| Field | Meaning |
| --- | --- |
| `citation_id` | Deterministic `sb_` ID for record ID, revision, exact quote and offsets |
| `record_id`, `source_id`, `source_kind` | Stable provenance, not a display title |
| `revision` | Current revision against which quote validation succeeded |
| `title` | Source title bounded to 256 characters |
| `source_uri`, `source_object_id`, `project` | Imported location/identifier and explicit nullable project |
| `dates` | Nullable `message_at`, `source_created_at`, `source_modified_at`, `imported_at` |
| `quote` | Exact saved-text substring, at most 420 characters |
| `start_char`, `end_char` | Half-open Unicode code-point offsets in saved normalized text |
| `context_before`, `context_after` | Exact surrounding text, at most 100 characters per side |
| `quote_selection`, `quote_relevance` | Extractive selection method and any uncertainty |
| `also_supported_by` | Optional bounded provenance/offsets for identical quotes |

The invariant is `saved_text[start_char:end_char] == quote`, with valid nonempty
ranges at the stated revision. Offsets are neither UTF-8 bytes nor UTF-16 units.
Changing record ID, revision, offsets or quote changes the deterministic ID.
An exact quote establishes source provenance, not the truth of its contents or
whether it completely answers the query. Ambiguous saved-card evidence is marked
`quote_relevance="uncertain"` with reason `ambiguous_card_evidence`; a source
excerpt without localized query evidence is likewise uncertain with reason
`unlocalized_evidence`. An excerpt can therefore be exact while its relevance is
unproven. The native view explains these cases in plain Russian rather than
showing internal reason codes; copied JSON retains the original fields.

The successful packet contains `schema_version=1`, `query`, `citations`, `search`,
`filters`, `budget_chars`, `used_chars`, `budget_unit`, `incomplete`, `reasons`,
`untrusted_content=true`, and `notice`. The accepted budget is 2,000–32,000
characters of canonical compact JSON (`ensure_ascii=False`, separators `,`/`:`),
including every packet field and metadata. `budget_unit` is
`canonical_json_unicode_characters`; it is not tokens, UTF-8 bytes, pretty-printed
JSON size or the enclosing HTTP/MCP transport. Metadata that cannot fit causes an
explicit budget error rather than silent quote/provenance alteration.

At most 50 retrieved material candidates feed at most 20 primary citations.
Source rotation spreads the available budget. Exact quote text is deduplicated,
retaining up to four additional supports per primary citation when they fit.
Support/citation/context limits, candidate truncation, search fallback, partial
index, changed sources and unusable evidence are reported through `reasons` and
`incomplete`; support metadata may be dropped to fit with a stated reason. Empty
search produces an empty citation list with no invented answer. Different quote
text remains distinct; this packet does not infer resolution of conflicting claims.

Revision, text slice, metadata and current source policy are checked again in a
single read snapshot before packaging, including current scoped coverage. A
long-lived MCP process observes persisted changes. A packet already returned or
copied is a snapshot; it cannot recall an earlier disclosure and may need refresh.
Every native copy repeats the complete `EvidenceRequest` first, refreshing
citations, `also_supported_by` and aggregate counts under current policy instead
of exporting a cached packet. An empty result or request error cancels export.
Request identity/parameters and the active sheet are checked after suspension, so
a later response cannot export for an old request or closed sheet. Verification
and original opening also refresh the packet, then reread a currently authorized
record and check ID, source kind, URI, revision, quote and context using Unicode
scalars. Material-detail source-URI copy similarly rereads its currently selected
record. None of these checks revoke text already rendered, returned or copied.

Date migration leaves unknown legacy fields null. `imported_at` records saving
the current text revision; an unchanged reimport can fill explicit source
dates/project without increasing the revision or inventing an old import time.
Connector-specific date semantics are documented in
[architecture](architecture.en.md#persistence-model). Existing freshness timestamps
are not evidence of source/message time.

Imported text, titles, URIs and quotes remain untrusted data. Extraction reuses
saved text/cards and the chosen retrieval mode, without card generation or paid
inference; semantic/hybrid may invoke explicitly enabled local query embeddings.
The agent writes its own answer and cites `citation_id`s. Native copy is an
explicit compact JSON operation preserving trust and additional-support metadata.
Checked HTTPS URIs open in the browser; local file URIs are revealed in Finder
(recognized line fragments identify the file, not an executable action). Opaque
Notes IDs are displayed without invented deep links.

## Implemented facts and history — Stage 4

The additional tools are:

| Tool | Parameters | Shared HTTP view |
| --- | --- | --- |
| `get_facts` | `query=""`, `limit=20`, `source_id=None`, `include_history=False` | `GET /api/memory/facts` (`q` for query) |
| `get_fact_history` | required `fact_id`, `limit=50`, `before=None` | `GET /api/memory/facts/{fact_id}/history` |

Fact queries allow 0–512 characters and use a case-folded literal substring match
on grouped wording, not semantic inference. Both limits accept 1–100; native
requests use 20 groups and 50 history events. An empty allowed-source set remains
empty. Current/snapshot source policy and permitted relation targets are applied
before grouping, limits and response assembly in a final read snapshot.

Projection hashes kind (`fact` or `decision`) plus wording normalized to Unicode
NFC and trimmed at its outer whitespace. Exact wording shares one entity with
multiple independent proof records; case changes, internal whitespace and
paraphrases do not establish equivalence. Materials are not deleted to merge
cards. Proof objects carry citation ID, record/source identifiers, revision,
exact `quote`, code-point `start`/`end`, up to 100 characters of context per side,
separate nullable source/import dates and support status. These offset field names
differ from evidence-bundle `start_char`/`end_char`.

Statuses describe provenance and relations:

| Status | Meaning |
| --- | --- |
| `source_supported` | Extractive support for stored wording, not established truth or endorsement |
| `candidate` | Support does not satisfy conservative extraction/framing checks; review required |
| `possible_conflict` | Source-backed suggested relation; not proven contradiction |
| `explicit_supersedes` | Narrow validated positive directional source wording connecting both claims |

Recognized false/proposal/hypothetical/example/fenced framing remains candidate.
These are deliberately limited language heuristics, not a complete negation
classifier. Explicit replacement requires a standalone positive form such as
`<new wording> supersedes <old wording>` or its supported Russian form, both
wordings in the exact proof and a valid surrounding context. A later date alone
cannot replace an earlier decision. Relations are checked again on text revision;
source and target require permitted proof. The packet does not silently choose a
winner between different decisions.

`get_facts` returns `schema_version`, `query`, `include_history`, `facts`,
`history_since`, `untrusted_content`, `notice` and `truncated`. Each fact contains
`id`, `kind`, `text`, `status`, `evidence`, `relations`, `historical_only`,
`evidence_truncated` and `relations_truncated`. Default results require active
current proof. `include_history=true` also discovers an entity with only permitted
old snapshots: `historical_only=true`, status `candidate`, proof `active=false`.
Current results retain current proof; use history to inspect past observations.
Past-only wording is not returned as a current decision.

History returns `fact`, `events`, `history_since`, `next_cursor`, `truncated`,
schema/trust metadata and notice. Each event has stable `event_id`, `observed_at`,
`active`, `status`, model/prompt version, exact proof fields and bounded relations.
`active=false` denotes saved past proof; historical relations carry
`historical=true`. Observation time is separate from the proof's source/message
dates. `history_since` marks feature enablement, not a reconstructed earlier
timeline. Only participating revisions observed/projected since then are saved;
the system cannot recover pre-existing missing versions.

History orders by observation time and event identity descending. Pass the opaque
`next_cursor` back as `before` for the same fact; cursors are bounded to 512
characters and validated against that fact. A cursor always points after the last
actually returned event, including a byte-limited page, so continuation does not
skip an event merely because it did not fit. Stable IDs allow consumers to avoid
duplicate events while loading pages.

Fact/history packets have a **32,000 UTF-8 byte** compact-JSON cap, excluding the
HTTP/MCP envelope. This differs from evidence bundles' Unicode-character budget.
Each fact includes at most eight proof records and eight relations; each history
event includes at most eight relations. Cached proof quotes are bounded to 4,000
characters (new model atom evidence to 2,000; relation evidence to 4,000).
Cached entity wording is at most 2,000 characters, new model wording 1,500.
`evidence_truncated` marks limited fact proof; `relations_truncated` marks limited
or pruned relations. Top-level `truncated` covers list/byte/metadata/proof/relation
limits, not evidence of complete source coverage. Oversized history metadata can
omit optional title/URI/object ID/model while preserving the exact quote/context
and cursor progress; nested relation metadata can be compacted then pruned with a
flag. A missing URI is not replaced by an invented link.

New card prompt/schema version 3 extracts optional fact relations through the
existing selected provider, queue, admission policies and attempt budget. This
does not add a second paid extraction pass. Saved validated atoms project locally
in the same card-save transaction. For previously ready cards, app-only
`POST /api/memory/facts/project` starts an existing-writer job with strict integer
`limit=100` (1–500). Explicit local projection works while automatic synchronization
is paused; bounded automatic projection respects the pause and resumes pending
work. Neither fact/history reads nor MCP startup reads project existing cards or
invoke a model. Reading saved records/cards/original references spends no
card-provider model tokens; a consuming agent can separately spend input tokens
on returned context.

Revision changes revalidate exact proof and framing. Lost support is not active;
historical snapshots remain accessible only while both their scope and the current
record scope are permitted. Deletion removes a source's proof, relations and
snapshots; revocation/exclusion blocks them and target-only disclosure. Native
fact/history actions requery the view and validate current material before acting;
past snapshots remain labelled as historical. Previously received/displayed text
and old backups cannot be retroactively recalled.

## Backup/restore operational boundary — Stage 6

These operational endpoints use the app's HTTP API and add no MCP tools:

| Endpoint | Request / result |
| --- | --- |
| `GET /api/backups` | Settings, managed-copy inventory, last error, archive limit and warning |
| `PUT /api/backups/settings` | Strict `enabled`, optional absolute `directory`, `interval_hours`, `max_copies`, `max_total_bytes` |
| `POST /api/backups/local` | Empty request; creates one managed unencrypted local copy |
| `POST /api/backups/export` | New absolute `path`, transient `passphrase`; creates standard-age encrypted output |
| `POST /api/backups/inspect` | Archive `path`, optional age `passphrase`; returns staged inspection and expiry |
| `POST /api/backups/restore` | Only the reviewed `inspection_id` UUID, not a new archive path |
| `DELETE /api/backups/{backup_id}` | Deletes only the matching managed inventory item |
| `POST /api/sources/{source_id}/relocate` | Explicit local `path` or current `notes_folder_ids` selection |
| `POST /api/sources/{source_id}/reauthenticate` | New `token`, `app_password` or completed `outlook_auth_id` for that source kind |
| `GET /api/memory/records/{record_id}/open-target` | Current source-aware target and captured record reference |

Scheduling defaults to disabled, 24 hours, ten copies, 1 GiB total. Strict ranges
are 1–168 hours, 1–50 copies, 1 MiB–10 GiB. It runs while the app is alive. Retention
uses managed UUID filenames and recorded size/hash identity; unrelated files and
exported age archives are outside pruning. A changed managed file is refused for
automatic deletion. Managed local archives are unencrypted.

Format v1 is a flat ZIP containing **exactly** `manifest.json`, `memory.sqlite3`,
`settings.json`, `sources.json` and `schedule.json` (mapped to live
`memory_schedule.json`). Manifest keys are `format="second-brain-backup"`,
`format_version=1`, `app_version`, timezone-aware `created_at`, random
`installation_id` UUID, `schema_fingerprint` and `files`; each file metadata entry
has exact byte size and lowercase SHA-256. Known lazy progress/provider tables are
normalized before the SQLite Backup API snapshot/configuration capture. Only the
current exact schema fingerprint is accepted; this is not a general migration
loader for unknown or older database schemas. Configuration schemas, checksums
and SQLite integrity are validated before live-state changes.

The database copy includes all cached/hidden text, policies, index generations,
vectors, cards and fact snapshots; visibility flags do not redact it. Keychain
credentials, weights and machine-local backup preferences/inventory are excluded.
No backup operation calls a model or transmits data to an external service.

The plaintext ZIP and total uncompressed content including manifest each have a
128 MiB hard cap. Encrypted output/input allows at most 128 MiB + 1 MiB for age
overhead. Inputs must be regular absolute files without symlink traversal;
duplicates, unexpected names, directories, encrypted ZIP entries, symlinks,
oversized payloads or invalid manifest/schema/integrity are rejected. The inner
ZIP is unencrypted even when wrapped in age.

Portable encryption uses standard passphrase age through pinned `pyrage==1.4.0`,
not a custom cryptographic format. New export passwords require 8–1,024 Unicode
characters and at most 4,096 UTF-8 bytes. Native password confirmation compares
exact UTF-8 bytes without Unicode normalization. A focused `NSSecureTextField`
adapter preserves byte-distinct edits in transient state; actual password edits
invalidate inspection, while unchanged control echoes do not. The bounded helper receives its secret
through stdin, not argv, settings or Keychain. Header/work/output/time limits
constrain untrusted ciphertext processing. Temporary plaintext staging/process
memory is not encrypted by this export feature and is not an erasure guarantee.
Existing export destinations are never overwritten.

Inspection stages a private copy of the selected bytes, then returns
`inspection_id`, `expires_at`, creation/app-version metadata, material/source
counts, `foreign_installation`, `requires_relocation`, `requires_reauthentication`
and warnings. The ID is process-local and expires after 600 seconds; restart or
expiry requires reinspection. Restore consumes only those staged bytes, so
replacing the original file after inspection cannot substitute a different archive.

Shared maintenance guards cover complete settings/source read–credential–modify–write
workflows and each of the seven MCP tool requests, including response construction;
they are not released between a workflow's individual storage accesses.
Snapshot/restore/recovery obtain the writer lease and exclusive boundary. Updated
readers temporarily fail with maintenance status instead of accessing half-replaced
configuration. Legacy
SQLite/WAL/SHM handles are detected conservatively and isolation retains a SQLite
exclusive lock; unsafe access requires the user to close old clients. No process
is killed automatically. SQLite Backup API restores into the existing database
file rather than replacing an inode under a reader. A durable pre-restore snapshot
and fsynced journal cover SQLite/JSON rollback. Recovery runs before migrations
and normal reads; unresolved recovery fails closed. Successful journal retirement
precedes cleanup, so cleanup failure does not reverse a completed transaction.

The successful restore response includes `documents`, `sources`,
`foreign_installation`, `restored=true`, `paused=true`,
`restart_recommended=false` and `cleanup_warning`. The last flag means that the
restored state is applied but some private staging/rollback cleanup was deferred.
A safe warning is persisted as backup-status `last_error` when possible; it is
not a failed-restore result. The native UI refreshes that status after restoration.
Before sending the restore POST, native app/memory epochs and caches reset
synchronously and the inspection is invalidated. Completion is bound to the
admitted UUID; a confirmed response then starts bootstrap/reload. Callbacks from
earlier epochs are rejected, including failures and sheet actions. A timeout,
rejected POST or failed bootstrap keeps memory actions closed and advises a safe
restart/reload rather than readmitting cached state. Model preparation separately releases its
credential lock before the final admission callback. Policy/pause checks govern
network admission; current policy/revision checks guard result persistence.

Restoration clears in-flight card claims, invalidates old browse generations,
pauses synchronization, disables card processing and disables embeddings. It does
not automatically resume paid/imported work. A different installation UUID clears
selected model/private-network opt-in, requires newly entered cloud credentials
or explicit local-model selection, marks local/session/Notes sources for explicit
relocation and remote sources for new authentication, and disables imports.
Matching old Keychain names do not bypass these gates. The UUID is an installation
marker, not authenticated hardware identity. Permitted cached reads remain subject
to restored visibility policies; repair does not itself enable import.

Original opening must use the fresh `open-target` response rather than blindly
opening stored provenance. Its `reference` contains exactly `id`, `revision`,
`source_id`, `source_kind`, `source_object_id` and `source_uri` from the captured
record; native callers compare that reference before opening. Local targets
reconstruct safe relative keys under a
currently selected non-symlink root and require regular files. Opaque legacy
`::file:` keys return no target until reimport. Remote targets accept only HTTPS
without credentials/nonstandard ports on `github.com`, `notion.so`/`www.notion.so`,
or `outlook.office.com`/`outlook.live.com` for the corresponding source kind.
IMAP/Notes identifiers are not given invented browser links. Restored old file URIs
remain provenance, not automatically authorized destinations.

This describes implemented contracts. Native GUI restoration and
Unicode checks, API cross-checks and source-matched portable-package checks passed
on the validation Mac. The 30 recorded quick-panel checks include user-confirmed
physical global-hotkey activation with the main window closed. A physical
second-Mac transfer, full CJK/VoiceOver behavior and real embedding quality remain
outside that verified scope. Detailed checks and limits are recorded in
[development](development.md) and the development plan.

## Offline passage regression slice

```sh
uv run --frozen python scripts/benchmark_evidence.py
```

This runs `EvidenceService` in lexical mode against ten hand-written synthetic
RU/EN passage-qrel cases in `tests/fixtures/evidence_cases.json`, with zero model
calls and temporary databases. It covers exact and late passages, saved-card
evidence, an ambiguous card, distinct conflicting quotes, and no-answer queries.
The current fixture run reports context precision/recall **1.0/1.0**, negative
false positives **0.0**, ambiguity signal **1.0** (`accepted=true`). Precision is
the fraction of returned quotes containing an expected substring, macro-averaged
over cases; empty negative cases contribute 1. Recall averages expected-substring
coverage over positive cases. Ambiguity signalling and negatives are reported
separately. These are deterministic fixture checks, not a broad retrieval-quality
claim, real-model semantic evaluation or evidence that an arbitrary answer is true.

## Offline evaluation

Run the reviewed 46-query bilingual fixture using current production BM25:

```sh
uv run --frozen python scripts/benchmark_search.py
uv run --frozen python scripts/benchmark_search.py --documents 10000 --output /tmp/second-brain-bm25.json
```

The benchmark creates and removes a temporary SQLite database. It imports only
`MemoryStore`, not application startup, user settings, connectors or a model.
It does not inspect `.data`, session directories, mail, Notes or Keychain. Tests
deny sockets for the in-process benchmark. Every original record and relevance
judgement is hand-written synthetic content in
`tests/fixtures/search_corpus.json`; the fixture hash is included in each report.
The larger corpus adds deterministic botanical records, including numerical token
distractors, without changing the reviewed qrels. It is a scaling probe rather
than a representative sample of a user's memory.

Dates and the freshness clock are fixed by the fixture's `as_of`, so relevance
metrics do not drift as the benchmark is rerun on another day. The code exercises
the actual `MemoryStore.search` BM25/prefix/freshness behavior, including existing
limitations. The baseline deliberately records false positives and weak
paraphrases rather than treating lexical overlap as semantic quality.

Metric definitions:

- Recall@5: macro-average fraction of judged relevant document IDs found in the
  first five results over positive queries. Source duplicates remain separate
  relevant documents in this Stage 0 metric.
- MRR@10: macro-average reciprocal first relevant rank among ten results over
  positive queries; no relevant result contributes zero.
- Duplicate fraction@5: `(returned_count - distinct_text_hashes) / returned_count`,
  zero for empty results, then macro-averaged over all queries. Exact body text
  equality defines a duplicate, independent of title or source.
- Negative false positive rate: fraction of no-answer queries returning any
  result, reported separately. Negative queries do not inflate recall or MRR.
- p50/p95: nearest-rank percentiles of individual search calls after database
  seeding, including SQLite connection/query, row materialization and Python
  ranking. There is no unmeasured query warmup: the first and repeated query
  passes are all included. Corpus generation is excluded; seeding is excluded
  and timed separately.
- Peak RSS: whole-process lifetime high-water RSS, including Python and native
  SQLite memory; not incremental search memory. `tracemalloc_peak_bytes` measures
  traced Python allocations across import and retrieval, not SQLite allocations.
  Tracing is enabled during measurement and adds overhead.

`results` records the first ranking per query; aggregate quality/time metrics use
all repetitions. `positive_evaluations` and `negative_evaluations` include repeats;
`dataset.queries` is the unique query count. Reports separate categories and languages. Query embedding,
precomputed vector retrieval and model cold start are explicitly `null` in Stage
0; they must be measured separately when Stage 2 exists. The baseline does not
prove a speed or semantic quality guarantee for future models.

Stage 2 acceptance compares the unchanged fixture and hash: paraphrase Recall@5
must improve by at least 0.15 absolute, and exact-term MRR@10 must not decrease.
The small reviewed corpus is the primary quality gate; the 10,000-document corpus
is a separate scaling measurement and regression check. Actual local-model
quality must be distinguished from deterministic fake-embedding integration
tests. If an installed model is unavailable or testing would require downloading
weights, record the gap without claiming the quality gate passed.

## Stage 2 measurement scope and remaining quality gate

`benchmark-vectors-10000.json` records an exact scan of 10,000 precomputed
768-dimensional synthetic vectors on Apple M5/arm64: p50 **234.311 ms**, p95
**263.839 ms**, Python scan peak **0.871 MiB**, whole-process peak RSS
**38.406 MiB**. It reads 128 vectors per batch and keeps 20 candidates. The
separate memory run is untimed; Python allocation tracing excludes native SQLite
memory, and process RSS includes fixture construction. This is vector retrieval
alone, not query embedding or end-to-end search latency. Reproduce with:

```sh
uv run --frozen python scripts/benchmark_vectors.py --count 10000 --dimension 768 --repeats 20
```

The real-model evaluator is a separate explicit operation, using only the
reviewed synthetic fixture and a disposable database:

```sh
uv run --frozen python scripts/benchmark_semantic.py --model INSTALLED_EMBEDDING_MODEL --allow-local-model
```

It compares hybrid paraphrase Recall@5 and exact MRR@10 with both its lexical
run and the committed BM25 baseline. A fake client cannot pass the real-model
acceptance flag; incomplete indexing or hybrid fallback also fails it. The
required +0.15 absolute paraphrase improvement with no exact-MRR regression has
**not passed verification**: no installed Ollama model was available for that
evaluation, and weights were not downloaded. Actual query-embedding latency and
controlled cold-model startup are also unmeasured. The evaluator does not unload
a user's model; `load_duration` reported by Ollama is recorded separately and is
not presented as a controlled cold-start measurement. Deterministic integration
vectors prove retrieval behavior, not semantic relevance.

## Recorded Stage 0 evidence

- RED: `.venv/bin/python -m pytest tests/test_search_benchmark.py` failed with
  `ModuleNotFoundError: No module named 'scripts.benchmark_search'`, the intended
  missing benchmark implementation.
- GREEN: the same command passed all 14 tests after implementation. Tests cover
  metric arithmetic, empty qrels, bilingual/scenario coverage, qrel integrity,
  deterministic 10,000-record construction, work bounds, socket denial and CLI
  JSON output from an isolated temporary store.
- Machine-readable measured reports are `benchmark-baseline.json` and
  `benchmark-baseline-10000.json`; inspect their hardware, fixture hash and timing
  scope before comparison. Root execution records final full-suite validation
  and the Stage 0 commit in the development plan.
