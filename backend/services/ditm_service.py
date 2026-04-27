"""
Orchestrates per-symbol DITM (Deep-In-The-Money) Long Call analysis:
  OHLC → Technicals (ENV) → Options → Strike → Greeks → Scoring

Final score = 0.5 × ENV_raw + 0.5 × Strike_raw (both 0–100 scale).
Macro gate: sets macro_hold flag; does NOT zero the score.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np
from scipy.stats import norm  # type: ignore

from services.data_service import get_ohlc
from services.greeks_service import black_scholes_call_delta
from services.options_service import get_all_expirations_calls_data
from services.indicators import (
    compute_iv_rank_percentile,
    compute_price_vs_52w_high,
    compute_trend_data,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DitmStrikeResult:
    strike: float
    delta: float
    mid: float
    extrinsic_pct: float           # extrinsic / strike (%), lower = better
    theta_annualized_pct: float    # |BS theta_annual| / strike * 100 (%)
    breakeven_pct: float           # (strike + mid - price) / price * 100 (%)
    capital_efficiency_pct: float  # mid / price * 100 (%)
    bid_ask_spread_pct: Optional[float]
    chain_oi: int
    env_score: float
    strike_score: float
    ditm_score: float              # 0.5*env_score + 0.5*strike_score
    env_detail: str = ""
    strike_detail: str = ""
    is_best: bool = False
    iv_fallback: bool = False      # True when hv30 used instead of chain IV


@dataclass
class DitmResult:
    symbol: str
    price: float
    sma_ratio: float               # SMA50 / SMA200
    hv_rank: float                 # HV Rank 0–100
    hv30: float                    # 30-day HV as % (e.g. 28.5 = 28.5%)
    weekly_rsi: float              # Weekly RSI(14)
    ret_200d: float                # 200d median-anchored return as % (e.g. 18.5)
    dist_from_52w_high_pct: float  # % below 52W high (negative = below)
    earnings_date: Optional[str]
    days_to_earnings: Optional[int]
    earnings_within_dte: bool
    dte: int
    expiration: str
    strikes: list[DitmStrikeResult] = field(default_factory=list)
    best_ditm_score: float = 0.0
    gap_3d_pct: float = 0.0        # max overnight gap last 3 sessions (%)
    macro_hold: bool = False        # True when VIX ≥ 25 and rising, or SPY < SMA200
    chain_median_oi: float = 0.0


@dataclass
class DitmError:
    symbol: str
    reason: str


# ---------------------------------------------------------------------------
# Macro gate (computed once per scan, not per-symbol)
# ---------------------------------------------------------------------------

def get_macro_context() -> dict:
    """
    Fetches ^VIX and SPY to determine macro regime.
    Returns: { vix_level, vix_5d_change, spy_above_sma200, macro_pass }
    Falls back to permissive values on any error so symbols are not blocked.
    """
    try:
        import yfinance as yf
        vix_hist = yf.Ticker("^VIX").history(period="15d", auto_adjust=True)
        if vix_hist is not None and len(vix_hist) >= 6:
            vix_level = float(vix_hist["Close"].iloc[-1])
            vix_5d_change = float(vix_hist["Close"].iloc[-1] - vix_hist["Close"].iloc[-6])
        else:
            vix_level, vix_5d_change = 18.0, 0.0
    except Exception:
        vix_level, vix_5d_change = 18.0, 0.0

    try:
        spy_df = get_ohlc("SPY", period="2y")
        spy_price = float(spy_df["Close"].iloc[-1])
        spy_sma200 = float(spy_df["Close"].rolling(200).mean().iloc[-1])
        spy_above_sma200 = spy_price > spy_sma200
    except Exception:
        spy_above_sma200 = True

    macro_pass = (vix_level < 25 or vix_5d_change <= 0) and spy_above_sma200
    return {
        "vix_level": round(vix_level, 2),
        "vix_5d_change": round(vix_5d_change, 2),
        "spy_above_sma200": spy_above_sma200,
        "macro_pass": macro_pass,
    }


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
# ENV scoring
# ---------------------------------------------------------------------------

def _score_trend_strength(
    price_above_sma50: bool,
    sma50_above_sma200: bool,
    price: float,
    sma200: float,
) -> float:
    if price_above_sma50 and sma50_above_sma200:
        return 30.0
    if price_above_sma50:
        return 18.0
    if sma50_above_sma200:
        return 10.0
    # Neither — small bias if at least above SMA200
    if sma200 > 0 and price > sma200:
        return 5.0
    return 0.0


def _score_hv_rank_inv(hv_rank: Optional[float]) -> float:
    """Inverted: low HV rank = cheap vol = better for buyers."""
    if hv_rank is None or math.isnan(hv_rank):
        return 6.0
    r = hv_rank
    if r <= 20:
        return 12.0
    if r <= 40:
        return _lerp(r, 20, 40, 12.0, 8.0)
    if r <= 60:
        return _lerp(r, 40, 60, 8.0, 4.0)
    if r <= 80:
        return _lerp(r, 60, 80, 4.0, 1.0)
    return 0.0


def _score_weekly_rsi(w_rsi: float, trend_pts: float) -> float:
    if math.isnan(w_rsi):
        return 5.0
    if 50 <= w_rsi <= 65:
        return 10.0
    if (45 <= w_rsi < 50) or (65 < w_rsi <= 70):
        return 7.0
    if (40 <= w_rsi < 45) or (70 < w_rsi <= 75):
        return 4.0
    if 35 <= w_rsi < 40 and trend_pts >= 22:
        # Oversold in strong uptrend — pullback entry
        return 6.0
    return 0.0


def _score_52w_dist(dist_pct: float) -> float:
    """dist_pct is negative when below 52W high (e.g. -10 = 10% below)."""
    if math.isnan(dist_pct):
        return 6.0
    d = abs(min(dist_pct, 0.0))  # % below high, positive
    if d <= 3:
        return 7.0
    if d <= 10:
        return _lerp(d, 3, 10, 7.0, 12.0)
    if d <= 20:
        return _lerp(d, 10, 20, 12.0, 9.0)
    if d <= 30:
        return _lerp(d, 20, 30, 9.0, 4.0)
    return 0.0


def _score_200d_return(ret_frac: float) -> float:
    """ret_frac is fraction (0.15 = 15%)."""
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
        return _lerp(pct, 0, 5, 1.0, 6.0)
    return 0.0


def _score_earnings_env(days: Optional[int]) -> float:
    if days is None or days > 60:
        return 8.0
    if days <= 7:
        return 0.0  # hard gate trigger
    if days <= 14:
        return 3.0
    return 8.0


def _score_liquidity_ditm(median_oi: float) -> float:
    """Uses log10(500) as reference (DITM chains less liquid than ATM)."""
    if median_oi <= 0:
        return 0.0
    return min(math.log10(max(median_oi, 1)) / math.log10(500), 1.0) * 13.0


def compute_ditm_env_score(
    price_above_sma50: bool,
    sma50_above_sma200: bool,
    price: float,
    sma200: float,
    hv_rank: Optional[float],
    weekly_rsi: float,
    dist_from_52w_high_pct: float,
    ret_200d_frac: float,
    days_to_earnings: Optional[int],
    chain_median_oi: float,
) -> tuple[float, str]:
    """
    Returns (env_raw_score 0–100, detail_string).
    Hard gates set score to 0: trend not P>SMA50>SMA200, HV rank >50, earnings ≤7d.
    """
    trend_pts = _score_trend_strength(price_above_sma50, sma50_above_sma200, price, sma200)
    hv_pts = _score_hv_rank_inv(hv_rank)
    rsi_pts = _score_weekly_rsi(weekly_rsi, trend_pts)
    dist_pts = _score_52w_dist(dist_from_52w_high_pct)
    ret_pts = _score_200d_return(ret_200d_frac)
    earn_pts = _score_earnings_env(days_to_earnings)
    lq_pts = _score_liquidity_ditm(chain_median_oi)

    raw = trend_pts + hv_pts + rsi_pts + dist_pts + ret_pts + earn_pts + lq_pts

    # Hard gates
    hv_rank_val = hv_rank if hv_rank is not None and not math.isnan(hv_rank) else 0.0
    hard_gate = (
        trend_pts < 22
        or hv_rank_val > 50
        or (days_to_earnings is not None and days_to_earnings <= 7)
    )
    if hard_gate:
        raw = 0.0

    detail = (
        f"Tr:{trend_pts:.1f} HV:{hv_pts:.1f} WRSI:{rsi_pts:.1f} "
        f"52W:{dist_pts:.1f} R2:{ret_pts:.1f} Ea:{earn_pts:.1f} LQ:{lq_pts:.1f}"
    )
    return round(raw, 2), detail


# ---------------------------------------------------------------------------
# Strike scoring
# ---------------------------------------------------------------------------

def _score_delta_ditm(delta: float) -> float:
    """delta is positive call delta, e.g. 0.82. Sweet spot 0.80–0.85."""
    if delta < 0.70:
        return 0.0
    if delta <= 0.75:
        return _lerp(delta, 0.70, 0.75, 0.0, 13.0)
    if delta <= 0.80:
        return _lerp(delta, 0.75, 0.80, 13.0, 18.0)
    if delta <= 0.85:
        return _lerp(delta, 0.80, 0.85, 18.0, 22.0)
    if delta <= 0.90:
        return _lerp(delta, 0.85, 0.90, 22.0, 18.0)
    return _lerp(delta, 0.90, 0.98, 18.0, 13.0)


def _score_extrinsic_pct(pct_frac: float) -> float:
    """pct_frac = extrinsic / strike as fraction. Lower = better."""
    p = pct_frac * 100  # to %
    if p < 2:
        return 28.0
    if p <= 4:
        return _lerp(p, 2, 4, 28.0, 22.0)
    if p <= 6:
        return _lerp(p, 4, 6, 22.0, 16.0)
    if p <= 9:
        return _lerp(p, 6, 9, 16.0, 7.0)
    if p <= 12:
        return _lerp(p, 9, 12, 7.0, 0.0)
    return 0.0


def _score_theta_pct(pct: float) -> float:
    """pct = annualised theta as % of strike. Lower = better."""
    if pct < 5:
        return 17.0
    if pct <= 10:
        return _lerp(pct, 5, 10, 17.0, 12.0)
    if pct <= 15:
        return _lerp(pct, 10, 15, 12.0, 7.0)
    if pct <= 20:
        return _lerp(pct, 15, 20, 7.0, 2.0)
    return 0.0


def _score_iv_percentile_ditm(iv_pct: Optional[float]) -> float:
    """Lower IV percentile = cheaper options = better for buyers."""
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
    """spread_pct in %. Lower = better."""
    if spread_pct is None or math.isnan(spread_pct):
        return 5.0
    if spread_pct <= 2:
        return 18.0
    if spread_pct <= 4:
        return _lerp(spread_pct, 2, 4, 18.0, 13.0)
    if spread_pct <= 7:
        return _lerp(spread_pct, 4, 7, 13.0, 7.0)
    if spread_pct <= 12:
        return _lerp(spread_pct, 7, 12, 7.0, 1.0)
    return 0.0


def _score_capital_eff(cap_pct: float) -> float:
    """cap_pct = mid / price * 100. DITM sweet spot: 25–35%."""
    if cap_pct < 25 or cap_pct > 65:
        return 0.0
    if cap_pct <= 35:
        return 5.0
    if cap_pct <= 50:
        return _lerp(cap_pct, 35, 50, 5.0, 3.0)
    return _lerp(cap_pct, 50, 65, 3.0, 1.0)


def compute_ditm_strike_score(
    delta: float,
    strike: float,
    mid: float,
    current_price: float,
    theta_annualized_pct: float,
    extrinsic_pct_of_strike_frac: float,
    bid_ask_spread_pct: Optional[float],
    iv_percentile: Optional[float],
) -> tuple[float, str]:
    """Returns (strike_raw_score 0–100, detail_string)."""
    delta_pts = _score_delta_ditm(delta)
    ext_pts = _score_extrinsic_pct(extrinsic_pct_of_strike_frac)
    theta_pts = _score_theta_pct(theta_annualized_pct)
    iv_pts = _score_iv_percentile_ditm(iv_percentile)
    spread_pts = _score_spread_ditm(bid_ask_spread_pct)
    cap_pct = (mid / current_price * 100) if current_price > 0 else 0.0
    cap_pts = _score_capital_eff(cap_pct)

    raw = delta_pts + ext_pts + theta_pts + iv_pts + spread_pts + cap_pts
    detail = (
        f"Δ:{delta_pts:.1f} Ext:{ext_pts:.1f} Th:{theta_pts:.1f} "
        f"IV:{iv_pts:.1f} BA:{spread_pts:.1f} Cap:{cap_pts:.1f}"
    )
    return round(raw, 2), detail


# ---------------------------------------------------------------------------
# Technical helpers
# ---------------------------------------------------------------------------

def _compute_weekly_rsi(df, period: int = 14) -> float:
    """Resample daily closes to weekly and compute Wilder RSI(14)."""
    weekly = df["Close"].resample("W").last().dropna()
    if len(weekly) < period + 1:
        return float("nan")
    delta = weekly.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain.iloc[-1] / avg_loss.iloc[-1] if avg_loss.iloc[-1] != 0 else float("inf")
    return round(100.0 - 100.0 / (1.0 + rs), 2)


def _compute_200d_return(df) -> float:
    """Returns close_today / median(closes[-205:-200]) - 1 as fraction."""
    close = df["Close"].values
    if len(close) < 206:
        return float("nan")
    anchor_slice = close[-205:-200]
    if len(anchor_slice) == 0:
        return float("nan")
    anchor = float(np.median(anchor_slice))
    return float("nan") if anchor <= 0 else round(float(close[-1]) / anchor - 1.0, 4)


def _compute_gap_3d(df) -> float:
    """Max absolute overnight gap % over the last 3 trading sessions."""
    if len(df) < 4:
        return 0.0
    recent = df.iloc[-4:].reset_index(drop=True)
    gaps = []
    for i in range(1, len(recent)):
        prev_close = float(recent["Close"].iloc[i - 1])
        curr_open = float(recent["Open"].iloc[i])
        if prev_close > 0:
            gaps.append(abs(curr_open / prev_close - 1.0) * 100)
    return round(max(gaps) if gaps else 0.0, 2)


def _bs_call_theta(S: float, K: float, r: float, T: float, sigma: float) -> float:
    """Annualised Black-Scholes theta for a long call (negative = cost of carry)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        theta_annual = (
            -(S * float(norm.pdf(d1)) * sigma) / (2.0 * math.sqrt(T))
            - r * K * math.exp(-r * T) * float(norm.cdf(d2))
        )
        return theta_annual
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Per-symbol processor
# ---------------------------------------------------------------------------

def process_symbol(
    symbol: str,
    min_dte: int = 90,
    max_dte: int = 180,
    rf_rate: float = 0.045,
    macro_context: Optional[dict] = None,
) -> tuple[list[DitmResult], Optional[DitmError]]:
    """
    Processes a single symbol across all valid expirations in [min_dte, max_dte].
    Returns (list_of_results, None) on success or ([], error) on failure.
    """
    sym = symbol.strip().upper()
    try:
        # 1. Price history
        df = get_ohlc(sym, period="2y")
        current_price = float(df["Close"].iloc[-1])
        close = df["Close"]

        # 2. Technical indicators
        trend = compute_trend_data(df)
        dist_52w = compute_price_vs_52w_high(df)
        iv_rank_raw, iv_pct_raw = compute_iv_rank_percentile(df)
        hv_rank: Optional[float] = None if math.isnan(iv_rank_raw) else iv_rank_raw
        iv_percentile: Optional[float] = None if math.isnan(iv_pct_raw) else iv_pct_raw

        # SMA200 value for gate check
        sma200_val: float = (
            float(close.rolling(200).mean().iloc[-1])
            if len(close) >= 200
            else float("nan")
        )

        # 30-day HV fallback sigma
        log_ret = np.log(close / close.shift(1)).dropna()
        hv30_sigma = float(log_ret.iloc[-30:].std(ddof=1) * np.sqrt(252)) if len(log_ret) >= 30 else 0.25
        hv30_pct = round(hv30_sigma * 100, 2)

        # Weekly RSI and 200d return
        w_rsi = _compute_weekly_rsi(df)
        ret_200d_frac = _compute_200d_return(df)
        ret_200d_pct = round((ret_200d_frac * 100) if not math.isnan(ret_200d_frac) else 0.0, 2)

        # Overnight gap
        gap_3d = _compute_gap_3d(df)

        # 3. Options chain for all expirations in range
        all_exps = get_all_expirations_calls_data(sym, min_dte, max_dte)

        results: list[DitmResult] = []
        for opts in all_exps:
            try:
                dte = opts["dte"]
                calls_df = opts["calls_df"]
                earnings_date = opts["earnings_date"]
                expiration = opts["expiration"]

                # Days-to-earnings
                days_to_earnings: Optional[int] = None
                earnings_within_dte = False
                if earnings_date:
                    try:
                        ed = date.fromisoformat(earnings_date)
                        days_to_earnings = (ed - date.today()).days
                        if 0 <= days_to_earnings <= dte:
                            earnings_within_dte = True
                    except ValueError:
                        pass

                T = dte / 365.0

                # ITM call strikes (strike < current_price)
                itm_strikes = sorted(
                    [s for s in calls_df["strike"].unique() if s < current_price],
                    reverse=True,  # nearest ITM first
                )

                # Pre-pass: collect OI for chain_median_oi and build candidate list
                _delta_ois: list[int] = []
                candidates: list[tuple] = []

                import pandas as _pd

                for sp in itm_strikes:
                    try:
                        row = calls_df[calls_df["strike"] == sp]
                        if row.empty:
                            continue

                        bid = float(row["bid"].iloc[0]) if not _pd.isna(row["bid"].iloc[0]) else 0.0
                        ask = float(row["ask"].iloc[0]) if not _pd.isna(row["ask"].iloc[0]) else 0.0
                        last = float(row["lastPrice"].iloc[0]) if not _pd.isna(row["lastPrice"].iloc[0]) else 0.0
                        oi_val = int(row["openInterest"].iloc[0]) if "openInterest" in row.columns and not _pd.isna(row["openInterest"].iloc[0]) else 0

                        # IV from chain; fall back to HV30
                        iv_raw = float(row["impliedVolatility"].iloc[0]) if "impliedVolatility" in row.columns and not _pd.isna(row["impliedVolatility"].iloc[0]) else float("nan")
                        iv_stale = math.isnan(iv_raw) or iv_raw <= 0.01
                        sig = hv30_sigma if iv_stale else iv_raw
                        used_hv = iv_stale

                        d = black_scholes_call_delta(current_price, sp, rf_rate, T, sig)

                        # Collect for chain_median_oi
                        if 0.60 <= d <= 0.95:
                            _delta_ois.append(oi_val)

                        # Need a usable premium
                        if bid > 0 and ask > 0:
                            mid_price = round((bid + ask) / 2.0, 4)
                        elif last > 0:
                            mid_price = round(last, 4)
                        else:
                            continue

                        if d >= 0.60:
                            candidates.append((sp, d, mid_price, sig, used_hv, oi_val, bid, ask))
                    except Exception:
                        continue

                chain_median_oi = float(np.median(_delta_ois)) if _delta_ois else 0.0

                # Primary: delta 0.70–0.90
                in_range = [c for c in candidates if 0.70 <= c[1] <= 0.90]
                # Fallback: up to 5 nearest to 0.82
                if not in_range and candidates:
                    in_range = sorted(candidates, key=lambda x: abs(x[1] - 0.82))[:5]

                strike_results: list[DitmStrikeResult] = []
                for sp, d, mid_price, sig_used, used_hv, oi_val, bid, ask in in_range:
                    try:
                        # Intrinsic / extrinsic
                        intrinsic = max(current_price - sp, 0.0)
                        extrinsic = max(mid_price - intrinsic, 0.0)
                        ext_pct_frac = extrinsic / sp if sp > 0 else 0.0

                        # Annualised theta (BS, annual)
                        theta_annual = _bs_call_theta(current_price, sp, rf_rate, T, sig_used)
                        theta_ann_pct = abs(theta_annual) / sp * 100 if sp > 0 else 0.0

                        # Capital efficiency and breakeven
                        cap_eff_pct = mid_price / current_price * 100 if current_price > 0 else 0.0
                        be_pct = (sp + mid_price - current_price) / current_price * 100 if current_price > 0 else 0.0

                        # Spread
                        spread_pct: Optional[float] = None
                        if bid > 0 and ask > 0 and mid_price > 0:
                            spread_pct = round((ask - bid) / mid_price * 100, 2)

                        # ENV score (shared per expiration)
                        env_s, env_detail = compute_ditm_env_score(
                            price_above_sma50=trend["price_above_sma50"],
                            sma50_above_sma200=trend["sma50_above_sma200"],
                            price=current_price,
                            sma200=sma200_val if not math.isnan(sma200_val) else 0.0,
                            hv_rank=hv_rank,
                            weekly_rsi=w_rsi,
                            dist_from_52w_high_pct=dist_52w if not math.isnan(dist_52w) else 0.0,
                            ret_200d_frac=ret_200d_frac if not math.isnan(ret_200d_frac) else 0.0,
                            days_to_earnings=days_to_earnings,
                            chain_median_oi=chain_median_oi,
                        )

                        # Strike score
                        strike_s, strike_detail = compute_ditm_strike_score(
                            delta=d,
                            strike=sp,
                            mid=mid_price,
                            current_price=current_price,
                            theta_annualized_pct=theta_ann_pct,
                            extrinsic_pct_of_strike_frac=ext_pct_frac,
                            bid_ask_spread_pct=spread_pct,
                            iv_percentile=iv_percentile,
                        )

                        final_s = round(0.5 * env_s + 0.5 * strike_s, 1)

                        strike_results.append(DitmStrikeResult(
                            strike=sp,
                            delta=round(d, 4),
                            mid=round(mid_price, 4),
                            extrinsic_pct=round(ext_pct_frac * 100, 2),
                            theta_annualized_pct=round(theta_ann_pct, 2),
                            breakeven_pct=round(be_pct, 2),
                            capital_efficiency_pct=round(cap_eff_pct, 2),
                            bid_ask_spread_pct=spread_pct,
                            chain_oi=oi_val,
                            env_score=env_s,
                            strike_score=strike_s,
                            ditm_score=final_s,
                            env_detail=env_detail,
                            strike_detail=strike_detail,
                            iv_fallback=used_hv,
                        ))
                    except Exception:
                        continue

                if not strike_results:
                    continue

                # Mark best; tie-break by delta proximity to ideal (0.82), then by extrinsic_pct (lower = better)
                best_idx = max(range(len(strike_results)), key=lambda i: (
                    strike_results[i].ditm_score,
                    -abs(strike_results[i].delta - 0.82),  # closer to ideal delta wins
                    -(strike_results[i].extrinsic_pct),    # lower time decay as final fallback
                ))
                strike_results[best_idx].is_best = True
                best_score_val = strike_results[best_idx].ditm_score

                macro_hold = not macro_context["macro_pass"] if macro_context else False

                _sma_ratio = trend["sma_ratio"]
                sma_ratio_val = (
                    round(float(_sma_ratio), 4)
                    if _sma_ratio is not None and not math.isnan(float(_sma_ratio))
                    else 0.0
                )

                results.append(DitmResult(
                    symbol=sym,
                    price=round(current_price, 4),
                    sma_ratio=sma_ratio_val,
                    hv_rank=hv_rank if hv_rank is not None else 0.0,
                    hv30=hv30_pct,
                    weekly_rsi=w_rsi if not math.isnan(w_rsi) else 0.0,
                    ret_200d=ret_200d_pct,
                    dist_from_52w_high_pct=dist_52w if not math.isnan(dist_52w) else 0.0,
                    earnings_date=earnings_date,
                    days_to_earnings=days_to_earnings,
                    earnings_within_dte=earnings_within_dte,
                    dte=dte,
                    expiration=expiration,
                    strikes=strike_results,
                    best_ditm_score=best_score_val,
                    gap_3d_pct=gap_3d,
                    macro_hold=macro_hold,
                    chain_median_oi=chain_median_oi,
                ))
            except Exception as exc:
                logger.debug("Expiry processing error for %s: %s", sym, exc)
                continue

        return results, None

    except Exception as exc:
        logger.warning("Failed to process symbol %s: %s", sym, exc)
        return [], DitmError(symbol=sym, reason=str(exc))
