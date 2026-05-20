"""
DITM environment + strike scorers — v3.2 lean model (see ADR-0008).

Both functions are pure (no I/O, no FastAPI, no yfinance).  They take
pre-computed indicator primitives and return (score_float, detail_str).

ENV (5 factors, 100 pts):
  Trend strength   25 pts  — soft SMA alignment (no hard gate)
  200d Return      15 pts  — momentum confirmation, compressed v3.2
  52W Distance     20 pts  — tent curve, sweet spot 3–12% off highs
  Trend Stability  10 pts  — R² of 50-day OLS price regression (v3.2)
  Weekly RSI       15 pts  — direction-aware, pullback-entry credit
  Chain Liquidity  15 pts  — log-scale median OI in 0.60–0.95 delta band
  Earnings penalty −15 pts — DTE-scaled, ≤7d full penalty, 8–14d half

Strike (5 factors, 100 pts):
  Δ position       20 pts  — sweet spot 0.82–0.90
  Leverage          25 pts  — delta × price / mid, flat top 2.5–4×
  Extrinsic %      25 pts  — extrinsic / strike, lower = better
  Bid-Ask spread   20 pts  — % of mid
  IV Percentile    10 pts  — lower = cheaper option for buyer
"""
from __future__ import annotations

import math
from typing import Optional

__all__ = ["compute_ditm_env_score", "compute_ditm_strike_score"]


# ---------------------------------------------------------------------------
# Linear interpolation helper
# ---------------------------------------------------------------------------

def _lerp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    t = max(0.0, min(1.0, t))
    return y0 + t * (y1 - y0)


# ---------------------------------------------------------------------------
# ENV scoring helpers
# ---------------------------------------------------------------------------

def _score_trend_strength(
    price_above_sma50: bool,
    sma50_above_sma200: bool,
    price: float,
    sma200: float,
) -> float:
    """v3: 25 pts. Soft factor — partial alignment earns proportional pts."""
    if price_above_sma50 and sma50_above_sma200:
        return 25.0
    if price_above_sma50:
        return 15.0
    if sma50_above_sma200:
        return 8.0
    if sma200 > 0 and price > sma200:
        return 4.0
    return 0.0


def _score_weekly_rsi(w_rsi: float, trend_pts: float) -> float:
    """v3: 15 pts. Sweet spot 50–65; 35–40 in a strong uptrend earns pullback credit."""
    if math.isnan(w_rsi):
        return 7.0
    if 50 <= w_rsi <= 65:
        return 15.0
    if (45 <= w_rsi < 50) or (65 < w_rsi <= 70):
        return 11.0
    if (40 <= w_rsi < 45) or (70 < w_rsi <= 75):
        return 6.0
    if 35 <= w_rsi < 40 and trend_pts >= 18:
        return 9.0
    return 0.0


def _score_52w_dist(dist_pct: float) -> float:
    """v3.2: 20 pts. Tent curve — sweet spot 3–12% below the 52W high."""
    if math.isnan(dist_pct):
        return 10.0
    d = abs(min(dist_pct, 0.0))
    if d < 3:
        return _lerp(d, 0, 3, 12.0, 20.0)
    if d <= 12:
        return 20.0
    if d <= 25:
        return _lerp(d, 12, 25, 20.0, 6.0)
    if d <= 40:
        return _lerp(d, 25, 40, 6.0, 0.0)
    return 0.0


def _score_200d_return(ret_frac: float) -> float:
    """v3.2: 15 pts (was 25). Cap at 25% return."""
    if math.isnan(ret_frac):
        return 5.0
    pct = ret_frac * 100
    if pct >= 25:
        return 15.0
    if pct >= 15:
        return _lerp(pct, 15, 25, 11.0, 15.0)
    if pct >= 5:
        return _lerp(pct, 5, 15, 6.0, 11.0)
    if pct >= 0:
        return _lerp(pct, 0, 5, 1.5, 6.0)
    return 0.0


def _score_trend_r2(r2: float) -> float:
    """v3.2 NEW: 10 pts. Smoothness of the 50-day OLS price regression."""
    if math.isnan(r2):
        return 5.0
    if r2 >= 0.85:
        return 10.0
    if r2 >= 0.70:
        return _lerp(r2, 0.70, 0.85, 7.5, 10.0)
    if r2 >= 0.50:
        return _lerp(r2, 0.50, 0.70, 4.0, 7.5)
    if r2 >= 0.30:
        return _lerp(r2, 0.30, 0.50, 1.0, 4.0)
    return 0.0


def _score_liquidity_ditm(median_oi: float) -> float:
    """v3: 15 pts. Log10 scale, reference point log10(500)."""
    if median_oi <= 0:
        return 0.0
    return min(math.log10(max(median_oi, 1)) / math.log10(500), 1.0) * 15.0


def _earnings_penalty_dte_scaled(days_to_earnings: Optional[int], dte: int) -> float:
    """v3: DTE-scaled earnings penalty (negative). ≤7d: −15×scale, 8–14d: −7×scale."""
    if days_to_earnings is None or days_to_earnings > 60:
        return 0.0
    scale = min(1.0, 30.0 / max(dte, 1))
    if days_to_earnings <= 7:
        return -15.0 * scale
    if days_to_earnings <= 14:
        return -7.0 * scale
    return 0.0


# ---------------------------------------------------------------------------
# Strike scoring helpers
# ---------------------------------------------------------------------------

def _score_delta_ditm(delta: float) -> float:
    """v3.2: 20 pts. Sweet spot 0.82–0.90."""
    if delta < 0.70:
        return 0.0
    if delta <= 0.75:
        return _lerp(delta, 0.70, 0.75, 0.0, 12.0)
    if delta <= 0.82:
        return _lerp(delta, 0.75, 0.82, 12.0, 20.0)
    if delta <= 0.90:
        return 20.0
    if delta <= 0.95:
        return _lerp(delta, 0.90, 0.95, 20.0, 14.0)
    return _lerp(delta, 0.95, 1.00, 14.0, 9.0)


def _score_leverage(leverage: float) -> float:
    """v3.2: 25 pts. Flat top 2.5–4×; hard zero ≥5×."""
    if math.isnan(leverage) or leverage <= 0:
        return 0.0
    if leverage < 1.5:
        return _lerp(leverage, 0.0, 1.5, 0.0, 8.0)
    if leverage < 2.0:
        return _lerp(leverage, 1.5, 2.0, 8.0, 17.0)
    if leverage <= 2.5:
        return _lerp(leverage, 2.0, 2.5, 17.0, 25.0)
    if leverage <= 4.0:
        return 25.0
    if leverage < 5.0:
        return _lerp(leverage, 4.0, 5.0, 25.0, 0.0)
    return 0.0


def _score_extrinsic_pct(pct_frac: float) -> float:
    """v3: 25 pts. pct_frac = extrinsic / strike as fraction. Lower = better."""
    p = pct_frac * 100
    if p < 2:
        return 25.0
    if p <= 4:
        return _lerp(p, 2, 4, 25.0, 19.0)
    if p <= 6:
        return _lerp(p, 4, 6, 19.0, 13.0)
    if p <= 9:
        return _lerp(p, 6, 9, 13.0, 5.0)
    if p <= 12:
        return _lerp(p, 9, 12, 5.0, 0.0)
    return 0.0


def _score_iv_percentile_ditm(iv_pct: Optional[float]) -> float:
    """v3: 10 pts. Lower IV percentile = cheaper options for buyers."""
    if iv_pct is None or math.isnan(iv_pct):
        return 5.0
    if iv_pct <= 25:
        return 10.0
    if iv_pct <= 50:
        return _lerp(iv_pct, 25, 50, 10.0, 7.0)
    if iv_pct <= 75:
        return _lerp(iv_pct, 50, 75, 7.0, 3.0)
    return 0.0


def _score_spread_ditm(spread_pct: Optional[float]) -> float:
    """v3: 20 pts. spread_pct in %. Lower = better."""
    if spread_pct is None or math.isnan(spread_pct):
        return 6.0
    if spread_pct <= 2:
        return 20.0
    if spread_pct <= 4:
        return _lerp(spread_pct, 2, 4, 20.0, 14.0)
    if spread_pct <= 7:
        return _lerp(spread_pct, 4, 7, 14.0, 7.0)
    if spread_pct <= 12:
        return _lerp(spread_pct, 7, 12, 7.0, 1.0)
    return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_ditm_env_score(
    price_above_sma50: bool,
    sma50_above_sma200: bool,
    price: float,
    sma200: float,
    weekly_rsi: float,
    dist_from_52w_high_pct: float,
    ret_200d_frac: float,
    days_to_earnings: Optional[int],
    chain_median_oi: float,
    dte: int,
    trend_r2: float = float("nan"),
) -> tuple[float, str]:
    """Returns (env_raw_score 0–100, detail_string).

    v3: no hard gates. v3.2: 200d Return 25→15 pts; Trend Stability (R²)
    added at 10 pts; 52W Distance tent curve.
    """
    trend_pts = _score_trend_strength(price_above_sma50, sma50_above_sma200, price, sma200)
    rsi_pts = _score_weekly_rsi(weekly_rsi, trend_pts)
    dist_pts = _score_52w_dist(dist_from_52w_high_pct)
    ret_pts = _score_200d_return(ret_200d_frac)
    r2_pts = _score_trend_r2(trend_r2)
    lq_pts = _score_liquidity_ditm(chain_median_oi)
    earn_pen = _earnings_penalty_dte_scaled(days_to_earnings, dte)

    raw = trend_pts + rsi_pts + dist_pts + ret_pts + r2_pts + lq_pts + earn_pen
    raw = max(0.0, min(100.0, raw))

    detail = (
        f"Tr:{trend_pts:.1f} Ret:{ret_pts:.1f} 52W:{dist_pts:.1f} "
        f"R2:{r2_pts:.1f} WRSI:{rsi_pts:.1f} LQ:{lq_pts:.1f} Earn:{earn_pen:.1f}"
    )
    return round(raw, 2), detail


def compute_ditm_strike_score(
    delta: float,
    strike: float,
    mid: float,
    current_price: float,
    extrinsic_pct_of_strike_frac: float,
    bid_ask_spread_pct: Optional[float],
    iv_percentile: Optional[float],
) -> tuple[float, str]:
    """Returns (strike_raw_score 0–100, detail_string).

    v3: drops Theta% (redundant with Extrinsic%) and Capital Efficiency
    (replaced by Leverage).
    """
    delta_pts = _score_delta_ditm(delta)
    leverage = (delta * current_price / mid) if mid > 0 else 0.0
    lev_pts = _score_leverage(leverage)
    ext_pts = _score_extrinsic_pct(extrinsic_pct_of_strike_frac)
    iv_pts = _score_iv_percentile_ditm(iv_percentile)
    spread_pts = _score_spread_ditm(bid_ask_spread_pct)

    raw = delta_pts + lev_pts + ext_pts + spread_pts + iv_pts
    detail = (
        f"\u0394:{delta_pts:.1f} Lev:{lev_pts:.1f} Ext:{ext_pts:.1f} "
        f"BA:{spread_pts:.1f} IV:{iv_pts:.1f}"
    )
    return round(raw, 2), detail
