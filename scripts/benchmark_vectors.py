"""Offline exact-search measurement with precomputed synthetic vectors, never a model benchmark."""

import argparse
import hashlib
import json
import math
import platform
import random
import resource
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain_settings.embedding_store import READ_BATCH, VectorStore, normalized
from brain_settings.memory_store import MemoryStore


def _cpu():
    if sys.platform == "darwin":
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        return result.stdout.strip() or platform.machine()
    return platform.processor() or platform.machine()


def _populate(store, count, dimension):
    vectors = VectorStore(store)
    state = vectors.configure(
        "synthetic-precomputed-v1", hashlib.sha256(b"synthetic-v1").hexdigest(), dimension
    )
    randomizer = random.Random(20261106)
    with store._connection(write=True) as connection:
        for index in range(count):
            saved = store._upsert(
                connection,
                source_id="synthetic-vector-source",
                source_kind="folder",
                source_object_id=f"record-{index}",
                title=f"Synthetic vector material {index}",
                text=f"Artificial benchmark record {index}. No personal content.",
                source_uri=f"file:///synthetic/vector-{index}",
            )
            row = connection.execute(
                "SELECT * FROM documents WHERE id=?", (saved["id"],)
            ).fetchone()
            vector = normalized([randomizer.uniform(-1, 1) for _ in range(dimension)])
            connection.execute(
                "INSERT INTO embedding_vectors VALUES(?,?,?,?,?,?)",
                (
                    state["generation"],
                    row["id"],
                    row["revision"],
                    row["content_hash"],
                    dimension,
                    struct.pack(f"<{dimension}f", *vector),
                ),
            )
    return vectors


def run(count=10000, dimension=768, repeats=20):
    if not 1 <= count <= 100000 or not 1 <= dimension <= 4096 or not 1 <= repeats <= 100:
        raise ValueError(
            "Synthetic benchmark bounds: 1–100000 materials, 1–4096 dimensions, 1–100 runs"
        )
    with tempfile.TemporaryDirectory(prefix="second-brain-vector-benchmark-") as directory:
        store = MemoryStore(Path(directory))
        vectors = _populate(store, count, dimension)
        query = [1.0] + [0.0] * (dimension - 1)
        vectors.search(query)  # Warm SQLite page and Python code caches only.
        elapsed = []
        for _ in range(repeats):
            started = time.perf_counter()
            vectors.search(query)
            elapsed.append((time.perf_counter() - started) * 1000)
        # Memory instrumentation is separate because tracemalloc changes CPU timings.
        tracemalloc.start()
        vectors.search(query)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        ordered = sorted(elapsed)
        p95 = ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]
        return {
            "kind": "precomputed_synthetic_exact_vector_scan",
            "materials": count,
            "dimension": dimension,
            "repeats": repeats,
            "read_batch": READ_BATCH,
            "candidate_heap_limit": 20,
            "hardware": {
                "cpu": _cpu(),
                "machine": platform.machine(),
                "system": platform.system(),
                "release": platform.release(),
                "python": platform.python_version(),
            },
            "vector_search_ms": {"p50": round(statistics.median(elapsed), 3), "p95": round(p95, 3)},
            "python_scan_peak_mib": round(peak / (1024 * 1024), 3),
            "process_peak_rss_mib": round(
                rss / (1024 * 1024 if sys.platform == "darwin" else 1024), 3
            ),
            "query_embedding_ms": None,
            "cold_model_start_ms": None,
            "semantic_quality": None,
            "limitations": [
                "No Ollama or model inference is used.",
                "Random synthetic vectors measure scan costs, not semantic relevance.",
                "Python scan peak excludes database creation and native SQLite allocations.",
                "Process peak RSS includes fixture construction; memory run is untimed.",
            ],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--dimension", type=int, default=768)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run(arguments.count, arguments.dimension, arguments.repeats)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
