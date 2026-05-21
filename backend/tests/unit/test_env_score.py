"""
Unit tests for `services.scoring.env.compute_env_score`.

v3.4 (current, ADR-0011):
  - CSP (Method D): IVP 60 + Tr_flipped 20 + OI 20 = 100  (SMA/SLP/RSI dropped)
  - CC  (v3.3):     IVP 35 + Tr 15 + SMA 5 + SLP 5 + RSI 20 + OI 20 = 100

Probes:
- IV Percentile (IVP) curve elbows (CSP path — × 60/35 vs v3.3 cap).
- CC SMA alignment / SMA slope elbows.
- 52W high distance — direction-aware divergence:
    CSP rewards distance FROM the high (flipped Method D); CC has a tent.
- RSI — CC only (CSP no longer scores RSI).
- Chain OI log scale.
- Earnings penalty.
- Back-compat params iv_rank, iv_hv_ratio, iv_stale, dte are accepted but
  confirmed to have zero effect on the score.
"""
from __future__ import annotations

import pytest

from services.scoring.env import compute_env_score


# Default inputs that produce a "neutral" environment (zero on every factor).
# Individual tests override one field at a time.
# v3.4: CSP path drops RSI scoring, so rsi=50 contributes 0 under direction='csp'.
def _neutral_kwargs() -> dict:
    return {
        "iv_rank": 0.0,
        "iv_hv_ratio": 0.0,
        "price_above_sma50": False,
        "sma50_above_sma200": False,
        "dist_from_52w_high_pct": -50.0,  # CSP Method D: 50% below high → Tr_flipped = 20 pts
        "rsi": 50.0,
        "chain_median_oi": 0.0,
        "earnings_within_dte": False,
        "direction": "csp",
        "dte": 0,
        "iv_stale": False,
        "sma_ratio": 0.0,
        "sma50_slope_pct": 0.0,
    }


# Baseline for the CSP neutral kwargs above: Tr_flipped = 20 pts at 50% below high.
_CSP_NEUTRAL_BASELINE = 20.0


# --- iv_rank back-compat (dropped in v3) ------------------------------------

def test_env_iv_rank_is_ignored_in_v3():
    """iv_rank is a back-compat parameter; v3 dropped HV Rank (redundant with
    strike-side IV Percentile). Changing it must not affect the score."""
    base = _neutral_kwargs()
    score_low, _ = compute_env_score(**{**base, "iv_rank": 0.0})
    score_high, _ = compute_env_score(**{**base, "iv_rank": 95.0})
    assert score_low == pytest.approx(score_high, abs=0.01)


# --- IV Percentile factor (60 pts under CSP Method D) ----------------------
# v3.4 rescales the v3.3 curve × 60/35. Curve shape is unchanged.
# CSP isolation: subtract the Tr_flipped baseline (20 pts at -50% below high).

@pytest.mark.parametrize(
    "pct, expected_pts",
    [
        (95.0, 60.0),                       # ≥90th ceiling — 35 × 60/35
        (90.0, 60.0),
        (82.5, 30.0 * 60.0 / 35.0),         # midpoint 75–90
        (75.0, 25.0 * 60.0 / 35.0),         # lower elbow of upper lerp
        (62.5, 17.5 * 60.0 / 35.0),         # midpoint 50–75
        (50.0, 10.0 * 60.0 / 35.0),         # lower elbow of mid lerp
        (40.0,  5.0 * 60.0 / 35.0),         # midpoint 30–50
        (30.0,  0.0),                       # lower boundary
        (15.0,  0.0),                       # well below 30th
    ],
)
def test_env_iv_percentile_factor_at_elbows(pct: float, expected_pts: float):
    kw = _neutral_kwargs()
    kw["iv_percentile"] = pct
    score, detail = compute_env_score(**kw)
    isolated = score - _CSP_NEUTRAL_BASELINE
    assert isolated == pytest.approx(expected_pts, abs=0.25)
    assert "IVP:" in detail


def test_env_iv_percentile_none_awards_zero():
    """When iv_percentile is None, IVP contributes 0 pts. CSP Method D leaves
    only the Tr_flipped baseline (20 pts at -50% below high) in the neutral kwargs."""
    kw = _neutral_kwargs()
    score, _ = compute_env_score(**kw)
    assert score == pytest.approx(_CSP_NEUTRAL_BASELINE, abs=0.1)


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


# --- SMA alignment factor (5 pts) — CC only in v3.4 -----------------------

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
    kw["direction"] = "cc"        # CSP Method D drops SMA; cover the CC curve only
    kw["dist_from_52w_high_pct"] = -50.0  # CC Tr tent = 0 here
    kw["sma_ratio"] = sma_ratio
    score, _ = compute_env_score(**kw)
    # CC neutral baseline: rsi=50 in CC sweet-spot (38–58) = 20 pts.
    sma_pts = score - 20.0
    assert sma_pts == pytest.approx(expected_pts, abs=0.01)


# --- SMA slope factor (5 pts) — CC only in v3.4 ---------------------------

@pytest.mark.parametrize(
    "slope, expected_pts",
    [
        (0.6, 5.0),
        (0.5, 5.0),
        (0.35, 4.0),
        (0.2, 3.0),
        (0.1, 1.5),
        (0.0, 0.0),
        (-0.1, 0.0),
    ],
)
def test_env_sma_slope_factor_elbows(slope: float, expected_pts: float):
    kw = _neutral_kwargs()
    kw["direction"] = "cc"
    kw["dist_from_52w_high_pct"] = -50.0
    kw["sma50_slope_pct"] = slope
    score, _ = compute_env_score(**kw)
    slp_pts = score - 20.0   # CC RSI=50 plateau
    assert slp_pts == pytest.approx(expected_pts, abs=0.15)


# --- Direction-aware divergence: 52W and RSI -------------------------------

def test_env_direction_diverges_at_52w_proximity():
    """v3.4 Method D flipped the CSP Tr curve. At 25% below the 52W high, CSP
    awards 16 pts (Tr_flipped 0→20 over 5–30%) while CC tent awards 7.5 pts
    (decay from 15 at 15% to 0 at 35%)."""
    kw = _neutral_kwargs()
    kw["rsi"] = 50.0
    kw["dist_from_52w_high_pct"] = -25.0
    csp_score, _ = compute_env_score(**kw)

    kw["direction"] = "cc"
    cc_score, _ = compute_env_score(**kw)

    # CSP Tr_flipped at 25% below = (25−5)/25 * 20 = 16. CC has 20 pts RSI on top.
    # Isolate Tr by computing differences against baselines is messy — just assert
    # the directional shape: CSP scores 16 from Tr; CC scores 20(RSI)+7.5(Tr).
    # Confirm CSP < CC (CC has +RSI+SMA-equivalent that CSP doesn't).
    assert csp_score == pytest.approx(16.0, abs=0.2)         # CSP = Tr_flipped only
    assert cc_score == pytest.approx(20.0 + 7.5, abs=0.5)    # CC = RSI(20) + Tr_tent(7.5)


def test_env_direction_diverges_at_rsi_60():
    """CSP Method D drops RSI; CC retains it. At RSI 60, CSP scores 0 from
    the RSI factor while CC scores ~17.6 ((75-60)/17 * 20)."""
    kw = _neutral_kwargs()
    kw["rsi"] = 60.0
    csp_score, _ = compute_env_score(**kw)

    kw["direction"] = "cc"
    kw["dist_from_52w_high_pct"] = -50.0   # CC Tr tent = 0 here
    cc_score, _ = compute_env_score(**kw)

    # CSP = Tr_flipped(20) only. CC = RSI(17.6).
    assert csp_score == pytest.approx(20.0, abs=0.2)
    assert cc_score == pytest.approx(17.6, abs=0.5)


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
    # CSP Method D: no RSI/SMA/SLP contribution — only Tr_flipped (20 pts at -50%).
    assert score == pytest.approx(_CSP_NEUTRAL_BASELINE, abs=0.1)


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
    """Maxed-out CSP v3.4 inputs → score should be ≥99.

    v3.4 Method D weights: IVP 60 + Tr_flipped 20 + OI 20 = 100.
    SMA/SLP/RSI are dropped under the CSP path.
    """
    kw = _neutral_kwargs()
    kw["iv_percentile"] = 95.0           # IVP: 60 pts (≥90th percentile)
    kw["dist_from_52w_high_pct"] = -35.0  # Tr_flipped: 20 pts (≥30% below high)
    kw["chain_median_oi"] = 10000.0       # OI: 20 pts (log-scale cap)
    score, detail = compute_env_score(**kw)
    assert score >= 99.0
    assert score <= 100.0
    # Verify the three Method D factors appear; SMA/SLP/RSI must be absent.
    for factor in ("IVP:", "Tr:", "OI:"):
        assert factor in detail
    for dropped in ("SMA:", "SLP:", "RSI:"):
        assert dropped not in detail
