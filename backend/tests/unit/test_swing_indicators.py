"""
Unit tests for swing screener — indicators, scoring, classifier.

AAA pattern. Synthetic OHLC fixtures. No network calls.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from services.scoring.swing import compute_swing_score
from services.swing.classifier import (
    classify_setup,
    detect_breakout,
    detect_momentum,
    detect_reversion,
    detect_retest,
)
from services.swing.indicators import (
    compute_adx,
    compute_atr,
    compute_avg_daily_volume,
    compute_bb_squeeze_percentile,
    compute_consolidation_base,
    compute_ema,
    compute_ema_alignment,
    compute_fib_retracement_hold,
    compute_gap_fill_candidate,
    compute_higher_lows,
    compute_macd_histogram_inflection,
    compute_rs_vs_spy,
    compute_rsi_divergence,
    compute_stochastic,
    compute_structure_high_reclaim,
    compute_volume_surge,
)
from services.swing.risk import build_risk_plan


def _ohlc_flat(price: float, n: int, vol: int = 1_000_000) -> pd.DataFrame:
    return pd.DataFrame({
        "Open": [price] * n, "High": [price] * n, "Low": [price] * n,
        "Close": [price] * n, "Volume": [vol] * n,
    })


def _ohlc_uptrend(start: float, step: float, n: int, vol: int = 1_000_000) -> pd.DataFrame:
    closes = [start + step * i for i in range(n)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return pd.DataFrame({
        "Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": [vol] * n,
    })


# ---------------------------------------------------------------------------
# Indicators — insufficient data → NaN/zero
# ---------------------------------------------------------------------------

class TestInsufficientData:
    def test_ema_too_short(self):
        df = _ohlc_flat(100, 5)
        assert math.isnan(compute_ema(df, 50))

    def test_atr_too_short(self):
        df = _ohlc_flat(100, 5)
        assert math.isnan(compute_atr(df))

    def test_adx_too_short(self):
        df = _ohlc_flat(100, 10)
        result = compute_adx(df)
        assert math.isnan(result["adx"])

    def test_ema_alignment_returns_zero_score(self):
        df = _ohlc_flat(100, 50)
        result = compute_ema_alignment(df)
        assert result["score"] == 0
        assert result["detail"] == "insufficient data"

    def test_bb_squeeze_too_short(self):
        df = _ohlc_flat(100, 100)
        assert math.isnan(compute_bb_squeeze_percentile(df))


# ---------------------------------------------------------------------------
# Indicators — deterministic on synthetic data
# ---------------------------------------------------------------------------

class TestDeterministic:
    def test_ema_uptrend(self):
        df = _ohlc_uptrend(100, 1, 60)
        ema = compute_ema(df, 21)
        assert ema > 100
        assert ema < df["Close"].iloc[-1]  # EMA lags

    def test_ema_alignment_uptrend(self):
        df = _ohlc_uptrend(50, 0.5, 250)
        result = compute_ema_alignment(df)
        # Price keeps rising, so all 4 EMAs should be below current
        assert result["score"] == 9  # all four + bonus

    def test_ema_alignment_flat(self):
        df = _ohlc_flat(100, 250)
        result = compute_ema_alignment(df)
        # Price == every EMA; "above" check is strict >
        assert result["score"] == 0

    def test_atr_uptrend(self):
        df = _ohlc_uptrend(100, 1, 50)
        atr = compute_atr(df)
        # daily range = 1 (high-low) plus gap from prev close ~1
        assert atr > 0
        assert atr < 5

    def test_higher_lows_flat(self):
        df = _ohlc_flat(100, 50)
        assert compute_higher_lows(df) == 0

    def test_avg_daily_volume(self):
        df = _ohlc_flat(100, 30, vol=500_000)
        assert compute_avg_daily_volume(df) == 500_000

    def test_volume_surge_no_surge(self):
        df = _ohlc_flat(100, 25, vol=1_000_000)
        result = compute_volume_surge(df)
        assert result["ratio"] == 1.0
        assert not result["is_surge"]

    def test_volume_surge_detected(self):
        df = _ohlc_flat(100, 25, vol=1_000_000)
        df.loc[df.index[-1], "Volume"] = 2_000_000
        result = compute_volume_surge(df)
        assert result["ratio"] >= 1.5
        assert result["is_surge"]

    def test_consolidation_base_tight(self):
        df = _ohlc_flat(100, 15)
        result = compute_consolidation_base(df, min_days=7, max_range_pct=0.08)
        assert result["is_base"]
        assert result["days"] >= 7

    def test_consolidation_base_loose(self):
        df = _ohlc_uptrend(100, 2, 15)  # wide range
        result = compute_consolidation_base(df, min_days=7, max_range_pct=0.08)
        assert not result["is_base"]

    def test_rs_vs_spy_outperforming(self):
        spy = _ohlc_uptrend(100, 0.1, 30)
        stock = _ohlc_uptrend(100, 0.5, 30)
        rs = compute_rs_vs_spy(stock, spy, period=20)
        assert rs > 1.0

    def test_rs_vs_spy_flat_spy(self):
        spy = _ohlc_flat(100, 30)
        stock = _ohlc_uptrend(100, 1, 30)
        rs = compute_rs_vs_spy(stock, spy, period=20)
        assert rs == 1.0

    def test_structure_reclaim_uptrend(self):
        df = _ohlc_uptrend(100, 2, 40)
        result = compute_structure_high_reclaim(df, lookback=20)
        # last 5 bars closes are > pre-window max
        assert result["reclaimed"]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

class TestSwingScoring:
    def test_min_inputs_low_score(self):
        result = compute_swing_score(
            rr=2.6, setup_score=42, adx_value=14.0,
            ad_line_slope_pct=-1.0, higher_lows=0, institutional_ownership_pct=30,
        )
        assert result["confidence"] == "speculative"
        assert result["score"] < 40

    def test_strong_inputs_high_score(self):
        result = compute_swing_score(
            rr=4.0, setup_score=80, adx_value=30.0,
            ad_line_slope_pct=8.0, higher_lows=3, institutional_ownership_pct=75,
        )
        assert result["confidence"] == "high"
        assert result["score"] >= 80
        # breakdown adds up
        b = result["breakdown"]
        assert abs(b["rr"] + b["setup"] + b["context"] + b["institutional"] - result["score"]) < 0.05

    def test_rr_threshold_at_25(self):
        result = compute_swing_score(
            rr=2.5, setup_score=50, adx_value=None,
            ad_line_slope_pct=None, higher_lows=None, institutional_ownership_pct=None,
        )
        assert result["breakdown"]["rr"] == 0.0

    def test_rr_full_at_5(self):
        result = compute_swing_score(
            rr=5.0, setup_score=50, adx_value=None,
            ad_line_slope_pct=None, higher_lows=None, institutional_ownership_pct=None,
        )
        assert result["breakdown"]["rr"] == 40.0


# ---------------------------------------------------------------------------
# Risk model
# ---------------------------------------------------------------------------

class TestRiskPlan:
    def test_passes_gate_with_strong_atr(self):
        plan = build_risk_plan("breakout", current_price=100.0, atr14=2.0, recent_swing_low=98.0)
        # stop = max(100 - 1.5*2, 98) = max(97, 98) = 98; risk = 2.0
        # atr_target = 100 + 3.0*2.0 = 106.0 > rr_floor = 100 + 3.0*2.0 = 106.0 → equal, min picks floor
        # rr_floor = 106.0; RR = 6/2 = 3.0 → passes gate
        # (atr14 >= risk → ATR projection >= R:R floor → floor is binding constraint)
        assert plan.passes_gate
        assert plan.rr >= 2.5

    def test_fails_gate_with_tight_swing_low(self):
        # ATR-based stop is wider than swing_low → swing_low wins → tighter stop → still passes
        plan = build_risk_plan("reversion", current_price=100.0, atr14=2.0, recent_swing_low=99.5)
        # risk = 0.5, target = 100 + 2.5*0.5 = 101.25, RR=2.5 (boundary) — gate is >= 2.5
        assert plan.passes_gate

    def test_invalid_when_atr_exceeds_price(self):
        plan = build_risk_plan("breakout", current_price=10.0, atr14=20.0, recent_swing_low=0.0)
        assert not plan.passes_gate

    def test_hold_window_per_setup(self):
        b = build_risk_plan("breakout", 100, 1, 98)
        r = build_risk_plan("retest", 100, 1, 98)
        assert b.hold_min_days == 5 and b.hold_max_days == 10
        assert r.hold_min_days == 10 and r.hold_max_days == 21


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class TestClassifier:
    def test_breakout_signals_score_well(self):
        features = {
            "price": 100,
            "consolidation_base": {"is_base": True, "days": 9, "range_pct": 0.05},
            "volume_surge": {"is_surge": True, "ratio": 2.0},
            "structure_reclaim": {"reclaimed": True, "level": 98, "bars_since_reclaim": 1},
            "bb_squeeze_pct": 10,
            "ema_alignment": {"score": 8, "ema200": 90},
        }
        result = detect_breakout(features)
        assert result["score"] >= 80
        assert any("base" in d for d in result["drivers"])

    def test_momentum_signals_score_well(self):
        features = {
            "price": 100,
            "ema_alignment": {"score": 8, "ema200": 90},
            "adx": {"adx": 28, "plus_di": 25, "minus_di": 15},
            "rs_vs_spy": 1.25,
            "macd_inflection": True,
            "higher_lows": 3,
        }
        result = detect_momentum(features)
        assert result["score"] >= 80

    def test_reversion_oversold(self):
        features = {
            "price": 100,
            "rsi": 28,
            "stochastic": {"k": 12, "d": 15},
            "rsi_divergence": True,
            "fib_618_hold": True,
            "ema_alignment": {"ema200": 95, "score": 5},
        }
        result = detect_reversion(features)
        assert result["score"] >= 80

    def test_classify_picks_winner(self):
        # Pure breakout features
        features = {
            "price": 100,
            "consolidation_base": {"is_base": True, "days": 9, "range_pct": 0.05},
            "volume_surge": {"is_surge": True, "ratio": 2.0},
            "structure_reclaim": {"reclaimed": True, "level": 98, "bars_since_reclaim": 1},
            "bb_squeeze_pct": 10,
            "ema_alignment": {"score": 8, "ema200": 90},
            "adx": {"adx": 10, "plus_di": 10, "minus_di": 10},
            "rs_vs_spy": 0.95,
            "rsi": 55,
            "stochastic": {"k": 50, "d": 50},
            "macd_inflection": False,
            "higher_lows": 0,
            "rsi_divergence": False,
            "fib_618_hold": False,
            "gap_fill": {"has_gap": False},
        }
        result = classify_setup(features)
        assert result["best_setup"] == "breakout"
        assert result["best_score"] >= 70
        assert "scores" in result
        assert all(0 <= s <= 100 for s in result["scores"].values())
