"""
Orchestrates per-symbol CC (Covered Call) analysis:
  OHLC → Technicals → Call Options → Strike → Premium → Delta → Returns
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from services.data_service import get_ohlc
from services.greeks_service import black_scholes_call_delta
from services.indicators import (
    compute_bollinger,
    compute_iv_rank_percentile,
    compute_price_vs_52w_high,
    compute_rsi,
    compute_sma_ratio,
    compute_trend_data,
    compute_volume_resistance,
)
from services.options_service import (
    get_all_expirations_calls_data,
    get_bid_ask_spread_pct,
    get_implied_volatility,
)
from services.scoring.env import compute_env_score
from services.scoring.strike import (
    compute_cc_final_score,
    compute_cc_strike_score,
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
class CcStrikeResult:
    strike: float
    delta: float
    premium: float
    annualized_return: float
    bid_ask_spread_pct: Optional[float]
    env_score: float
    strike_score: float
    cc_score: float
    env_detail: str = ""
    strike_detail: str = ""
    is_best: bool = False
    iv_fallback: bool = False   # True when hv_sigma was used instead of yfinance IV
    stale_premium: bool = False # True when lastPrice was used instead of (bid+ask)/2
    iv_hv_ratio: Optional[float] = None   # sig / hv_sigma for this strike
    dist_pct: Optional[float] = None      # % gap from strike to nearest resistance above
    em_buffer_pct: Optional[float] = None # how far strike is outside 1σ move, as % of 1σ
    otm_pct: float = 0.0                  # % above current price
    lq_count: int = 0                     # OI or volume used for LQ score
    roc_annualized: Optional[float] = None  # annualized return on capital % for ROC scoring
    iv_stale: bool = False                # True when IV was NaN/zero (IV/HV pts forced to 0)


@dataclass
class CcResult:
    symbol: str
    price: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    sma_ratio: float
    rsi: float
    iv_rank: Optional[float]
    iv_percentile: Optional[float]
    earnings_date: Optional[str]
    earnings_within_dte: bool
    vol_resistance_126_1: Optional[float]
    vol_resistance_126_2: Optional[float]
    vol_resistance_126_3: Optional[float]
    dte: int
    expiration: str
    strikes: list[CcStrikeResult] = field(default_factory=list)
    best_cc_score: float = 0.0
    using_hv_fallback: bool = False
    expected_move: float = 0.0
    dist_from_52w_high_pct: float = 0.0  # 0 = at 52w high, -10 = 10% below
    chain_median_oi: float = 0.0     # median OI in 0.20–0.40 delta range (CC)


@dataclass
class CcError:
    symbol: str
    reason: str


def process_cc_symbol(
    symbol: str,
    min_dte: int = 30,
    max_dte: int = 60,
    rf_rate: float = 0.045,
) -> tuple[list[CcResult], Optional[CcError]]:
    """
    CC per-symbol entry point. Now a thin wrapper around the unified
    `services.screener.runner.run` driven by `CC_CONFIG`. Behaviour is
    preserved bit-for-bit relative to the legacy implementation
    (kept as `_legacy_process_cc_symbol` for one-commit revert).
    """
    rows, err = _run(
        symbol,
        CC_CONFIG,
        min_dte=min_dte,
        max_dte=max_dte,
        rf_rate=rf_rate,
    )
    if err is not None:
        return [], CcError(symbol=err.symbol, reason=err.reason)
    return rows, None


def _legacy_process_cc_symbol(
    symbol: str,
    min_dte: int = 30,
    max_dte: int = 60,
    rf_rate: float = 0.045,
) -> tuple[list[CcResult], Optional[CcError]]:
    """
    Processes a single symbol across all valid expirations in [min_dte, max_dte].
    Returns (list_of_results, None) on success or ([], error) on failure.
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
        vol_resistances_126 = compute_volume_resistance(df, lookback=126)

        # Pre-compute HV sigma fallback once
        from datetime import datetime as _dt

        import numpy as np
        import pytz as _pytz
        log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()
        hv_sigma = float(log_ret.iloc[-30:].std(ddof=1) * np.sqrt(252)) if len(log_ret) >= 30 else 0.25

        # SMA50 10-day % change (v3.1 slope factor)
        _sma50_legacy = df["Close"].rolling(50).mean().dropna()
        sma50_slope_pct = (
            (float(_sma50_legacy.iloc[-1]) / float(_sma50_legacy.iloc[-11]) - 1) * 100
            if len(_sma50_legacy) >= 11 else 0.0
        )

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

        # 3. All expirations in range (call chains)
        all_exps = get_all_expirations_calls_data(sym, min_dte, max_dte)

        results: list[CcResult] = []
        for opts in all_exps:
            try:
                dte = opts["dte"]
                calls_df = opts["calls_df"]
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

                # 4. Compute metrics for all OTM liquid call strikes
                T = dte / 365.0
                _IDEAL_DELTA = 0.225  # midpoint of +0.10 to +0.35 target

                # OTM calls: strikes ABOVE current price (slight tolerance for ATM)
                all_strikes_sorted = sorted(calls_df["strike"].unique())
                otm_strikes = [s for s in all_strikes_sorted if s > current_price * 0.98]

                candidates: list[tuple] = []
                # Separate OI-only pass so chain_median_oi is never empty due to missing premiums
                _delta_range_ois_all: list[int] = []
                for sp in otm_strikes:
                    try:
                        row = calls_df[calls_df["strike"] == sp]
                        if row.empty:
                            continue
                        bid = float(row["bid"].iloc[0]) if not __import__('pandas').isna(row["bid"].iloc[0]) else 0.0
                        ask = float(row["ask"].iloc[0]) if not __import__('pandas').isna(row["ask"].iloc[0]) else 0.0
                        last = float(row["lastPrice"].iloc[0]) if not __import__('pandas').isna(row["lastPrice"].iloc[0]) else 0.0
                        oi_val = int(row["openInterest"].iloc[0]) if not __import__('pandas').isna(row["openInterest"].iloc[0]) else 0
                        vol_val = int(row["volume"].iloc[0]) if not __import__('pandas').isna(row["volume"].iloc[0]) else 0
                        sig_raw = get_implied_volatility(calls_df, sp)
                        # Stale-IV: NaN or essentially zero → flag and force IV/HV to 0 pts
                        iv_stale_row = math.isnan(sig_raw) or sig_raw <= 0.01
                        used_hv = False
                        iv_hv_ratio_val: Optional[float] = None
                        if iv_stale_row:
                            sig = hv_sigma
                            used_hv = True
                        else:
                            sig = sig_raw
                            if hv_sigma > 0:
                                iv_hv_ratio_val = round(sig / hv_sigma, 4)
                        d = black_scholes_call_delta(current_price, sp, rf_rate, T, sig)
                        # Always collect OI for chain_median_oi regardless of premium availability
                        if 0.1 < d < 0.4:
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

                # Primary filter: +0.10 to +0.35 delta
                in_range = [c for c in candidates if 0.10 <= c[1] <= 0.35]

                # Fallback: if nothing in range, take up to 5 strikes nearest to ideal delta
                if not in_range and candidates:
                    in_range = sorted(candidates, key=lambda x: abs(x[1] - _IDEAL_DELTA))[:5]

                strike_results: list[CcStrikeResult] = []
                for sp, d, prem, used_hv, stale_prem, iv_hv_ratio_val, sig_used, oi_val, vol_val, iv_stale_row in in_range:
                    try:
                        spread_raw = get_bid_ask_spread_pct(calls_df, sp)
                        spread_s: Optional[float] = None if math.isnan(spread_raw) else spread_raw
                        # CC annualized return = premium yield against current stock price (per share)
                        ret_s = round((prem / current_price) * 100.0, 4) if current_price > 0 else 0.0
                        ann_ret_s = round(ret_s * (365.0 / dte), 4) if dte > 0 else 0.0

                        env_s, env_detail = compute_env_score(
                            iv_rank=iv_rank,
                            iv_hv_ratio=iv_hv_ratio_val,
                            price_above_sma50=trend["price_above_sma50"],
                            sma50_above_sma200=trend["sma50_above_sma200"],
                            dist_from_52w_high_pct=dist_52w,
                            rsi=rsi,
                            chain_median_oi=chain_median_oi,
                            earnings_within_dte=earnings_within_dte,
                            direction='cc',
                            dte=dte,
                            iv_stale=iv_stale_row,
                            sma_ratio=sma_ratio,
                            sma50_slope_pct=sma50_slope_pct,
                        )
                        strike_s, strike_detail, strike_raw = compute_cc_strike_score(
                            delta=d,
                            current_price=current_price,
                            strike=sp,
                            iv_used=sig_used,
                            dte=dte,
                            vol_resistance_1=vol_resistances_126[0] if len(vol_resistances_126) > 0 else None,
                            vol_resistance_2=vol_resistances_126[1] if len(vol_resistances_126) > 1 else None,
                            vol_resistance_3=vol_resistances_126[2] if len(vol_resistances_126) > 2 else None,
                            bid_ask_spread_pct=spread_s,
                            open_interest=oi_val,
                            market_open=_market_open,
                            volume=vol_val,
                            credit=prem,
                        )
                        final_s = compute_cc_final_score(env_s, strike_s)
                        strike_results.append(CcStrikeResult(
                            strike=sp,
                            delta=d,
                            premium=round(prem, 4),
                            annualized_return=ann_ret_s,
                            bid_ask_spread_pct=spread_s,
                            env_score=env_s,
                            strike_score=strike_s,
                            cc_score=final_s,
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

                # Tie-break by ROC (higher = better)
                best_idx = max(range(len(strike_results)), key=lambda i: (
                    strike_results[i].cc_score,
                    strike_results[i].roc_annualized or 0.0,
                ))
                strike_results[best_idx].is_best = True
                best_score_val = strike_results[best_idx].cc_score
                expected_move = round(current_price * hv_sigma * math.sqrt(dte / 365.0), 2)

                results.append(CcResult(
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
                    vol_resistance_126_1=vol_resistances_126[0] if len(vol_resistances_126) > 0 else None,
                    vol_resistance_126_2=vol_resistances_126[1] if len(vol_resistances_126) > 1 else None,
                    vol_resistance_126_3=vol_resistances_126[2] if len(vol_resistances_126) > 2 else None,
                    dte=dte,
                    expiration=expiration,
                    strikes=strike_results,
                    best_cc_score=best_score_val,
                    using_hv_fallback=any(s.iv_fallback for s in strike_results),
                    expected_move=expected_move,
                    dist_from_52w_high_pct=round(dist_52w, 2),
                    chain_median_oi=chain_median_oi,
                ))
            except Exception as e:
                logger.debug("Error processing expiration %s for %s: %s", opts.get("expiration", "?"), sym, e)
                continue

        if not results:
            return [], CcError(symbol=sym, reason="No valid CC strikes found in DTE range")

        return results, None

    except Exception as e:
        logger.warning("CC screener failed for %s: %s", sym, e)
        return [], CcError(symbol=sym, reason=str(e))


# ---------------------------------------------------------------------------
# Phase 4: ScreenerConfig adapters
# ---------------------------------------------------------------------------


def _cc_symbol_factory(_sym: str, df, current_price: float) -> tuple[Indicators, SymbolMetrics]:
    """Build symbol-level Indicators + render-only SymbolMetrics for CC.

    Same shape as CSP's factory, except it computes `vol_resistance_*`
    (not vol_supports) since CC scoring looks at resistance levels above
    the current price.
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
    vol_res = compute_volume_resistance(df, lookback=126)
    log_ret = _np2.log(df["Close"] / df["Close"].shift(1)).dropna()
    hv_sigma = float(log_ret.iloc[-30:].std(ddof=1) * _np2.sqrt(252)) if len(log_ret) >= 30 else 0.25

    # SMA50 10-day % change (v3.1 slope factor)
    _sma50_series = df["Close"].rolling(50).mean()
    _valid_sma = _sma50_series.dropna()
    sma50_slope_pct = (
        (float(_valid_sma.iloc[-1]) / float(_valid_sma.iloc[-11]) - 1) * 100
        if len(_valid_sma) >= 11 else 0.0
    )

    indicators = Indicators(
        price=current_price,
        sma50=trend.get("sma50", 0.0),
        sma200=trend.get("sma200", 0.0),
        price_above_sma50=trend["price_above_sma50"],
        sma50_above_sma200=trend["sma50_above_sma200"],
        dist_from_52w_high_pct=dist_52w,
        chain_median_oi=0.0,
        earnings_within_dte=False,
        days_to_earnings=None,
        dte=0,
        rsi=rsi,
        hv_rank=hv_rank,
        sma_ratio=sma_r,
        sma50_slope_pct=sma50_slope_pct,
        vol_resistance_1=vol_res[0] if len(vol_res) > 0 else None,
        vol_resistance_2=vol_res[1] if len(vol_res) > 1 else None,
        vol_resistance_3=vol_res[2] if len(vol_res) > 2 else None,
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


def _cc_strike_context_builder(
    inputs: StrikeBuildInputs,
    indicators: Indicators,
) -> StrikeContext:
    """Assemble the per-strike StrikeContext for CC. Reads vol-resistances
    from `indicators` and bid/ask spread from the chain DataFrame on
    `inputs`."""
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
        vol_resistance_1=indicators.vol_resistance_1,
        vol_resistance_2=indicators.vol_resistance_2,
        vol_resistance_3=indicators.vol_resistance_3,
    )


def _cc_env_scorer(ind: Indicators) -> tuple[float, str]:
    """Adapter: Indicators bundle → legacy `compute_env_score` kwargs (CC)."""
    return compute_env_score(
        iv_rank=ind.hv_rank,
        iv_hv_ratio=ind.iv_hv_ratio,
        price_above_sma50=ind.price_above_sma50,
        sma50_above_sma200=ind.sma50_above_sma200,
        dist_from_52w_high_pct=ind.dist_from_52w_high_pct,
        rsi=ind.rsi if ind.rsi is not None else 0.0,
        chain_median_oi=ind.chain_median_oi,
        earnings_within_dte=ind.earnings_within_dte,
        direction='cc',
        dte=ind.dte,
        iv_stale=ind.iv_stale,
        sma_ratio=ind.sma_ratio,
        sma50_slope_pct=ind.sma50_slope_pct,
    )


def _cc_strike_scorer_adapter(ctx: StrikeContext) -> tuple[float, str, dict]:
    """Adapter: StrikeContext → legacy `compute_cc_strike_score` kwargs."""
    return compute_cc_strike_score(
        delta=ctx.delta,
        current_price=ctx.current_price,
        strike=ctx.strike,
        iv_used=ctx.iv_used,
        dte=ctx.dte,
        vol_resistance_1=ctx.vol_resistance_1,
        vol_resistance_2=ctx.vol_resistance_2,
        vol_resistance_3=ctx.vol_resistance_3,
        bid_ask_spread_pct=ctx.bid_ask_spread_pct,
        open_interest=ctx.open_interest,
        market_open=ctx.market_open,
        volume=ctx.volume,
        credit=ctx.credit,
    )


def _cc_tie_break(bundle: StrikeBundle) -> tuple[float, ...]:
    """Tie-break by ROC-annualized (higher = better). Mirrors legacy."""
    roc = bundle.strike_raw.get("roc_annualized")
    if roc is None or (isinstance(roc, float) and math.isnan(roc)):
        return (0.0,)
    return (float(roc),)


def _cc_result_factory(
    ctx: ExpirationContext,
    bundles: list[StrikeBundle],
) -> CcResult:
    """Build CcResult + CcStrikeResult list from runner bundle data.

    Mirrors the legacy result-construction block bit-for-bit so the CC
    characterization tests keep passing.
    """
    ind = ctx.indicators
    metrics = ctx.metrics

    strike_results: list[CcStrikeResult] = []
    for b in bundles:
        c = b.candidate
        # CC annualized return: per-share premium yield against stock price
        ret = round((c.premium / ctx.current_price) * 100.0, 4) if ctx.current_price > 0 else 0.0
        ann_ret = round(ret * (365.0 / ctx.dte), 4) if ctx.dte > 0 else 0.0

        em_buf = b.strike_raw.get("em_buffer_pct", float("nan"))
        em_buf = None if (isinstance(em_buf, float) and math.isnan(em_buf)) else em_buf
        roc = b.strike_raw.get("roc_annualized", float("nan"))
        roc = None if (isinstance(roc, float) and math.isnan(roc)) else roc

        strike_results.append(CcStrikeResult(
            strike=c.strike,
            delta=c.delta,
            premium=round(c.premium, 4),
            annualized_return=ann_ret,
            bid_ask_spread_pct=b.bid_ask_spread_pct,
            env_score=b.env_score,
            strike_score=b.strike_score,
            cc_score=b.final_score,
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

    best_score = max((s.cc_score for s in strike_results), default=0.0)
    return CcResult(
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
        vol_resistance_126_1=ind.vol_resistance_1,
        vol_resistance_126_2=ind.vol_resistance_2,
        vol_resistance_126_3=ind.vol_resistance_3,
        dte=ctx.dte,
        expiration=ctx.expiration,
        strikes=strike_results,
        best_cc_score=best_score,
        using_hv_fallback=any(sr.iv_fallback for sr in strike_results),
        expected_move=round(ctx.current_price * (metrics.hv_sigma or 0.0) * math.sqrt(ctx.dte / 365.0), 2),
        dist_from_52w_high_pct=round(ind.dist_from_52w_high_pct, 2),
        chain_median_oi=ctx.chain_median_oi,
    )


CC_CONFIG = ScreenerConfig(
    name="cc",
    direction="short_call",
    chain_fetcher=lambda s, lo, hi: get_all_expirations_calls_data(s, lo, hi),
    delta_fn=black_scholes_call_delta,
    ohlc_fetcher=lambda s, **kw: get_ohlc(s, **kw),
    iv_lookup=lambda chain_df, strike: get_implied_volatility(chain_df, strike),
    strike_filter=lambda price, strike: strike > price * 0.98,
    delta_range=(0.10, 0.35),
    ideal_delta=0.225,
    strike_sort="asc",
    oi_delta_band=(0.10, 0.40),
    symbol_factory=_cc_symbol_factory,
    strike_context_builder=_cc_strike_context_builder,
    env_scorer=_cc_env_scorer,
    strike_scorer=_cc_strike_scorer_adapter,
    final_blend=(0.4, 0.6),
    tie_break_key=_cc_tie_break,
    result_factory=_cc_result_factory,
)
