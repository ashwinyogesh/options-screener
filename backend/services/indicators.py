"""
Pure technical indicators computed from OHLC DataFrames.

All functions in this module are stateless and side-effect-free. They accept a
pandas DataFrame with at least `Close` (and `High`/`Low`/`Volume` where noted)
and return a primitive or a small dict.

No I/O, no caching, no scoring: this module is the lowest layer of the
indicators → scoring → orchestration stack.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_bollinger(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0) -> dict:
    """
    Returns {'bb_upper': float, 'bb_middle': float, 'bb_lower': float}
    based on the last complete window in the close series.
    """
    close = df["Close"]
    if len(close) < period:
        raise ValueError(f"Not enough data for Bollinger Bands: need {period}, got {len(close)}")
    sma = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=1)
    bb_middle = float(sma.iloc[-1])
    bb_std = float(std.iloc[-1])
    return {
        "bb_upper": round(bb_middle + std_mult * bb_std, 4),
        "bb_middle": round(bb_middle, 4),
        "bb_lower": round(bb_middle - std_mult * bb_std, 4),
    }


def compute_sma_ratio(df: pd.DataFrame) -> float:
    """
    Returns SMA50 / SMA200.
    > 1.0 → price structure is bullish (50 above 200).
    < 1.0 → bearish.
    Returns NaN if not enough data.
    """
    close = df["Close"]
    if len(close) < 200:
        return float("nan")
    sma50  = float(close.rolling(50).mean().iloc[-1])
    sma200 = float(close.rolling(200).mean().iloc[-1])
    if sma200 == 0:
        return float("nan")
    return round(sma50 / sma200, 4)


def compute_trend_data(df: pd.DataFrame, slope_days: int = 10) -> dict:
    """
    Returns trend indicators needed for the revised CSP scorer:
      sma_ratio          : float  (sma50 / sma200, for display)
      price_above_sma50  : bool
      sma50_above_sma200 : bool
      sma50_slope_pct    : float  (% change in SMA50 over last slope_days days)
    """
    close = df["Close"]
    nan = float("nan")
    if len(close) < 200:
        return {
            "sma_ratio": nan,
            "price_above_sma50": False,
            "sma50_above_sma200": False,
            "sma50_slope_pct": nan,
        }
    sma50_series = close.rolling(50).mean()
    sma200_series = close.rolling(200).mean()
    sma50 = float(sma50_series.iloc[-1])
    sma200 = float(sma200_series.iloc[-1])
    current = float(close.iloc[-1])
    sma_ratio = round(sma50 / sma200, 4) if sma200 != 0 else nan

    sma50_valid = sma50_series.dropna()
    if len(sma50_valid) > slope_days:
        past = float(sma50_valid.iloc[-(slope_days + 1)])
        sma50_slope_pct = round((sma50 - past) / past * 100, 4) if past != 0 else nan
    else:
        sma50_slope_pct = nan

    return {
        "sma_ratio": sma_ratio,
        "price_above_sma50": current > sma50,
        "sma50_above_sma200": sma50 > sma200,
        "sma50_slope_pct": sma50_slope_pct,
    }


def compute_rsi(df: pd.DataFrame, period: int = 14) -> float:
    """
    Wilder-smoothed RSI(14).
    Returns float in [0, 100], or NaN if insufficient data.
    """
    close = df["Close"]
    if len(close) < period + 1:
        return float("nan")
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    # Wilder smoothing = exponential with alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain.iloc[-1] / avg_loss.iloc[-1] if avg_loss.iloc[-1] != 0 else float("inf")
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


def compute_trend_persistence(df: pd.DataFrame, lookback: int = 60) -> float:
    """
    % of last `lookback` sessions where Close > SMA50.
    Returns 0.0–100.0, or NaN if insufficient data.
    Better than RSI(14) for LEAPS horizon: measures sustained uptrend, not short-term noise.
    """
    close = df["Close"]
    if len(close) < 50 + lookback:
        return float("nan")
    sma50 = close.rolling(50).mean()
    recent_close = close.iloc[-lookback:]
    recent_sma50 = sma50.iloc[-lookback:]
    above = (recent_close.values > recent_sma50.values).sum()
    return round(float(above) / lookback * 100.0, 1)


def compute_iv_rank_percentile(
    df: pd.DataFrame,
    hv_window: int = 30,
    rank_window: int = 252,
) -> tuple[float, float]:
    """
    Uses rolling 30-day historical volatility (annualised) as an IV proxy.

    Returns (iv_rank, iv_percentile):
      iv_rank       = (HV_today - HV_min_252) / (HV_max_252 - HV_min_252) * 100
                      → How high is today's IV relative to its 52-week range.
      iv_percentile = % of days in last 252 where HV < today's HV
                      → How many days had lower IV than today.

    Both in [0, 100]. Returns (nan, nan) if not enough data.
    """
    close = df["Close"]
    if len(close) < hv_window + rank_window:
        return float("nan"), float("nan")

    log_ret = np.log(close / close.shift(1)).dropna()
    hv = log_ret.rolling(hv_window).std(ddof=1) * np.sqrt(252)
    hv = hv.dropna()

    if len(hv) < rank_window:
        return float("nan"), float("nan")

    window = hv.iloc[-rank_window:]
    current = float(hv.iloc[-1])
    hv_min, hv_max = float(window.min()), float(window.max())

    iv_rank = (
        round((current - hv_min) / (hv_max - hv_min) * 100, 2)
        if hv_max != hv_min else 50.0
    )
    iv_percentile = round(float((window < current).sum()) / len(window) * 100, 2)
    return iv_rank, iv_percentile


def compute_volume_support(df: pd.DataFrame, n_bins: int = 50, lookback: int = 252) -> list[float]:
    """
    Volume Profile support levels.

    Steps:
      1. Take up to `lookback` trading days of OHLC + Volume.
      2. Compute typical price = (H + L + C) / 3 per day.
      3. Bin typical prices into `n_bins` equal-width buckets.
      4. Sum volume in each bucket.
      5. Keep only buckets whose midpoint is below today's close.
      6. Return the midpoints of the top-3 buckets by volume,
         sorted descending (nearest support first).

    Returns a list of 0–3 floats. Empty list if insufficient data.
    """
    data = df.tail(lookback).copy()
    if len(data) < 20:
        return []

    current_price = float(data["Close"].iloc[-1])
    typical = (data["High"] + data["Low"] + data["Close"]) / 3.0

    try:
        bins = pd.cut(typical, bins=n_bins)
        vol_by_bin = data["Volume"].groupby(bins).sum()

        # Midpoint of each bin interval
        bin_mids = pd.Series(
            [interval.mid for interval in vol_by_bin.index],
            index=vol_by_bin.index,
        )

        # Only levels below current price
        below_mask = bin_mids < current_price
        below_vol = vol_by_bin[below_mask]
        below_mids = bin_mids[below_mask]

        if below_vol.empty:
            return []

        top3_labels = below_vol.nlargest(3).index
        support_prices = sorted(
            [float(below_mids[lbl]) for lbl in top3_labels],
            reverse=True,  # nearest (highest) first
        )
        return [round(p, 2) for p in support_prices]

    except Exception:
        return []


def compute_volume_resistance(df: pd.DataFrame, n_bins: int = 50, lookback: int = 252) -> list[float]:
    """
    Volume Profile resistance levels above current price.

    Same method as compute_volume_support but returns the midpoints of the top-3
    high-volume bins ABOVE current price, sorted ascending (nearest resistance first).
    """
    data = df.tail(lookback).copy()
    if len(data) < 20:
        return []

    current_price = float(data["Close"].iloc[-1])
    typical = (data["High"] + data["Low"] + data["Close"]) / 3.0

    try:
        bins = pd.cut(typical, bins=n_bins)
        vol_by_bin = data["Volume"].groupby(bins).sum()
        bin_mids = pd.Series(
            [interval.mid for interval in vol_by_bin.index],
            index=vol_by_bin.index,
        )

        above_mask = bin_mids > current_price
        above_vol = vol_by_bin[above_mask]
        above_mids = bin_mids[above_mask]

        if above_vol.empty:
            return []

        top3_labels = above_vol.nlargest(3).index
        resistance_prices = sorted(
            [float(above_mids[lbl]) for lbl in top3_labels]
        )  # ascending = nearest resistance first
        return [round(p, 2) for p in resistance_prices]

    except Exception:
        return []


def compute_rvol(df: pd.DataFrame, period: int = 20) -> float:
    """Relative volume: today's volume / avg(volume, last `period` days excluding today)."""
    vol = df["Volume"]
    if len(vol) < period + 1:
        return float("nan")
    avg_vol = float(vol.iloc[-(period + 1):-1].mean())
    if avg_vol == 0:
        return float("nan")
    return round(float(vol.iloc[-1]) / avg_vol, 2)


def compute_roc(df: pd.DataFrame, period: int = 21) -> float:
    """Rate of Change: % price change over `period` trading days."""
    close = df["Close"]
    if len(close) < period + 1:
        return float("nan")
    past = float(close.iloc[-(period + 1)])
    if past == 0:
        return float("nan")
    return round((float(close.iloc[-1]) - past) / past * 100, 2)


def compute_price_vs_52w_high(df: pd.DataFrame) -> float:
    """Returns % distance from 52-week high. 0 = at high, -10 = 10% below high."""
    close = df["Close"]
    lookback = min(252, len(close))
    if lookback < 20:
        return float("nan")
    high_52w = float(close.iloc[-lookback:].max())
    if high_52w == 0:
        return float("nan")
    return round((float(close.iloc[-1]) - high_52w) / high_52w * 100, 2)


def compute_sma20_slope(df: pd.DataFrame, n: int = 5) -> float:
    """% change in SMA20 over the last `n` days (short-term trend acceleration)."""
    close = df["Close"]
    if len(close) < 20 + n:
        return float("nan")
    sma20 = close.rolling(20).mean()
    past_sma = float(sma20.iloc[-(n + 1)])
    if past_sma == 0:
        return float("nan")
    return round((float(sma20.iloc[-1]) - past_sma) / past_sma * 100, 4)


def compute_price_vs_sma(df: pd.DataFrame, period: int = 20) -> float:
    """% by which current price is above/below SMA(period)."""
    close = df["Close"]
    if len(close) < period:
        return float("nan")
    sma = float(close.rolling(period).mean().iloc[-1])
    if sma == 0:
        return float("nan")
    return round((float(close.iloc[-1]) - sma) / sma * 100, 2)


def compute_dist_from_sma200(df: pd.DataFrame) -> float:
    """% above SMA200. Positive = above (bullish), negative = below."""
    close = df["Close"]
    if len(close) < 200:
        return float("nan")
    sma200 = float(close.rolling(200).mean().iloc[-1])
    if sma200 == 0:
        return float("nan")
    return round((float(close.iloc[-1]) - sma200) / sma200 * 100, 2)


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """
    Returns {'macd': float, 'signal': float, 'histogram': float}.
    histogram > 0 and growing = bullish momentum.
    """
    close = df["Close"]
    nan = float("nan")
    if len(close) < slow + signal:
        return {"macd": nan, "signal": nan, "histogram": nan}
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd":      round(float(macd_line.iloc[-1]), 4),
        "signal":    round(float(signal_line.iloc[-1]), 4),
        "histogram": round(float(histogram.iloc[-1]), 4),
    }
