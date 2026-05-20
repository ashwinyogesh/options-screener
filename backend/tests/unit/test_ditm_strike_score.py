"""
Unit tests for `services.scoring.ditm.compute_ditm_strike_score`.

v3.2 strike factors (100 pts max):
  Delta position   20 pts — sweet spot 0.82–0.90
  Leverage         25 pts — flat top 2.5–4×; hard zero ≥5×
  Extrinsic %      25 pts — extrinsic / strike (fraction); lower = better
  Bid-Ask spread   20 pts — % of mid; lower = better
  IV Percentile    10 pts — lower = cheaper for buyers

Tests probe each factor at documented boundary elbows independently.
"""
from __future__ import annotations

import math

import pytest

from services.scoring.ditm import compute_ditm_strike_score


# ---------------------------------------------------------------------------
# Baseline helpers
# ---------------------------------------------------------------------------

def _base_kwargs() -> dict:
    """Inputs that score 0 on every factor except where overridden."""
    return {
        "delta": 0.50,                          # below 0.70 → Δ=0
        "strike": 100.0,
        "mid": 10.0,
        "current_price": 105.0,                 # leverage = 0.50*105/10 = 5.25 → hard 0
        "extrinsic_pct_of_strike_frac": 0.20,  # 20% → Ext=0
        "bid_ask_spread_pct": 20.0,             # 20% → BA=0
        "iv_percentile": 90.0,                  # high IV → IV=0
    }


def _score(**overrides) -> tuple[float, str]:
    kw = _base_kwargs()
    kw.update(overrides)
    return compute_ditm_strike_score(**kw)


# ---------------------------------------------------------------------------
# Delta factor — 20 pts
# ---------------------------------------------------------------------------

class TestDeltaFactor:
    @pytest.mark.parametrize("delta, expected_pts", [
        (0.86,  20.0),   # within sweet spot 0.82–0.90 → full
        (0.82,  20.0),   # lower edge
        (0.90,  20.0),   # upper edge
        (0.775, 14.9),   # midpoint 0.75–0.82: 12 + (0.025/0.07)*8 = 14.86 → :.1f = 14.9
        (0.75,  12.0),   # lower elbow of ramp
        (0.70,   0.0),   # floor
        (0.65,   0.0),   # below floor
        (0.925, 17.0),   # midpoint 0.90–0.95: 20 + (0.025/0.05)*(14-20) = 17
        (0.95,  14.0),   # upper elbow
        (0.975,  11.5),  # midpoint 0.95–1.0: 14 + (0.025/0.05)*(9-14) = 11.5
        (1.00,   9.0),   # deep ITM tail
    ])
    def test_delta_factor_elbows(self, delta: float, expected_pts: float):
        # Arrange: override delta and set leverage safe (2.5–4 range)
        _, detail = _score(
            delta=delta,
            current_price=100.0,
            mid=20.0,          # leverage = delta*100/20 = 5*delta; varies
            extrinsic_pct_of_strike_frac=0.01,  # Ext near max to isolate delta
            bid_ask_spread_pct=1.0,
            iv_percentile=20.0,
        )
        delta_pts = float(detail.split("\u0394:")[1].split()[0])
        assert delta_pts == pytest.approx(expected_pts, abs=0.2)


# ---------------------------------------------------------------------------
# Leverage factor — 25 pts (flat top 2.5–4×; hard 0 ≥5×)
# ---------------------------------------------------------------------------

class TestLeverageFactor:
    def _leverage_pts(self, leverage: float) -> float:
        # Use delta=0.85 (full 20 pts), set mid so leverage = delta*price/mid
        # leverage = 0.85 * 100 / mid → mid = 85 / leverage
        delta = 0.85
        current_price = 100.0
        mid = delta * current_price / leverage if leverage > 0 else 1.0
        _, detail = compute_ditm_strike_score(
            delta=delta,
            strike=100.0,
            mid=mid,
            current_price=current_price,
            extrinsic_pct_of_strike_frac=0.01,
            bid_ask_spread_pct=1.0,
            iv_percentile=20.0,
        )
        return float(detail.split("Lev:")[1].split()[0])

    @pytest.mark.parametrize("leverage, expected_pts", [
        (3.0,  25.0),  # within flat top 2.5–4×
        (2.5,  25.0),  # lower boundary
        (4.0,  25.0),  # upper boundary
        (2.0,  17.0),  # lower elbow
        (1.75, 12.5),  # midpoint 1.5–2.0: 8 + (0.25/0.5)*9 = 12.5
        (1.5,   8.0),  # lower elbow
        (0.75,  4.0),  # midpoint 0–1.5: lerp(0.75, 0, 1.5, 0, 8) = 4
        (4.5,  12.5),  # midpoint 4–5: 25 + (0.5/1)*(-25) = 12.5
        (5.0,   0.0),  # hard zero at 5×
        (6.0,   0.0),  # beyond hard zero
        (0.0,   0.0),  # zero leverage
    ])
    def test_leverage_factor_elbows(self, leverage: float, expected_pts: float):
        assert self._leverage_pts(leverage) == pytest.approx(expected_pts, abs=0.2)

    def test_leverage_nan_returns_zero(self):
        _, detail = _score(delta=0.85, mid=0.0)  # mid=0 → leverage=0
        assert "Lev:0.0" in detail


# ---------------------------------------------------------------------------
# Extrinsic % factor — 25 pts (lower = better)
# ---------------------------------------------------------------------------

class TestExtrinsicFactor:
    @pytest.mark.parametrize("pct_frac, expected_pts", [
        (0.00,  25.0),   # < 2% → full
        (0.01,  25.0),   # 1% still full
        (0.03,  22.0),   # midpoint 2–4%: 25 + (1/2)*(19-25) = 22
        (0.04,  19.0),   # lower elbow of first lerp
        (0.05,  16.0),   # midpoint 4–6%: 19 + (1/2)*(13-19) = 16
        (0.06,  13.0),   # lower elbow
        (0.075, 9.0),    # midpoint 6–9%: 13 + (1.5/3)*(5-13) = 9
        (0.09,   5.0),   # lower elbow
        (0.105,  2.5),   # midpoint 9–12%: 5 + (1.5/3)*(0-5) = 2.5
        (0.12,   0.0),   # at zero boundary
        (0.20,   0.0),   # beyond boundary
    ])
    def test_extrinsic_factor_elbows(self, pct_frac: float, expected_pts: float):
        _, detail = _score(
            delta=0.86, current_price=100.0, mid=20.0,
            extrinsic_pct_of_strike_frac=pct_frac,
            bid_ask_spread_pct=1.0,
            iv_percentile=20.0,
        )
        ext_pts = float(detail.split("Ext:")[1].split()[0])
        assert ext_pts == pytest.approx(expected_pts, abs=0.1)


# ---------------------------------------------------------------------------
# Bid-Ask spread factor — 20 pts (lower = better)
# ---------------------------------------------------------------------------

class TestSpreadFactor:
    @pytest.mark.parametrize("spread_pct, expected_pts", [
        (1.0,  20.0),   # ≤2% → full
        (2.0,  20.0),   # boundary
        (3.0,  17.0),   # midpoint 2–4: 20 + (1/2)*(14-20) = 17
        (4.0,  14.0),   # lower elbow
        (5.5,  10.5),   # midpoint 4–7: 14 + (1.5/3)*(7-14) = 10.5
        (7.0,   7.0),   # lower elbow
        (9.5,   4.0),   # midpoint 7–12: 7 + (2.5/5)*(1-7) = 4
        (12.0,  1.0),   # lower elbow
        (15.0,  0.0),   # beyond boundary
    ])
    def test_spread_factor_elbows(self, spread_pct: float, expected_pts: float):
        _, detail = _score(
            delta=0.86, current_price=100.0, mid=20.0,
            extrinsic_pct_of_strike_frac=0.01,
            bid_ask_spread_pct=spread_pct,
            iv_percentile=20.0,
        )
        ba_pts = float(detail.split("BA:")[1].split()[0])
        assert ba_pts == pytest.approx(expected_pts, abs=0.1)

    def test_spread_none_gives_fallback(self):
        _, detail = _score(
            delta=0.86, current_price=100.0, mid=20.0,
            extrinsic_pct_of_strike_frac=0.01,
            bid_ask_spread_pct=None,
            iv_percentile=20.0,
        )
        assert "BA:6.0" in detail

    def test_spread_nan_gives_fallback(self):
        _, detail = _score(
            delta=0.86, current_price=100.0, mid=20.0,
            extrinsic_pct_of_strike_frac=0.01,
            bid_ask_spread_pct=float("nan"),
            iv_percentile=20.0,
        )
        assert "BA:6.0" in detail


# ---------------------------------------------------------------------------
# IV Percentile factor — 10 pts (lower = better for buyers)
# ---------------------------------------------------------------------------

class TestIVPercentileFactor:
    @pytest.mark.parametrize("iv_pct, expected_pts", [
        (10.0,  10.0),   # ≤25th → full
        (25.0,  10.0),   # boundary
        (37.5,   8.5),   # midpoint 25–50: 10 + (12.5/25)*(7-10) = 8.5
        (50.0,   7.0),   # lower elbow
        (62.5,   5.0),   # midpoint 50–75: 7 + (12.5/25)*(3-7) = 5
        (75.0,   3.0),   # lower elbow
        (80.0,   0.0),   # above 75th → 0
        (99.0,   0.0),   # well above
    ])
    def test_iv_percentile_factor_elbows(self, iv_pct: float, expected_pts: float):
        _, detail = _score(
            delta=0.86, current_price=100.0, mid=20.0,
            extrinsic_pct_of_strike_frac=0.01,
            bid_ask_spread_pct=1.0,
            iv_percentile=iv_pct,
        )
        iv_pts = float(detail.split("IV:")[1])
        assert iv_pts == pytest.approx(expected_pts, abs=0.1)

    def test_iv_percentile_none_gives_fallback(self):
        _, detail = _score(
            delta=0.86, current_price=100.0, mid=20.0,
            extrinsic_pct_of_strike_frac=0.01,
            bid_ask_spread_pct=1.0,
            iv_percentile=None,
        )
        assert "IV:5.0" in detail

    def test_iv_percentile_nan_gives_fallback(self):
        _, detail = _score(
            delta=0.86, current_price=100.0, mid=20.0,
            extrinsic_pct_of_strike_frac=0.01,
            bid_ask_spread_pct=1.0,
            iv_percentile=float("nan"),
        )
        assert "IV:5.0" in detail


# ---------------------------------------------------------------------------
# compute_ditm_strike_score integration: bounds and detail format
# ---------------------------------------------------------------------------

class TestStrikeScoreBounds:
    def test_perfect_inputs_score_at_most_100(self):
        # Arrange: best values for each factor
        score, _ = compute_ditm_strike_score(
            delta=0.86,
            strike=100.0,
            mid=28.0,            # leverage = 0.86*100/28 ≈ 3.07 → 25 pts
            current_price=100.0,
            extrinsic_pct_of_strike_frac=0.005,  # 0.5% → 25 pts
            bid_ask_spread_pct=0.5,
            iv_percentile=10.0,
        )

        # Assert
        assert score <= 100.0

    def test_all_zero_inputs_score_is_zero(self):
        score, _ = compute_ditm_strike_score(
            delta=0.50,              # below 0.70 floor → Δ=0
            strike=100.0,
            mid=10.0,               # leverage = 0.50*100/10 = 5.0 → hard 0
            current_price=100.0,
            extrinsic_pct_of_strike_frac=0.20,
            bid_ask_spread_pct=20.0,
            iv_percentile=80.0,
        )
        assert score == 0.0

    def test_detail_string_contains_all_five_keys(self):
        _, detail = compute_ditm_strike_score(
            delta=0.86, strike=100.0, mid=25.0, current_price=100.0,
            extrinsic_pct_of_strike_frac=0.01,
            bid_ask_spread_pct=1.0,
            iv_percentile=20.0,
        )
        for key in ("\u0394:", "Lev:", "Ext:", "BA:", "IV:"):
            assert key in detail

    def test_score_rounded_to_two_decimal_places(self):
        score, _ = compute_ditm_strike_score(
            delta=0.86, strike=100.0, mid=25.0, current_price=100.0,
            extrinsic_pct_of_strike_frac=0.03,
            bid_ask_spread_pct=3.0,
            iv_percentile=40.0,
        )
        # Round-trip: str representation must not exceed 2 decimal places
        assert score == round(score, 2)
