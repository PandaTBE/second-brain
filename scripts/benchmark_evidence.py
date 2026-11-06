"""Offline passage-quality slice, independent of model generation and user memory."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain_settings.evidence import EvidenceService
from brain_settings.memory_store import MemoryStore

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "evidence_cases.json"


def _seed(store: MemoryStore, case: dict) -> None:
    for index, document in enumerate(case["documents"]):
        text = (
            document.get("prefix", "")
            + document.get("repeat", "") * document.get("repeat_count", 0)
            + document["text"]
            + document.get("between", "") * document.get("between_count", 0)
            + document.get("suffix", "")
        )
        saved = store.upsert_document(
            source_id=f"synthetic-{case['id']}-{index}",
            source_kind="folder",
            source_object_id=f"case:{index}",
            title=document["title"],
            text=text,
            source_uri=f"file:///synthetic/{case['id']}-{index}.md",
        )
        if document.get("card_keyword"):
            store.save_card(
                saved["id"],
                saved["revision"],
                {
                    "title": document["title"],
                    "summary": document["card_keyword"],
                    "keywords": [document["card_keyword"]],
                    "questions": [],
                    "atoms": [
                        {"kind": "fact", "text": proof, "evidence": proof}
                        for proof in document["proofs"]
                    ],
                },
            )


def _evaluate(case: dict, packet: dict) -> dict:
    quotes = [item["quote"] for item in packet["citations"]]
    expected = case["expected_quotes"]
    recall = (
        sum(any(proof in quote for quote in quotes) for proof in expected) / len(expected)
        if (expected)
        else None
    )
    precision = (
        sum(any(proof in quote for proof in expected) for quote in quotes) / len(quotes)
        if (quotes)
        else 1.0
        if not expected
        else 0.0
    )
    return {
        "id": case["id"],
        "context_precision": precision,
        "context_recall": recall,
        "negative_false_positive": bool(quotes) if not expected else None,
        "ambiguity_signaled": "ambiguous_card_evidence" in packet["reasons"]
        if (case.get("expect_ambiguity"))
        else None,
    }


def run_benchmark(fixture: Path = FIXTURE) -> dict:
    raw = fixture.read_bytes()
    cases = json.loads(raw)["cases"]
    results = []
    with TemporaryDirectory(prefix="second-brain-evidence-") as temporary:
        for index, case in enumerate(cases):
            store = MemoryStore(Path(temporary) / str(index))
            _seed(store, case)
            packet = EvidenceService(store).bundle(case["query"], mode="lexical")
            results.append(_evaluate(case, packet))
    positives = [row for row in results if row["context_recall"] is not None]
    negatives = [row for row in results if row["negative_false_positive"] is not None]
    ambiguous = [row for row in results if row["ambiguity_signaled"] is not None]
    precision = sum(row["context_precision"] for row in results) / len(results)
    recall = sum(row["context_recall"] for row in positives) / len(positives)
    false_positives = sum(row["negative_false_positive"] for row in negatives) / len(negatives)
    ambiguity = sum(row["ambiguity_signaled"] for row in ambiguous) / len(ambiguous)
    return {
        "schema_version": 1,
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "cases": len(cases),
        "mode": "lexical",
        "model_calls": 0,
        "context_precision": precision,
        "context_recall": recall,
        "negative_false_positive_rate": false_positives,
        "ambiguity_signal_rate": ambiguity,
        "accepted": precision == recall == ambiguity == 1.0 and false_positives == 0,
        "scope": "Small synthetic passage-qrels regression slice; not real-model semantic quality.",
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_benchmark()
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
