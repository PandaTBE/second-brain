"""Explicit local-model evaluation against synthetic qrels in a disposable database."""

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain_settings.embeddings import EmbeddingManager
from brain_settings.errors import AppError
from brain_settings.memory_store import MemoryStore
from brain_settings.ollama import OllamaClient
from brain_settings.retrieval import Retrieval
from scripts.benchmark_search import FIXTURE_PATH, _seed, evaluate_query, load_corpus, summarize


class MeasuredClient:
    def __init__(self, client):
        self.client = client
        self.reported_load_ms = []

    def embedding_catalog(self, **kwargs):
        return self.client.embedding_catalog(**kwargs)

    def embed(self, *args, **kwargs):
        result = self.client.embed(*args, **kwargs)
        duration = result.get("load_duration")
        if not isinstance(duration, bool) and isinstance(duration, (int, float)) and duration >= 0:
            self.reported_load_ms.append(duration / 1000000)
        return result


def _evaluate(retrieval, queries, mode):
    evaluations, timings, used_modes = [], [], []
    for query in queries:
        started = time.perf_counter()
        response = retrieval.search(
            query["query"], limit=10, source_id=query.get("source_id"), mode=mode
        )
        elapsed = (time.perf_counter() - started) * 1000
        evaluations.append(
            evaluate_query(query, response["results"], elapsed)
            | {"category": query["category"], "language": query["language"]}
        )
        timings.append(response["search"])
        used_modes.append(response["search"]["used_mode"])
    groups = {
        group: summarize([row for row in evaluations if row["category"] == group])
        for group in sorted({row["category"] for row in evaluations})
    }
    return {
        "overall": summarize(evaluations),
        "groups": groups,
        "languages": {
            language: summarize([row for row in evaluations if row["language"] == language])
            for language in ("ru", "en")
        },
        "used_modes": {key: used_modes.count(key) for key in sorted(set(used_modes))},
        "query_timings": timings,
    }


def _acceptance(modes, fully_indexed, real_client, baseline):
    lexical = modes["lexical"]["groups"]
    hybrid = modes["hybrid"]["groups"]
    saved = baseline["groups"]
    paraphrase = hybrid["paraphrase"]["recall_at_5"]
    exact = hybrid["exact"]["mrr_at_10"]
    delta = paraphrase - lexical["paraphrase"]["recall_at_5"]
    exact_delta = exact - lexical["exact"]["mrr_at_10"]
    required_recall = (
        max(lexical["paraphrase"]["recall_at_5"], saved["paraphrase"]["recall_at_5"]) + 0.15
    )
    required_mrr = max(lexical["exact"]["mrr_at_10"], saved["exact"]["mrr_at_10"])
    fallbacks = any(key != "hybrid" for key in modes["hybrid"]["used_modes"])
    return {
        "required_paraphrase_delta": 0.15,
        "required_paraphrase_recall": round(required_recall, 6),
        "required_exact_mrr": required_mrr,
        "paraphrase_recall_delta": round(delta, 6),
        "exact_mrr_delta": round(exact_delta, 6),
        "passed": bool(
            real_client
            and fully_indexed
            and not fallbacks
            and paraphrase >= required_recall - 1e-9
            and exact >= required_mrr - 1e-9
        ),
    }


def run(model_id, *, allow_local_model=False, client=None):
    if not allow_local_model:
        raise ValueError("An explicit --allow-local-model opt-in is required")
    corpus = load_corpus()
    fixture_hash = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    baseline_path = Path(__file__).resolve().parent.parent / "docs" / "benchmark-baseline.json"
    baseline = json.loads(baseline_path.read_text())
    if baseline["dataset"]["fixture_sha256"] != fixture_hash:
        raise ValueError("Synthetic fixture does not match the committed BM25 baseline")
    as_of = datetime.fromisoformat(corpus["as_of"])

    class BenchmarkClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return as_of.astimezone(tz) if tz else as_of.replace(tzinfo=None)

    measured = MeasuredClient(client if client is not None else OllamaClient())
    with tempfile.TemporaryDirectory(prefix="second-brain-semantic-eval-") as directory:
        with patch("brain_settings.memory_store.datetime", BenchmarkClock):
            store = MemoryStore(Path(directory))
            _seed(store, corpus["documents"])
            manager = EmbeddingManager(store, measured)
            state = manager.configure(enabled=True, model_id=model_id)
            while manager.vectors.pending(limit=1):
                manager.index_batch(limit=16)
            state = manager.vectors.settings()
            retrieval = Retrieval(store, measured)
            modes = {
                mode: _evaluate(retrieval, corpus["queries"], mode)
                for mode in ("lexical", "semantic", "hybrid")
            }
    complete = state["pending"] == state["errors"] == 0
    return {
        "schema_version": 1,
        "kind": "synthetic_corpus_local_model_evaluation",
        "model_execution": "installed_local_ollama" if client is None else "injected_test_client",
        "model": {key: state[key] for key in ("model_id", "digest", "dimension", "prep_version")},
        "dataset": {
            "fixture": "tests/fixtures/search_corpus.json",
            "personal_data": False,
            "fixture_sha256": fixture_hash,
            "documents": len(corpus["documents"]),
            "queries": len(corpus["queries"]),
        },
        "fully_indexed": complete,
        "index": state,
        "modes": modes,
        "acceptance": _acceptance(modes, complete, client is None, baseline),
        "baseline": {"fixture_sha256": fixture_hash, "groups": baseline["groups"]},
        "cold_model_start_ms": None,
        "ollama_reported_load_ms": measured.reported_load_ms,
        "limitations": [
            "This command never downloads models or reads personal configuration/data.",
            "Cold start is unmeasured: existing user models are never unloaded.",
            "Ollama load durations are server-reported, not a controlled cold-start run.",
            "Query embedding timings include local catalog/digest validation.",
            "Ranking scores are not probabilities; negative-query metrics need review.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--allow-local-model", action="store_true")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        report = run(arguments.model, allow_local_model=arguments.allow_local_model)
    except (AppError, ValueError) as error:
        print(
            json.dumps(
                {
                    "error": getattr(error, "code", "explicit_opt_in_required"),
                    "message": str(error),
                },
                ensure_ascii=False,
            )
        )
        return 2
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(encoded)
    print(encoded, end="")
    return 0 if report["acceptance"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
