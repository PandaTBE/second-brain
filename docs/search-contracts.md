# Retrieval and evidence contracts

Contracts updated 2026-11-06; Stage 0 implementation baseline `7fe1f49`.
Source policies, local retrieval and evidence bundles below describe unreleased
source builds tracked by the [development plan](agent-memory-plan.ru.md).
Published `v0.1.0` assets remain unchanged.

## Existing compatibility boundary

The following stdio MCP names and required arguments remain stable:

| Tool | Required arguments | Existing response fields |
| --- | --- | --- |
| `search_memory` | `query` | `results`, `untrusted_content`, `notice` |
| `get_record` | `record_id` | `record`, `offset`, `total_chars`, `next_offset`, `untrusted_content`, `notice` |
| `get_context_brief` | none | `records`, `untrusted_content`, `notice`, `message` |
| `get_sync_status` | none | `memory`, `mcp_read_only`, `message` |

`search_memory` keeps optional `limit=8` (1–20) and `source_id=None`, and adds
optional `mode="lexical"` in unreleased builds.
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

## Implemented source-policy boundary — Stage 1, unreleased

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

## Implemented local retrieval — Stage 2, unreleased

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

## Implemented evidence bundle — Stage 3, unreleased

Read-only MCP `get_evidence_bundle` requires `query` (1–512 characters) and accepts
`source_id=None`, `project=None`, `date_from=None`, `date_to=None`,
`budget_chars=12000`, and `mode="hybrid"`. The shared `EvidenceService` also backs
`GET /api/memory/evidence`, where the query parameter is `q`; other parameter names
are identical. Native HTTP retrieval uses local-search permission; MCP uses MCP
permission. The four original tools remain compatible, including a query-free
`get_context_brief()`.

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

### Offline passage regression slice

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
