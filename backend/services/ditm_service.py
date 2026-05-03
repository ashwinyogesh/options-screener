"""
Orchestrates per-symbol DITM (Deep-In-The-Money) Long Call analysis:
  OHLC → Technicals (ENV) → Options → Strike → Greeks → Scoring

Final score = 0.5 × ENV_raw + 0.5 × Strike_raw (both 0–100 scale).

v3 lean model (ADR-0008):
  ENV (5 factors, 100): Trend 25 + 200d Return 25 + 52W (flipped) 20
                        + Weekly RSI 15 + Chain Liquidity 15
  Strike (5, 100):      Δ 20 + Leverage 25 + Extrinsic% 25
                        + Bid-Ask 20 + IV Percentile 10
  Earnings penalty:     DTE-scaled −15 ENV pts (≤7d), −7 (8–14d).
  Macro hold:           0.85× multiplier on final_score (was display-only).
  Hard gates removed:   HV Rank > 50 (eliminated half the universe by
                        construction), Trend < 22 (collapsed to a soft
                        25-pt factor), Earnings ≤ 7d (now DTE-scaled).
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
from services.indicators import (
    compute_iv_rank_percentile,
    compute_price_vs_52w_high,
    compute_trend_data,
)
from services.options_service import (
    get_all_expirations_calls_data,
    get_implied_volatility,
)
from services.screener import (
    Indicators,
    ScreenerConfig,
    StrikeBuildInputs,
    StrikeContext,
    SymbolMetrics,
)
from services.screener.runner import (
    Candidate,
    ExpirationContext,
    StrikeBundle,
)
from services.screener.runner import (
    run as _run,
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
    iv_percentile: Optional[float] = None  # 0–100, HV-based; v3 strike-side single vol-cheapness factor


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
# ENV scoring (v3)
# ---------------------------------------------------------------------------

def _score_trend_strength(
    price_above_sma50: bool,
    sma50_above_sma200: bool,
    price: float,
    sma200: float,
) -> float:
    """v3: 25 pts. Soft factor (no longer a hard gate). Full alignment is
    still the strongest tier, but partial alignment now earns proportional
    pts instead of zeroing the whole ENV."""
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
    """v3: 15 pts. Sweet spot 50–65; 35–40 in a strong uptrend earns
    pullback-entry credit."""
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
    """v3.2: 20 pts. Tent curve — rewards 3–12% below the 52W high.

    Avoids buying right at local exhaustion (0% = possible all-time-high
    reversal risk). Sweet spot is slightly off highs where momentum is
    confirmed but a local top has not been set.

    Curve: 0%→0% ramp to 3%; peak 20 at 3–12%; linear decay 12→30%; 0 beyond.
    """
    if math.isnan(dist_pct):
        return 10.0
    d = abs(min(dist_pct, 0.0))
    if d < 3:
        return _lerp(d, 0, 3, 12.0, 20.0)    # partial credit right at all-time high
    if d <= 12:
        return 20.0                           # sweet spot: 3–12% off highs
    if d <= 25:
        return _lerp(d, 12, 25, 20.0, 6.0)
    if d <= 40:
        return _lerp(d, 25, 40, 6.0, 0.0)
    return 0.0


def _score_200d_return(ret_frac: float) -> float:
    """v3.2: 15 pts (was 25). Compressed to reduce momentum-cluster dominance.

    Cap kept at 25% (no need to reward 80% vs 30% — both confirm a strong
    trend). Weight reduced so Trend Stability (R²) can be added as an
    independent orthogonal signal.
    """
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
    """v3.2 NEW: 10 pts. Trend Stability — R² of OLS price regression (50-day).

    Measures *smoothness* of the trend, not direction or magnitude.
    High R² → clean, consistent trend (DITM-friendly: delta-heavy position
    survives chop poorly). Low R² → choppy/range-bound (theta bleeds you).

    Thresholds: > 0.7 = clean trend; 0.4–0.7 = moderate; < 0.4 = choppy.
    """
    if math.isnan(r2):
        return 5.0   # neutral default when insufficient data
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
    """v3: 15 pts. Reference log10(500) (DITM chains thinner than ATM)."""
    if median_oi <= 0:
        return 0.0
    return min(math.log10(max(median_oi, 1)) / math.log10(500), 1.0) * 15.0


def _earnings_penalty_dte_scaled(days_to_earnings: Optional[int], dte: int) -> float:
    """v3: DTE-aware earnings penalty (audit finding #9).

    Returns a NEGATIVE value to subtract from ENV. A 7-day-out earnings on
    a 365-DTE LEAP is a small ding (~−1.2 ENV); on a 30-DTE position it is
    fatal (−15 ENV).
    """
    if days_to_earnings is None or days_to_earnings > 60:
        return 0.0
    scale = min(1.0, 30.0 / max(dte, 1))
    if days_to_earnings <= 7:
        return -15.0 * scale
    if days_to_earnings <= 14:
        return -7.0 * scale
    return 0.0


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
    trend_r2: float = float("nan"),   # v3.2: R² of 50-day OLS price regression
) -> tuple[float, str]:
    """
    Returns (env_raw_score 0–100, detail_string).

    v3: no hard gates. HV Rank dropped (duplicate of strike-side IV
    Percentile). Trend gate dropped (soft 25-pt factor). Earnings ≤7d is
    a DTE-scaled penalty.

    v3.2: 200d Return 25→15 pts; Trend Stability (R²) added at 10 pts;
    52W Distance curve shifted to tent peaking at 3–12% off highs.
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


# ---------------------------------------------------------------------------
# Strike scoring (v3)
# ---------------------------------------------------------------------------

def _score_delta_ditm(delta: float) -> float:
    """v3.2: 20 pts. Sweet spot shifted to 0.82–0.90 (reduces gamma risk,
    more stock-like behaviour).

    Ramps up through 0.75–0.82, flat top 0.82–0.90, soft decay above 0.90.
    Below 0.70 = 0 (option is not deep enough in the money).
    """
    if delta < 0.70:
        return 0.0
    if delta <= 0.75:
        return _lerp(delta, 0.70, 0.75, 0.0, 12.0)
    if delta <= 0.82:
        return _lerp(delta, 0.75, 0.82, 12.0, 20.0)
    if delta <= 0.90:
        return 20.0                              # flat top — sweet zone
    if delta <= 0.95:
        return _lerp(delta, 0.90, 0.95, 20.0, 14.0)
    return _lerp(delta, 0.95, 1.00, 14.0, 9.0)


def _score_leverage(leverage: float) -> float:
    """v3.2: 25 pts. The headline DITM metric: delta × price / mid.

    v3.2 changes: flat top extended to 2.5–4.0×; sharper decay 4–5×;
    hard zero at ≥5× (leverage that high usually reflects a mispriced or
    wide-spread option rather than a genuinely advantaged setup).
    """
    if math.isnan(leverage) or leverage <= 0:
        return 0.0
    if leverage < 1.5:
        return _lerp(leverage, 0.0, 1.5, 0.0, 8.0)
    if leverage < 2.0:
        return _lerp(leverage, 1.5, 2.0, 8.0, 17.0)
    if leverage <= 2.5:
        return _lerp(leverage, 2.0, 2.5, 17.0, 25.0)
    if leverage <= 4.0:
        return 25.0                              # flat top expanded to 4×
    if leverage < 5.0:
        return _lerp(leverage, 4.0, 5.0, 25.0, 0.0)  # sharp decay
    return 0.0                                   # hard zero ≥5×


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
    """v3: 10 pts. Lower IV percentile = cheaper options for buyers.
    Single vol-cheapness factor (HV Rank ENV factor was duplicate)."""
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


def compute_ditm_strike_score(
    delta: float,
    strike: float,
    mid: float,
    current_price: float,
    extrinsic_pct_of_strike_frac: float,
    bid_ask_spread_pct: Optional[float],
    iv_percentile: Optional[float],
) -> tuple[float, str]:
    """v3: returns (strike_raw_score 0–100, detail_string).

    Drops Theta% (audit #4 — same signal as Extrinsic%) and Capital
    Efficiency (audit #1 — replaced by Leverage which absorbs its role
    while including delta).
    """
    delta_pts = _score_delta_ditm(delta)
    leverage = (delta * current_price / mid) if mid > 0 else 0.0
    lev_pts = _score_leverage(leverage)
    ext_pts = _score_extrinsic_pct(extrinsic_pct_of_strike_frac)
    iv_pts = _score_iv_percentile_ditm(iv_percentile)
    spread_pts = _score_spread_ditm(bid_ask_spread_pct)

    raw = delta_pts + lev_pts + ext_pts + spread_pts + iv_pts
    detail = (
        f"Δ:{delta_pts:.1f} Lev:{lev_pts:.1f} Ext:{ext_pts:.1f} "
        f"BA:{spread_pts:.1f} IV:{iv_pts:.1f}"
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
    DITM per-symbol entry point. Now a thin wrapper around the unified
    `services.screener.runner.run` driven by `DITM_CONFIG`. Behaviour is
    preserved bit-for-bit relative to the legacy implementation
    (kept as `_legacy_process_symbol` for one-commit revert).

    `macro_context` is render-only — applied on the resulting DitmResult
    rows here, NOT threaded through the runner. This keeps the runner
    signature uniform across screeners.
    """
    rows, err = _run(
        symbol,
        DITM_CONFIG,
        min_dte=min_dte,
        max_dte=max_dte,
        rf_rate=rf_rate,
    )
    if err is not None:
        return [], DitmError(symbol=err.symbol, reason=err.reason)
    if rows and macro_context is not None:
        # Match legacy subscript semantics: KeyError on a malformed
        # macro_context propagates and is caught by the caller (router).
        macro_hold = not macro_context["macro_pass"]
        for r in rows:
            r.macro_hold = macro_hold
            if macro_hold:
                # v3 audit finding #10: macro_hold demotes scores by 15%.
                # Was display-only in v2.
                for s in r.strikes:
                    s.ditm_score = round(s.ditm_score * 0.85, 1)
                r.best_ditm_score = round(r.best_ditm_score * 0.85, 1)
    return rows, None


def _legacy_process_symbol(
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

        # v3.2: R² of OLS price regression over last 50 days
        _prices_leg = df["Close"].dropna().values
        _prices_leg = _prices_leg[-50:] if len(_prices_leg) >= 50 else _prices_leg
        if len(_prices_leg) >= 10:
            _x_leg = np.arange(len(_prices_leg), dtype=float)
            _coeffs_leg = np.polyfit(_x_leg, _prices_leg, 1)
            _fitted_leg = np.polyval(_coeffs_leg, _x_leg)
            _ss_res_leg = float(np.sum((_prices_leg - _fitted_leg) ** 2))
            _ss_tot_leg = float(np.sum((_prices_leg - _prices_leg.mean()) ** 2))
            trend_r2_leg = float(1.0 - _ss_res_leg / _ss_tot_leg) if _ss_tot_leg > 1e-9 else float("nan")
        else:
            trend_r2_leg = float("nan")

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
                            weekly_rsi=w_rsi,
                            dist_from_52w_high_pct=dist_52w if not math.isnan(dist_52w) else 0.0,
                            ret_200d_frac=ret_200d_frac if not math.isnan(ret_200d_frac) else 0.0,
                            days_to_earnings=days_to_earnings,
                            chain_median_oi=chain_median_oi,
                            dte=dte,
                            trend_r2=trend_r2_leg,
                        )

                        # Strike score
                        strike_s, strike_detail = compute_ditm_strike_score(
                            delta=d,
                            strike=sp,
                            mid=mid_price,
                            current_price=current_price,
                            extrinsic_pct_of_strike_frac=ext_pct_frac,
                            bid_ask_spread_pct=spread_pct,
                            iv_percentile=iv_percentile,
                        )

                        # v3 macro-hold demotion (audit #10)
                        macro_hold_legacy = not macro_context["macro_pass"] if macro_context else False
                        macro_mult = 0.85 if macro_hold_legacy else 1.0
                        final_s = round((0.5 * env_s + 0.5 * strike_s) * macro_mult, 1)

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
                    iv_percentile=iv_percentile,
                ))
            except Exception as exc:
                logger.debug("Expiry processing error for %s: %s", sym, exc)
                continue

        return results, None

    except Exception as exc:
        logger.warning("Failed to process symbol %s: %s", sym, exc)
        return [], DitmError(symbol=sym, reason=str(exc))


# ---------------------------------------------------------------------------
# Phase 4: ScreenerConfig adapters
# ---------------------------------------------------------------------------


def _ditm_symbol_factory(_sym: str, df, current_price: float) -> tuple[Indicators, SymbolMetrics]:
    """Build symbol-level Indicators + render-only SymbolMetrics for DITM.

    Computes trend (sma50/sma200/flags), HV-rank, IV-percentile,
    dist-from-52w, weekly RSI, 200d return, hv_sigma (annualised), and
    the 3-day overnight gap.
    """
    trend = compute_trend_data(df)
    dist_52w = compute_price_vs_52w_high(df)
    iv_rank_raw, iv_pct_raw = compute_iv_rank_percentile(df)
    hv_rank: Optional[float] = None if math.isnan(iv_rank_raw) else iv_rank_raw
    iv_percentile: Optional[float] = None if math.isnan(iv_pct_raw) else iv_pct_raw

    close = df["Close"]
    log_ret = np.log(close / close.shift(1)).dropna()
    hv_sigma = float(log_ret.iloc[-30:].std(ddof=1) * np.sqrt(252)) if len(log_ret) >= 30 else 0.25

    w_rsi = _compute_weekly_rsi(df)
    ret_200d_frac = _compute_200d_return(df)
    gap_3d = _compute_gap_3d(df)

    # v3.2: R² of OLS price regression over last 50 days (trend smoothness)
    _prices = df["Close"].dropna().values
    _prices = _prices[-50:] if len(_prices) >= 50 else _prices
    if len(_prices) >= 10:
        _x = np.arange(len(_prices), dtype=float)
        _coeffs = np.polyfit(_x, _prices, 1)
        _fitted = np.polyval(_coeffs, _x)
        _ss_res = float(np.sum((_prices - _fitted) ** 2))
        _ss_tot = float(np.sum((_prices - _prices.mean()) ** 2))
        _trend_r2 = float(1.0 - _ss_res / _ss_tot) if _ss_tot > 1e-9 else float("nan")
    else:
        _trend_r2 = float("nan")

    sma50 = trend.get("sma50") or 0.0
    sma200 = trend.get("sma200") or 0.0

    indicators = Indicators(
        price=current_price,
        sma50=float(sma50),
        sma200=float(sma200) if not math.isnan(float(sma200)) else 0.0,
        price_above_sma50=trend["price_above_sma50"],
        sma50_above_sma200=trend["sma50_above_sma200"],
        dist_from_52w_high_pct=dist_52w if not math.isnan(dist_52w) else 0.0,
        chain_median_oi=0.0,        # filled per-expiration
        earnings_within_dte=False,  # filled per-expiration
        days_to_earnings=None,      # filled per-expiration
        dte=0,                      # filled per-expiration
        hv_rank=hv_rank,
        weekly_rsi=None if math.isnan(w_rsi) else w_rsi,
        ret_200d_frac=None if math.isnan(ret_200d_frac) else ret_200d_frac,
        trend_r2=None if math.isnan(_trend_r2) else _trend_r2,
    )
    metrics = SymbolMetrics(
        sma_ratio=(
            round(float(trend["sma_ratio"]), 4)
            if trend.get("sma_ratio") is not None and not math.isnan(float(trend["sma_ratio"]))
            else None
        ),
        hv_sigma=hv_sigma,
        iv_percentile=iv_percentile,
        gap_3d_pct=gap_3d,
    )
    return indicators, metrics


def _ditm_strike_context_builder(
    inputs: StrikeBuildInputs,
    indicators: Indicators,
) -> StrikeContext:
    """Assemble the per-strike StrikeContext for DITM. Computes intrinsic /
    extrinsic / theta inline (so the strike scorer receives precomputed
    fields). Spread is strict: bid>0 AND ask>0, no last-price fallback
    (matches legacy DITM)."""
    cand: Candidate = inputs.candidate
    current_price = inputs.current_price
    sp = cand.strike

    # Mid is `cand.premium` (already (bid+ask)/2 when bid&ask>0, else last).
    mid_price = cand.premium if cand.premium is not None else 0.0

    # Strict spread: only when bid>0 AND ask>0
    spread: Optional[float] = None
    if cand.bid > 0 and cand.ask > 0:
        m = (cand.bid + cand.ask) / 2.0
        if m > 0:
            spread = round((cand.ask - cand.bid) / m * 100, 2)

    # Intrinsic / extrinsic
    intrinsic = max(current_price - sp, 0.0)
    extrinsic = max(mid_price - intrinsic, 0.0)
    ext_pct_frac = extrinsic / sp if sp > 0 else 0.0

    # Annualised theta as % of strike
    theta_annual = _bs_call_theta(current_price, sp, inputs.rf_rate, inputs.T, cand.iv_used)
    theta_ann_pct = abs(theta_annual) / sp * 100 if sp > 0 else 0.0

    return StrikeContext(
        delta=cand.delta,
        strike=sp,
        current_price=current_price,
        bid_ask_spread_pct=spread,
        open_interest=cand.open_interest,
        volume=cand.volume,
        market_open=inputs.market_open,
        iv_used=cand.iv_used,
        dte=indicators.dte,
        mid=mid_price,
        extrinsic_pct_of_strike_frac=ext_pct_frac,
        theta_annualized_pct=theta_ann_pct,
        iv_percentile=inputs.metrics.iv_percentile,
    )


def _ditm_env_scorer(ind: Indicators) -> tuple[float, str]:
    """Adapter: Indicators → v3 `compute_ditm_env_score`. No hard gates in
    v3 (audit findings #2/#9). Earnings is a DTE-scaled penalty applied
    inside the function."""
    return compute_ditm_env_score(
        price_above_sma50=ind.price_above_sma50,
        sma50_above_sma200=ind.sma50_above_sma200,
        price=ind.price,
        sma200=ind.sma200,
        weekly_rsi=ind.weekly_rsi if ind.weekly_rsi is not None else float("nan"),
        dist_from_52w_high_pct=ind.dist_from_52w_high_pct,
        ret_200d_frac=ind.ret_200d_frac if ind.ret_200d_frac is not None else 0.0,
        days_to_earnings=ind.days_to_earnings,
        chain_median_oi=ind.chain_median_oi,
        dte=ind.dte,
        trend_r2=ind.trend_r2 if ind.trend_r2 is not None else float("nan"),
    )


def _ditm_strike_scorer_adapter(ctx: StrikeContext) -> tuple[float, str, dict]:
    """Adapter: StrikeContext → v3 `compute_ditm_strike_score` kwargs.
    Theta% is no longer scored (audit #4) but `theta_annualized_pct` is
    still kept in `raw` for the result_factory and frontend display."""
    score, detail = compute_ditm_strike_score(
        delta=ctx.delta,
        strike=ctx.strike,
        mid=ctx.mid or 0.0,
        current_price=ctx.current_price,
        extrinsic_pct_of_strike_frac=ctx.extrinsic_pct_of_strike_frac or 0.0,
        bid_ask_spread_pct=ctx.bid_ask_spread_pct,
        iv_percentile=ctx.iv_percentile,
    )
    raw = {
        "extrinsic_pct": round((ctx.extrinsic_pct_of_strike_frac or 0.0) * 100, 2),
        "theta_annualized_pct": round(ctx.theta_annualized_pct or 0.0, 2),
        "mid": ctx.mid or 0.0,
    }
    return score, detail, raw


def _ditm_tie_break(bundle: StrikeBundle) -> tuple[float, ...]:
    """Tie-break: closer to ideal delta (0.82) wins, then lower extrinsic %.
    Mirrors legacy `(score, -|delta-0.82|, -extrinsic_pct)`."""
    delta_proximity = -abs(bundle.candidate.delta - 0.82)
    extrinsic_neg = -float(bundle.strike_raw.get("extrinsic_pct", 0.0))
    return (delta_proximity, extrinsic_neg)


def _ditm_result_factory(
    ctx: ExpirationContext,
    bundles: list[StrikeBundle],
) -> DitmResult:
    """Build DitmResult + DitmStrikeResult list from runner bundle data.
    `macro_hold` is left False here; the wrapper sets it from the
    caller-supplied `macro_context` after the runner returns."""
    ind = ctx.indicators
    metrics = ctx.metrics

    strike_results: list[DitmStrikeResult] = []
    for b in bundles:
        c = b.candidate
        mid_price = c.premium if c.premium is not None else 0.0
        intrinsic = max(ctx.current_price - c.strike, 0.0)
        extrinsic = max(mid_price - intrinsic, 0.0)
        ext_pct_frac = extrinsic / c.strike if c.strike > 0 else 0.0
        be_pct = ((c.strike + mid_price - ctx.current_price) / ctx.current_price * 100
                  if ctx.current_price > 0 else 0.0)
        cap_eff_pct = mid_price / ctx.current_price * 100 if ctx.current_price > 0 else 0.0
        theta_ann_pct = float(b.strike_raw.get("theta_annualized_pct", 0.0))

        strike_results.append(DitmStrikeResult(
            strike=c.strike,
            delta=round(c.delta, 4),
            mid=round(mid_price, 4),
            extrinsic_pct=round(ext_pct_frac * 100, 2),
            theta_annualized_pct=round(theta_ann_pct, 2),
            breakeven_pct=round(be_pct, 2),
            capital_efficiency_pct=round(cap_eff_pct, 2),
            bid_ask_spread_pct=b.bid_ask_spread_pct,
            chain_oi=c.open_interest,
            env_score=b.env_score,
            strike_score=b.strike_score,
            ditm_score=b.final_score,
            env_detail=b.env_detail,
            strike_detail=b.strike_detail,
            is_best=b.is_best,
            iv_fallback=c.iv_fallback,
        ))

    best_score = max((s.ditm_score for s in strike_results), default=0.0)

    hv30_pct = round((metrics.hv_sigma or 0.0) * 100, 2)
    return DitmResult(
        symbol=ctx.symbol,
        price=round(ctx.current_price, 4),
        sma_ratio=metrics.sma_ratio if metrics.sma_ratio is not None else 0.0,
        hv_rank=ind.hv_rank if ind.hv_rank is not None else 0.0,
        hv30=hv30_pct,
        weekly_rsi=ind.weekly_rsi if ind.weekly_rsi is not None else 0.0,
        ret_200d=round((ind.ret_200d_frac * 100) if ind.ret_200d_frac is not None else 0.0, 2),
        dist_from_52w_high_pct=ind.dist_from_52w_high_pct,
        earnings_date=ctx.earnings_date,
        days_to_earnings=ind.days_to_earnings,
        earnings_within_dte=ctx.earnings_within_dte,
        dte=ctx.dte,
        expiration=ctx.expiration,
        strikes=strike_results,
        best_ditm_score=best_score,
        gap_3d_pct=metrics.gap_3d_pct or 0.0,
        macro_hold=False,
        chain_median_oi=ctx.chain_median_oi,
        iv_percentile=metrics.iv_percentile,
    )


# DITM-specific symbol-level prep: handled inline in _ditm_symbol_factory.


DITM_CONFIG = ScreenerConfig(
    name="ditm",
    direction="long_call",
    chain_fetcher=lambda s, lo, hi: get_all_expirations_calls_data(s, lo, hi),
    delta_fn=black_scholes_call_delta,
    ohlc_fetcher=lambda s, **kw: get_ohlc(s, **kw),
    iv_lookup=lambda chain_df, strike: get_implied_volatility(chain_df, strike),
    strike_filter=lambda price, strike: strike < price,
    delta_range=(0.70, 0.90),
    ideal_delta=0.82,
    oi_delta_band=(0.60, 0.95),
    oi_delta_band_inclusive=True,
    symbol_factory=_ditm_symbol_factory,
    strike_context_builder=_ditm_strike_context_builder,
    env_scorer=_ditm_env_scorer,
    strike_scorer=_ditm_strike_scorer_adapter,
    final_blend=(0.5, 0.5),
    strike_sort="desc",
    candidate_delta_predicate=lambda d: d >= 0.60,
    tie_break_key=_ditm_tie_break,
    result_factory=_ditm_result_factory,
)
