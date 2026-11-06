"""Offline retrieval evaluation against reviewed synthetic relevance judgements.

Run from any directory with this checkout's Python. Only a temporary directory is
opened; no application settings, connectors, models or personal data are loaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from brain_settings.memory_store import MemoryStore  # noqa: E402

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "search_corpus.json"
MAX_DOCUMENTS = 100_000
MAX_REPEATS = 20


def load_corpus(path: Path = FIXTURE_PATH) -> dict:
    """Validate benchmark data, including source-scoped relevance judgements."""
    corpus = json.loads(path.read_text(encoding="utf-8"))
    documents = corpus.get("documents", [])
    queries = corpus.get("queries", [])
    if corpus.get("version") != 1 or not documents or not queries:
        raise ValueError("Unsupported or empty benchmark fixture")
    indexed = {document["id"]: document for document in documents}
    query_ids = {query["id"] for query in queries}
    if len(indexed) != len(documents) or len(query_ids) != len(queries):
        raise ValueError("Duplicate benchmark identifier")
    for query in queries:
        relevant = query["relevant"]
        if not set(relevant) <= indexed.keys() or len(relevant) != len(set(relevant)):
            raise ValueError("Invalid relevance judgement")
        if query.get("source_id") and any(
            indexed[identifier]["source_id"] != query["source_id"] for identifier in relevant
        ):
            raise ValueError("Source-filtered relevance judgement crosses source boundary")
        if query["language"] not in ("ru", "en") or not query["query"].strip():
            raise ValueError("Invalid benchmark query")
    datetime.fromisoformat(corpus["as_of"])
    return corpus


def build_corpus(corpus: dict, size: int | None = None) -> list[dict]:
    """Extend the hand-written fixture with deterministic, irrelevant documents."""
    originals = corpus["documents"]
    count = len(originals) if size is None else size
    if isinstance(count, bool) or not isinstance(count, int):
        raise ValueError("Document count must be an integer")
    if not len(originals) <= count <= MAX_DOCUMENTS:
        raise ValueError(f"Document count must be between {len(originals)} and {MAX_DOCUMENTS}")
    padding = [
        {
            "id": f"filler-{index:06d}",
            "source_id": f"synthetic-volume-{index % 8}",
            "source_kind": "folder",
            "title": f"Synthetic botanical observation {index:06d}",
            "text": (
                f"Botanical specimen {index:06d}: leaf width {index % 97} millimetres. "
                f"Petal colour sample {index % 31}. Soil acidity {index % 7}. "
                "Синтетическое наблюдение растения: высота стебля и форма листа."
            ),
            "source_uri": f"synthetic:volume/{index:06d}",
            "created_at": "2026-02-01T00:00:00+00:00",
            "updated_at": "2026-02-01T00:00:00+00:00",
        }
        for index in range(count - len(originals))
    ]
    return [*originals, *padding]


def evaluate_query(query: dict, hits: list[dict], latency_ms: float) -> dict:
    """Score a ranking; empty qrels are negatives, not perfect recall examples."""
    relevant = set(query["relevant"])
    identifiers = [hit["source_object_id"] for hit in hits]
    first = next((rank for rank, item in enumerate(identifiers[:10], 1) if item in relevant), 0)
    texts = [hashlib.sha256(hit["text"].encode()).hexdigest() for hit in hits[:5]]
    return {
        "recall_at_5": len(relevant.intersection(identifiers[:5])) / len(relevant)
        if relevant else None,
        "mrr_at_10": 1 / first if first else (0.0 if relevant else None),
        "duplicate_fraction_at_5": (len(texts) - len(set(texts))) / len(texts) if texts else 0.0,
        "negative_false_positive": bool(hits) if not relevant else None,
        "latency_ms": latency_ms,
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def summarize(results: list[dict]) -> dict:
    """Macro-average positives and report negatives separately; nearest-rank time."""
    positives = [result for result in results if result["recall_at_5"] is not None]
    negatives = [result for result in results if result["negative_false_positive"] is not None]
    times = [result["latency_ms"] for result in results]
    return {
        "evaluations": len(results),
        "positive_evaluations": len(positives),
        "negative_evaluations": len(negatives),
        "recall_at_5": _mean([result["recall_at_5"] for result in positives]),
        "mrr_at_10": _mean([result["mrr_at_10"] for result in positives]),
        "duplicate_fraction_at_5": _mean(
            [result["duplicate_fraction_at_5"] for result in results]
        ),
        "negative_false_positive_rate": _mean(
            [int(result["negative_false_positive"]) for result in negatives]
        ),
        "p50_ms": _percentile(times, 0.5),
        "p95_ms": _percentile(times, 0.95),
    }


def _seed(store: MemoryStore, documents: list[dict]) -> None:
    for document in documents:
        store.upsert_document(
            source_object_id=document["id"],
            **{key: value for key, value in document.items() if key != "id"},
        )


def _evaluate(store: MemoryStore, queries: list[dict], repeats: int) -> tuple[list, list]:
    evaluations, rankings = [], []
    for iteration in range(repeats):
        for query in queries:
            started = time.perf_counter()
            hits = store.search(query["query"], limit=10, source_id=query.get("source_id"))
            latency = (time.perf_counter() - started) * 1000
            score = evaluate_query(query, hits, latency)
            evaluations.append({
                **score, "category": query["category"], "language": query["language"]
            })
            if iteration == 0:
                rankings.append({
                    "query_id": query["id"],
                    "query": query["query"],
                    "category": query["category"],
                    "language": query["language"],
                    "relevant": query["relevant"],
                    "retrieved": [hit["source_object_id"] for hit in hits],
                    **score,
                })
    return evaluations, rankings


def run_benchmark(*, documents: int | None = None, repeats: int = 3) -> dict:
    """Measure existing BM25 with a fixed freshness clock in a disposable store."""
    if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= MAX_REPEATS:
        raise ValueError(f"Repeats must be between 1 and {MAX_REPEATS}")
    corpus = load_corpus()
    material = build_corpus(corpus, documents)
    as_of = datetime.fromisoformat(corpus["as_of"])

    class BenchmarkClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return as_of.astimezone(tz) if tz else as_of.replace(tzinfo=None)

    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    try:
        with tempfile.TemporaryDirectory(prefix="second-brain-benchmark-") as directory:
            with patch("brain_settings.memory_store.datetime", BenchmarkClock):
                started = time.perf_counter()
                store = MemoryStore(Path(directory))
                _seed(store, material)
                seed_seconds = time.perf_counter() - started
                evaluations, rankings = _evaluate(store, corpus["queries"], repeats)
                database_bytes = sum(path.stat().st_size for path in Path(directory).iterdir())
        traced_peak = tracemalloc.get_traced_memory()[1]
    finally:
        if not already_tracing:
            tracemalloc.stop()
    return _report(corpus, material, repeats, evaluations, rankings, seed_seconds,
                   traced_peak, database_bytes)


def _hardware() -> dict:
    if sys.platform != "darwin":
        return {"hardware_details": "not collected on this platform"}
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string", "hw.model", "hw.memsize"],
            capture_output=True, text=True, check=True, timeout=2,
        )
        cpu, model, memory = result.stdout.strip().splitlines()
        return {"cpu": cpu, "hardware_model": model, "physical_memory_bytes": int(memory)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"hardware_details": "unavailable"}


def _report(corpus, material, repeats, evaluations, rankings, seed_seconds,
            traced_peak, database_bytes) -> dict:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "schema_version": 1,
        "mode": "lexical",
        "implementation": "MemoryStore.search: FTS5 BM25 + existing freshness weighting",
        "as_of": corpus["as_of"],
        "dataset": {
            "fixture": "tests/fixtures/search_corpus.json",
            "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
            "documents": len(material),
            "queries": len(corpus["queries"]),
            "repeats": repeats,
            "personal_data": False,
        },
        "machine": {
            "os": platform.system(), "os_version": platform.release(),
            "architecture": platform.machine(), "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            **_hardware(),
        },
        "overall": summarize(evaluations),
        "groups": {
            group: summarize([result for result in evaluations if result["category"] == group])
            for group in sorted({query["category"] for query in corpus["queries"]})
        },
        "languages": {
            language: summarize([
                result for result in evaluations if result["language"] == language
            ])
            for language in ("ru", "en")
        },
        "timing": {
            "seed_seconds": seed_seconds,
            "lexical_search": (
                "p50/p95 measure SQL + ranking after seeding; first and repeated queries"
            ),
            "unmeasured_warmup_queries": 0,
            "tracing_enabled": True,
            "query_embedding_ms": None,
            "model_cold_start_ms": None,
            "precomputed_vector_search_ms": None,
            "embedding_status": "not measured: Stage 0 has no embedding model or vector index",
        },
        "memory": {
            "peak_rss_bytes": int(rss if sys.platform == "darwin" else rss * 1024),
            "tracemalloc_peak_bytes": traced_peak,
            "database_bytes": database_bytes,
            "scope": (
                "whole benchmark process; RSS is process lifetime high-water, not search delta"
            ),
        },
        "results": rankings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=None)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        report = run_benchmark(documents=arguments.documents, repeats=arguments.repeats)
    except ValueError as error:
        parser.error(str(error))
    encoded = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if arguments.output:
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
