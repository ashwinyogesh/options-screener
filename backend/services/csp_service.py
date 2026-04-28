"""
Orchestrates per-symbol CSP analysis:
  OHLC → Technicals → Options → Strike → Premium → Delta → Returns
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from services.data_service import get_ohlc
from services.greeks_service import black_scholes_put_delta
from services.indicators import (
    compute_bollinger,
    compute_iv_rank_percentile,
    compute_price_vs_52w_high,
    compute_rsi,
    compute_sma_ratio,
    compute_trend_data,
    compute_volume_support,
)
from services.options_service import (
    get_all_expirations_data,
    get_bid_ask_spread_pct,
    get_implied_volatility,
)
from services.scoring.env import compute_env_score
from services.scoring.strike import (
    compute_csp_final_score,
    compute_csp_strike_score,
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


@dataclass
class CspStrikeResult:
    strike: float
    delta: float
    premium: float
    annualized_return: float
    bid_ask_spread_pct: Optional[float]
    env_score: float
    strike_score: float
    csp_score: float            # final = 0.4×env + 0.6×strike
    env_detail: str = ""
    strike_detail: str = ""
    is_best: bool = False
    iv_fallback: bool = False   # True when hv_sigma was used instead of yfinance IV
    stale_premium: bool = False # True when lastPrice was used instead of (bid+ask)/2
    iv_hv_ratio: Optional[float] = None   # sig / hv_sigma for this strike (None when HV fallback used)
    dist_pct: Optional[float] = None      # % gap from strike to nearest support below
    em_buffer_pct: Optional[float] = None # how far strike is outside 1σ move, as % of 1σ
    otm_pct: float = 0.0                  # % below current price
    lq_count: int = 0                     # OI or volume used for LQ score
    roc_annualized: Optional[float] = None  # annualized return on capital % for ROC scoring
    iv_stale: bool = False                # True when IV was NaN/zero (IV/HV pts forced to 0)


@dataclass
class CspResult:
    symbol: str
    price: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    sma_ratio: float           # SMA50 / SMA200  (>1 = bullish)
    rsi: float                 # RSI(14)
    iv_rank: Optional[float]   # (HV_today - HV_min) / (HV_max - HV_min) * 100
    iv_percentile: Optional[float]  # % of days HV < today's HV
    earnings_date: Optional[str]
    earnings_within_dte: bool
    vol_support_126_1: Optional[float]
    vol_support_126_2: Optional[float]
    vol_support_126_3: Optional[float]
    dte: int
    expiration: str
    strikes: list[CspStrikeResult] = field(default_factory=list)
    best_csp_score: float = 0.0
    using_hv_fallback: bool = False  # True when any strike in this row used hv_sigma
    expected_move: float = 0.0       # price × hv_sigma × √(dte/365)
    dist_from_52w_high_pct: float = 0.0  # 0 = at 52w high, -10 = 10% below
    chain_median_oi: float = 0.0     # median OI in 0.10–0.40 delta range


@dataclass
class CspError:
    symbol: str
    reason: str


def process_symbol(
    symbol: str,
    min_dte: int = 30,
    max_dte: int = 60,
    rf_rate: float = 0.045,
) -> tuple[list[CspResult], Optional[CspError]]:
    """
    CSP per-symbol entry point. Now a thin wrapper around the unified
    `services.screener.runner.run` driven by `CSP_CONFIG`. Behaviour is
    preserved bit-for-bit relative to the legacy implementation
    (kept as `_legacy_process_symbol` for one-commit revert).
    """
    rows, err = _run(
        symbol,
        CSP_CONFIG,
        min_dte=min_dte,
        max_dte=max_dte,
        rf_rate=rf_rate,
    )
    if err is not None:
        return [], CspError(symbol=err.symbol, reason=err.reason)
    return rows, None


def _legacy_process_symbol(
    symbol: str,
    min_dte: int = 30,
    max_dte: int = 60,
    rf_rate: float = 0.045,
) -> tuple[list[CspResult], Optional[CspError]]:
    """
    Processes a single symbol across all valid expirations in [min_dte, max_dte].
    Returns (list_of_results, None) on success or ([], error) on failure.

    Legacy implementation; preserved for one-commit revert. The live entry
    point is `process_symbol`, which delegates to the unified runner.
    """
    sym = symbol.strip().upper()
    try:
        # 1. Price history
        df = get_ohlc(sym, period="2y")
        current_price = float(df["Close"].iloc[-1])

        # 2. Technical indicators (computed once, shared across expirations)
        bb = compute_bollinger(df)
        sma_ratio = compute_sma_ratio(df)
        trend = compute_trend_data(df)
        rsi = compute_rsi(df)
        dist_52w = compute_price_vs_52w_high(df)
        iv_rank_raw, iv_pct_raw = compute_iv_rank_percentile(df)
        iv_rank: Optional[float] = None if math.isnan(iv_rank_raw) else iv_rank_raw
        iv_percentile: Optional[float] = None if math.isnan(iv_pct_raw) else iv_pct_raw
        vol_supports_126 = compute_volume_support(df, lookback=126)

        # Pre-compute HV sigma fallback once
        from datetime import datetime as _dt

        import numpy as np
        import pytz as _pytz
        log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()
        hv_sigma = float(log_ret.iloc[-30:].std(ddof=1) * np.sqrt(252)) if len(log_ret) >= 30 else 0.25

        # Detect if US market is currently open
        try:
            _et = _pytz.timezone("America/New_York")
            _now = _dt.now(_et)
            _weekday = _now.weekday()
            _market_open = (
                _weekday < 5
                and _now.hour * 60 + _now.minute >= 9 * 60 + 30
                and _now.hour * 60 + _now.minute < 16 * 60
            )
        except Exception:
            _market_open = False

        # 3. All expirations in range
        all_exps = get_all_expirations_data(sym, min_dte, max_dte)

        results: list[CspResult] = []
        for opts in all_exps:
            try:
                dte = opts["dte"]
                puts_df = opts["puts_df"]
                earnings_date = opts["earnings_date"]
                expiration = opts["expiration"]

                # Earnings-within-DTE flag
                earnings_within_dte = False
                if earnings_date:
                    try:
                        ed = date.fromisoformat(earnings_date)
                        today = date.today()
                        days_to_earnings = (ed - today).days
                        if 0 <= days_to_earnings <= dte:
                            earnings_within_dte = True
                    except ValueError:
                        pass

                # 4. Compute metrics for all OTM liquid strikes
                T = dte / 365.0
                _IDEAL_DELTA = -0.225  # midpoint of -0.10 to -0.35 target

                all_strikes_sorted = sorted(puts_df["strike"].unique(), reverse=True)  # ATM-first
                otm_strikes = [s for s in all_strikes_sorted if s < current_price * 1.02]

                # Pre-compute (strike, delta, premium, iv_fallback, stale_prem) for every OTM liquid strike
                candidates: list[tuple[float, float, float, bool, bool]] = []
                # Separate OI-only pass so chain_median_oi is never empty due to missing premiums
                _delta_range_ois_all: list[int] = []
                for sp in otm_strikes:
                    try:
                        row = puts_df[puts_df["strike"] == sp]
                        if row.empty:
                            continue
                        bid = float(row["bid"].iloc[0]) if not __import__('pandas').isna(row["bid"].iloc[0]) else 0.0
                        ask = float(row["ask"].iloc[0]) if not __import__('pandas').isna(row["ask"].iloc[0]) else 0.0
                        last = float(row["lastPrice"].iloc[0]) if not __import__('pandas').isna(row["lastPrice"].iloc[0]) else 0.0
                        _market_closed_row = (bid == 0.0 and ask == 0.0)
                        oi_val = int(row["openInterest"].iloc[0]) if not __import__('pandas').isna(row["openInterest"].iloc[0]) else 0
                        vol_val = int(row["volume"].iloc[0]) if not __import__('pandas').isna(row["volume"].iloc[0]) else 0
                        sig_raw = get_implied_volatility(puts_df, sp)
                        # Stale-IV: NaN or essentially zero → flag and force IV/HV to 0 pts
                        iv_stale_row = math.isnan(sig_raw) or sig_raw <= 0.01
                        used_hv = False
                        iv_hv_ratio_val: Optional[float] = None
                        if iv_stale_row:
                            sig = hv_sigma   # still need a sigma for delta / EM math
                            used_hv = True
                        else:
                            sig = sig_raw
                            if hv_sigma > 0:
                                iv_hv_ratio_val = round(sig / hv_sigma, 4)
                        d = black_scholes_put_delta(current_price, sp, rf_rate, T, sig)
                        # Always collect OI for chain_median_oi regardless of premium availability
                        if 0.1 < abs(d) < 0.4:
                            _delta_range_ois_all.append(oi_val)
                        if bid > 0 and ask > 0:
                            prem = round((bid + ask) / 2.0, 4)
                            stale_prem = False
                        elif last > 0:
                            prem = round(last, 4)
                            stale_prem = True
                        else:
                            continue  # no usable premium — skip for trading candidates only
                        candidates.append((sp, d, prem, used_hv, stale_prem, iv_hv_ratio_val, sig, oi_val, vol_val, iv_stale_row))
                    except Exception:
                        continue

                # Chain median OI: use the all-strikes pass (independent of premium availability)
                chain_median_oi = float(np.median(_delta_range_ois_all)) if _delta_range_ois_all else 0.0

                # Primary filter: -0.35 to -0.10 delta
                in_range = [c for c in candidates if -0.35 <= c[1] <= -0.10]

                # Fallback: if nothing in range, take up to 5 strikes nearest to ideal delta
                if not in_range and candidates:
                    in_range = sorted(candidates, key=lambda x: abs(x[1] - _IDEAL_DELTA))[:5]

                strike_results: list[CspStrikeResult] = []
                for sp, d, prem, used_hv, stale_prem, iv_hv_ratio_val, sig_used, oi_val, vol_val, iv_stale_row in in_range:
                    try:
                        spread_raw = get_bid_ask_spread_pct(puts_df, sp)
                        spread_s: Optional[float] = None if math.isnan(spread_raw) else spread_raw
                        collateral_s = round(sp * 100.0, 2)
                        ret_s = round((prem * 100) / collateral_s * 100.0, 4) if collateral_s > 0 else 0.0
                        ann_ret_s = round(ret_s * (365.0 / dte), 4) if dte > 0 else 0.0

                        # Env score uses iv_hv_ratio from the best-available sig for this strike
                        env_s_strike, env_detail = compute_env_score(
                            iv_rank=iv_rank,
                            iv_hv_ratio=iv_hv_ratio_val,
                            price_above_sma50=trend["price_above_sma50"],
                            sma50_above_sma200=trend["sma50_above_sma200"],
                            dist_from_52w_high_pct=dist_52w,
                            rsi=rsi,
                            chain_median_oi=chain_median_oi,
                            earnings_within_dte=earnings_within_dte,
                            direction='csp',
                            dte=dte,
                            iv_stale=iv_stale_row,
                        )
                        strike_s, strike_detail, strike_raw = compute_csp_strike_score(
                            delta=d,
                            current_price=current_price,
                            strike=sp,
                            iv_used=sig_used,
                            dte=dte,
                            vol_support_1=vol_supports_126[0] if len(vol_supports_126) > 0 else None,
                            vol_support_2=vol_supports_126[1] if len(vol_supports_126) > 1 else None,
                            vol_support_3=vol_supports_126[2] if len(vol_supports_126) > 2 else None,
                            bid_ask_spread_pct=spread_s,
                            open_interest=oi_val,
                            market_open=_market_open,
                            volume=vol_val,
                            credit=prem,
                        )
                        final_s = compute_csp_final_score(env_s_strike, strike_s)
                        strike_results.append(CspStrikeResult(
                            strike=sp,
                            delta=d,
                            premium=round(prem, 4),
                            annualized_return=ann_ret_s,
                            bid_ask_spread_pct=spread_s,
                            env_score=env_s_strike,
                            strike_score=strike_s,
                            csp_score=final_s,
                            env_detail=env_detail,
                            strike_detail=strike_detail,
                            iv_hv_ratio=iv_hv_ratio_val,
                            iv_fallback=used_hv,
                            stale_premium=stale_prem,
                            dist_pct=strike_raw.get('dist_pct'),
                            em_buffer_pct=None if __import__('math').isnan(strike_raw.get('em_buffer_pct', float('nan'))) else strike_raw.get('em_buffer_pct'),
                            otm_pct=strike_raw.get('otm_pct', 0.0),
                            lq_count=int(strike_raw.get('lq_count', 0)),
                            roc_annualized=None if __import__('math').isnan(strike_raw.get('roc_annualized', float('nan'))) else strike_raw.get('roc_annualized'),
                            iv_stale=iv_stale_row,
                        ))
                    except Exception:
                        continue

                if not strike_results:
                    continue

                # Mark the highest-scoring strike as best; tie-break by ROC (higher = better)
                best_idx = max(range(len(strike_results)), key=lambda i: (
                    strike_results[i].csp_score,
                    strike_results[i].roc_annualized or 0.0,
                ))
                strike_results[best_idx].is_best = True
                best_score_val = strike_results[best_idx].csp_score

                results.append(CspResult(
                    symbol=sym,
                    price=round(current_price, 4),
                    bb_upper=bb["bb_upper"],
                    bb_middle=bb["bb_middle"],
                    bb_lower=bb["bb_lower"],
                    sma_ratio=sma_ratio,
                    rsi=rsi,
                    iv_rank=iv_rank,
                    iv_percentile=iv_percentile,
                    earnings_date=earnings_date,
                    earnings_within_dte=earnings_within_dte,
                    vol_support_126_1=vol_supports_126[0] if len(vol_supports_126) > 0 else None,
                    vol_support_126_2=vol_supports_126[1] if len(vol_supports_126) > 1 else None,
                    vol_support_126_3=vol_supports_126[2] if len(vol_supports_126) > 2 else None,
                    dte=dte,
                    expiration=expiration,
                    strikes=strike_results,
                    best_csp_score=best_score_val,
                    using_hv_fallback=any(sr.iv_fallback for sr in strike_results),
                    expected_move=round(current_price * hv_sigma * math.sqrt(dte / 365.0), 2),
                    dist_from_52w_high_pct=round(dist_52w, 2),
                    chain_median_oi=chain_median_oi,
                ))
            except Exception as exc:
                logger.debug("Skipping expiration %s for %s: %s", opts.get("expiration"), sym, exc)
                continue

        if not results:
            return [], CspError(symbol=sym, reason="No valid expirations processed")
        return results, None

    except Exception as exc:
        logger.warning("Failed to process '%s': %s", sym, exc)
        return [], CspError(symbol=sym, reason=str(exc))


# ---------------------------------------------------------------------------
# Phase-3 unified-runner adapters
# ---------------------------------------------------------------------------
#
# `process_symbol` (above) now delegates to `services.screener.runner.run`
# driven by `CSP_CONFIG`. The legacy body is preserved as
# `_legacy_process_symbol` for one-commit revert; remove after CC + DITM
# migrations land in Phase 4.


def _csp_symbol_factory(_sym: str, df, current_price: float) -> tuple[Indicators, SymbolMetrics]:
    """Build the symbol-level Indicators bundle + render-only SymbolMetrics for
    CSP-shaped screeners.

    Computes BB, SMA-ratio, RSI, HV-rank, IV-percentile, dist-from-52w,
    vol-supports, and HV-sigma in one pass over the OHLC frame. Per-expiration
    fields (`dte`, `earnings_within_dte`, `chain_median_oi`, `iv_hv_ratio`,
    `iv_stale`) are layered onto Indicators by the runner via
    `dataclasses.replace`.
    """
    import numpy as _np2
    bb = compute_bollinger(df)
    sma_r = compute_sma_ratio(df)
    trend = compute_trend_data(df)
    rsi = compute_rsi(df)
    dist_52w = compute_price_vs_52w_high(df)
    iv_rank_raw, iv_pct_raw = compute_iv_rank_percentile(df)
    hv_rank = None if math.isnan(iv_rank_raw) else iv_rank_raw
    iv_pct = None if math.isnan(iv_pct_raw) else iv_pct_raw
    vol_sups = compute_volume_support(df, lookback=126)
    log_ret = _np2.log(df["Close"] / df["Close"].shift(1)).dropna()
    hv_sigma = float(log_ret.iloc[-30:].std(ddof=1) * _np2.sqrt(252)) if len(log_ret) >= 30 else 0.25

    indicators = Indicators(
        price=current_price,
        sma50=trend.get("sma50", 0.0),
        sma200=trend.get("sma200", 0.0),
        price_above_sma50=trend["price_above_sma50"],
        sma50_above_sma200=trend["sma50_above_sma200"],
        dist_from_52w_high_pct=dist_52w,
        chain_median_oi=0.0,        # filled per-expiration by runner
        earnings_within_dte=False,  # filled per-expiration by runner
        days_to_earnings=None,      # filled per-expiration
        dte=0,                      # filled per-expiration
        rsi=rsi,
        hv_rank=hv_rank,
        vol_support_1=vol_sups[0] if len(vol_sups) > 0 else None,
        vol_support_2=vol_sups[1] if len(vol_sups) > 1 else None,
        vol_support_3=vol_sups[2] if len(vol_sups) > 2 else None,
    )
    metrics = SymbolMetrics(
        bb_upper=bb["bb_upper"],
        bb_middle=bb["bb_middle"],
        bb_lower=bb["bb_lower"],
        sma_ratio=sma_r,
        hv_sigma=hv_sigma,
        iv_percentile=iv_pct,
    )
    return indicators, metrics


def _csp_strike_context_builder(
    inputs: StrikeBuildInputs,
    indicators: Indicators,
) -> StrikeContext:
    """Assemble the per-strike StrikeContext for CSP. Reads vol-supports
    from `indicators` (computed once per symbol) and bid/ask spread from
    the chain DataFrame on `inputs`."""
    cand: Candidate = inputs.candidate
    spread_raw = get_bid_ask_spread_pct(inputs.chain_df, cand.strike)
    spread: Optional[float] = None if math.isnan(spread_raw) else spread_raw
    return StrikeContext(
        delta=cand.delta,
        strike=cand.strike,
        current_price=inputs.current_price,
        bid_ask_spread_pct=spread,
        open_interest=cand.open_interest,
        volume=cand.volume,
        market_open=inputs.market_open,
        iv_used=cand.iv_used,
        dte=indicators.dte,
        credit=cand.premium,
        vol_support_1=indicators.vol_support_1,
        vol_support_2=indicators.vol_support_2,
        vol_support_3=indicators.vol_support_3,
    )


def _csp_env_scorer(ind: Indicators) -> tuple[float, str]:
    """Adapter: Indicators bundle → legacy `compute_env_score` kwargs (CSP)."""
    return compute_env_score(
        iv_rank=ind.hv_rank,
        iv_hv_ratio=ind.iv_hv_ratio,
        price_above_sma50=ind.price_above_sma50,
        sma50_above_sma200=ind.sma50_above_sma200,
        dist_from_52w_high_pct=ind.dist_from_52w_high_pct,
        rsi=ind.rsi if ind.rsi is not None else 0.0,
        chain_median_oi=ind.chain_median_oi,
        earnings_within_dte=ind.earnings_within_dte,
        direction='csp',
        dte=ind.dte,
        iv_stale=ind.iv_stale,
    )


def _csp_strike_scorer_adapter(ctx: StrikeContext) -> tuple[float, str, dict]:
    """Adapter: StrikeContext → legacy `compute_csp_strike_score` kwargs."""
    return compute_csp_strike_score(
        delta=ctx.delta,
        current_price=ctx.current_price,
        strike=ctx.strike,
        iv_used=ctx.iv_used,
        dte=ctx.dte,
        vol_support_1=ctx.vol_support_1,
        vol_support_2=ctx.vol_support_2,
        vol_support_3=ctx.vol_support_3,
        bid_ask_spread_pct=ctx.bid_ask_spread_pct,
        open_interest=ctx.open_interest,
        market_open=ctx.market_open,
        volume=ctx.volume,
        credit=ctx.credit,
    )


def _csp_tie_break(bundle) -> tuple[float, ...]:
    """Tie-break by ROC annualised (higher is better)."""
    raw = bundle.strike_raw
    val = raw.get("roc_annualized")
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return (0.0,)
    return (float(val),)


def _csp_result_factory(
    ctx: ExpirationContext,
    bundles: list[StrikeBundle],
) -> CspResult:
    """Builds CspResult + CspStrikeResult list from runner bundle data.

    Mirrors the legacy result-construction block bit-for-bit so the
    characterization tests keep passing."""
    ind = ctx.indicators
    metrics = ctx.metrics

    strike_results: list[CspStrikeResult] = []
    for b in bundles:
        c = b.candidate
        # Annualised return (legacy formula): (premium*100 / collateral) * (365/dte) * 100
        collateral = round(c.strike * 100.0, 2)
        ret = round((c.premium * 100) / collateral * 100.0, 4) if collateral > 0 else 0.0
        ann_ret = round(ret * (365.0 / ctx.dte), 4) if ctx.dte > 0 else 0.0

        em_buf = b.strike_raw.get("em_buffer_pct", float("nan"))
        em_buf = None if (isinstance(em_buf, float) and math.isnan(em_buf)) else em_buf
        roc = b.strike_raw.get("roc_annualized", float("nan"))
        roc = None if (isinstance(roc, float) and math.isnan(roc)) else roc

        strike_results.append(CspStrikeResult(
            strike=c.strike,
            delta=c.delta,
            premium=round(c.premium, 4),
            annualized_return=ann_ret,
            bid_ask_spread_pct=b.bid_ask_spread_pct,
            env_score=b.env_score,
            strike_score=b.strike_score,
            csp_score=b.final_score,
            env_detail=b.env_detail,
            strike_detail=b.strike_detail,
            is_best=b.is_best,
            iv_fallback=c.iv_fallback,
            stale_premium=c.stale_premium,
            iv_hv_ratio=c.iv_hv_ratio,
            dist_pct=b.strike_raw.get("dist_pct"),
            em_buffer_pct=em_buf,
            otm_pct=b.strike_raw.get("otm_pct", 0.0),
            lq_count=int(b.strike_raw.get("lq_count", 0)),
            roc_annualized=roc,
            iv_stale=c.iv_stale,
        ))

    best_score = max((s.csp_score for s in strike_results), default=0.0)
    return CspResult(
        symbol=ctx.symbol,
        price=round(ctx.current_price, 4),
        bb_upper=metrics.bb_upper or 0.0,
        bb_middle=metrics.bb_middle or 0.0,
        bb_lower=metrics.bb_lower or 0.0,
        sma_ratio=metrics.sma_ratio or 0.0,
        rsi=ind.rsi if ind.rsi is not None else 0.0,
        iv_rank=ind.hv_rank,
        iv_percentile=metrics.iv_percentile,
        earnings_date=ctx.earnings_date,
        earnings_within_dte=ctx.earnings_within_dte,
        vol_support_126_1=ind.vol_support_1,
        vol_support_126_2=ind.vol_support_2,
        vol_support_126_3=ind.vol_support_3,
        dte=ctx.dte,
        expiration=ctx.expiration,
        strikes=strike_results,
        best_csp_score=best_score,
        using_hv_fallback=any(sr.iv_fallback for sr in strike_results),
        expected_move=round(ctx.current_price * (metrics.hv_sigma or 0.0) * math.sqrt(ctx.dte / 365.0), 2),
        dist_from_52w_high_pct=round(ind.dist_from_52w_high_pct, 2),
        chain_median_oi=ctx.chain_median_oi,
    )


CSP_CONFIG = ScreenerConfig(
    name="csp",
    direction="short_put",
    chain_fetcher=lambda s, lo, hi: get_all_expirations_data(s, lo, hi),
    delta_fn=black_scholes_put_delta,
    ohlc_fetcher=lambda s, **kw: get_ohlc(s, **kw),
    iv_lookup=lambda chain_df, strike: get_implied_volatility(chain_df, strike),
    strike_filter=lambda price, strike: strike < price * 1.02,
    delta_range=(-0.35, -0.10),
    ideal_delta=-0.225,
    strike_sort="desc",
    oi_delta_band=(-0.40, -0.10),
    symbol_factory=_csp_symbol_factory,
    strike_context_builder=_csp_strike_context_builder,
    env_scorer=_csp_env_scorer,
    strike_scorer=_csp_strike_scorer_adapter,
    final_blend=(0.4, 0.6),
    tie_break_key=_csp_tie_break,
    result_factory=_csp_result_factory,
)


__all__ = [
    "CspError",
    "CspResult",
    "CspStrikeResult",
    "CSP_CONFIG",
    "process_symbol",
]
