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
for _name in ("main", "config", "detector", "cosmos_client", "smoothing"):
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
# assign_stage — smoothed inputs + monotone hysteresis (ADR-0029)
# ---------------------------------------------------------------------------

from detector import assign_stage  # noqa: E402
from smoothing import LifecycleState  # noqa: E402


def _cluster(dominant_fraction: float = 0.8, n_clusters: int = 1) -> ClusterResult:
    """Minimal ClusterResult stub for stage-rule tests."""
    return ClusterResult(
        labels=[0, 0, 0],
        n_clusters=n_clusters,
        dominant_cluster=0,
        dominant_fraction=dominant_fraction,
    )


class TestAssignStage:
    """Tests for ADR-0029 stable lifecycle stage assignment.

    Key invariants verified:
      * n_clusters == 0 → stage 0, prior state preserved.
      * Cold start (prev_stage=0) accepts target stage immediately.
      * Adjacent stage transitions require 2 confirmation runs.
      * Movement is capped to ±1 stage per commit (no skip).
      * Smoothed inputs are persisted on the returned state.
    """

    def test_no_clusters_returns_stage_zero_preserves_state(self) -> None:
        prior = LifecycleState(smoothed_inputs={"tier1_pct": 0.5}, pending_stage=2, pending_streak=1)
        stage, conf, new_state = assign_stage(
            {"tier1_pct": 0.30}, _cluster(n_clusters=0),
            prior_state=prior, prev_stage=2,
        )
        assert stage == 0
        assert conf == 0.0
        # Stage 0 means insufficient data — prior state must carry over unchanged.
        assert new_state is prior

    def test_cold_start_accepts_target_immediately(self) -> None:
        """prev_stage=0 → no hysteresis, target stage is committed at once."""
        timeline = {"tier1_pct": 0.05, "financial_term_density": 0.20,
                    "dd_post_ratio": 0.0, "contributor_count_growth_7d": 0.0}
        stage, _, _ = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        # Low tier1, low growth, low dd → breadth score < 0.15 → stage 1.
        assert stage == 1

    def test_cold_start_stage_3_when_breadth_high(self) -> None:
        """Wide-spread narrative (high tier1, growth, dd) → stage 3 on cold start."""
        timeline = {
            "tier1_pct": 0.60,
            "contributor_count_growth_7d": 0.50,
            "dd_post_ratio": 0.30,
        }
        stage, _, _ = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        assert stage == 3

    def test_held_stage_resets_pending(self) -> None:
        """Target == prev_stage → return prev_stage, clear pending counters."""
        prior = LifecycleState(smoothed_inputs={"tier1_pct": 0.05}, pending_stage=2, pending_streak=1)
        timeline = {"tier1_pct": 0.05}
        stage, _, new_state = assign_stage(
            timeline, _cluster(dominant_fraction=1.0),
            prior_state=prior, prev_stage=1,
        )
        assert stage == 1
        assert new_state.pending_stage == 0
        assert new_state.pending_streak == 0

    def test_single_run_at_new_target_does_not_commit(self) -> None:
        """First observation of a higher target → hold prev, set pending."""
        prior = LifecycleState(smoothed_inputs={"tier1_pct": 0.05})
        timeline = {"tier1_pct": 0.60, "contributor_count_growth_7d": 0.50, "dd_post_ratio": 0.30}
        stage, _, new_state = assign_stage(
            timeline, _cluster(dominant_fraction=1.0),
            prior_state=prior, prev_stage=1,
        )
        # Hysteresis: prev=1 holds; target=3 is pending after 1 run (< confirm_runs=2).
        assert stage == 1
        assert new_state.pending_stage == 3
        assert new_state.pending_streak == 1

    def test_two_runs_at_same_target_commits_one_step(self) -> None:
        """Second consecutive observation commits a ±1 stage move toward target."""
        # First run already saw target=3 once (streak=1); EMA already saturated.
        prior = LifecycleState(
            smoothed_inputs={"tier1_pct": 0.60, "contributor_count_growth_7d": 0.50, "dd_post_ratio": 0.30},
            pending_stage=3,
            pending_streak=1,
        )
        timeline = {"tier1_pct": 0.60, "contributor_count_growth_7d": 0.50, "dd_post_ratio": 0.30}
        stage, _, new_state = assign_stage(
            timeline, _cluster(dominant_fraction=1.0),
            prior_state=prior, prev_stage=1,
        )
        # Committed +1 step (1 → 2), NOT a direct jump to 3.
        assert stage == 2
        assert new_state.pending_stage == 0
        assert new_state.pending_streak == 0

    def test_changing_target_resets_pending_streak(self) -> None:
        """Different target this run than last → pending_streak resets to 1."""
        prior = LifecycleState(smoothed_inputs={"tier1_pct": 0.60}, pending_stage=3, pending_streak=1)
        timeline = {"tier1_pct": 0.20, "dd_post_ratio": 0.15}
        stage, _, new_state = assign_stage(
            timeline, _cluster(dominant_fraction=1.0),
            prior_state=prior, prev_stage=1,
        )
        # Target is now 2 (not 3), so pending resets to 2 with streak=1, prev=1 held.
        assert stage == 1
        assert new_state.pending_stage == 2
        assert new_state.pending_streak == 1

    def test_overlay_stage_5_consensus(self) -> None:
        """Axis overlay fires when bull/researched/gini conditions hold (cold start)."""
        timeline = {
            "tier1_pct": 0.30,
            "conviction_bull_share": 0.70,
            "conviction_researched_share": 0.30,
            "gini_14d": 0.20,
        }
        stage, _, _ = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        assert stage == 5

    def test_overlay_stage_6_saturation(self) -> None:
        timeline = {
            "tier1_pct": 0.30,
            "conviction_bull_share": 0.80,
            "conviction_researched_share": 0.20,
            "gini_14d": 0.60,
        }
        stage, _, _ = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        assert stage == 6

    def test_overlay_skipped_when_axis_data_absent(self) -> None:
        """No axis data → overlay returns None → stage falls back to breadth band."""
        timeline = {"tier1_pct": 0.05, "financial_term_density": 0.20, "gini_14d": 0.20}
        stage, _, _ = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        assert stage not in (5, 6)

    def test_smoothed_inputs_persisted_on_new_state(self) -> None:
        """EMA-smoothed inputs flow through to the returned state for next run."""
        prior = LifecycleState(smoothed_inputs={"tier1_pct": 0.10})
        timeline = {"tier1_pct": 0.30}
        _, _, new_state = assign_stage(
            timeline, _cluster(dominant_fraction=1.0),
            prior_state=prior, prev_stage=1,
        )
        # EMA: 0.4*0.30 + 0.6*0.10 = 0.18
        assert new_state.smoothed_inputs["tier1_pct"] == pytest.approx(0.18, abs=1e-6)

    def test_confidence_scales_with_dominant_fraction(self) -> None:
        """Lower cluster dominance → proportionally lower confidence."""
        timeline = {"tier1_pct": 0.05, "financial_term_density": 0.20}
        _, conf_high, _ = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        _, conf_low, _ = assign_stage(timeline, _cluster(dominant_fraction=0.5))
        assert conf_low < conf_high
        assert conf_low == pytest.approx(conf_high * 0.5, rel=0.05)
