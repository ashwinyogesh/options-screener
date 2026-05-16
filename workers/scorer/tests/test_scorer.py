"""Unit tests for the ACS scorer math (workers/scorer/scorer.py).

Covers NARRATIVE_METHODOLOGY.md §5.1–§5.4:
- Components A, B, C, D, E math
- Adjustments: gini_high, decelerating, late_stage
- Bounds: [0, 100], CI ±15%
- Time decay: ACS(t) = ACS_raw * e^{-0.07 * t}
- Dominant signal fallback (raw sentiment when conviction absent)
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Pin sys.path BEFORE module-level imports of flat worker modules — see
# conftest.py for the per-test variant (this guards collection-time imports).
_WORKER_ROOT = str(Path(__file__).resolve().parent.parent)
if _WORKER_ROOT in sys.path:
    sys.path.remove(_WORKER_ROOT)
sys.path.insert(0, _WORKER_ROOT)
for _name in ("main", "config", "cosmos_client", "kv_secrets", "scorer"):
    sys.modules.pop(_name, None)

from scorer import (  # noqa: E402
    _ACS_TIME_DECAY_RATE,
    _STAGE_MAP,
    _days_since,
    _dominant_signal,
    compute_acs,
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "A_max": 25.0,
    "B_max": 20.0,
    "C_max": 20.0,
    "D_max": 20.0,
    "E_max": 15.0,
}


def _doc(**overrides: object) -> dict:
    """Minimal valid ticker_timeline doc; override per-test."""
    base: dict = {
        "ticker": "NVDA",
        "decay_weighted_density_14d": 0.0,
        "unique_authors_14d": 0,
        "mentions_14d": 0,
        "gini_14d": 0.0,
        "lifecycle_stage": 0,
        "stage_confidence": 0.0,
        "conviction_researched_bull_ratio": 0.0,
        "conviction_researched_bear_ratio": 0.0,
        "conviction_dd_norm": 0.0,
        "acceleration_7d": 0.0,
        "computed_at": datetime.now(tz=timezone.utc).isoformat(),
        # Component E sub-signals (pre-populated by main.py via get_market_confirmation)
        "rs_14d_norm": 0.0,
        "opt_ratio_norm": 0.0,
        "institutional_13f_norm": 0.0,
    }
    base.update(overrides)
    return base


# ---------- Component A ----------


class TestComponentA:
    def test_zero_density_yields_zero(self) -> None:
        result = compute_acs(_doc(), DEFAULT_WEIGHTS)
        assert result.components["A"] == 0.0

    def test_full_density_yields_a_max(self) -> None:
        result = compute_acs(_doc(decay_weighted_density_14d=1.0), DEFAULT_WEIGHTS)
        assert result.components["A"] == pytest.approx(25.0)

    def test_density_clipped_at_one(self) -> None:
        result = compute_acs(_doc(decay_weighted_density_14d=5.0), DEFAULT_WEIGHTS)
        assert result.components["A"] == pytest.approx(25.0)

    def test_half_density(self) -> None:
        result = compute_acs(_doc(decay_weighted_density_14d=0.5), DEFAULT_WEIGHTS)
        assert result.components["A"] == pytest.approx(12.5)


# ---------- Component B ----------


class TestComponentB:
    def test_zero_when_mentions_le_one(self) -> None:
        result = compute_acs(_doc(unique_authors_14d=5, mentions_14d=1), DEFAULT_WEIGHTS)
        assert result.components["B"] == 0.0

    def test_zero_authors_yields_zero(self) -> None:
        result = compute_acs(_doc(unique_authors_14d=0, mentions_14d=20), DEFAULT_WEIGHTS)
        assert result.components["B"] == 0.0

    def test_clipped_to_b_max(self) -> None:
        # High authors / low mentions intentionally inflates B; must clip.
        result = compute_acs(_doc(unique_authors_14d=100, mentions_14d=10), DEFAULT_WEIGHTS)
        assert result.components["B"] == pytest.approx(20.0)

    def test_gini_reduces_b(self) -> None:
        # Use small inputs so B is below the clip ceiling.
        no_gini = compute_acs(
            _doc(unique_authors_14d=1, mentions_14d=10, gini_14d=0.0),
            DEFAULT_WEIGHTS,
        )
        half_gini = compute_acs(
            _doc(unique_authors_14d=1, mentions_14d=10, gini_14d=0.5),
            DEFAULT_WEIGHTS,
        )
        assert no_gini.components["B"] < 20.0  # sanity: not clipped
        assert half_gini.components["B"] == pytest.approx(no_gini.components["B"] * 0.5, abs=1e-3)


# ---------- Component C ----------


class TestComponentC:
    def test_unknown_stage_yields_zero(self) -> None:
        result = compute_acs(_doc(lifecycle_stage=99, stage_confidence=1.0), DEFAULT_WEIGHTS)
        assert result.components["C"] == 0.0

    @pytest.mark.parametrize("stage", list(_STAGE_MAP.keys()))
    def test_full_confidence_scales_with_stage_map(self, stage: int) -> None:
        result = compute_acs(
            _doc(lifecycle_stage=stage, stage_confidence=1.0),
            DEFAULT_WEIGHTS,
        )
        expected = (_STAGE_MAP[stage] / 20.0) * 1.0 * 20.0
        # Bypass post-acs adjustments (late_stage haircut) by reading the
        # raw component, not the final acs.
        assert result.components["C"] == pytest.approx(expected)

    def test_low_confidence_scales_linearly(self) -> None:
        full = compute_acs(_doc(lifecycle_stage=3, stage_confidence=1.0), DEFAULT_WEIGHTS)
        half = compute_acs(_doc(lifecycle_stage=3, stage_confidence=0.5), DEFAULT_WEIGHTS)
        assert half.components["C"] == pytest.approx(full.components["C"] * 0.5)


# ---------- Component D ----------


class TestComponentD:
    def test_zero_thesis_yields_zero(self) -> None:
        result = compute_acs(_doc(), DEFAULT_WEIGHTS)
        assert result.components["D"] == 0.0

    def test_weighted_average_formula(self) -> None:
        result = compute_acs(
            _doc(
                conviction_researched_bull_ratio=0.5,
                conviction_researched_bear_ratio=0.5,
                conviction_dd_norm=0.5,
            ),
            DEFAULT_WEIGHTS,
        )
        # 0.6*0.5 + 0.2*0.5 + 0.2*0.5 = 0.5 → 0.5 * 20 = 10
        assert result.components["D"] == pytest.approx(10.0)

    def test_clipped_to_d_max(self) -> None:
        result = compute_acs(
            _doc(
                conviction_researched_bull_ratio=2.0,
                conviction_researched_bear_ratio=2.0,
                conviction_dd_norm=2.0,
            ),
            DEFAULT_WEIGHTS,
        )
        assert result.components["D"] == pytest.approx(20.0)

    def test_floored_at_zero_for_exit_signal_dominant(self) -> None:
        """conv_norm < 0 (e.g. exit_signal-heavy 14d window) must not push
        Component D negative — every ACS component is bounded in [0, max]."""
        result = compute_acs(
            _doc(
                conviction_researched_bull_ratio=0.0,
                conviction_researched_bear_ratio=0.0,
                conviction_dd_norm=-0.5,  # all exit_signal extreme
            ),
            DEFAULT_WEIGHTS,
        )
        assert result.components["D"] == 0.0


# ---------- Component E ----------


class TestComponentE:
    def test_zero_when_all_signals_absent(self) -> None:
        result = compute_acs(_doc(), DEFAULT_WEIGHTS)
        assert result.components["E"] == 0.0

    def test_full_rs_only_yields_six(self) -> None:
        result = compute_acs(_doc(rs_14d_norm=1.0), DEFAULT_WEIGHTS)
        assert result.components["E"] == pytest.approx(6.0)

    def test_full_opt_only_yields_five(self) -> None:
        result = compute_acs(_doc(opt_ratio_norm=1.0), DEFAULT_WEIGHTS)
        assert result.components["E"] == pytest.approx(5.0)

    def test_full_institutional_only_yields_four(self) -> None:
        result = compute_acs(_doc(institutional_13f_norm=1.0), DEFAULT_WEIGHTS)
        assert result.components["E"] == pytest.approx(4.0)

    def test_all_full_yields_e_max(self) -> None:
        result = compute_acs(
            _doc(rs_14d_norm=1.0, opt_ratio_norm=1.0, institutional_13f_norm=1.0),
            DEFAULT_WEIGHTS,
        )
        # 6 + 5 + 4 = 15 = E_max
        assert result.components["E"] == pytest.approx(15.0)

    def test_capped_at_e_max(self) -> None:
        # Overflow inputs — E must not exceed E_max.
        result = compute_acs(
            _doc(rs_14d_norm=5.0, opt_ratio_norm=5.0, institutional_13f_norm=5.0),
            DEFAULT_WEIGHTS,
        )
        assert result.components["E"] == pytest.approx(15.0)

    def test_partial_signal_linearity(self) -> None:
        half = compute_acs(_doc(rs_14d_norm=0.5), DEFAULT_WEIGHTS)
        full = compute_acs(_doc(rs_14d_norm=1.0), DEFAULT_WEIGHTS)
        assert half.components["E"] == pytest.approx(full.components["E"] * 0.5, abs=1e-4)

    def test_missing_fields_treated_as_zero(self) -> None:
        # Simulate a doc that pre-dates Phase 6.1 (no E fields at all).
        doc = _doc()
        del doc["rs_14d_norm"]
        del doc["opt_ratio_norm"]
        del doc["institutional_13f_norm"]
        result = compute_acs(doc, DEFAULT_WEIGHTS)
        assert result.components["E"] == 0.0


# ---------- Adjustments ----------


class TestAdjustments:
    def _max_doc(self, **overrides: object) -> dict:
        # Maxes out A through D so adjustments are visible.
        base = _doc(
            decay_weighted_density_14d=1.0,  # A=25
            unique_authors_14d=100,
            mentions_14d=10,                 # B clipped to 20
            lifecycle_stage=3,
            stage_confidence=1.0,            # C=20
            conviction_researched_bull_ratio=1.0,  # D=12
        )
        base.update(overrides)
        return base

    def test_no_flags_when_clean(self) -> None:
        result = compute_acs(self._max_doc(), DEFAULT_WEIGHTS)
        assert result.flags == []

    def test_gini_high_haircut(self) -> None:
        clean = compute_acs(self._max_doc(gini_14d=0.0), DEFAULT_WEIGHTS)
        haircut = compute_acs(self._max_doc(gini_14d=0.70), DEFAULT_WEIGHTS)
        assert "gini_high" in haircut.flags
        # Both gini change and 0.6× multiplier apply; check the latter
        # by comparing raw component sums.
        assert haircut.acs < clean.acs

    def test_decelerating_streak_haircut(self) -> None:
        # 3 strictly decreasing daily counts → flag fires.
        buckets = [
            {"day": "2026-05-10", "count": 5, "unique_authors": 3},
            {"day": "2026-05-11", "count": 10, "unique_authors": 5},
            {"day": "2026-05-12", "count": 8, "unique_authors": 4},
            {"day": "2026-05-13", "count": 6, "unique_authors": 3},
            {"day": "2026-05-14", "count": 2, "unique_authors": 2},
        ]
        clean = compute_acs(self._max_doc(daily_buckets=[]), DEFAULT_WEIGHTS)
        haircut = compute_acs(self._max_doc(daily_buckets=buckets), DEFAULT_WEIGHTS)
        assert "decelerating_3d" in haircut.flags
        assert haircut.acs == pytest.approx(clean.acs * 0.8, abs=0.5)

    def test_no_streak_no_flag(self) -> None:
        # Increasing → no flag.
        buckets = [
            {"day": "2026-05-12", "count": 2, "unique_authors": 1},
            {"day": "2026-05-13", "count": 4, "unique_authors": 2},
            {"day": "2026-05-14", "count": 6, "unique_authors": 3},
        ]
        result = compute_acs(self._max_doc(daily_buckets=buckets), DEFAULT_WEIGHTS)
        assert "decelerating_3d" not in result.flags

    def test_short_history_no_streak(self) -> None:
        # Only 2 buckets — cannot detect a 3-day streak.
        buckets = [
            {"day": "2026-05-13", "count": 5, "unique_authors": 2},
            {"day": "2026-05-14", "count": 1, "unique_authors": 1},
        ]
        result = compute_acs(self._max_doc(daily_buckets=buckets), DEFAULT_WEIGHTS)
        assert "decelerating_3d" not in result.flags

    def test_late_stage_haircut(self) -> None:
        early = compute_acs(self._max_doc(lifecycle_stage=3), DEFAULT_WEIGHTS)
        late = compute_acs(self._max_doc(lifecycle_stage=4), DEFAULT_WEIGHTS)
        assert "late_stage" in late.flags
        # Stage 4 also lowers C component, but the *flag* multiplier is 0.5.
        # Just assert late is materially smaller.
        assert late.acs < early.acs * 0.6

    def test_small_cap_haircut_applied(self) -> None:
        clean = compute_acs(self._max_doc(market_cap=200_000_000_000), DEFAULT_WEIGHTS)
        small = compute_acs(self._max_doc(market_cap=50_000_000), DEFAULT_WEIGHTS)
        assert "small_cap" in small.flags
        assert "small_cap" not in clean.flags
        assert small.acs == pytest.approx(clean.acs * 0.85, abs=0.5)

    def test_small_cap_boundary_at_threshold(self) -> None:
        # Exactly $100M should NOT trigger (strict <).
        result = compute_acs(self._max_doc(market_cap=100_000_000), DEFAULT_WEIGHTS)
        assert "small_cap" not in result.flags

    def test_market_cap_missing_skips_haircut(self) -> None:
        result = compute_acs(self._max_doc(), DEFAULT_WEIGHTS)
        assert "small_cap" not in result.flags

    def test_market_cap_zero_skips_haircut(self) -> None:
        # 0 or negative is treated as "unknown" — don't penalize.
        result = compute_acs(self._max_doc(market_cap=0), DEFAULT_WEIGHTS)
        assert "small_cap" not in result.flags

    def test_combined_flags_compound(self) -> None:
        buckets = [
            {"day": "2026-05-12", "count": 9, "unique_authors": 4},
            {"day": "2026-05-13", "count": 6, "unique_authors": 3},
            {"day": "2026-05-14", "count": 2, "unique_authors": 2},
        ]
        result = compute_acs(
            self._max_doc(
                gini_14d=0.7,
                daily_buckets=buckets,
                lifecycle_stage=4,
                market_cap=50_000_000,
            ),
            DEFAULT_WEIGHTS,
        )
        assert set(result.flags) == {
            "gini_high", "decelerating_3d", "late_stage", "small_cap",
        }


# ---------- Bounds & CI ----------


class TestBoundsAndCi:
    def test_acs_within_0_100(self) -> None:
        result = compute_acs(_doc(), DEFAULT_WEIGHTS)
        assert 0.0 <= result.acs <= 100.0

    def test_acs_capped_at_100(self) -> None:
        result = compute_acs(
            _doc(
                decay_weighted_density_14d=1.0,
                unique_authors_14d=1000,
                mentions_14d=10,
                lifecycle_stage=3,
                stage_confidence=1.0,
                conviction_researched_bull_ratio=1.0,
                conviction_dd_norm=1.0,
                conviction_researched_bear_ratio=1.0,
            ),
            DEFAULT_WEIGHTS,
        )
        assert result.acs <= 100.0

    def test_ci_falls_back_to_heuristic_when_no_buckets(self) -> None:
        # No daily_buckets → ±15% heuristic.
        result = compute_acs(_doc(decay_weighted_density_14d=0.5), DEFAULT_WEIGHTS)
        assert result.acs_ci_lower == pytest.approx(result.acs * 0.85, abs=0.01)
        assert result.acs_ci_upper == pytest.approx(result.acs * 1.15, abs=0.01)

    def test_ci_bootstrap_when_buckets_present(self) -> None:
        # 14 days of varied counts → bootstrap CI should produce a band
        # that brackets the point estimate.
        buckets = [
            {"day": f"2026-05-{d:02d}", "count": c, "unique_authors": 3}
            for d, c in zip(range(1, 15), [2, 4, 3, 6, 5, 7, 4, 8, 6, 9, 7, 5, 4, 3])
        ]
        result = compute_acs(
            _doc(
                ticker="NVDA",
                decay_weighted_density_14d=0.5,
                daily_buckets=buckets,
            ),
            DEFAULT_WEIGHTS,
        )
        assert 0.0 <= result.acs_ci_lower <= result.acs
        assert result.acs <= result.acs_ci_upper <= 100.0
        # A non-trivial sample range should produce a non-trivial band.
        assert result.acs_ci_upper - result.acs_ci_lower > 0.0

    def test_ci_lower_clamped_at_zero(self) -> None:
        result = compute_acs(_doc(), DEFAULT_WEIGHTS)
        assert result.acs_ci_lower >= 0.0

    def test_ci_upper_clamped_at_100(self) -> None:
        # Force a high acs by maxing every component.
        result = compute_acs(
            _doc(
                decay_weighted_density_14d=1.0,
                unique_authors_14d=1000,
                mentions_14d=10,
                lifecycle_stage=3,
                stage_confidence=1.0,
                conviction_researched_bull_ratio=1.0,
                conviction_dd_norm=1.0,
                conviction_researched_bear_ratio=1.0,
            ),
            DEFAULT_WEIGHTS,
        )
        assert result.acs_ci_upper <= 100.0

    def test_ci_deterministic_for_same_ticker(self) -> None:
        # Bootstrap RNG is seeded off ticker — two runs match exactly.
        buckets = [
            {"day": f"2026-05-{d:02d}", "count": c, "unique_authors": 3}
            for d, c in zip(range(1, 15), [2, 4, 3, 6, 5, 7, 4, 8, 6, 9, 7, 5, 4, 3])
        ]
        doc = _doc(ticker="ABC", decay_weighted_density_14d=0.5, daily_buckets=buckets)
        first = compute_acs(doc, DEFAULT_WEIGHTS)
        second = compute_acs(doc, DEFAULT_WEIGHTS)
        assert first.acs_ci_lower == second.acs_ci_lower
        assert first.acs_ci_upper == second.acs_ci_upper


# ---------- Time decay ----------


class TestTimeDecay:
    def test_decay_equals_acs_when_fresh(self) -> None:
        result = compute_acs(_doc(decay_weighted_density_14d=0.5), DEFAULT_WEIGHTS)
        # 'computed_at' = now → days_since == 0 → decay_acs == acs.
        assert result.decay_acs == pytest.approx(result.acs)

    def test_decay_reduces_acs_over_time(self) -> None:
        ten_days_ago = (datetime.now(tz=timezone.utc) - timedelta(days=10)).isoformat()
        result = compute_acs(
            _doc(decay_weighted_density_14d=0.5, computed_at=ten_days_ago),
            DEFAULT_WEIGHTS,
        )
        expected = result.acs * math.exp(-_ACS_TIME_DECAY_RATE * 10)
        assert result.decay_acs == pytest.approx(expected, abs=0.5)

    def test_days_since_zero_when_unparseable(self) -> None:
        assert _days_since("") == 0.0
        assert _days_since("not-a-date") == 0.0

    def test_days_since_handles_z_suffix(self) -> None:
        # ISO with 'Z' must parse (e.g. Cosmos may emit 2026-05-14T00:00:00Z).
        five_days_ago = (datetime.now(tz=timezone.utc) - timedelta(days=5))
        iso_z = five_days_ago.isoformat().replace("+00:00", "Z")
        assert _days_since(iso_z) == pytest.approx(5.0, abs=0.1)


# ---------- Dominant signal ----------


class TestDominantSignal:
    def test_picks_highest_ratio(self) -> None:
        doc = _doc(
            conviction_researched_bull_ratio=0.6,
            conviction_researched_bear_ratio=0.2,
            conviction_emotional_bull_ratio=0.1,
        )
        assert _dominant_signal(doc) == "researched_bull"

    def test_falls_back_to_sentiment_when_no_conviction(self) -> None:
        doc = {"bullish_ratio": 0.7, "bearish_ratio": 0.3}
        assert _dominant_signal(doc) == "bullish"

    def test_unknown_when_nothing_set(self) -> None:
        assert _dominant_signal({}) == "unknown"
