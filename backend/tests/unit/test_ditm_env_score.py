"""
Unit tests for `services.scoring.ditm.compute_ditm_env_score`.

v3.2 factors (100 pts max):
  Trend Strength   25 pts — soft SMA alignment
  200d Return      15 pts — momentum, compressed v3.2
  52W Distance     20 pts — tent curve 3–12% off highs
  Trend Stability  10 pts — R² of 50-day OLS regression (v3.2 NEW)
  Weekly RSI       15 pts — pullback-entry credit
  Chain Liquidity  15 pts — log10 scale, ref point 500 OI
  Earnings penalty −15/−7 pts — DTE-scaled

Tests probe the *shape* of each factor at documented boundary elbows.
They do not pin exact full-score outputs — that is the characterization
tests' job.
"""
from __future__ import annotations

import math

import pytest

from services.scoring.ditm import compute_ditm_env_score


# ---------------------------------------------------------------------------
# Baseline helpers
# ---------------------------------------------------------------------------

def _base_kwargs() -> dict:
    """Inputs that score near-zero on every sub-factor except where overridden."""
    return {
        "price_above_sma50": False,
        "sma50_above_sma200": False,
        "price": 100.0,
        "sma200": 120.0,           # price below sma200 → Trend=0
        "weekly_rsi": 30.0,        # outside all credit bands → RSI=0
        "dist_from_52w_high_pct": -50.0,  # too far → 52W=0
        "ret_200d_frac": -0.10,    # negative return → 200d=0
        "days_to_earnings": None,  # no penalty
        "chain_median_oi": 0.0,    # zero OI → LQ=0
        "dte": 120,
        "trend_r2": float("nan"),  # NaN → R²=5 (fallback)
    }


def _score(**overrides) -> tuple[float, str]:
    kw = _base_kwargs()
    kw.update(overrides)
    return compute_ditm_env_score(**kw)


# ---------------------------------------------------------------------------
# Trend Strength factor — 25 pts
# ---------------------------------------------------------------------------

class TestTrendStrengthFactor:
    def test_full_alignment_returns_25pts(self):
        # Arrange
        kw = _base_kwargs()
        kw.update(price_above_sma50=True, sma50_above_sma200=True,
                  price=110.0, sma200=90.0)

        # Act
        score, detail = compute_ditm_env_score(**kw)

        # Assert: Trend=25 + R2 fallback=5 = 10 pts (others zero / negative zero)
        assert "Tr:25.0" in detail

    def test_only_price_above_sma50_returns_15pts(self):
        kw = _base_kwargs()
        kw.update(price_above_sma50=True, sma50_above_sma200=False,
                  price=110.0, sma200=90.0)
        _, detail = compute_ditm_env_score(**kw)
        assert "Tr:15.0" in detail

    def test_only_sma50_above_sma200_returns_8pts(self):
        kw = _base_kwargs()
        kw.update(price_above_sma50=False, sma50_above_sma200=True,
                  price=80.0, sma200=90.0)
        _, detail = compute_ditm_env_score(**kw)
        assert "Tr:8.0" in detail

    def test_price_above_sma200_only_returns_4pts(self):
        kw = _base_kwargs()
        kw.update(price_above_sma50=False, sma50_above_sma200=False,
                  price=110.0, sma200=100.0)
        _, detail = compute_ditm_env_score(**kw)
        assert "Tr:4.0" in detail

    def test_price_below_both_returns_0pts(self):
        _, detail = _score(price_above_sma50=False, sma50_above_sma200=False,
                           price=80.0, sma200=100.0)
        assert "Tr:0.0" in detail


# ---------------------------------------------------------------------------
# 200d Return factor — 15 pts
# ---------------------------------------------------------------------------

class TestReturn200dFactor:
    @pytest.mark.parametrize("ret_frac, expected_pts", [
        (0.30,  15.0),   # ≥25% → full credit
        (0.25,  15.0),   # exactly at cap
        (0.20,  13.0),   # midpoint 15–25: 11 + (5/10)*4 = 13
        (0.15,  11.0),   # lower elbow of upper lerp
        (0.10,   8.5),   # midpoint 5–15: 6 + (5/10)*5 = 8.5
        (0.05,   6.0),   # lower elbow of mid lerp
        (0.025,   3.8),  # midpoint 0–5: 1.5 + (2.5/5)*4.5 = 3.75 → :.1f = 3.8
        (0.00,   1.5),   # zero return → base
        (-0.10,  0.0),   # negative → 0
    ])
    def test_200d_return_elbows(self, ret_frac: float, expected_pts: float):
        _, detail = _score(ret_200d_frac=ret_frac)
        assert f"Ret:{expected_pts}" in detail

    def test_200d_return_nan_gives_fallback(self):
        _, detail = _score(ret_200d_frac=float("nan"))
        assert "Ret:5.0" in detail


# ---------------------------------------------------------------------------
# 52W Distance factor — 20 pts (tent curve)
# ---------------------------------------------------------------------------

class TestDist52wFactor:
    @pytest.mark.parametrize("dist_pct, expected_pts", [
        (  0.0, 12.0),   # at the high: lerp(0, 0,3, 12,20) = 12
        ( -1.5, 16.0),   # midpoint 0–3: 12 + (1.5/3)*8 = 16
        ( -3.0, 20.0),   # lower boundary of tent peak
        ( -7.5, 20.0),   # within sweet spot
        (-12.0, 20.0),   # upper boundary of sweet spot
        (-18.5, 13.0),   # midpoint 12–25: 20 + (6.5/13)*(6-20) ≈ 13
        (-25.0,  6.0),   # lower edge of upper tent slope
        (-32.5,  3.0),   # midpoint 25–40: 6 + (7.5/15)*(0-6) = 3
        (-40.0,  0.0),   # at the tail
        (-60.0,  0.0),   # beyond tail
    ])
    def test_52w_dist_tent_curve(self, dist_pct: float, expected_pts: float):
        _, detail = _score(dist_from_52w_high_pct=dist_pct)
        assert f"52W:{expected_pts}" in detail

    def test_52w_dist_nan_gives_fallback(self):
        _, detail = _score(dist_from_52w_high_pct=float("nan"))
        assert "52W:10.0" in detail


# ---------------------------------------------------------------------------
# Trend Stability / R² factor — 10 pts (v3.2 NEW)
# ---------------------------------------------------------------------------

class TestTrendR2Factor:
    @pytest.mark.parametrize("r2, expected_pts", [
        (0.90, 10.0),   # ≥0.85 → full
        (0.85, 10.0),   # exactly at threshold
        (0.775,  8.8),  # midpoint 0.70–0.85: 7.5 + (0.075/0.15)*2.5 = 8.75 → :.1f = 8.8
        (0.70,  7.5),   # lower elbow
        (0.60,   5.8),  # midpoint 0.50–0.70: 4 + (0.10/0.20)*3.5 = 5.75 → :.1f = 5.8
        (0.50,  4.0),   # lower elbow
        (0.40,  2.5),   # midpoint 0.30–0.50: 1 + (0.10/0.20)*3 = 2.5
        (0.30,  1.0),   # lower boundary
        (0.20,  0.0),   # below 0.30 → 0
    ])
    def test_trend_r2_elbows(self, r2: float, expected_pts: float):
        _, detail = _score(trend_r2=r2)
        assert f"R2:{expected_pts}" in detail

    def test_trend_r2_nan_gives_fallback(self):
        _, detail = _score(trend_r2=float("nan"))
        assert "R2:5.0" in detail


# ---------------------------------------------------------------------------
# Weekly RSI factor — 15 pts (direction-aware)
# ---------------------------------------------------------------------------

class TestWeeklyRSIFactor:
    @pytest.mark.parametrize("w_rsi, trend_pts, expected_pts", [
        (57.5,  0.0, 15.0),  # sweet spot 50–65
        (50.0,  0.0, 15.0),  # lower bound of sweet spot
        (65.0,  0.0, 15.0),  # upper bound of sweet spot
        (47.5,  0.0, 11.0),  # 45–50 fringe
        (67.5,  0.0, 11.0),  # 65–70 fringe
        (42.5,  0.0,  6.0),  # 40–45
        (72.5,  0.0,  6.0),  # 70–75
        (37.5, 18.0,  9.0),  # 35–40 strong-uptrend pullback credit
        (37.5,  0.0,  0.0),  # 35–40 weak trend → no credit
        (20.0,  0.0,  0.0),  # below all bands
    ])
    def test_weekly_rsi_bands(self, w_rsi: float, trend_pts: float, expected_pts: float):
        # Arrange: inject trend_pts by setting full alignment when trend_pts≥18
        kw = _base_kwargs()
        if trend_pts >= 18:
            kw.update(price_above_sma50=True, sma50_above_sma200=True,
                      price=110.0, sma200=90.0)
        kw["weekly_rsi"] = w_rsi

        # Act
        _, detail = compute_ditm_env_score(**kw)

        # Assert
        assert f"WRSI:{expected_pts}" in detail

    def test_weekly_rsi_nan_gives_fallback(self):
        _, detail = _score(weekly_rsi=float("nan"))
        assert "WRSI:7.0" in detail


# ---------------------------------------------------------------------------
# Chain Liquidity factor — 15 pts (log10 scale)
# ---------------------------------------------------------------------------

class TestLiquidityFactor:
    @pytest.mark.parametrize("median_oi, expected_pts", [
        (500.0, 15.0),   # reference point → full
        (1000.0, 15.0),  # above reference → capped at 15
        (50.0,   pytest.approx(15.0 * math.log10(50) / math.log10(500), abs=0.1)),
        (1.0,    0.0),   # log10(1)=0 → 0 pts
        (0.0,    0.0),   # zero OI
        (-5.0,   0.0),   # negative OI
    ])
    def test_liquidity_log_scale(self, median_oi: float, expected_pts):
        _, detail = _score(chain_median_oi=median_oi)
        lq_val = float(detail.split("LQ:")[1].split()[0])
        assert lq_val == pytest.approx(expected_pts, abs=0.1)


# ---------------------------------------------------------------------------
# Earnings penalty — DTE-scaled
# ---------------------------------------------------------------------------

class TestEarningsPenalty:
    def test_no_earnings_no_penalty(self):
        _, detail = _score(days_to_earnings=None, dte=120)
        assert "Earn:0.0" in detail

    def test_earnings_far_out_no_penalty(self):
        _, detail = _score(days_to_earnings=90, dte=120)
        assert "Earn:0.0" in detail

    def test_earnings_within_7d_full_scaled_penalty(self):
        # scale = min(1.0, 30/120) = 0.25 → penalty = -15 * 0.25 = -3.75
        _, detail = _score(days_to_earnings=3, dte=120)
        assert "Earn:-3.8" in detail  # -3.75 rounds to -3.8 via :.1f

    def test_earnings_within_7d_short_dte_full_penalty(self):
        # scale = min(1.0, 30/20) = 1.0 → penalty = -15.0
        _, detail = _score(days_to_earnings=5, dte=20)
        assert "Earn:-15.0" in detail

    def test_earnings_8_to_14d_half_scaled_penalty(self):
        # scale = min(1.0, 30/60) = 0.5 → penalty = -7 * 0.5 = -3.5
        _, detail = _score(days_to_earnings=10, dte=60)
        assert "Earn:-3.5" in detail

    def test_earnings_15_to_60d_no_penalty(self):
        _, detail = _score(days_to_earnings=30, dte=120)
        assert "Earn:0.0" in detail

    def test_earnings_penalty_dte_zero_safe(self):
        # dte=0 → max(dte, 1)=1 → scale=1.0
        _, detail = _score(days_to_earnings=3, dte=0)
        assert "Earn:-15.0" in detail


# ---------------------------------------------------------------------------
# compute_ditm_env_score integration: score bounded 0–100
# ---------------------------------------------------------------------------

class TestEnvScoreBounds:
    def test_all_neutral_score_is_nonnegative(self):
        score, _ = compute_ditm_env_score(**_base_kwargs())
        assert score >= 0.0

    def test_perfect_inputs_score_at_most_100(self):
        score, _ = compute_ditm_env_score(
            price_above_sma50=True,
            sma50_above_sma200=True,
            price=110.0,
            sma200=90.0,
            weekly_rsi=57.5,
            dist_from_52w_high_pct=-7.0,
            ret_200d_frac=0.30,
            days_to_earnings=None,
            chain_median_oi=1000.0,
            dte=120,
            trend_r2=0.95,
        )
        assert score <= 100.0

    def test_worst_inputs_score_is_zero(self):
        score, _ = compute_ditm_env_score(
            price_above_sma50=False,
            sma50_above_sma200=False,
            price=50.0,
            sma200=200.0,
            weekly_rsi=20.0,
            dist_from_52w_high_pct=-80.0,
            ret_200d_frac=-0.50,
            days_to_earnings=1,
            chain_median_oi=0.0,
            dte=120,
            trend_r2=0.0,
        )
        assert score == 0.0

    def test_detail_string_contains_all_six_keys(self):
        _, detail = compute_ditm_env_score(**_base_kwargs())
        for key in ("Tr:", "Ret:", "52W:", "R2:", "WRSI:", "LQ:", "Earn:"):
            assert key in detail
