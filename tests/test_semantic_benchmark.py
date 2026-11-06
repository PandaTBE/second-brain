import pytest
from test_embeddings import FakeEmbedding

from scripts.benchmark_semantic import run


def test_semantic_benchmark_requires_explicit_local_model_opt_in():
    with pytest.raises(ValueError, match="explicit"):
        run("embed:small")


def test_isolated_pipeline_compares_same_qrels_and_reports_unproven_quality():
    report = run("embed:small", allow_local_model=True, client=FakeEmbedding())
    assert report["kind"] == "synthetic_corpus_local_model_evaluation"
    assert report["model_execution"] == "injected_test_client"
    assert report["modes"]["lexical"]["overall"]["evaluations"] >= 40
    assert report["modes"]["hybrid"]["overall"]["evaluations"] >= 40
    assert report["modes"]["semantic"]["overall"]["evaluations"] >= 40
    assert report["cold_model_start_ms"] is None
    assert report["acceptance"]["required_paraphrase_delta"] == 0.15
    assert report["acceptance"]["passed"] is False


def test_acceptance_cannot_lower_target_when_same_run_lexical_regresses():
    from scripts.benchmark_semantic import _acceptance

    modes = {
        "lexical": {"groups": {"paraphrase": {"recall_at_5": 0.0}, "exact": {"mrr_at_10": 0.5}}},
        "hybrid": {
            "groups": {"paraphrase": {"recall_at_5": 0.2}, "exact": {"mrr_at_10": 0.5}},
            "used_modes": {"hybrid": 48},
        },
    }
    baseline = {"groups": {"paraphrase": {"recall_at_5": 0.6}, "exact": {"mrr_at_10": 1.0}}}
    assert _acceptance(modes, True, True, baseline)["passed"] is False
