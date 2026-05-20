"""Unit tests for the smoothing / hysteresis layer (ADR-0029).

Pure-function tests — no Cosmos, no detector orchestration.  Each test
exercises one knob of the smoothing pipeline in isolation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Same sys.path pin pattern as test_detector.py — see conftest.py rationale.
_WORKER_ROOT = str(Path(__file__).resolve().parent.parent)
if _WORKER_ROOT in sys.path:
    sys.path.remove(_WORKER_ROOT)
sys.path.insert(0, _WORKER_ROOT)
for _name in ("main", "config", "detector", "cosmos_client", "smoothing"):
    sys.modules.pop(_name, None)

from smoothing import (  # noqa: E402
    EMA_ALPHA,
    LifecycleState,
    STAGE1_MAX,
    STAGE2_MAX,
    apply_hysteresis,
    breadth_score,
    breadth_to_stage,
    compute_confidence,
    ema_smooth,
    overlay_stage,
)


# ---------------------------------------------------------------------------
# ema_smooth
# ---------------------------------------------------------------------------

class TestEmaSmooth:
    def test_cold_start_takes_first_reading_as_is(self) -> None:
        raw = {"tier1_pct": 0.42}
        out = ema_smooth(raw, prior={})
        assert out["tier1_pct"] == pytest.approx(0.42)

    def test_subsequent_run_blends_at_alpha(self) -> None:
        raw = {"tier1_pct": 0.40}
        prior = {"tier1_pct": 0.10}
        out = ema_smooth(raw, prior)
        # 0.4 * 0.40 + 0.6 * 0.10 = 0.22
        assert out["tier1_pct"] == pytest.approx(0.4 * 0.40 + 0.6 * 0.10)

    def test_missing_input_preserves_prior(self) -> None:
        """Aggregator gap should not zero out the smoothed value."""
        raw = {}  # no metrics this run
        prior = {"tier1_pct": 0.55}
        out = ema_smooth(raw, prior)
        assert out["tier1_pct"] == pytest.approx(0.55)

    def test_non_smoothed_keys_ignored(self) -> None:
        """Only SMOOTHED_KEYS are processed; other keys must not leak in."""
        raw = {"tier1_pct": 0.20, "random_field": 999}
        out = ema_smooth(raw, prior={})
        assert "random_field" not in out

    def test_alpha_at_one_means_no_smoothing(self) -> None:
        raw = {"tier1_pct": 0.80}
        prior = {"tier1_pct": 0.10}
        out = ema_smooth(raw, prior, alpha=1.0)
        assert out["tier1_pct"] == pytest.approx(0.80)


# ---------------------------------------------------------------------------
# breadth_score + breadth_to_stage
# ---------------------------------------------------------------------------

class TestBreadthScore:
    def test_all_zeros_score_is_zero(self) -> None:
        assert breadth_score({}) == pytest.approx(0.0)

    def test_monotone_in_tier1(self) -> None:
        s_low = breadth_score({"tier1_pct": 0.10})
        s_high = breadth_score({"tier1_pct": 0.50})
        assert s_high > s_low

    def test_growth_normalised_at_0_5(self) -> None:
        """contributor_growth=0.5 contributes its full weight (0.3); beyond 0.5 saturates."""
        s_at = breadth_score({"contributor_count_growth_7d": 0.50})
        s_above = breadth_score({"contributor_count_growth_7d": 1.20})
        assert s_at == pytest.approx(0.30)
        assert s_above == pytest.approx(0.30)  # saturates


class TestBreadthToStage:
    @pytest.mark.parametrize(
        "score,expected_stage",
        [
            (0.0, 1),
            (STAGE1_MAX - 0.001, 1),
            (STAGE1_MAX, 2),
            (STAGE2_MAX - 0.001, 2),
            (STAGE2_MAX, 3),
            (0.80, 3),
        ],
    )
    def test_band_boundaries(self, score: float, expected_stage: int) -> None:
        assert breadth_to_stage(score) == expected_stage


# ---------------------------------------------------------------------------
# overlay_stage
# ---------------------------------------------------------------------------

class TestOverlayStage:
    def test_returns_none_when_axis_data_missing(self) -> None:
        assert overlay_stage({"gini_14d": 0.20}) is None

    def test_stage_5_consensus_fires(self) -> None:
        s = overlay_stage(
            {"conviction_bull_share": 0.70, "conviction_researched_share": 0.30, "gini_14d": 0.20}
        )
        assert s == 5

    def test_stage_6_saturation_fires(self) -> None:
        s = overlay_stage(
            {"conviction_bull_share": 0.80, "conviction_researched_share": 0.20, "gini_14d": 0.60}
        )
        assert s == 6

    def test_high_researched_share_blocks_stage_5(self) -> None:
        s = overlay_stage(
            {"conviction_bull_share": 0.70, "conviction_researched_share": 0.50, "gini_14d": 0.20}
        )
        assert s is None


# ---------------------------------------------------------------------------
# apply_hysteresis
# ---------------------------------------------------------------------------

class TestHysteresis:
    def test_cold_start_accepts_target_immediately(self) -> None:
        committed, new_state = apply_hysteresis(target=3, prev_stage=0, state=LifecycleState())
        assert committed == 3
        assert new_state.pending_stage == 0

    def test_held_when_target_matches_prev(self) -> None:
        prior = LifecycleState(pending_stage=3, pending_streak=5)  # any prior pending
        committed, new_state = apply_hysteresis(target=2, prev_stage=2, state=prior)
        assert committed == 2
        # Pending is reset when target == prev.
        assert new_state.pending_stage == 0
        assert new_state.pending_streak == 0

    def test_first_run_at_new_target_sets_pending_holds_prev(self) -> None:
        committed, new_state = apply_hysteresis(target=3, prev_stage=1, state=LifecycleState())
        assert committed == 1
        assert new_state.pending_stage == 3
        assert new_state.pending_streak == 1

    def test_second_consecutive_run_commits_one_step_only(self) -> None:
        prior = LifecycleState(pending_stage=3, pending_streak=1)
        committed, new_state = apply_hysteresis(target=3, prev_stage=1, state=prior)
        assert committed == 2          # ±1 step cap — NOT a direct jump to 3
        assert new_state.pending_stage == 0
        assert new_state.pending_streak == 0

    def test_changing_target_resets_streak(self) -> None:
        prior = LifecycleState(pending_stage=3, pending_streak=1)
        committed, new_state = apply_hysteresis(target=2, prev_stage=1, state=prior)
        assert committed == 1
        assert new_state.pending_stage == 2
        assert new_state.pending_streak == 1

    def test_downward_transition_caps_at_minus_one(self) -> None:
        prior = LifecycleState(pending_stage=1, pending_streak=1)
        committed, new_state = apply_hysteresis(target=1, prev_stage=3, state=prior)
        assert committed == 2          # 3 → 2 only, not direct to 1
        assert new_state.pending_stage == 0

    def test_custom_confirm_runs_three_requires_three(self) -> None:
        state = LifecycleState()
        # First run.
        committed, state = apply_hysteresis(target=3, prev_stage=1, state=state, confirm_runs=3)
        assert committed == 1
        # Second.
        committed, state = apply_hysteresis(target=3, prev_stage=1, state=state, confirm_runs=3)
        assert committed == 1
        # Third — now commits.
        committed, state = apply_hysteresis(target=3, prev_stage=1, state=state, confirm_runs=3)
        assert committed == 2


# ---------------------------------------------------------------------------
# compute_confidence
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_held_committed_lower_than_matched(self) -> None:
        """Mid-transition (committed != target) → 0.5x certainty multiplier."""
        c_match = compute_confidence(score=0.25, target_stage=2, committed_stage=2, dominant_fraction=1.0)
        c_mid = compute_confidence(score=0.25, target_stage=2, committed_stage=1, dominant_fraction=1.0)
        assert c_mid == pytest.approx(c_match * 0.5, rel=1e-3)

    def test_band_center_maxes_proximity(self) -> None:
        """Score at band centre (0.25 for stage 2) yields full proximity factor."""
        center = (STAGE1_MAX + STAGE2_MAX) / 2.0
        c = compute_confidence(score=center, target_stage=2, committed_stage=2, dominant_fraction=1.0)
        assert c == pytest.approx(1.0)

    def test_band_boundary_drops_proximity_to_zero(self) -> None:
        c = compute_confidence(score=STAGE1_MAX, target_stage=2, committed_stage=2, dominant_fraction=1.0)
        # Score sits exactly on the lower boundary of stage 2 → proximity = 0.
        assert c == pytest.approx(0.0, abs=1e-3)


# ---------------------------------------------------------------------------
# LifecycleState round-trip
# ---------------------------------------------------------------------------

class TestLifecycleStateRoundtrip:
    def test_from_doc_with_no_state_yields_defaults(self) -> None:
        s = LifecycleState.from_doc({})
        assert s.smoothed_inputs == {}
        assert s.pending_stage == 0
        assert s.pending_streak == 0

    def test_from_doc_extracts_state_block(self) -> None:
        doc = {
            "lifecycle_state": {
                "smoothed_inputs": {"tier1_pct": 0.42},
                "pending_stage": 3,
                "pending_streak": 1,
            }
        }
        s = LifecycleState.from_doc(doc)
        assert s.smoothed_inputs == {"tier1_pct": 0.42}
        assert s.pending_stage == 3
        assert s.pending_streak == 1

    def test_to_dict_round_trips(self) -> None:
        s = LifecycleState(smoothed_inputs={"tier1_pct": 0.10}, pending_stage=2, pending_streak=1)
        round_tripped = LifecycleState.from_doc({"lifecycle_state": s.to_dict()})
        assert round_tripped == s


def test_ema_alpha_default_constant() -> None:
    """Guard against accidental tuning — change requires methodology doc update."""
    assert EMA_ALPHA == 0.4
