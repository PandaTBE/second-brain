from scripts.benchmark_vectors import run


def test_precomputed_vector_benchmark_separates_unmeasured_model_stages():
    report = run(count=24, dimension=8, repeats=2)
    assert report["materials"] == 24
    assert report["dimension"] == 8
    assert report["read_batch"] == 128
    assert report["vector_search_ms"]["p95"] >= 0
    assert report["query_embedding_ms"] is None
    assert report["cold_model_start_ms"] is None
    assert report["semantic_quality"] is None
    assert report["python_scan_peak_mib"] > 0
