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


# ---------------------------------------------------------------------------
# assign_stage — §4 lifecycle stage rules
# ---------------------------------------------------------------------------

from detector import assign_stage  # noqa: E402


def _cluster(dominant_fraction: float = 0.8, n_clusters: int = 1) -> ClusterResult:
    """Minimal ClusterResult stub for stage-rule tests."""
    return ClusterResult(
        labels=[0, 0, 0],
        n_clusters=n_clusters,
        dominant_cluster=0,
        dominant_fraction=dominant_fraction,
    )


class TestAssignStage:
    def test_no_clusters_returns_stage_zero(self) -> None:
        """n_clusters == 0 → stage 0 'insufficient data' regardless of timeline."""
        stage, conf = assign_stage({"tier1_pct": 0.30, "dd_post_ratio": 0.20}, _cluster(n_clusters=0))
        assert stage == 0
        assert conf == 0.0

    def test_stage_1_niche_technical(self) -> None:
        """tier1_pct < 0.20 AND financial_term_density >= 0.15 → stage 1."""
        timeline = {"tier1_pct": 0.10, "financial_term_density": 0.20}
        stage, conf = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        assert stage == 1
        assert conf == pytest.approx(0.7)  # base × 0.7 multiplier

    def test_stage_2_early_conviction(self) -> None:
        """tier1_pct ∈ [0.20, 0.50] AND dd_post_ratio >= 0.10 AND gini < 0.45."""
        timeline = {"tier1_pct": 0.30, "dd_post_ratio": 0.15, "gini_14d": 0.30}
        stage, conf = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        assert stage == 2
        assert conf == pytest.approx(1.0)

    def test_stage_3_expanding_awareness(self) -> None:
        """contributor_count_growth_7d >= 0.30 → stage 3, overrides any earlier match."""
        timeline = {
            "tier1_pct": 0.10,                 # would satisfy stage 1
            "financial_term_density": 0.20,
            "contributor_count_growth_7d": 0.35,
        }
        stage, conf = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        assert stage == 3

    def test_stage_5_consensus(self) -> None:
        """emotional_bull_ratio >= 0.50 AND gini_14d < 0.30 → stage 5 (overrides 1/2/3)."""
        timeline = {
            "tier1_pct": 0.30, "dd_post_ratio": 0.15, "gini_14d": 0.20,
            "conviction_emotional_bull_ratio": 0.55,
        }
        stage, conf = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        assert stage == 5
        assert conf == pytest.approx(0.85)

    def test_stage_6_saturation_overrides_consensus(self) -> None:
        """emotional_bull >= 0.65 AND gini >= 0.55 → stage 6 (highest priority)."""
        timeline = {
            "conviction_emotional_bull_ratio": 0.70,
            "gini_14d": 0.60,
        }
        stage, conf = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        assert stage == 6
        assert conf == pytest.approx(0.90)

    def test_catch_all_assigns_stage_1_low_confidence(self) -> None:
        """If no rule matches, stage defaults to 1 at 0.4 × dominant_fraction."""
        # tier1_pct in middle, no DD, no growth, no emotional spike — no rule fires.
        timeline = {"tier1_pct": 0.10, "financial_term_density": 0.05}
        stage, conf = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        assert stage == 1
        assert conf == pytest.approx(0.4)

    def test_confidence_scales_with_dominant_fraction(self) -> None:
        """Lower cluster dominance → proportionally lower stage confidence."""
        timeline = {"tier1_pct": 0.30, "dd_post_ratio": 0.15, "gini_14d": 0.30}
        _, conf_high = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        _, conf_low = assign_stage(timeline, _cluster(dominant_fraction=0.5))
        assert conf_low == pytest.approx(0.5)
        assert conf_high > conf_low
