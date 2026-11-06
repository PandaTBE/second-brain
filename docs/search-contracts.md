# Retrieval and evidence contracts

Contracts updated 2026-11-06; Stage 0 implementation baseline `7fe1f49`.
Source policies and local retrieval below describe unreleased source builds.
The proposed evidence bundle remains a later
[development-plan](agent-memory-plan.ru.md) contract, not an available tool.

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

## Proposed evidence bundle

Add a read-only tool `get_evidence_bundle` with required `query` and optional
source/project/date filters and a bounded context budget. Implementations should
use consistent parameter names across HTTP and MCP; the exact optional names and
defaults are fixed in Stage 3 after filter semantics are implemented. A project
filter must match persisted metadata rather than guessing from document titles.
A date filter must say which persisted date it uses and how undated records are
handled. No timestamp is fabricated to satisfy a filter.

Each evidence item must expose:

| Field | Meaning |
| --- | --- |
| `citation_id` | Deterministic ID for document ID, revision, exact quote and offsets |
| `record_id`, `source_id`, `source_kind` | Stable provenance, not a display title |
| `revision` | Revision against which quote validation succeeded |
| `title` | Bounded source title |
| `source_uri` / source identifier | Original location or opaque identifier as actually imported |
| source/message/modified/import dates | Separate optional fields with explicit semantics; unknown is null or omitted |
| `quote` | Exact substring of the saved normalized text |
| `start`, `end` | Half-open Unicode code point offsets in that text |
| surrounding context | Bounded exact text around the quote, clearly distinct from the quote |

The invariant is `saved_text[start:end] == quote`, with
`0 <= start < end <= len(saved_text)`, at the returned revision. Offsets are not
UTF-8 byte offsets or UTF-16 code units. A stable citation ID can be derived from
the document ID, revision, offsets and a quote hash; changing any of these changes
the ID. A citation is a reproducible source reference, not a claim of truth.

The response reports requested/used mode, applied filters, budget use, truncation
or incompleteness, and an empty evidence list when nothing was found. The budget
unit must be stated: characters are not exact model tokens. Quotes and surrounding
context share a total bounded text budget; metadata and total serialized response
also have fixed bounds. Deduplicate exact fragments while retaining their source
support. Distribute the budget across relevant sources so one long document does
not consume it all. Conflicting statements remain visible with their provenance.

Read the current revision and current policy when forming each item. Excluded,
deleted, or revoked sources must not leak through snippets, direct reads, brief,
history, graph, evidence, or aggregate timestamps/counts. A long-lived MCP process
must observe persisted changes made by another process. A policy change cannot
undo a model request already transmitted, but it blocks subsequent admission and
stale result persistence.

Imported text, titles, URIs and quotes remain untrusted data. Source instructions
are never executed. The agent writes its own answer and cites `citation_id`s.
Only explicitly permitted URI schemes may open an original. An opaque Notes ID
is displayed as an identifier; no deep link is invented.

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
