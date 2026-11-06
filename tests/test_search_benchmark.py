import json
import math
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.benchmark_search import (
    FIXTURE_PATH,
    build_corpus,
    evaluate_query,
    load_corpus,
    run_benchmark,
    summarize,
)


def test_reviewed_corpus_has_bilingual_qrels_and_required_scenarios():
    corpus = load_corpus()
    assert len(corpus["queries"]) >= 40
    assert {query["language"] for query in corpus["queries"]} == {"ru", "en"}
    assert {query["category"] for query in corpus["queries"]} >= {
        "exact", "paraphrase", "negative", "conflict", "date", "source"
    }
    documents = {document["id"] for document in corpus["documents"]}
    assert all(set(query["relevant"]) <= documents for query in corpus["queries"])
    assert all(query["relevant"] == [] for query in corpus["queries"]
               if query["category"] == "negative")


def test_metrics_use_macro_recall_first_relevant_rank_and_content_duplicates():
    query = {"relevant": ["a", "b"]}
    hits = [
        {"source_object_id": "x", "text": "same"},
        {"source_object_id": "a", "text": "same"},
        {"source_object_id": "b", "text": "different"},
    ]
    score = evaluate_query(query, hits, 2.5)
    assert score["recall_at_5"] == 1
    assert score["mrr_at_10"] == 0.5
    assert score["duplicate_fraction_at_5"] == pytest.approx(1 / 3)
    assert score["latency_ms"] == 2.5
    assert evaluate_query(query, [], 1)["mrr_at_10"] == 0


def test_negatives_are_measured_separately_without_dividing_by_zero():
    negative = evaluate_query({"relevant": []}, [], 1)
    false_positive = evaluate_query(
        {"relevant": []}, [{"source_object_id": "x", "text": "x"}], 3
    )
    positive = evaluate_query({"relevant": ["a"]}, [], 2)
    result = summarize([negative, false_positive, positive])
    assert result["recall_at_5"] == 0
    assert result["mrr_at_10"] == 0
    assert result["negative_evaluations"] == 2
    assert result["negative_false_positive_rate"] == 0.5
    assert result["p50_ms"] == 2
    assert result["p95_ms"] == 3
    assert summarize([])["recall_at_5"] is None


@pytest.mark.parametrize("size", [True, 0, 10, 100_001])
def test_size_limits_reject_invalid_or_smaller_than_fixture(size):
    with pytest.raises(ValueError):
        build_corpus(load_corpus(), size)


def test_large_corpus_is_deterministic_unique_and_synthetic():
    first = build_corpus(load_corpus(), 10_000)
    second = build_corpus(load_corpus(), 10_000)
    assert first == second
    assert len(first) == 10_000
    assert len({document["id"] for document in first}) == 10_000
    assert first[-1]["source_uri"].startswith("synthetic:")


def test_benchmark_runs_actual_bm25_offline_in_a_temporary_database(monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("The synthetic benchmark must not access the network")

    monkeypatch.setattr(socket, "socket", deny_network)
    corpus = load_corpus()
    report = run_benchmark(repeats=1)
    assert report["mode"] == "lexical"
    assert report["dataset"]["documents"] == len(corpus["documents"])
    assert report["dataset"]["queries"] == len(corpus["queries"])
    assert report["groups"]["exact"]["mrr_at_10"] > 0.8
    assert report["groups"]["paraphrase"]["recall_at_5"] < 1
    assert report["timing"]["query_embedding_ms"] is None
    assert report["timing"]["model_cold_start_ms"] is None
    assert report["timing"]["precomputed_vector_search_ms"] is None
    assert report["machine"]["architecture"]
    assert report["memory"]["peak_rss_bytes"] > 0
    assert math.isfinite(report["overall"]["p95_ms"])
    assert {result["query_id"] for result in report["results"]} == {
        query["id"] for query in corpus["queries"]
    }


def test_cli_emits_machine_readable_report_without_configuration_or_network(tmp_path):
    output = tmp_path / "baseline.json"
    result = subprocess.run(
        [sys.executable, "scripts/benchmark_search.py", "--repeats", "1", "--output", str(output)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == json.loads(output.read_text())
    assert json.loads(result.stdout)["dataset"]["fixture_sha256"]


def test_fixture_validation_rejects_unknown_qrels(tmp_path):
    corpus = json.loads(FIXTURE_PATH.read_text())
    corpus["queries"][0]["relevant"] = ["not-a-document"]
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(corpus))
    with pytest.raises(ValueError, match="relevance"):
        load_corpus(path)


@pytest.mark.parametrize("repeats", [False, 0, 21])
def test_repeat_limits_bound_benchmark_work(repeats):
    with pytest.raises(ValueError):
        run_benchmark(repeats=repeats)
