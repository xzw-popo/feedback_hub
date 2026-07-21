"""Opt-in baseline for the deployed exact-search capacity."""
from __future__ import annotations

import json
import time

import numpy as np
import pytest


@pytest.mark.performance
def test_exact_search_127k_x_1024_baseline(tmp_path):
    """Only runs when explicitly selected with ``-m performance``."""
    from feedback_hub.vector_index.search import _rank_scores

    rows, dimension, limit = 127_000, 1024, 20
    rng = np.random.default_rng(20260721)
    path = tmp_path / "vectors.npy"
    matrix = np.lib.format.open_memmap(path, mode="w+", dtype=np.float32, shape=(rows, dimension))
    for offset in range(0, rows, 1_000):
        values = rng.standard_normal((min(1_000, rows - offset), dimension), dtype=np.float32)
        matrix[offset:offset + len(values)] = values / np.linalg.norm(values, axis=1, keepdims=True)
    del matrix
    started = time.perf_counter()
    vectors = np.load(path, mmap_mode="r", allow_pickle=False)
    cold_load_seconds = time.perf_counter() - started
    query = np.asarray(vectors[123], dtype=np.float32)
    timings, repeated = [], []
    for _ in range(10):
        started = time.perf_counter()
        scores = np.einsum("d,nd->n", query, vectors, dtype=np.float32)
        ids = _rank_scores(scores, np.arange(rows), limit)
        timings.append((time.perf_counter() - started) * 1000)
        repeated.append(ids.tolist())
        assert len(ids) == limit
        assert np.isfinite(scores[ids]).all()
    assert all(ids == repeated[0] for ids in repeated)
    print(json.dumps({"cold_load_seconds": cold_load_seconds, "query_p50_ms": float(np.percentile(timings, 50)), "query_p95_ms": float(np.percentile(timings, 95))}, sort_keys=True))
