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
from services.scoring.ditm import compute_ditm_env_score, compute_ditm_strike_score

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
    trend_r2: Optional[float] = None       # v3.2: R² of 50-day OLS price regression (trend smoothness)


@dataclass
class DitmError:
    symbol: str
    reason: str


# ---------------------------------------------------------------------------
# Macro gate (computed once per scan, not per-symbol)
# ---------------------------------------------------------------------------

_VIX_THRESHOLD = 25.0        # macro hold when VIX >= this and rising
_VIX_LOOKBACK_DAYS = 6       # bars needed for 5-day VIX change
_VIX_FALLBACK = (18.0, 0.0)  # (level, 5d_change) used on data error


def get_macro_context() -> dict:
    """
    Fetches ^VIX and SPY to determine macro regime.
    Returns: { vix_level, vix_5d_change, spy_above_sma200, macro_pass }
    Falls back to permissive values on any error so symbols are not blocked.
    """
    try:
        import yfinance as yf
        vix_hist = yf.Ticker("^VIX").history(period="15d", auto_adjust=True)
        if vix_hist is not None and len(vix_hist) >= _VIX_LOOKBACK_DAYS:
            vix_level = float(vix_hist["Close"].iloc[-1])
            vix_5d_change = float(vix_hist["Close"].iloc[-1] - vix_hist["Close"].iloc[-6])
        else:
            vix_level, vix_5d_change = _VIX_FALLBACK
    except Exception:
        vix_level, vix_5d_change = _VIX_FALLBACK

    try:
        spy_df = get_ohlc("SPY", period="2y")
        spy_price = float(spy_df["Close"].iloc[-1])
        spy_sma200 = float(spy_df["Close"].rolling(200).mean().iloc[-1])
        spy_above_sma200 = spy_price > spy_sma200
    except Exception:
        spy_above_sma200 = True

    macro_pass = (vix_level < _VIX_THRESHOLD or vix_5d_change <= 0) and spy_above_sma200
    return {
        "vix_level": round(vix_level, 2),
        "vix_5d_change": round(vix_5d_change, 2),
        "spy_above_sma200": spy_above_sma200,
        "macro_pass": macro_pass,
    }


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
    DITM per-symbol entry point. Thin wrapper around the unified
    `services.screener.runner.run` driven by `DITM_CONFIG`.

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
        trend_r2=round(ind.trend_r2, 4) if ind.trend_r2 is not None else None,
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
