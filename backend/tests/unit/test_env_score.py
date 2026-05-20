"""
Unit tests for `services.scoring.env.compute_env_score`.

v3.3 (current): IVP 35 + Tr 15 + SMA 5 + SLP 5 + RSI 20 + OI 20 = 100.

Probes:
- IV Percentile (IVP) curve elbows at the 30th/50th/75th/90th boundaries.
- SMA alignment (5 pts) and SMA50 slope (5 pts) elbows.
- 52W high distance and RSI direction-aware divergence (CSP vs CC).
- Chain median OI log scale and cap.
- Earnings penalty.
- Back-compat params iv_rank, iv_hv_ratio, iv_stale, dte are accepted but
  confirmed to have zero effect on the score (explicitly unused since v3.3).

These tests do NOT pin the exact 0–100 outputs at every input — that's the
characterization tests' job. They probe the *shape* of each factor at its
documented boundaries so that calibration drift is caught at the unit level.
"""
from __future__ import annotations

import pytest

from services.scoring.env import compute_env_score


# Default inputs that produce a "neutral" environment (zero on every factor).
# Individual tests override one field at a time.
def _neutral_kwargs() -> dict:
    return {
        "iv_rank": 0.0,
        "iv_hv_ratio": 0.0,
        "price_above_sma50": False,
        "sma50_above_sma200": False,
        "dist_from_52w_high_pct": -50.0,
        "rsi": 50.0,            # CSP: 42–62 sweet spot → 20 pts in v3
        "chain_median_oi": 0.0,
        "earnings_within_dte": False,
        "direction": "csp",
        "dte": 0,
        "iv_stale": False,
        "sma_ratio": 0.0,         # v3.1: below 0.98 → SMA=0
        "sma50_slope_pct": 0.0,   # v3.1: flat → SLP=0
    }


# --- iv_rank back-compat (dropped in v3) ------------------------------------

def test_env_iv_rank_is_ignored_in_v3():
    """iv_rank is a back-compat parameter; v3 dropped HV Rank (redundant with
    strike-side IV Percentile). Changing it must not affect the score."""
    base = _neutral_kwargs()
    score_low, _ = compute_env_score(**{**base, "iv_rank": 0.0})
    score_high, _ = compute_env_score(**{**base, "iv_rank": 95.0})
    assert score_low == pytest.approx(score_high, abs=0.01)


# --- IV Percentile factor (35 pts) — v3.3 primary vol signal ---------------
# Replaces IV/HV Ratio. Curve: <30th=0, 30-50th→0→10, 50-75th→10→25,
# 75-90th→25→35, ≥90th=35.
# Baseline (neutral_kwargs has iv_percentile=None): IVP contributes 0 pts.
# Isolation: subtract the RSI=50 plateau (20 pts) from the total.

@pytest.mark.parametrize(
    "pct, expected_pts",
    [
        (95.0, 35.0),   # above 90th ceiling → full credit
        (90.0, 35.0),   # exactly at ceiling
        (82.5, 30.0),   # midpoint 75–90: 25 + (7.5/15)*10 = 30
        (75.0, 25.0),   # lower elbow of upper lerp
        (62.5, 17.5),   # midpoint 50–75: 10 + (12.5/25)*15 = 17.5
        (50.0, 10.0),   # lower elbow of mid lerp
        (40.0,  5.0),   # midpoint 30–50: (10/20)*10 = 5
        (30.0,  0.0),   # lower boundary — no credit below 30th
        (15.0,  0.0),   # well below 30th
    ],
)
def test_env_iv_percentile_factor_at_elbows(pct: float, expected_pts: float):
    kw = _neutral_kwargs()
    kw["iv_percentile"] = pct
    score, detail = compute_env_score(**kw)
    isolated = score - 20.0  # subtract the RSI=50 plateau (20 pts)
    assert isolated == pytest.approx(expected_pts, abs=0.15)
    assert "IVP:" in detail


def test_env_iv_percentile_none_awards_zero():
    """When iv_percentile is None (IV data unavailable), IVP contributes 0 pts."""
    kw = _neutral_kwargs()
    # iv_percentile is not set → defaults to None inside compute_env_score
    score, _ = compute_env_score(**kw)
    # Only the 50-RSI sweet spot contributes (20 pts).
    assert score == pytest.approx(20.0, abs=0.1)


# --- Back-compat: iv_hv_ratio and iv_stale are explicitly unused in v3.3 ---

def test_env_iv_hv_ratio_is_ignored_in_v33():
    """iv_hv_ratio is a back-compat parameter; v3.3 dropped it in favour of
    iv_percentile. Changing its value must not affect the score."""
    base = _neutral_kwargs()
    score_low, _  = compute_env_score(**{**base, "iv_hv_ratio": 0.0})
    score_high, _ = compute_env_score(**{**base, "iv_hv_ratio": 2.5})
    assert score_low == pytest.approx(score_high, abs=0.01)


def test_env_iv_stale_is_ignored_in_v33():
    """iv_stale is a back-compat parameter; v3.3 dropped the IV/HV factor so
    the stale gate has nothing to zero out. Toggling it must not affect score."""
    base = _neutral_kwargs()
    score_fresh, _ = compute_env_score(**{**base, "iv_stale": False})
    score_stale, _ = compute_env_score(**{**base, "iv_stale": True})
    assert score_fresh == pytest.approx(score_stale, abs=0.01)


# --- SMA alignment factor (5 pts) — v3.1 restored signal -------------------

@pytest.mark.parametrize(
    "sma_ratio, expected_pts",
    [
        (1.05, 5.0),    # strong alignment
        (1.01, 3.0),    # borderline above 1.0
        (0.99, 1.5),    # borderline below 1.0
        (0.95, 0.0),    # below 0.98 threshold
    ],
)
def test_env_sma_alignment_factor_elbows(sma_ratio: float, expected_pts: float):
    kw = _neutral_kwargs()
    kw["sma_ratio"] = sma_ratio
    score, _ = compute_env_score(**kw)
    # Subtract baseline: rsi=50 in CSP sweet-spot = 20 pts; everything else = 0
    sma_pts = score - 20.0
    assert sma_pts == pytest.approx(expected_pts, abs=0.01)


# --- SMA slope factor (5 pts) — v3.1 momentum confirmation ------------------

@pytest.mark.parametrize(
    "slope, expected_pts",
    [
        (0.6, 5.0),    # above 0.5% ceiling → full credit
        (0.5, 5.0),    # exactly at ceiling
        (0.35, 4.0),   # lerp 3→5 at midpoint 0.2–0.5%
        (0.2, 3.0),    # lower elbow of upper lerp
        (0.1, 1.5),    # midpoint of 0–0.2% ramp
        (0.0, 0.0),    # flat → 0
        (-0.1, 0.0),   # declining → 0
    ],
)
def test_env_sma_slope_factor_elbows(slope: float, expected_pts: float):
    kw = _neutral_kwargs()
    kw["sma50_slope_pct"] = slope
    score, _ = compute_env_score(**kw)
    slp_pts = score - 20.0
    assert slp_pts == pytest.approx(expected_pts, abs=0.15)


# --- Direction-aware divergence: 52W and RSI -------------------------------

def test_env_direction_diverges_at_52w_proximity():
    """At 0% below the 52W high, CSP awards 15 Tr pts (v3.1) while CC awards 0
    (assignment risk near all-time high). Divergence = 15 pts."""
    kw = _neutral_kwargs()
    kw["dist_from_52w_high_pct"] = 0.0
    csp_score, _ = compute_env_score(**kw)

    kw["direction"] = "cc"
    cc_score, _ = compute_env_score(**kw)

    # v3.1: CSP awards 15 Tr pts at 52W high; CC awards 0.
    assert csp_score - cc_score == pytest.approx(15.0, abs=0.2)


def test_env_direction_diverges_at_rsi_60():
    """RSI 60: in the CSP sweet-spot (42–62 → 10 pts) but on the CC ceiling
    decay (58 < rsi <= 70 → 10 - (60-58)/12 * 10 ≈ 8.33)."""
    kw = _neutral_kwargs()
    kw["rsi"] = 60.0
    csp_score, _ = compute_env_score(**kw)

    kw["direction"] = "cc"
    cc_score, _ = compute_env_score(**kw)

    # CSP: 10 pts on RSI. CC: ~8.33 pts.
    assert csp_score > cc_score


# --- Chain OI factor (8 pts, log scale) ------------------------------------

def test_env_chain_oi_log_scale_caps_at_5000():
    kw = _neutral_kwargs()
    kw["chain_median_oi"] = 5000.0
    score_at_cap, _ = compute_env_score(**kw)

    kw["chain_median_oi"] = 50000.0
    score_above_cap, _ = compute_env_score(**kw)

    # Both should award the full 8 pts (log10 fraction is clamped to 1.0).
    assert score_at_cap == pytest.approx(score_above_cap, abs=0.1)


def test_env_chain_oi_zero_awards_zero():
    kw = _neutral_kwargs()
    kw["chain_median_oi"] = 0.0
    score, _ = compute_env_score(**kw)
    # No OI contribution; only the 50-RSI plateau (20 pts in v3).
    assert score == pytest.approx(20.0, abs=0.1)


# --- dte back-compat (dropped in v3) ----------------------------------------

def test_env_dte_is_ignored_in_v3():
    """dte is a back-compat parameter; v3 dropped the DTE sweet-spot factor
    (DTE is now enforced as a hard filter upstream, not a soft ENV score)."""
    base = _neutral_kwargs()
    score_0, _ = compute_env_score(**{**base, "dte": 0})
    score_35, _ = compute_env_score(**{**base, "dte": 35})
    score_90, _ = compute_env_score(**{**base, "dte": 90})
    assert score_0 == pytest.approx(score_35, abs=0.01)
    assert score_0 == pytest.approx(score_90, abs=0.01)


# --- Earnings penalty ------------------------------------------------------

def test_env_earnings_penalty_applied():
    kw = _neutral_kwargs()
    score_no_earnings, _ = compute_env_score(**kw)

    kw["earnings_within_dte"] = True
    score_with_earnings, detail = compute_env_score(**kw)

    assert score_no_earnings - score_with_earnings == pytest.approx(15.0, abs=0.1)
    assert "Ear:-15" in detail


# --- Smoke: full-score CSP environment -------------------------------------

def test_env_full_score_csp_top_environment():
    """Maxed-out inputs in every factor → score should be ≥99 (allowing
    for small rounding in the rescaled curves).

    v3.3 weights: IVP 35 + Tr 15 + SMA 5 + SLP 5 + RSI 20 + OI 20 = 100.
    """
    kw = _neutral_kwargs()
    kw["iv_percentile"] = 95.0         # IVP: 35 pts (≥90th percentile)
    kw["dist_from_52w_high_pct"] = 0.0 # Tr: 15 pts (CSP flat top ≤5%)
    kw["sma_ratio"] = 1.05             # SMA: 5 pts (>1.02)
    kw["sma50_slope_pct"] = 0.6        # SLP: 5 pts (≥0.5%)
    kw["rsi"] = 50.0                   # RSI: 20 pts (42–62 sweet spot)
    kw["chain_median_oi"] = 10000.0    # OI: 20 pts (log-scale cap)
    score, detail = compute_env_score(**kw)
    assert score >= 99.0
    assert score <= 100.0
    # Verify all six factors appear in the detail string
    for factor in ("IVP:", "Tr:", "SMA:", "SLP:", "RSI:", "OI:"):
        assert factor in detail
