"""
Computes technical indicators from OHLC DataFrames:
  - Bollinger Bands (20, 2)
  - SMA50/SMA200 ratio (trend signal)
  - RSI(14)
  - IV Rank + IV Percentile (HV-based proxy over 252 days)
  - Volume profile support levels
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


def compute_momentum_score(
    rvol: float,
    rsi: float,
    dist_from_52w_high_pct: float,
    sma_ratio: float,
    roc_21: float,
) -> float:
    """
    Composite momentum score 0–100. Weights:
      RVOL                  30 pts  (3× avg vol = max)
      RSI in 55–72 zone     20 pts
      Price near 52w high   25 pts  (<5% below = near max)
      SMA50/200 ratio       15 pts  (1.10+ = max)
      ROC(21)               10 pts  (10%+ = max)
    """
    import math as _math
    score = 0.0

    if not _math.isnan(rvol) and rvol > 0:
        score += min(rvol / 3.0, 1.0) * 30

    if not _math.isnan(rsi):
        if 55 <= rsi <= 72:
            score += 20
        elif (50 <= rsi < 55) or (72 < rsi <= 80):
            score += 12
        elif 45 <= rsi < 50:
            score += 5

    if not _math.isnan(dist_from_52w_high_pct):
        pct_below = abs(min(dist_from_52w_high_pct, 0.0))
        score += max(0.0, 1.0 - pct_below / 20.0) * 25

    if not _math.isnan(sma_ratio):
        score += min(max((sma_ratio - 1.0) / 0.10, 0.0), 1.0) * 15

    if not _math.isnan(roc_21):
        score += min(max(roc_21 / 10.0, 0.0), 1.0) * 10

    return round(score, 1)


def compute_env_score(
    *,
    iv_rank: float | None,
    iv_hv_ratio: float | None,
    price_above_sma50: bool,
    sma50_above_sma200: bool,
    dist_from_52w_high_pct: float,
    rsi: float,
    chain_median_oi: float,
    earnings_within_dte: bool,
) -> tuple[float, str]:
    """
    Environment Score 0–100 (+penalty).
    Measures whether *now* is a good time to sell puts on this stock.

    Volatility Edge (55):  IV Rank (30) + IV/HV Ratio (25)
    Trend Structure (30):  SMA Alignment (15) + 52W High Distance (15)
    Momentum (10):         RSI(14) (10)
    Liquidity (5):         Chain Median OI (5)  [circuit-breaker only; near-always maxed on liquid tickers]
    Earnings in DTE:       −15 penalty

    Returns (score, detail_string) where detail_string lists each component's earned pts.
    """
    import math as _math
    score = 0.0
    bk: dict[str, float] = {}

    # --- IV Rank (30 pts) ---
    p = 0.0
    if iv_rank is not None and not _math.isnan(iv_rank):
        if iv_rank >= 80:
            p = 30.0
        elif iv_rank >= 60:
            p = 18.0 + (iv_rank - 60) / 20.0 * 7.0
        elif iv_rank >= 40:
            p = 9.0 + (iv_rank - 40) / 20.0 * 9.0
        elif iv_rank >= 20:
            p = (iv_rank - 20) / 20.0 * 9.0
    score += p; bk['IV'] = p

    # --- IV / HV Ratio (25 pts) ---
    p = 0.0
    if iv_hv_ratio is not None and not _math.isnan(iv_hv_ratio):
        if iv_hv_ratio >= 1.7:
            p = 25.0
        elif iv_hv_ratio >= 1.4:
            p = 12.5 + (iv_hv_ratio - 1.4) / 0.3 * 7.5
        elif iv_hv_ratio >= 1.1:
            p = 6.0 + (iv_hv_ratio - 1.1) / 0.3 * 6.5
        elif iv_hv_ratio >= 0.9:
            p = 2.5 + (iv_hv_ratio - 0.9) / 0.2 * 3.5
        elif iv_hv_ratio >= 0.8:
            p = (iv_hv_ratio - 0.8) / 0.1 * 2.5
    score += p; bk['IH'] = p

    # --- SMA Alignment (15 pts): categorical ---
    p = 0.0
    if price_above_sma50 and sma50_above_sma200:
        p = 15.0
    elif price_above_sma50:
        p = 9.0
    elif sma50_above_sma200:
        p = 5.0
    score += p; bk['SMA'] = p

    # --- 52W High Distance (15 pts) ---
    p = 0.0
    if not _math.isnan(dist_from_52w_high_pct):
        pct_below = abs(min(dist_from_52w_high_pct, 0.0))
        if pct_below <= 5:
            p = 15.0
        elif pct_below <= 10:
            p = 11.0 - (pct_below - 5) / 5.0 * 4.0
        elif pct_below <= 20:
            p = 7.0 - (pct_below - 10) / 10.0 * 4.0
        elif pct_below <= 30:
            p = 3.0 - (pct_below - 20) / 10.0 * 3.0
    score += p; bk['52W'] = p

    # --- RSI(14) (10 pts) ---
    p = 0.0
    if not _math.isnan(rsi):
        if 42 <= rsi <= 62:
            p = 10.0
        elif 35 <= rsi < 42:
            p = 6.0 + (rsi - 35) / 7.0 * 4.0
        elif 62 < rsi <= 75:
            p = 10.0 * (75 - rsi) / 13.0
        elif 30 <= rsi < 35:
            p = 2.0
    score += p; bk['RSI'] = p

    # --- Chain Median OI (5 pts — circuit breaker for illiquid chains) ---
    p = 0.0
    if not _math.isnan(chain_median_oi) and chain_median_oi > 0:
        p = min(_math.log10(chain_median_oi) / _math.log10(5000), 1.0) * 5.0
    score += p; bk['OI'] = p

    # --- Earnings penalty ---
    earn_p = 0.0
    if earnings_within_dte:
        earn_p = -15.0
        score += earn_p

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    if earn_p != 0:
        detail += f' Ear:{round(earn_p)}'
    return round(score, 1), detail


def compute_csp_strike_score(
    *,
    delta: float,
    current_price: float,
    strike: float,
    iv_used: float,
    dte: int,
    vol_support_1: float | None,
    vol_support_2: float | None,
    vol_support_3: float | None,
    bid_ask_spread_pct: float | None,
    open_interest: int,
    market_open: bool,
    volume: int,
) -> tuple[float, str]:
    """
    Strike Safety Score 0–100.
    Measures how safe *this specific strike* is at *this expiration*.

    Delta (18):              Bell-curve peak at −0.20→−0.25
    Distance vs Support (18): Nearest vol-support level below strike  [+5 from 13]
    Expected Move Buffer (20): How far strike is outside 1σ move  [+5 from 15]
    % OTM from Spot (12):    Raw distance cushion from current price
    Bid-Ask Spread % (27):   Execution quality at this strike  [+5 from 22]
    OI / Volume (5):         Circuit-breaker for illiquid strikes  [−15 from 20]

    Returns (score, detail_string) where detail_string lists each component's earned pts.
    """
    import math as _math
    score = 0.0
    bk: dict[str, float] = {}

    # --- Delta bell-curve (18 pts) ---
    p = 0.0
    if not _math.isnan(delta):
        if -0.25 <= delta <= -0.20:
            p = 18.0
        elif (-0.30 <= delta < -0.25) or (-0.20 < delta <= -0.15):
            p = 12.0
        elif -0.15 < delta <= -0.10:
            p = 6.0
        elif delta < -0.30:
            p = 7.0
    score += p; bk['Δ'] = p

    # --- Distance vs Nearest Support Below Strike (18 pts) ---
    p = 0.0
    _csp_dist_pct: float | None = None
    supports = [s for s in [vol_support_1, vol_support_2, vol_support_3] if s is not None]
    supports_below = [s for s in supports if s < strike]
    if supports_below:
        nearest = max(supports_below)
        gap_pct = (strike - nearest) / strike * 100.0
        _csp_dist_pct = round(gap_pct, 2)
        if gap_pct <= 0:
            p = 18.0
        elif gap_pct <= 5:
            p = 18.0 - gap_pct / 5.0 * 8.0
        elif gap_pct <= 10:
            p = 10.0 - (gap_pct - 5) / 5.0 * 10.0
    elif supports:
        p = 7.0
    score += p; bk['Sup'] = p

    # --- Expected Move Buffer (20 pts) ---
    p = 0.0
    _em_buffer_pct: float = float('nan')
    if not _math.isnan(iv_used) and iv_used > 0 and dte > 0:
        T = dte / 365.0
        em = current_price * iv_used * _math.sqrt(T)
        em_lower = current_price - em
        sigmas_outside = (em_lower - strike) / em
        _em_buffer_pct = round(sigmas_outside * 100, 2)
        if sigmas_outside >= 0.20:
            p = 20.0
        elif sigmas_outside >= 0.0:
            p = 13.0 + sigmas_outside / 0.20 * 7.0
        elif sigmas_outside >= -0.10:
            p = 5.0 + (sigmas_outside + 0.10) / 0.10 * 8.0
    score += p; bk['EM'] = p

    # --- % OTM from Spot (12 pts) ---
    p = 0.0
    otm_pct = (current_price - strike) / current_price * 100.0
    if otm_pct >= 15:
        p = 12.0
    elif otm_pct >= 10:
        p = 9.0 + (otm_pct - 10) / 5.0 * 3.0
    elif otm_pct >= 5:
        p = 6.0 + (otm_pct - 5) / 5.0 * 3.0
    elif otm_pct >= 2:
        p = 2.0 + (otm_pct - 2) / 3.0 * 4.0
    score += p; bk['OTM'] = p

    # --- Bid-Ask Spread % (27 pts) ---
    p = 0.0
    if bid_ask_spread_pct is not None and not _math.isnan(bid_ask_spread_pct):
        if bid_ask_spread_pct <= 1.0:
            p = 27.0
        elif bid_ask_spread_pct <= 3.0:
            p = 18.0 + (3.0 - bid_ask_spread_pct) / 2.0 * 9.0
        elif bid_ask_spread_pct <= 5.0:
            p = 10.0 + (5.0 - bid_ask_spread_pct) / 2.0 * 8.0
        elif bid_ask_spread_pct <= 8.0:
            p = 2.5 + (8.0 - bid_ask_spread_pct) / 3.0 * 7.5
    score += p; bk['BA'] = p

    # --- OI / Volume at this strike (5 pts — circuit breaker) ---
    p = 0.0
    liquidity_count = volume if (market_open and volume > 0) else open_interest
    if liquidity_count >= 1000:
        p = 5.0
    elif liquidity_count >= 500:
        p = 3.5 + (liquidity_count - 500) / 500.0 * 1.5
    elif liquidity_count >= 200:
        p = 2.0 + (liquidity_count - 200) / 300.0 * 1.5
    elif liquidity_count >= 100:
        p = (liquidity_count - 100) / 100.0 * 2.0
    score += p; bk['LQ'] = p

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    raw = {
        'dist_pct': _csp_dist_pct,
        'em_buffer_pct': _em_buffer_pct,
        'otm_pct': otm_pct,
        'lq_count': liquidity_count,
    }
    return round(max(0.0, min(100.0, score)), 1), detail, raw


def compute_csp_final_score(env_score: float, strike_score: float) -> float:
    """Final Score = 0.4 × Env Score + 0.6 × Strike Score."""
    return round(0.4 * env_score + 0.6 * strike_score, 1)


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


def compute_cc_strike_score(
    *,
    delta: float,
    current_price: float,
    strike: float,
    iv_used: float,
    dte: int,
    vol_resistance_1: float | None,
    vol_resistance_2: float | None,
    vol_resistance_3: float | None,
    bid_ask_spread_pct: float | None,
    open_interest: int,
    market_open: bool,
    volume: int,
) -> tuple[float, str]:
    """
    CC Strike Safety Score 0–100.
    Measures how safe *this specific call strike* is at *this expiration*.

    Delta (18):               Bell-curve peak at +0.20→+0.25
    Distance vs Resistance (18): Nearest vol-resistance level above current price  [+5 from 13]
    Expected Move Buffer (20):  How far strike is above 1σ upward move  [+5 from 15]
    % OTM from Spot (12):    Raw distance cushion above current price
    Bid-Ask Spread % (27):   Execution quality at this strike  [+5 from 22]
    OI / Volume (5):         Circuit-breaker for illiquid strikes  [−15 from 20]

    Returns (score, detail_string) where detail_string lists each component's earned pts.
    """
    import math as _math
    score = 0.0
    bk: dict[str, float] = {}

    # --- Delta bell-curve (18 pts) ---
    p = 0.0
    if not _math.isnan(delta):
        if 0.20 <= delta <= 0.25:
            p = 18.0
        elif (0.15 <= delta < 0.20) or (0.25 < delta <= 0.30):
            p = 12.0
        elif 0.10 <= delta < 0.15:
            p = 6.0
        elif delta > 0.30:
            p = 7.0
    score += p; bk['Δ'] = p

    # --- Distance vs Nearest Resistance Above Current Price (18 pts) ---
    p = 0.0
    _cc_dist_pct: float | None = None
    resistances = [r for r in [vol_resistance_1, vol_resistance_2, vol_resistance_3] if r is not None]
    resistances_above_price = [r for r in resistances if r > current_price]
    if resistances_above_price:
        nearest_R = min(resistances_above_price)
        gap_pct = (nearest_R - strike) / strike * 100.0
        _cc_dist_pct = round(gap_pct, 2)
        if gap_pct <= 0:
            p = 18.0
            if all(r <= strike for r in resistances_above_price):
                p += 5.0
        elif gap_pct <= 5:
            p = 18.0 - gap_pct / 5.0 * 8.0
        elif gap_pct <= 10:
            p = 10.0 - (gap_pct - 5) / 5.0 * 10.0
    score += p; bk['Res'] = p

    # --- Expected Move Buffer (20 pts) ---
    p = 0.0
    _cc_em_buffer_pct: float = float('nan')
    if not _math.isnan(iv_used) and iv_used > 0 and dte > 0:
        T = dte / 365.0
        em = current_price * iv_used * _math.sqrt(T)
        em_upper = current_price + em
        sigmas_outside = (strike - em_upper) / em
        _cc_em_buffer_pct = round(sigmas_outside * 100, 2)
        if sigmas_outside >= 0.20:
            p = 20.0
        elif sigmas_outside >= 0.0:
            p = 13.0 + sigmas_outside / 0.20 * 7.0
        elif sigmas_outside >= -0.10:
            p = 5.0 + (sigmas_outside + 0.10) / 0.10 * 8.0
    score += p; bk['EM'] = p

    # --- % OTM from Spot (12 pts) ---
    p = 0.0
    otm_pct = (strike - current_price) / current_price * 100.0
    if otm_pct >= 15:
        p = 12.0
    elif otm_pct >= 10:
        p = 9.0 + (otm_pct - 10) / 5.0 * 3.0
    elif otm_pct >= 5:
        p = 6.0 + (otm_pct - 5) / 5.0 * 3.0
    elif otm_pct >= 2:
        p = 2.0 + (otm_pct - 2) / 3.0 * 4.0
    score += p; bk['OTM'] = p

    # --- Bid-Ask Spread % (27 pts) ---
    p = 0.0
    if bid_ask_spread_pct is not None and not _math.isnan(bid_ask_spread_pct):
        if bid_ask_spread_pct <= 1.0:
            p = 27.0
        elif bid_ask_spread_pct <= 3.0:
            p = 18.0 + (3.0 - bid_ask_spread_pct) / 2.0 * 9.0
        elif bid_ask_spread_pct <= 5.0:
            p = 10.0 + (5.0 - bid_ask_spread_pct) / 2.0 * 8.0
        elif bid_ask_spread_pct <= 8.0:
            p = 2.5 + (8.0 - bid_ask_spread_pct) / 3.0 * 7.5
    score += p; bk['BA'] = p

    # --- OI / Volume at this strike (5 pts — circuit breaker) ---
    p = 0.0
    liquidity_count = volume if (market_open and volume > 0) else open_interest
    if liquidity_count >= 1000:
        p = 5.0
    elif liquidity_count >= 500:
        p = 3.5 + (liquidity_count - 500) / 500.0 * 1.5
    elif liquidity_count >= 200:
        p = 2.0 + (liquidity_count - 200) / 300.0 * 1.5
    elif liquidity_count >= 100:
        p = (liquidity_count - 100) / 100.0 * 2.0
    score += p; bk['LQ'] = p

    detail = ' '.join(f"{k}:{round(v)}" for k, v in bk.items())
    raw = {
        'dist_pct': _cc_dist_pct,
        'em_buffer_pct': _cc_em_buffer_pct,
        'otm_pct': otm_pct,
        'lq_count': liquidity_count,
    }
    return round(max(0.0, min(100.0, score)), 1), detail, raw



def compute_cc_final_score(env_score: float, strike_score: float) -> float:
    """CC Final Score = 0.4 × Env Score + 0.6 × Strike Score."""
    return round(0.4 * env_score + 0.6 * strike_score, 1)


def compute_ditm_env_score(
    *,
    iv_hv_ratio: float | None,
    price_above_sma50: bool,
    sma50_above_sma200: bool,
    sma50_slope_pct: float | None,
    dist_from_52w_high_pct: float,
    trend_persistence: float | None,
    chain_median_oi: float,
    days_to_earnings: int | None,
    iv_rank: float | None,
) -> float:
    """
    DITM Environment Score 0–100.
    For BUYING calls: LOW IV is good (cheap premium), STRONG TREND is critical.

    IV Cheapness (45):        IV/HV Ratio inverted (sole IV metric — edge vs realized vol)
    Trend Strength (30):      SMA Alignment + SMA50 Slope + 52W High Proximity (composite)
    Trend Persistence (10):   % of last 60 sessions above SMA50 (LEAPS-appropriate momentum)
    Liquidity (10):           Chain Median OI
    Earnings penalty:         tiered, softened when IV Rank >50 (already priced in)
    """
    import math as _math
    score = 0.0

    # --- IV/HV Ratio INVERTED (45 pts) — sole IV metric ---
    # Measures edge: IV < HV means options cheaper than what stock actually moves
    # <0.7=45, 0.7–0.9 linear 45→27, 0.9–1.1 linear 27→13, 1.1–1.5 linear 13→2, >1.5=0
    if iv_hv_ratio is not None and not _math.isnan(iv_hv_ratio):
        if iv_hv_ratio < 0.7:
            score += 45.0
        elif iv_hv_ratio < 0.9:
            score += 45.0 - (iv_hv_ratio - 0.7) / 0.2 * 18.0
        elif iv_hv_ratio < 1.1:
            score += 27.0 - (iv_hv_ratio - 0.9) / 0.2 * 14.0
        elif iv_hv_ratio < 1.5:
            score += 13.0 - (iv_hv_ratio - 1.1) / 0.4 * 11.0
        # >=1.5: 0 pts

    # --- Trend Strength Composite (30 pts) ---
    # SMA Alignment: 15 pts
    if price_above_sma50 and sma50_above_sma200:
        score += 15.0
    elif price_above_sma50:
        score += 9.0
    elif sma50_above_sma200:
        score += 4.0

    # SMA50 Slope: 7 pts (positive and rising = uptrend has momentum)
    if sma50_slope_pct is not None and not _math.isnan(sma50_slope_pct):
        if sma50_slope_pct > 1.0:
            score += 7.0
        elif sma50_slope_pct > 0.3:
            score += 7.0 - (1.0 - sma50_slope_pct) / 0.7 * 2.0
        elif sma50_slope_pct > 0.0:
            score += 5.0 - (0.3 - sma50_slope_pct) / 0.3 * 3.0
        elif sma50_slope_pct > -0.5:
            score += 1.0
        # <= -0.5%: 0 — declining SMA50

    # 52W High Proximity: 8 pts
    if not _math.isnan(dist_from_52w_high_pct):
        pct_below = abs(min(dist_from_52w_high_pct, 0.0))
        if pct_below <= 5:
            score += 8.0
        elif pct_below <= 15:
            score += 8.0 - (pct_below - 5) / 10.0 * 5.0
        elif pct_below <= 30:
            score += 3.0 - (pct_below - 15) / 15.0 * 3.0
        # >30%: 0

    # --- Trend Persistence (10 pts) — % of last 60 sessions above SMA50 ---
    # Replaces RSI(14): better signal for LEAPS horizon
    # >=75%=10, 60–75 linear 10→6, 50–60 linear 6→3, 40–50=1, <40=0
    if trend_persistence is not None and not _math.isnan(trend_persistence):
        if trend_persistence >= 75:
            score += 10.0
        elif trend_persistence >= 60:
            score += 10.0 - (75 - trend_persistence) / 15.0 * 4.0
        elif trend_persistence >= 50:
            score += 6.0 - (60 - trend_persistence) / 10.0 * 3.0
        elif trend_persistence >= 40:
            score += 1.0
        # <40%: 0 — stock spends too much time below SMA50

    # --- Chain Median OI (10 pts) — log scale ---
    if not _math.isnan(chain_median_oi) and chain_median_oi > 0:
        score += min(_math.log10(chain_median_oi) / _math.log10(5000), 1.0) * 10.0

    # --- Earnings penalty (tiered; softened when IV Rank >50 = already priced) ---
    if days_to_earnings is not None and days_to_earnings >= 0:
        if days_to_earnings < 14:
            penalty = 15.0 if (iv_rank is None or iv_rank <= 50) else 8.0
        elif days_to_earnings < 30:
            penalty = 8.0 if (iv_rank is None or iv_rank <= 50) else 4.0
        elif days_to_earnings < 60:
            penalty = 3.0 if (iv_rank is None or iv_rank <= 50) else 1.0
        else:
            penalty = 0.0
        score -= penalty

    return round(score, 1)


def compute_ditm_strike_score(
    *,
    delta: float,
    current_price: float,
    strike: float,
    premium: float,
    bid_ask_spread_pct: float | None,
    open_interest: int,
    market_open: bool,
    volume: int,
) -> float:
    """
    DITM Strike Quality Score 0–100.
    Measures how efficient *this specific deep ITM call* is to buy.

    Delta (35):           Deep ITM sweet spot 0.80–0.85 (dropped Moneyness — correlated)
    Extrinsic % (35):     Extrinsic / Stock Price × 100 — lower = less time premium wasted
    Bid-Ask Spread (20):  Execution cost (raised from 15 — key cost for DITM)
    OI / Volume (10):     Liquidity depth at this specific strike
    """
    import math as _math
    score = 0.0

    # --- Delta (35 pts) — sweet spot 0.80–0.85 ---
    if not _math.isnan(delta):
        if 0.80 <= delta <= 0.85:
            score += 35.0
        elif (0.75 <= delta < 0.80) or (0.85 < delta <= 0.90):
            score += 28.0
        elif (0.70 <= delta < 0.75) or (0.90 < delta <= 0.95):
            score += 18.0
        elif (0.65 <= delta < 0.70) or (0.95 < delta < 1.0):
            score += 9.0
        # <0.65: 0

    # --- Extrinsic % (35 pts) — extrinsic / stock price, lower is better ---
    intrinsic = max(0.0, current_price - strike)
    extrinsic = max(0.0, premium - intrinsic)
    extrinsic_pct = (extrinsic / current_price * 100.0) if current_price > 0 else 100.0

    if extrinsic_pct <= 1.0:
        score += 35.0
    elif extrinsic_pct <= 2.0:
        score += 35.0 - (extrinsic_pct - 1.0) * 9.0
    elif extrinsic_pct <= 4.0:
        score += 26.0 - (extrinsic_pct - 2.0) / 2.0 * 12.0
    elif extrinsic_pct <= 6.0:
        score += 14.0 - (extrinsic_pct - 4.0) / 2.0 * 9.0
    elif extrinsic_pct <= 9.0:
        score += 5.0 - (extrinsic_pct - 6.0) / 3.0 * 5.0
    # >9%: 0

    # --- Bid-Ask Spread % (20 pts) ---
    if bid_ask_spread_pct is not None and not _math.isnan(bid_ask_spread_pct):
        if bid_ask_spread_pct <= 1.0:
            score += 20.0
        elif bid_ask_spread_pct <= 3.0:
            score += 20.0 - (bid_ask_spread_pct - 1.0) / 2.0 * 7.0
        elif bid_ask_spread_pct <= 5.0:
            score += 13.0 - (bid_ask_spread_pct - 3.0) / 2.0 * 6.0
        elif bid_ask_spread_pct <= 8.0:
            score += 7.0 - (bid_ask_spread_pct - 5.0) / 3.0 * 5.0
        elif bid_ask_spread_pct <= 12.0:
            score += 2.0
        # >12%: 0

    # --- OI / Volume (10 pts) ---
    liquidity_count = volume if (market_open and volume > 0) else open_interest
    if liquidity_count >= 500:
        score += 10.0
    elif liquidity_count >= 200:
        score += 10.0 - (500 - liquidity_count) / 300.0 * 4.0
    elif liquidity_count >= 100:
        score += 6.0 - (200 - liquidity_count) / 100.0 * 3.0
    elif liquidity_count >= 50:
        score += 3.0 - (100 - liquidity_count) / 50.0 * 3.0
    # <50: 0

    return round(max(0.0, min(100.0, score)), 1)


def compute_ditm_final_score(env_score: float, strike_score: float) -> float:
    """DITM Final Score = 0.35 × Env + 0.65 × Strike (strike quality dominates for DITM)."""
    return round(0.35 * env_score + 0.65 * strike_score, 1)
