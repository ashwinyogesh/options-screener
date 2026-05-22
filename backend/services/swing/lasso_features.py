"""
Feature extraction for the v3 Lasso swing scorer.

Computes the 39-feature vector expected by `services.scoring.swing_lasso`
from the OHLC frame + SPY frame + VIX close series + the already-computed
indicators used by the v2.3 scorer. Strict no-look-ahead: only uses bars
up to and including the most recent close.

Mirrors scripts/augment_swing_ledger.py — keep both in sync if the
feature set changes.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# ---- price-series helpers -------------------------------------------------


def _safe_float(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    val = (100 - 100 / (1 + rs)).iloc[-1]
    return _safe_float(val)


def _macd_hist(close: pd.Series) -> float | None:
    if len(close) < 35:
        return None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return _safe_float((macd - signal).iloc[-1])


def _atr14(df: pd.DataFrame) -> float | None:
    if len(df) < 15:
        return None
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    val = tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1]
    return _safe_float(val)


def _obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["Close"].diff()).fillna(0)
    return (direction * df["Volume"]).cumsum()


# ---- main extractor -------------------------------------------------------


def compute_lasso_features(
    df: pd.DataFrame,
    spy_df: pd.DataFrame,
    vix_close: pd.Series | None,
    *,
    # carried through from v2 pipeline (no recompute)
    rr_planned: float,
    setup_score: float,
    adx_value: float | None,
    ad_line_slope_pct: float | None,
    higher_lows: int | None,
    institutional_ownership_pct: float | None,
    extended: bool,
    setup_label: str,
    regime_label: str,
) -> dict[str, float]:
    """Return the full 39-feature dict the v3 Lasso model expects.

    `df` and `spy_df` are full-history OHLC frames; the function uses only
    bars up to and including the last index. `vix_close` is a daily VIX
    Close series (^VIX). Pass `None` to skip VIX features (defaults to NaN
    → the lasso scorer will treat them as mean-imputed).
    """
    if df.empty:
        return {}
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]
    px = _safe_float(close.iloc[-1])
    if px is None or px <= 0:
        return {}

    n = len(df)
    sma20 = close.rolling(20).mean().iloc[-1] if n >= 20 else float("nan")
    sma50 = close.rolling(50).mean().iloc[-1] if n >= 50 else float("nan")
    sma200 = close.rolling(200).mean().iloc[-1] if n >= 200 else float("nan")
    std20 = close.rolling(20).std().iloc[-1] if n >= 20 else float("nan")

    high_52w = high.iloc[-252:].max() if n >= 252 else high.max()
    low_52w = low.iloc[-252:].min() if n >= 252 else low.min()

    rsi14 = _rsi(close) or float("nan")
    macd_hist = _macd_hist(close) or float("nan")
    atr_val = _atr14(df) or float("nan")
    atr_pct = (atr_val / px * 100.0) if not math.isnan(atr_val) else float("nan")

    log_ret = np.log(close / close.shift(1))
    vol20 = float("nan")
    if n >= 21:
        v = log_ret.rolling(20).std().iloc[-1]
        if pd.notna(v):
            vol20 = float(v * math.sqrt(252) * 100.0)

    bb_pos = float("nan")
    if not math.isnan(sma20) and not math.isnan(std20) and std20:
        bb_pos = float((px - sma20) / (2 * std20))

    obv = _obv(df)
    obv_slope_20 = float("nan")
    if len(obv) > 21 and obv.iloc[-21] != 0:
        obv_slope_20 = float((obv.iloc[-1] - obv.iloc[-21]) / abs(obv.iloc[-21]) * 100.0)

    vol_surge_20 = float("nan")
    if len(vol) > 21 and vol.iloc[-21:-1].mean() > 0:
        vol_surge_20 = float(vol.iloc[-1] / vol.iloc[-21:-1].mean())

    # Base structure (prior 20 bars excluding today)
    win20 = df.iloc[-21:-1]
    base_depth = float("nan")
    base_length = float("nan")
    if len(win20):
        base_max = float(win20["High"].max())
        if base_max:
            base_depth = (base_max - px) / base_max * 100.0
        # days since the lowest bar of the prior 20
        idx_low = win20["Low"].idxmin()
        try:
            today_ord = pd.Timestamp(df.index[-1]).toordinal()
            low_ord = pd.Timestamp(idx_low).toordinal()
            base_length = float(today_ord - low_ord)
        except Exception:
            pass

    gap_up = 1 if n >= 2 and float(df["Open"].iloc[-1]) > float(close.iloc[-2]) else 0
    inside_bar = 0
    nr7 = 0
    if n >= 2:
        prev_h = float(high.iloc[-2])
        prev_l = float(low.iloc[-2])
        if float(high.iloc[-1]) < prev_h and float(low.iloc[-1]) > prev_l:
            inside_bar = 1
    if n >= 7:
        cur_range = float(high.iloc[-1] - low.iloc[-1])
        prior_min_range = float((high.iloc[-7:-1] - low.iloc[-7:-1]).min())
        if cur_range < prior_min_range:
            nr7 = 1

    def _ret(period: int) -> float:
        if n <= period:
            return float("nan")
        return float((close.iloc[-1] / close.iloc[-1 - period] - 1) * 100.0)

    ret_1m, ret_3m, ret_6m = _ret(21), _ret(63), _ret(126)

    # Market context — SPY
    spy_slope_50 = spy_ret_5d = rs_vs_spy_3m = float("nan")
    if spy_df is not None and not spy_df.empty:
        spy_close = spy_df["Close"]
        if len(spy_close) >= 51:
            spy_slope_50 = float((spy_close.iloc[-1] / spy_close.iloc[-51] - 1) * 100.0 / 50.0)
            spy_ret_5d = float((spy_close.iloc[-1] / spy_close.iloc[-6] - 1) * 100.0)
        if len(spy_close) >= 64 and n >= 64:
            sym_r = (close.iloc[-1] / close.iloc[-64] - 1) * 100.0
            spy_r = (spy_close.iloc[-1] / spy_close.iloc[-64] - 1) * 100.0
            rs_vs_spy_3m = float(sym_r - spy_r)

    # VIX context
    vix_level = vix_vs_med20 = float("nan")
    if vix_close is not None and len(vix_close) >= 21:
        vix_level = float(vix_close.iloc[-1])
        med = float(vix_close.iloc[-21:-1].median())
        if med > 0:
            vix_vs_med20 = (vix_level / med - 1) * 100.0

    # Distances (handle NaN-safe)
    def _dist(v: float, ref: float) -> float:
        if math.isnan(ref) or ref == 0 or math.isnan(v):
            return float("nan")
        return float((v - ref) / ref * 100.0)

    dist_sma20 = _dist(px, float(sma20)) if not pd.isna(sma20) else float("nan")
    dist_sma50 = _dist(px, float(sma50)) if not pd.isna(sma50) else float("nan")
    dist_sma200 = _dist(px, float(sma200)) if not pd.isna(sma200) else float("nan")
    pct_off_52w_high = (
        _dist(px, float(high_52w)) if not pd.isna(high_52w) and high_52w else float("nan")
    )
    pct_above_52w_low = (
        _dist(px, float(low_52w)) if not pd.isna(low_52w) and low_52w else float("nan")
    )

    feats: dict[str, float] = {
        # ── carry-throughs from v2 pipeline ────────────────────────────
        "rr_planned": float(rr_planned),
        "setup_score": float(setup_score),
        "adx_value": float(adx_value) if adx_value is not None else float("nan"),
        "ad_line_slope_pct": float(ad_line_slope_pct)
        if ad_line_slope_pct is not None
        else float("nan"),
        "higher_lows": float(higher_lows) if higher_lows is not None else 0.0,
        "institutional_ownership_pct": float(institutional_ownership_pct)
        if institutional_ownership_pct is not None
        else float("nan"),
        "extended": 1.0 if extended else 0.0,
        # ── augmented from OHLC ───────────────────────────────────────
        "rsi14": float(rsi14),
        "macd_hist": float(macd_hist),
        "atr_pct": float(atr_pct),
        "vol20": float(vol20),
        "bb_pos": float(bb_pos),
        "dist_sma20": float(dist_sma20),
        "dist_sma50": float(dist_sma50),
        "dist_sma200": float(dist_sma200),
        "pct_off_52w_high": float(pct_off_52w_high),
        "pct_above_52w_low": float(pct_above_52w_low),
        "ret_1m": float(ret_1m),
        "ret_3m": float(ret_3m),
        "ret_6m": float(ret_6m),
        "vol_surge_20": float(vol_surge_20),
        "obv_slope_20": float(obv_slope_20),
        "base_depth": float(base_depth),
        "base_length": float(base_length),
        "gap_up": float(gap_up),
        "inside_bar": float(inside_bar),
        "nr7": float(nr7),
        # ── market context ────────────────────────────────────────────
        "spy_slope_50": float(spy_slope_50),
        "spy_ret_5d": float(spy_ret_5d),
        "vix_level": float(vix_level),
        "vix_vs_med20": float(vix_vs_med20),
        "rs_vs_spy_3m": float(rs_vs_spy_3m),
        "log_price": math.log(px),
        # ── one-hot encodings ─────────────────────────────────────────
        "setup_breakout": 1.0 if setup_label == "breakout" else 0.0,
        "setup_momentum": 1.0 if setup_label == "momentum" else 0.0,
        "setup_reversion": 1.0 if setup_label == "reversion" else 0.0,
        # `setup_retest` was excluded by Lasso (perfectly collinear with the others
        # under drop_first=False) — model has no coefficient for it.
        "regime_label_neutral": 1.0 if regime_label == "neutral" else 0.0,
        "regime_label_risk_off": 1.0 if regime_label == "risk_off" else 0.0,
        "regime_label_risk_on": 1.0 if regime_label == "risk_on" else 0.0,
    }
    return feats


__all__ = ["compute_lasso_features"]
