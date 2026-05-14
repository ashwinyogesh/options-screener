"""Regression tests for cluster() edge cases.

Phase 5 ramp-up produces tickers with very few embedded signals in the
72h window. HDBSCAN raises ValueError on n=1; cluster() must short-circuit.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Pin sys.path BEFORE module-level imports so `detector` resolves to this
# worker (workers/narrative-detector) and not a sibling worker that may have
# already been imported under a clashing flat module name. The autouse
# fixture in conftest.py handles per-test isolation; this block handles the
# import-time collection phase.
_WORKER_ROOT = str(Path(__file__).resolve().parent.parent)
if _WORKER_ROOT in sys.path:
    sys.path.remove(_WORKER_ROOT)
sys.path.insert(0, _WORKER_ROOT)
for _name in ("main", "config", "detector", "cosmos_client"):
    sys.modules.pop(_name, None)

from detector import ClusterResult, cluster  # noqa: E402


def test_cluster_empty_returns_trivial_result() -> None:
    result = cluster([])

    assert isinstance(result, ClusterResult)
    assert result.labels == []
    assert result.n_clusters == 0
    assert result.dominant_cluster == -1
    assert result.dominant_fraction == 0.0


def test_cluster_single_sample_does_not_raise() -> None:
    """HDBSCAN raises ValueError on n=1; cluster() must short-circuit."""
    result = cluster([[0.1] * 1536])

    assert result.labels == [-1]
    assert result.n_clusters == 0
    assert result.dominant_cluster == -1
    assert result.dominant_fraction == 0.0


def test_cluster_below_min_cluster_size_returns_all_noise() -> None:
    """With min_cluster_size=3 and n=2, every point is noise by definition."""
    embeddings = [[0.1] * 1536, [0.2] * 1536]

    result = cluster(embeddings, min_cluster_size=3)

    assert result.labels == [-1, -1]
    assert result.n_clusters == 0


def test_cluster_at_min_cluster_size_runs_hdbscan() -> None:
    """n == min_cluster_size must NOT short-circuit — HDBSCAN handles it.

    The exact cluster assignment depends on sklearn's HDBSCAN internals
    (degenerate inputs may yield all noise); we only assert that the call
    completes and returns one label per input.
    """
    embeddings = [[1.0, 0.0] + [0.0] * 1534] * 3

    result = cluster(embeddings, min_cluster_size=3)

    assert len(result.labels) == 3
