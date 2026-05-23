"""
Swing-screener indicators — pure-equity technical signals.

All functions are stateless. They accept a pandas DataFrame with OHLC columns
and return primitives or small dicts. No I/O, no scoring — lowest layer.

These indicators are swing-screener-specific. Shared indicators (Bollinger,
RSI, MACD, volume profile, IV percentile, SMA trend) live in
`services/indicators.py`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Restored: trend / volatility primitives
# ---------------------------------------------------------------------------

def compute_ema(df: pd.DataFrame, period: int) -> float:
    """Latest EMA(period) of Close. NaN if insufficient data."""
    close = df["Close"]
    if len(close) < period:
        return float("nan")
    return round(float(close.ewm(span=period, adjust=False).mean().iloc[-1]), 4)


def compute_ema_alignment(df: pd.DataFrame) -> dict:
    """
    Scores how many EMA levels (8, 21, 50, 200) current price is above.

    Returns:
      score   : 0–9  (+2 per EMA above, +1 bonus if all four)
      ema8/21/50/200 : float
      detail  : "above 8,21,50 | below 200"
    """
    close = df["Close"]
    nan = float("nan")
    if len(close) < 200:
        return {"score": 0, "ema8": nan, "ema21": nan, "ema50": nan, "ema200": nan, "detail": "insufficient data"}

    price = float(close.iloc[-1])
    emas = {p: float(close.ewm(span=p, adjust=False).mean().iloc[-1]) for p in (8, 21, 50, 200)}
    above = [p for p in (8, 21, 50, 200) if price > emas[p]]
    score = len(above) * 2 + (1 if len(above) == 4 else 0)
    below = [p for p in (8, 21, 50, 200) if p not in above]
    detail = f"above {','.join(str(p) for p in above) or 'none'} | below {','.join(str(p) for p in below) or 'none'}"
    return {
        "score": score,
        "ema8": round(emas[8], 4),
        "ema21": round(emas[21], 4),
        "ema50": round(emas[50], 4),
        "ema200": round(emas[200], 4),
        "detail": detail,
    }


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Wilder-smoothed Average True Range. NaN if insufficient data."""
    if len(df) < period + 2:
        return float("nan")
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return round(float(atr.iloc[-1]), 4)


def compute_adx(df: pd.DataFrame, period: int = 14) -> dict:
    """
    Wilder-smoothed ADX with +DI / -DI.
    Returns {adx, plus_di, minus_di}. NaN values if insufficient data.
    """
    nan = float("nan")
    default = {"adx": nan, "plus_di": nan, "minus_di": nan}
    if len(df) < period * 2 + 2:
        return default

    high = df["High"].values.astype(float)
    low = df["Low"].values.astype(float)
    close = df["Close"].values.astype(float)

    prev_close = np.roll(close, 1); prev_close[0] = np.nan
    prev_high = np.roll(high, 1); prev_high[0] = np.nan
    prev_low = np.roll(low, 1); prev_low[0] = np.nan

    tr_arr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    alpha = 1.0 / period
    s_tr = pd.Series(tr_arr).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    s_plus = pd.Series(plus_dm).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    s_minus = pd.Series(minus_dm).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    safe_tr = s_tr.replace(0.0, np.nan)
    plus_di_ser = 100.0 * s_plus / safe_tr
    minus_di_ser = 100.0 * s_minus / safe_tr
    di_sum = plus_di_ser + minus_di_ser
    di_diff = (plus_di_ser - minus_di_ser).abs()
    dx = (100.0 * di_diff / di_sum.replace(0.0, np.nan)).fillna(0.0)
    adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    return {
        "adx": round(float(adx.iloc[-1]), 2),
        "plus_di": round(float(plus_di_ser.iloc[-1]), 2),
        "minus_di": round(float(minus_di_ser.iloc[-1]), 2),
    }


def compute_bb_squeeze_percentile(df: pd.DataFrame, period: int = 20, lookback: int = 252) -> float:
    """
    Percentile rank of today's BB width in its `lookback`-day history.
    0 = tightest ever (max squeeze), 100 = widest ever.
    """
    close = df["Close"]
    if len(close) < period + lookback:
        return float("nan")
    sma = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=1)
    width = (2 * std) / sma.replace(0, float("nan"))
    width = width.dropna()
    if len(width) < lookback:
        return float("nan")
    window = width.iloc[-lookback:]
    current_w = float(window.iloc[-1])
    pct = float((window < current_w).sum()) / len(window) * 100.0
    return round(pct, 2)


def compute_rs_vs_spy(df: pd.DataFrame, spy_df: pd.DataFrame, period: int = 20) -> float:
    """Relative strength vs SPY over `period` days. Returns 1.0 on flat SPY."""
    _SPY_FLAT = 0.005
    close = df["Close"]; spy = spy_df["Close"]
    if len(close) < period + 1 or len(spy) < period + 1:
        return float("nan")
    sret = (float(close.iloc[-1]) - float(close.iloc[-(period + 1)])) / float(close.iloc[-(period + 1)])
    spret = (float(spy.iloc[-1]) - float(spy.iloc[-(period + 1)])) / float(spy.iloc[-(period + 1)])
    if abs(spret) < _SPY_FLAT:
        return 1.0
    return round(sret / spret, 4)


def compute_ad_line_slope(df: pd.DataFrame, slope_period: int = 20) -> float:
    """
    A/D line slope (% change) over slope_period days.
    Positive = money flowing in; negative = distribution.
    """
    if len(df) < slope_period + 2:
        return float("nan")
    high, low, close, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    hl_range = (high - low).replace(0, float("nan"))
    clv = ((close - low) - (high - close)) / hl_range
    ad = (clv * vol).cumsum().dropna()
    if len(ad) < slope_period + 1:
        return float("nan")
    past = float(ad.iloc[-(slope_period + 1)])
    if past == 0:
        return float(ad.iloc[-1] - past)
    return round((float(ad.iloc[-1]) - past) / abs(past) * 100, 4)


def compute_higher_lows(df: pd.DataFrame, lookback: int = 30) -> int:
    """
    Count of consecutive higher swing-low transitions in last `lookback` bars.
    Swing low = bar whose Low < both neighbours.
    """
    if len(df) < lookback + 2:
        return 0
    lows = df["Low"].iloc[-(lookback + 2):]
    swing_lows: list[float] = []
    for i in range(1, len(lows) - 1):
        if lows.iloc[i] < lows.iloc[i - 1] and lows.iloc[i] < lows.iloc[i + 1]:
            swing_lows.append(float(lows.iloc[i]))
    if len(swing_lows) < 2:
        return 0
    count = 0
    for i in range(1, len(swing_lows)):
        if swing_lows[i] > swing_lows[i - 1]:
            count += 1
        else:
            count = 0
    return count


def compute_avg_daily_volume(df: pd.DataFrame, period: int = 20) -> float:
    """Mean daily share volume over last `period` sessions."""
    if len(df) < period:
        return float("nan")
    return round(float(df["Volume"].tail(period).mean()), 0)


# ---------------------------------------------------------------------------
# New: swing-spec-specific signals
# ---------------------------------------------------------------------------

def compute_stochastic(df: pd.DataFrame, period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> dict:
    """Slow stochastic %K and %D. NaN if insufficient data."""
    if len(df) < period + smooth_k + smooth_d:
        return {"k": float("nan"), "d": float("nan")}
    high = df["High"].rolling(period).max()
    low = df["Low"].rolling(period).min()
    rng = (high - low).replace(0, float("nan"))
    raw_k = 100 * (df["Close"] - low) / rng
    k = raw_k.rolling(smooth_k).mean()
    d = k.rolling(smooth_d).mean()
    return {"k": round(float(k.iloc[-1]), 2), "d": round(float(d.iloc[-1]), 2)}


def compute_rsi_divergence(df: pd.DataFrame, lookback: int = 30) -> bool:
    """
    Bullish RSI divergence: price prints a lower low while RSI prints a higher low.
    Returns True only if the most recent 2 swing lows (within lookback) show this pattern.
    """
    if len(df) < lookback + 14:
        return False
    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)

    close_window = df["Close"].iloc[-lookback:]
    rsi_window = rsi.iloc[-lookback:]
    # find local swing lows in close (Low < neighbours)
    lows_idx: list[int] = []
    for i in range(1, len(close_window) - 1):
        if close_window.iloc[i] < close_window.iloc[i - 1] and close_window.iloc[i] < close_window.iloc[i + 1]:
            lows_idx.append(i)
    if len(lows_idx) < 2:
        return False
    a, b = lows_idx[-2], lows_idx[-1]
    price_ll = close_window.iloc[b] < close_window.iloc[a]
    rsi_hl = rsi_window.iloc[b] > rsi_window.iloc[a]
    return bool(price_ll and rsi_hl)


def compute_macd_histogram_inflection(df: pd.DataFrame, lookback: int = 5) -> bool:
    """
    True if MACD histogram crossed above zero within the last `lookback` bars.
    Standard 12/26/9 MACD.
    """
    close = df["Close"]
    if len(close) < 35 + lookback:
        return False
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    recent = hist.iloc[-(lookback + 1):]
    # any zero-cross from negative to positive?
    for i in range(1, len(recent)):
        if recent.iloc[i - 1] <= 0 and recent.iloc[i] > 0:
            return True
    return False


def compute_macd_histogram_value(df: pd.DataFrame) -> float | None:
    """
    Return the current MACD histogram value (fast=12, slow=26, signal=9).

    Positive = bullish momentum (MACD above signal line).
    Negative = bearish momentum.
    Returns None if insufficient data (< 35 bars).

    v3.0: added for IC-based scoring — rho(macd_hist, r_realized) = +0.209
    on the 3,366-trade walk-forward backtest (2024-2026).
    """
    close = df["Close"]
    if len(close) < 35:
        return None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    return round(float(hist.iloc[-1]), 4)


def compute_bb_position(df: pd.DataFrame, period: int = 20) -> float | None:
    """
    Return price's position within the 20-day Bollinger Bands (±2σ).

    0.0 = at lower band, 0.5 = at middle (SMA), 1.0 = at upper band.
    Can be < 0 (below lower) or > 1 (above upper) in extreme moves.
    Returns None if insufficient data.

    v3.0: added for IC-based scoring — rho(bb_pos, r_realized) = +0.180
    on the 3,366-trade walk-forward backtest (2024-2026). Higher position
    (price near or above upper band) correlates with better forward returns —
    a momentum-continuation signal.
    """
    close = df["Close"]
    if len(close) < period:
        return None
    sma = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=1)
    upper = sma + 2.0 * std
    lower = sma - 2.0 * std
    price = float(close.iloc[-1])
    lo = float(lower.iloc[-1])
    hi = float(upper.iloc[-1])
    if hi == lo:
        return 0.5
    return round((price - lo) / (hi - lo), 4)


def compute_consolidation_base(df: pd.DataFrame, min_days: int = 7, max_range_pct: float = 0.08) -> dict:
    """
    Detect a tight consolidation base: most recent N>=min_days where
    (high-low)/midpoint <= max_range_pct.

    Returns {days, range_pct, is_base}.
    Walks backward from the latest bar; the base ends at the latest bar.
    """
    if len(df) < min_days + 1:
        return {"days": 0, "range_pct": float("nan"), "is_base": False, "base_high": float("nan"), "base_low": float("nan")}
    high = df["High"]; low = df["Low"]
    # Walk back from end, expand window while range stays tight.
    days = 0
    best_days = 0
    best_range = float("nan")
    best_high = float("nan")
    best_low = float("nan")
    for n in range(min_days, min(60, len(df)) + 1):
        win_high = float(high.iloc[-n:].max())
        win_low = float(low.iloc[-n:].min())
        mid = (win_high + win_low) / 2.0
        if mid == 0:
            break
        rng = (win_high - win_low) / mid
        if rng <= max_range_pct:
            days = n
            best_days = n
            best_range = rng
            best_high = win_high
            best_low = win_low
        else:
            break
    return {
        "days": best_days,
        "range_pct": round(best_range, 4) if not np.isnan(best_range) else float("nan"),
        "is_base": best_days >= min_days,
        "base_high": round(best_high, 4) if not np.isnan(best_high) else float("nan"),
        "base_low": round(best_low, 4) if not np.isnan(best_low) else float("nan"),
    }


def compute_volume_surge(df: pd.DataFrame, lookback: int = 20, surge_ratio: float = 1.5) -> dict:
    """
    Latest bar volume vs lookback-bar average.
    Returns {ratio, is_surge}.
    """
    if len(df) < lookback + 1:
        return {"ratio": float("nan"), "is_surge": False}
    avg = float(df["Volume"].iloc[-(lookback + 1):-1].mean())
    if avg <= 0:
        return {"ratio": float("nan"), "is_surge": False}
    latest = float(df["Volume"].iloc[-1])
    ratio = latest / avg
    return {"ratio": round(ratio, 2), "is_surge": ratio >= surge_ratio}


def compute_fib_retracement_hold(df: pd.DataFrame, level: float = 0.618, tolerance: float = 0.02, lookback: int = 60) -> bool:
    """
    True if price is currently within `tolerance` of the Fibonacci `level` retracement
    of the most recent meaningful swing (low → high within `lookback`).
    """
    if len(df) < lookback:
        return False
    win = df.iloc[-lookback:]
    swing_low = float(win["Low"].min())
    swing_high = float(win["High"].max())
    if swing_high <= swing_low:
        return False
    # Only meaningful if the high came AFTER the low (otherwise it's a downswing)
    low_idx = int(win["Low"].values.argmin())
    high_idx = int(win["High"].values.argmax())
    if high_idx <= low_idx:
        return False
    fib_price = swing_high - (swing_high - swing_low) * level
    current = float(df["Close"].iloc[-1])
    return abs(current - fib_price) / fib_price <= tolerance


def compute_gap_fill_candidate(df: pd.DataFrame, within_pct: float = 0.05, lookback: int = 60) -> dict:
    """
    Find the nearest unfilled gap within `within_pct` of current price.
    Returns {has_gap, gap_level, distance_pct}.
    Gap up = Low[i] > High[i-1]; gap down = High[i] < Low[i-1].
    "Unfilled" = subsequent bars haven't traded through the gap.
    """
    nan = float("nan")
    none_result = {"has_gap": False, "gap_level": nan, "distance_pct": nan}
    if len(df) < lookback + 1:
        return none_result
    win = df.iloc[-lookback:]
    high = win["High"].values
    low = win["Low"].values
    current = float(df["Close"].iloc[-1])

    nearest_gap = None
    nearest_dist = float("inf")
    for i in range(1, len(win)):
        # gap up at bar i
        if low[i] > high[i - 1]:
            gap_top = float(low[i])
            gap_bot = float(high[i - 1])
            # filled if any subsequent bar's Low <= gap_top AND High >= gap_bot... simplified:
            # gap is filled when price has revisited the gap_bot
            subsequent_low = float(win["Low"].iloc[i + 1:].min()) if i + 1 < len(win) else current
            if subsequent_low > gap_bot:  # unfilled
                level = gap_bot
                dist_pct = abs(current - level) / current
                if dist_pct < nearest_dist:
                    nearest_dist = dist_pct
                    nearest_gap = level
        # gap down at bar i
        elif high[i] < low[i - 1]:
            gap_top = float(low[i - 1])
            gap_bot = float(high[i])
            subsequent_high = float(win["High"].iloc[i + 1:].max()) if i + 1 < len(win) else current
            if subsequent_high < gap_top:  # unfilled
                level = gap_top
                dist_pct = abs(current - level) / current
                if dist_pct < nearest_dist:
                    nearest_dist = dist_pct
                    nearest_gap = level

    if nearest_gap is None or nearest_dist > within_pct:
        return none_result
    return {
        "has_gap": True,
        "gap_level": round(nearest_gap, 4),
        "distance_pct": round(nearest_dist * 100, 2),
    }


def compute_structure_high_reclaim(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    True if price has reclaimed the prior `lookback`-bar structure high within the last 5 bars.
    Structure high = the highest High in the bars BEFORE the last 5.
    Returns {reclaimed, level, bars_since_reclaim}.
    """
    nan = float("nan")
    if len(df) < lookback + 5:
        return {"reclaimed": False, "level": nan, "bars_since_reclaim": -1}
    pre_window = df.iloc[-(lookback + 5):-5]
    recent = df.iloc[-5:]
    structure_high = float(pre_window["High"].max())
    # Find first bar in the last 5 whose Close > structure_high
    for i, (_, row) in enumerate(recent.iterrows()):
        if float(row["Close"]) > structure_high:
            return {
                "reclaimed": True,
                "level": round(structure_high, 4),
                "bars_since_reclaim": len(recent) - 1 - i,
            }
    return {"reclaimed": False, "level": round(structure_high, 4), "bars_since_reclaim": -1}
