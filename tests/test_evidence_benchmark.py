import socket

from scripts.benchmark_evidence import run_benchmark


def test_offline_passage_quality_gate_covers_late_card_ambiguous_and_negative(monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("Evidence benchmark must stay offline")

    monkeypatch.setattr(socket, "socket", deny_network)
    report = run_benchmark()
    assert report["cases"] == 10
    assert report["context_precision"] == report["context_recall"] == 1.0
    assert report["negative_false_positive_rate"] == 0
    assert report["ambiguity_signal_rate"] == 1
    assert report["accepted"] is True
