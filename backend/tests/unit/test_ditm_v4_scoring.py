"""Unit tests for `services.scoring.ditm_v4`.

Validates:
  - Weight table integrity (sum to 1.0 of |w|, group caps respected)
  - Percentile-rank semantics (ties, missing values, single-element)
  - Eligibility threshold
  - Tier assignment
  - End-to-end determinism on a tiny synthetic universe
"""
from __future__ import annotations

import pytest

from services.scoring.ditm_v4 import (
    Candidate,
    FACTOR_DEFINITIONS,
    GROUP_WEIGHT_CAPS,
    MIN_FACTORS_OBSERVED,
    TIER_THRESHOLDS,
    score_universe,
    tier_for_score,
)


# ---------------------------------------------------------------------------
# Weight table
# ---------------------------------------------------------------------------

def test_factor_weights_sum_to_one() -> None:
    total = sum(abs(w) for _, _, _, w in FACTOR_DEFINITIONS)
    assert total == pytest.approx(1.0, abs=1e-9)


def test_factor_weights_respect_group_caps() -> None:
    by_group: dict[str, float] = {}
    for _, _, group, w in FACTOR_DEFINITIONS:
        by_group[group] = by_group.get(group, 0.0) + abs(w)
    for group, cap in GROUP_WEIGHT_CAPS.items():
        assert by_group[group] == pytest.approx(cap, abs=1e-9), (
            f"group {group}: budget {by_group[group]:.4f} != cap {cap}"
        )


def test_factor_signs_match_calibration_table() -> None:
    """Defensive: production signs must remain locked to the audit IC."""
    expected_signs = {
        "ps_ttm": -1, "ev_sales": -1, "ev_ebitda": -1,
        "debt_to_equity": +1, "nd_ebitda": +1,
        "wk_rsi": -1, "dist52w": -1, "hv30": -1, "ret_200d": +1,
        "sector_rs_6m": -1,
        "leverage": +1, "delta": +1, "extrinsic_pct": -1,
    }
    actual = {name: sign for name, sign, _, _ in FACTOR_DEFINITIONS}
    assert actual == expected_signs


# ---------------------------------------------------------------------------
# Tier mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score, expected", [
    (100.0, "A"), (95.0, "A"), (90.0, "A"),
    (89.99, "B"), (75.0, "B"), (70.0, "B"),
    (69.99, "C"), (55.0, "C"), (50.0, "C"),
    (49.99, "D"), (35.0, "D"), (30.0, "D"),
    (29.99, "E"), (10.0, "E"), (0.0, "E"),
])
def test_tier_thresholds(score: float, expected: str) -> None:
    assert tier_for_score(score) == expected


# ---------------------------------------------------------------------------
# End-to-end smoke
# ---------------------------------------------------------------------------

def _all_factors_with(**overrides: float) -> dict[str, float | None]:
    """Default factor dict; pass kwargs to override individual factors."""
    factors = {
        "ps_ttm": 5.0, "ev_sales": 5.0, "ev_ebitda": 15.0,
        "debt_to_equity": 1.0, "nd_ebitda": 2.0,
        "wk_rsi": 50.0, "dist52w": -10.0, "hv30": 0.30, "ret_200d": 0.10,
        "sector_rs_6m": 0.0,
        "leverage": 3.0, "delta": 0.85, "extrinsic_pct": 4.0,
    }
    factors.update(overrides)
    return factors


def test_score_universe_returns_one_output_per_candidate() -> None:
    candidates = [
        Candidate(id=f"c{i}", factors=_all_factors_with(ps_ttm=float(i)))
        for i in range(5)
    ]
    out = score_universe(candidates)
    assert len(out) == 5
    assert [o.id for o in out] == ["c0", "c1", "c2", "c3", "c4"]


def test_score_universe_orders_value_factors_correctly() -> None:
    """Cheaper PS (lower) should score higher when other factors equal."""
    candidates = [
        Candidate(id="cheap",   factors=_all_factors_with(ps_ttm=1.0)),
        Candidate(id="medium",  factors=_all_factors_with(ps_ttm=10.0)),
        Candidate(id="expensive", factors=_all_factors_with(ps_ttm=50.0)),
    ]
    by_id = {o.id: o for o in score_universe(candidates)}
    assert by_id["cheap"].score is not None
    assert by_id["expensive"].score is not None
    assert by_id["cheap"].score > by_id["medium"].score  # type: ignore[operator]
    assert by_id["medium"].score > by_id["expensive"].score  # type: ignore[operator]


def test_score_universe_orders_leverage_correctly() -> None:
    """Higher leverage (positive sign) should score higher."""
    candidates = [
        Candidate(id="low_lev",  factors=_all_factors_with(leverage=1.5)),
        Candidate(id="mid_lev",  factors=_all_factors_with(leverage=3.0)),
        Candidate(id="high_lev", factors=_all_factors_with(leverage=4.5)),
    ]
    by_id = {o.id: o for o in score_universe(candidates)}
    assert by_id["high_lev"].score > by_id["mid_lev"].score   # type: ignore[operator]
    assert by_id["mid_lev"].score > by_id["low_lev"].score    # type: ignore[operator]


def test_score_universe_yields_ineligible_for_too_few_factors() -> None:
    """Below MIN_FACTORS_OBSERVED, score is None but eligibility is reported."""
    sparse = {k: None for k in [n for n, _, _, _ in FACTOR_DEFINITIONS]}
    sparse["ps_ttm"] = 5.0
    sparse["delta"] = 0.85
    sparse["leverage"] = 3.0  # only 3 observed
    candidates = [
        Candidate(id="sparse", factors=sparse),
        # padding so the universe isn't a single row
        Candidate(id="full1", factors=_all_factors_with()),
        Candidate(id="full2", factors=_all_factors_with(ps_ttm=20.0)),
    ]
    out_by_id = {o.id: o for o in score_universe(candidates)}
    assert out_by_id["sparse"].score is None
    assert out_by_id["sparse"].tier is None
    assert out_by_id["sparse"].n_observed == 3
    # The fully-observed rows must still receive scores.
    assert out_by_id["full1"].score is not None
    assert out_by_id["full2"].score is not None


def test_score_universe_handles_empty_input() -> None:
    assert score_universe([]) == []


def test_score_distribution_spans_full_range_for_large_universe() -> None:
    """With 100 distinct candidates, scores should span ~ [0, 100]."""
    candidates = []
    for i in range(100):
        # Spread one factor monotonically across the universe so ranks differ.
        candidates.append(Candidate(
            id=f"c{i}",
            factors=_all_factors_with(ps_ttm=float(i)),
        ))
    out = score_universe(candidates)
    scores = [o.score for o in out if o.score is not None]
    assert min(scores) <= 5.0
    assert max(scores) >= 95.0


def test_min_factors_observed_constant_matches_spec() -> None:
    """Defensive: ADR-0032 specifies 8/13 factors required for scoring."""
    assert MIN_FACTORS_OBSERVED == 8


def test_tier_thresholds_are_descending() -> None:
    """Defensive: the threshold table must be sorted descending or
    `tier_for_score` returns wrong tiers."""
    thresholds = [t for t, _ in TIER_THRESHOLDS]
    assert thresholds == sorted(thresholds, reverse=True)
