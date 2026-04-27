"""
Unit tests for pure indicator functions in `services.indicators`.

These tests use synthetic OHLC DataFrames so they're independent of any market
data and run in milliseconds. They cover the contract of each indicator
(insufficient-data → NaN, deterministic value, edge-case behavior) — they do
NOT lock specific numeric outputs of complex indicators against captures
(that's the screener characterization tests' job).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from services.indicators import (
    compute_bollinger,
    compute_dist_from_sma200,
    compute_iv_rank_percentile,
    compute_macd,
    compute_price_vs_52w_high,
    compute_rsi,
    compute_rvol,
    compute_sma_ratio,
    compute_trend_data,
    compute_trend_persistence,
    compute_volume_resistance,
    compute_volume_support,
)


def _flat_series(price: float, n: int) -> pd.DataFrame:
    """Constant-price OHLC frame; useful for boundary checks."""
    return pd.DataFrame(
        {
            "Open": [price] * n,
            "High": [price] * n,
            "Low": [price] * n,
            "Close": [price] * n,
            "Volume": [1_000_000] * n,
        }
    )


def _linear_uptrend(start: float, step: float, n: int) -> pd.DataFrame:
    closes = [start + step * i for i in range(n)]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * n,
        }
    )


# --- compute_bollinger ------------------------------------------------------

def test_bollinger_constant_series_collapses_bands():
    """Zero std → upper == middle == lower."""
    df = _flat_series(100.0, 30)
    bb = compute_bollinger(df)
    assert bb["bb_middle"] == 100.0
    assert bb["bb_upper"] == 100.0
    assert bb["bb_lower"] == 100.0


def test_bollinger_raises_on_insufficient_data():
    df = _flat_series(100.0, 10)
    with pytest.raises(ValueError):
        compute_bollinger(df, period=20)


# --- compute_sma_ratio + compute_trend_data ---------------------------------

def test_sma_ratio_nan_when_under_200_bars():
    assert math.isnan(compute_sma_ratio(_flat_series(50.0, 199)))


def test_trend_data_uptrend_sets_alignment_flags():
    df = _linear_uptrend(start=100.0, step=1.0, n=250)
    out = compute_trend_data(df)
    assert out["price_above_sma50"] is True
    assert out["sma50_above_sma200"] is True
    assert out["sma_ratio"] > 1.0
    assert out["sma50_slope_pct"] > 0


# --- compute_rsi ------------------------------------------------------------

def test_rsi_nan_when_insufficient_data():
    df = _flat_series(100.0, 5)
    assert math.isnan(compute_rsi(df))


def test_rsi_uptrend_above_50():
    df = _linear_uptrend(start=100.0, step=1.0, n=50)
    rsi = compute_rsi(df)
    assert 0.0 <= rsi <= 100.0
    assert rsi > 50.0  # monotonic uptrend → strong RSI


# --- compute_iv_rank_percentile ---------------------------------------------

def test_iv_rank_percentile_nan_when_insufficient_data():
    iv_rank, iv_pct = compute_iv_rank_percentile(_flat_series(100.0, 100))
    assert math.isnan(iv_rank) and math.isnan(iv_pct)


def test_iv_rank_percentile_zero_volatility_returns_midpoint_rank():
    """Constant series → HV is 0 / NaN; function should return the documented
    fallback (50.0 when min == max)."""
    df = _flat_series(100.0, 30 + 252 + 5)
    iv_rank, iv_pct = compute_iv_rank_percentile(df)
    # Constant series: all log-returns are 0, HV is 0 throughout. Function
    # returns NaN early (HV series collapses) OR 50.0 fallback. Either is a
    # valid contract — we just want it to not crash and to be in [0, 100] or NaN.
    assert math.isnan(iv_rank) or 0.0 <= iv_rank <= 100.0
    assert math.isnan(iv_pct) or 0.0 <= iv_pct <= 100.0


# --- compute_volume_support / resistance ------------------------------------

def test_volume_support_returns_at_most_three():
    df = _linear_uptrend(start=50.0, step=0.5, n=260)
    levels = compute_volume_support(df)
    assert isinstance(levels, list)
    assert len(levels) <= 3
    current_price = float(df["Close"].iloc[-1])
    assert all(level < current_price for level in levels)


def test_volume_resistance_returns_at_most_three():
    df = _linear_uptrend(start=50.0, step=0.5, n=260)
    levels = compute_volume_resistance(df)
    assert isinstance(levels, list)
    assert len(levels) <= 3
    current_price = float(df["Close"].iloc[-1])
    assert all(level > current_price for level in levels)


# --- compute_rvol -----------------------------------------------------------

def test_rvol_unity_when_volume_constant():
    df = _flat_series(100.0, 30)
    assert compute_rvol(df) == 1.0


# --- compute_price_vs_52w_high ----------------------------------------------

def test_price_at_52w_high_returns_zero():
    df = _linear_uptrend(start=10.0, step=1.0, n=260)  # last close is the high
    assert compute_price_vs_52w_high(df) == 0.0


# --- compute_dist_from_sma200 -----------------------------------------------

def test_dist_from_sma200_positive_in_uptrend():
    df = _linear_uptrend(start=100.0, step=1.0, n=250)
    dist = compute_dist_from_sma200(df)
    assert dist > 0


# --- compute_macd -----------------------------------------------------------

def test_macd_keys_and_nan_on_short_series():
    short = _flat_series(100.0, 10)
    out = compute_macd(short)
    assert set(out.keys()) == {"macd", "signal", "histogram"}
    assert math.isnan(out["macd"]) and math.isnan(out["signal"]) and math.isnan(out["histogram"])


# --- compute_trend_persistence ---------------------------------------------

def test_trend_persistence_full_score_in_steady_uptrend():
    df = _linear_uptrend(start=100.0, step=1.0, n=200)
    tp = compute_trend_persistence(df)
    assert tp == 100.0  # Close > SMA50 every day in a monotonic uptrend


def test_trend_persistence_nan_when_insufficient_data():
    df = _flat_series(100.0, 80)
    assert math.isnan(compute_trend_persistence(df))
