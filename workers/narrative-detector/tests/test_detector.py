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


def _cluster(
    dominant_fraction: float = 0.8,
    n_clusters: int = 1,
    n_embedded: int = 10,
) -> ClusterResult:
    """Minimal ClusterResult stub for stage-rule tests."""
    return ClusterResult(
        labels=[0, 0, 0],
        n_clusters=n_clusters,
        dominant_cluster=0,
        dominant_fraction=dominant_fraction,
        n_embedded=n_embedded,
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

    def test_below_n_min_embedded_returns_stage_zero_preserves_state(self) -> None:
        """n_embedded below the floor → stage 0 (genuine insufficient data)."""
        prior = LifecycleState(smoothed_inputs={"tier1_pct": 0.5}, pending_stage=2, pending_streak=1)
        stage, conf, new_state = assign_stage(
            {"tier1_pct": 0.30},
            _cluster(n_clusters=0, n_embedded=2),  # well below N_MIN_EMBEDDED=5
            prior_state=prior, prev_stage=2,
        )
        assert stage == 0
        assert conf == 0.0
        # Prior state must carry over unchanged.
        assert new_state is prior

    def test_polysemic_ticker_still_classifies(self) -> None:
        """n_clusters == 0 but n_embedded >= floor → GOOGL fix: still assign a stage.

        This is the headline behaviour added by the ADR-0030 amendment.  A
        megacap with 18 embedded posts spanning multiple sub-themes (cloud /
        AI / antitrust) produces ``n_clusters == 0`` after the intra-cluster
        similarity floor demotes its low-coherence clusters to noise.  The
        old gate sent it to stage 0; the new gate honours the underlying
        breadth metrics and classifies normally — at lower confidence.
        """
        timeline = {
            "tier1_pct": 0.30,
            "contributor_count_growth_7d": 0.50,
            "dd_post_ratio": 0.20,
        }
        stage, conf, _ = assign_stage(
            timeline,
            _cluster(dominant_fraction=0.0, n_clusters=0, n_embedded=15),
        )
        # Cold start + high breadth → stage 3 committed immediately.
        assert stage == 3
        # Coherence floored at 0.3, volume saturated at n=15 → confidence > 0.
        assert conf > 0.0

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
        """Higher cluster dominance → higher confidence (above the floor)."""
        timeline = {"tier1_pct": 0.05, "financial_term_density": 0.20}
        _, conf_high, _ = assign_stage(timeline, _cluster(dominant_fraction=1.0))
        _, conf_mid, _ = assign_stage(timeline, _cluster(dominant_fraction=0.5))
        # 0.5 and 1.0 are both above the 0.3 coherence floor, so they pass
        # through proportionally.
        assert conf_mid < conf_high
        assert conf_mid == pytest.approx(conf_high * 0.5, rel=0.05)

    def test_coherence_floor_lifts_polysemic_confidence(self) -> None:
        """dominant_fraction below COHERENCE_FLOOR (0.3) is clamped up.

        Without the floor, a polysemic cluster (dom_frac=0.0) would land at
        zero confidence and effectively disappear from the screener.  The
        floor ensures it's still ranked, just at lower confidence than a
        coherent cluster.
        """
        timeline = {"tier1_pct": 0.05, "financial_term_density": 0.20}
        _, conf_zero, _ = assign_stage(timeline, _cluster(dominant_fraction=0.0))
        _, conf_floor, _ = assign_stage(timeline, _cluster(dominant_fraction=0.3))
        # Both are clamped to the same 0.3 floor → equal confidence.
        assert conf_zero == pytest.approx(conf_floor, rel=1e-3)
        assert conf_zero > 0.0

    def test_volume_factor_dampens_thin_clusters(self) -> None:
        """Confidence at n_embedded=5 should be exactly half of n_embedded=10."""
        timeline = {"tier1_pct": 0.05, "financial_term_density": 0.20}
        _, conf_full, _ = assign_stage(
            timeline, _cluster(dominant_fraction=1.0, n_embedded=10),
        )
        _, conf_thin, _ = assign_stage(
            timeline, _cluster(dominant_fraction=1.0, n_embedded=5),
        )
        # Linear ramp: 5/10 = 0.5x.  Above 10 the factor saturates.
        assert conf_thin == pytest.approx(conf_full * 0.5, rel=1e-3)

    def test_volume_factor_saturates_above_threshold(self) -> None:
        """Confidence does not keep rising past n_embedded == N_VOLUME_FULL."""
        timeline = {"tier1_pct": 0.05, "financial_term_density": 0.20}
        _, conf_10, _ = assign_stage(
            timeline, _cluster(dominant_fraction=1.0, n_embedded=10),
        )
        _, conf_100, _ = assign_stage(
            timeline, _cluster(dominant_fraction=1.0, n_embedded=100),
        )
        assert conf_10 == pytest.approx(conf_100, rel=1e-3)
