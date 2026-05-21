"""
Unit tests for the CSP and CC strike scorers in `services.scoring.strike`.

Probes:
- Delta bell-curve elbows (sweet spot, shoulders, far OTM/ITM).
- Distance-vs-support / distance-vs-resistance scoring.
- Expected-Move buffer at zero / negative buffer.
- OTM percentage tiering.
- Bid-Ask spread tiering.
- Liquidity (OI / volume + market_open switch).
- ROC factor.
- CSP vs CC direction divergence (puts use negative deltas, calls positive).

Same philosophy as `test_env_score.py`: probe shapes at boundaries, don't pin
exact end-to-end outputs (characterization tests cover that).
"""
from __future__ import annotations

import pytest

from services.scoring.strike import (
    compute_cc_final_score,
    compute_cc_strike_score,
    compute_csp_final_score,
    compute_csp_strike_score,
)


def _csp_neutral_kwargs() -> dict:
    """CSP inputs that produce a near-zero score on every factor.

    Note: with iv_used=NaN the EM factor zeros; with no supports given, support
    awards 0; with delta NaN the Δ factor zeros; spread None → 0; volume/OI 0
    → 0; credit None → ROC 0. OTM at strike == price → 0.
    """
    return {
        "delta": float("nan"),
        "current_price": 100.0,
        "strike": 100.0,
        "iv_used": float("nan"),
        "dte": 30,
        "vol_support_1": None,
        "vol_support_2": None,
        "vol_support_3": None,
        "bid_ask_spread_pct": None,
        "open_interest": 0,
        "market_open": False,
        "volume": 0,
        "credit": None,
    }


def _cc_neutral_kwargs() -> dict:
    return {
        "delta": float("nan"),
        "current_price": 100.0,
        "strike": 100.0,
        "iv_used": float("nan"),
        "dte": 30,
        "vol_resistance_1": None,
        "vol_resistance_2": None,
        "vol_resistance_3": None,
        "bid_ask_spread_pct": None,
        "open_interest": 0,
        "market_open": False,
        "volume": 0,
        "credit": None,
    }


# === CSP =====================================================================

# --- Delta bell-curve (40 pts smooth) — v3.4 Method D ------------------------

@pytest.mark.parametrize(
    "delta, expected_pts",
    [
        (-0.22, 40.0),   # sweet spot (offset ≤0.025) — 25 × 40/25
        (-0.20, 40.0),   # exactly at flat-top boundary
        (-0.25, 40.0),   # exactly at flat-top boundary
        (-0.18, 34.24),  # shoulder — 21.4 × 40/25
        (-0.28, 31.36),  # shoulder — 19.6 × 40/25
        (-0.12, 18.88),  # outer — 11.8 × 40/25
        (-0.10, 14.4),   # outer edge — 9.0 × 40/25
        (-0.05, 0.0),    # too close to ATM (offset >0.175)
        (0.10, 0.0),     # wrong sign
    ],
)
def test_csp_delta_factor_at_elbows(delta: float, expected_pts: float):
    kw = _csp_neutral_kwargs()
    kw["delta"] = delta
    score, _, _ = compute_csp_strike_score(**kw)
    assert score == pytest.approx(expected_pts, abs=0.2)


# --- S/R distance back-compat (dropped in v3) -----------------------------

def test_csp_support_inputs_are_ignored_in_v3():
    """vol_support_* are back-compat parameters; v3 dropped S/R distance as a
    scored factor. dist_pct is always None; score is unaffected by support values."""
    kw_no_sup = _csp_neutral_kwargs()
    kw_no_sup["strike"] = 90.0
    score_no, _, raw_no = compute_csp_strike_score(**kw_no_sup)
    kw_with_sup = {**kw_no_sup, "vol_support_1": 89.0}
    score_with, _, raw_with = compute_csp_strike_score(**kw_with_sup)

    assert raw_no["dist_pct"] is None
    assert raw_with["dist_pct"] is None
    assert score_no == pytest.approx(score_with, abs=0.01)


# --- Expected Move buffer (20 pts) -----------------------------------------

def test_csp_em_buffer_diagnostic_at_half_em_boundary():
    """v3: EM buffer is diagnostic only (does not contribute to score).
    em_buffer_pct is still computed: ≈0 when strike is exactly at the 0.5×EM boundary."""
    kw = _csp_neutral_kwargs()
    kw["current_price"] = 100.0
    kw["iv_used"] = 0.30
    kw["dte"] = 30
    # em = 100 * 0.30 * sqrt(30/365) ≈ 8.6; 0.5×em boundary ≈ 95.7
    kw["strike"] = 95.7
    _, _, raw = compute_csp_strike_score(**kw)
    assert raw["em_buffer_pct"] == pytest.approx(0.0, abs=2.0)


# --- Bid-Ask spread (15 pts) — v3.4 Method D --------------------------------

@pytest.mark.parametrize(
    "spread, expected_min",
    [
        (0.5, 15.0),
        (1.0, 15.0),
        (3.0, 10.2),   # 17 × 15/25
        (5.0, 5.4),    # 9 × 15/25
        (8.0, 1.2),    # 2 × 15/25
        (12.0, 0.0),
    ],
)
def test_csp_bid_ask_factor_at_elbows(spread: float, expected_min: float):
    kw = _csp_neutral_kwargs()
    kw["bid_ask_spread_pct"] = spread
    score, _, _ = compute_csp_strike_score(**kw)
    assert score >= expected_min - 0.5


# --- Liquidity (15 pts) ---------------------------------------------------

def test_csp_liquidity_uses_oi_when_market_closed():
    kw = _csp_neutral_kwargs()
    kw["open_interest"] = 1500
    kw["volume"] = 0
    kw["market_open"] = False
    score, _, raw = compute_csp_strike_score(**kw)
    assert raw["lq_count"] == 1500
    assert score == pytest.approx(15.0, abs=0.1)


def test_csp_liquidity_uses_volume_when_market_open():
    kw = _csp_neutral_kwargs()
    kw["open_interest"] = 50
    kw["volume"] = 1500
    kw["market_open"] = True
    score, _, raw = compute_csp_strike_score(**kw)
    assert raw["lq_count"] == 1500
    assert score == pytest.approx(15.0, abs=0.1)


# --- ROC factor (30 pts, ceiling 12%) — v3.4 Method D -----------------------

def test_csp_roc_factor_strong_premium():
    kw = _csp_neutral_kwargs()
    kw["strike"] = 100.0
    kw["dte"] = 30
    kw["credit"] = 3.0  # capital = 97; roc ≈ 37.6 → cap at 12% → 30 pts (Method D)
    score, _, raw = compute_csp_strike_score(**kw)
    assert raw["roc_annualized"] >= 12.0
    assert score >= 30.0


# --- Final-blend helpers ---------------------------------------------------

def test_csp_final_score_blend():
    assert compute_csp_final_score(env_score=50.0, strike_score=100.0) == pytest.approx(80.0, abs=0.1)
    assert compute_csp_final_score(env_score=100.0, strike_score=50.0) == pytest.approx(70.0, abs=0.1)


# === CC ======================================================================

# --- Delta bell-curve (25 pts smooth) — v3.1 positive deltas for calls ------

@pytest.mark.parametrize(
    "delta, expected_pts",
    [
        (0.22, 25.0),    # sweet spot
        (0.20, 25.0),    # boundary
        (0.25, 25.0),    # boundary
        (0.18, 21.4),    # shoulder (offset 0.045)
        (0.28, 19.6),    # shoulder (offset 0.055)
        (0.12, 11.8),    # outer (offset 0.105)
        (0.35, 9.0),     # outer edge (offset 0.125 → 9.0)
        (0.05, 0.0),
        (-0.10, 0.0),    # wrong sign
    ],
)
def test_cc_delta_factor_at_elbows(delta: float, expected_pts: float):
    kw = _cc_neutral_kwargs()
    kw["delta"] = delta
    score, _, _ = compute_cc_strike_score(**kw)
    assert score == pytest.approx(expected_pts, abs=0.2)


# --- CC vs CSP delta divergence --------------------------------------------

def test_cc_and_csp_delta_factor_mirror_signs():
    """Both screeners award full Δ credit at their respective sweet spots:
    CSP at -0.22 (40 pts under v3.4 Method D), CC at +0.22 (25 pts under v3.3).
    Each gives zero on the opposite sign."""
    csp_kw = _csp_neutral_kwargs()
    csp_kw["delta"] = -0.22
    csp_score, _, _ = compute_csp_strike_score(**csp_kw)
    assert csp_score == pytest.approx(40.0, abs=0.1)

    csp_kw["delta"] = 0.22
    csp_wrong_sign, _, _ = compute_csp_strike_score(**csp_kw)
    assert csp_wrong_sign == 0.0

    cc_kw = _cc_neutral_kwargs()
    cc_kw["delta"] = 0.22
    cc_score, _, _ = compute_cc_strike_score(**cc_kw)
    assert cc_score == pytest.approx(25.0, abs=0.1)

    cc_kw["delta"] = -0.22
    cc_wrong_sign, _, _ = compute_cc_strike_score(**cc_kw)
    assert cc_wrong_sign == 0.0


# --- CC resistance back-compat (dropped in v3) ----------------------------

def test_cc_resistance_inputs_are_ignored_in_v3():
    """v3 dropped S/R distance as a scored factor. dist_pct is always None;
    resistance values do not affect score."""
    kw = _cc_neutral_kwargs()
    kw["current_price"] = 100.0
    kw["strike"] = 110.0
    kw_with_res = {**kw, "vol_resistance_1": 105.0}
    score_no, _, raw_no = compute_cc_strike_score(**kw)
    score_with, _, raw_with = compute_cc_strike_score(**kw_with_res)
    assert raw_no["dist_pct"] is None
    assert raw_with["dist_pct"] is None
    assert score_no == pytest.approx(score_with, abs=0.01)


# --- CC OTM factor (diagnostic only in v3) ---------------------------------

def test_cc_otm_pct_is_diagnostic_only_in_v3():
    """OTM% is still computed for display but does not contribute to score in v3."""
    kw = _cc_neutral_kwargs()
    kw["current_price"] = 100.0
    kw["strike"] = 115.0  # 15% OTM upward
    _, _, raw = compute_cc_strike_score(**kw)
    assert raw["otm_pct"] == pytest.approx(15.0, abs=0.1)


# --- CC final-blend helper -------------------------------------------------

def test_cc_final_score_blend():
    assert compute_cc_final_score(env_score=50.0, strike_score=100.0) == pytest.approx(80.0, abs=0.1)
    assert compute_cc_final_score(env_score=0.0, strike_score=0.0) == 0.0
