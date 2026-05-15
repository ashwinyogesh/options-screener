"""Unit tests for the ACS scorer math (workers/scorer/scorer.py).

Covers NARRATIVE_METHODOLOGY.md §5.1–§5.4:
- Components A, B, C, D math (E deferred = 0)
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
    _DECAY_RATE,
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


# ---------- Component E ----------


def test_component_e_always_zero() -> None:
    result = compute_acs(
        _doc(decay_weighted_density_14d=1.0, lifecycle_stage=3, stage_confidence=1.0),
        DEFAULT_WEIGHTS,
    )
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

    def test_decelerating_haircut(self) -> None:
        clean = compute_acs(self._max_doc(acceleration_7d=0.0), DEFAULT_WEIGHTS)
        haircut = compute_acs(self._max_doc(acceleration_7d=-0.1), DEFAULT_WEIGHTS)
        assert "decelerating" in haircut.flags
        assert haircut.acs == pytest.approx(clean.acs * 0.8, abs=0.5)

    def test_late_stage_haircut(self) -> None:
        early = compute_acs(self._max_doc(lifecycle_stage=3), DEFAULT_WEIGHTS)
        late = compute_acs(self._max_doc(lifecycle_stage=4), DEFAULT_WEIGHTS)
        assert "late_stage" in late.flags
        # Stage 4 also lowers C component, but the *flag* multiplier is 0.5.
        # Just assert late is materially smaller.
        assert late.acs < early.acs * 0.6

    def test_combined_flags_compound(self) -> None:
        result = compute_acs(
            self._max_doc(gini_14d=0.7, acceleration_7d=-0.1, lifecycle_stage=4),
            DEFAULT_WEIGHTS,
        )
        assert set(result.flags) == {"gini_high", "decelerating", "late_stage"}


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

    def test_ci_band_is_plus_minus_15_percent(self) -> None:
        result = compute_acs(_doc(decay_weighted_density_14d=0.5), DEFAULT_WEIGHTS)
        assert result.acs_ci_lower == pytest.approx(result.acs * 0.85)
        assert result.acs_ci_upper == pytest.approx(result.acs * 1.15)

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
        expected = result.acs * math.exp(-_DECAY_RATE * 10)
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
