"""
Orchestrates per-symbol DITM (Deep In The Money) Long Call analysis.
Finds the best deep-ITM call strikes to buy as stock substitutes.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from services.data_service import get_ohlc
from services.greeks_service import black_scholes_call_delta
from services.options_service import (
    get_bid_ask_spread_pct,
    get_implied_volatility,
    get_all_expirations_calls_data,
)
from services.technical_service import (
    compute_ditm_env_score,
    compute_ditm_strike_score,
    compute_ditm_final_score,
    compute_iv_rank_percentile,
    compute_price_vs_52w_high,
    compute_rsi,
    compute_sma_ratio,
    compute_trend_data,
    compute_volume_support,
)

logger = logging.getLogger(__name__)


@dataclass
class DitmStrikeResult:
    strike: float
    delta: float
    premium: float
    intrinsic: float
    extrinsic: float
    extrinsic_pct: float   # extrinsic / stock_price × 100
    moneyness_pct: float   # (price − strike) / price × 100
    leverage: float        # stock_price / premium  (how much stock you control per $ spent)
    bid_ask_spread_pct: Optional[float]
    env_score: float
    strike_score: float
    ditm_score: float
    is_best: bool = False
    iv_fallback: bool = False
    stale_premium: bool = False


@dataclass
class DitmResult:
    symbol: str
    price: float
    sma_ratio: float
    rsi: float
    iv_rank: Optional[float]
    iv_percentile: Optional[float]
    earnings_date: Optional[str]
    earnings_within_dte: bool
    vol_support_1: Optional[float]
    vol_support_2: Optional[float]
    vol_support_3: Optional[float]
    dte: int
    expiration: str
    strikes: list[DitmStrikeResult] = field(default_factory=list)
    best_ditm_score: float = 0.0
    using_hv_fallback: bool = False


@dataclass
class DitmError:
    symbol: str
    reason: str


def process_ditm_symbol(
    symbol: str,
    min_dte: int = 90,
    max_dte: int = 210,
    rf_rate: float = 0.045,
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

        # 2. Technical indicators
        sma_ratio = compute_sma_ratio(df)
        trend = compute_trend_data(df)
        rsi = compute_rsi(df)
        dist_52w = compute_price_vs_52w_high(df)
        iv_rank_raw, iv_pct_raw = compute_iv_rank_percentile(df)
        iv_rank: Optional[float] = None if math.isnan(iv_rank_raw) else iv_rank_raw
        iv_percentile: Optional[float] = None if math.isnan(iv_pct_raw) else iv_pct_raw
        vol_supports = compute_volume_support(df)

        # Pre-compute HV sigma fallback
        import numpy as np
        from datetime import datetime as _dt
        import pytz as _pytz
        log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()
        hv_sigma = float(log_ret.iloc[-30:].std(ddof=1) * np.sqrt(252)) if len(log_ret) >= 30 else 0.25

        # Market open detection
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

        results: list[DitmResult] = []
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

                T = dte / 365.0
                _IDEAL_DELTA = 0.825  # midpoint of 0.80–0.85 target

                # ITM calls: strikes BELOW current price
                all_strikes_sorted = sorted(calls_df["strike"].unique())
                itm_strikes = [s for s in all_strikes_sorted if s < current_price]

                candidates: list[tuple] = []
                for sp in itm_strikes:
                    try:
                        row = calls_df[calls_df["strike"] == sp]
                        if row.empty:
                            continue
                        bid = float(row["bid"].iloc[0]) if not __import__('pandas').isna(row["bid"].iloc[0]) else 0.0
                        ask = float(row["ask"].iloc[0]) if not __import__('pandas').isna(row["ask"].iloc[0]) else 0.0
                        last = float(row["lastPrice"].iloc[0]) if not __import__('pandas').isna(row["lastPrice"].iloc[0]) else 0.0
                        oi_val = int(row["openInterest"].iloc[0]) if not __import__('pandas').isna(row["openInterest"].iloc[0]) else 0
                        vol_val = int(row["volume"].iloc[0]) if not __import__('pandas').isna(row["volume"].iloc[0]) else 0
                        if bid > 0 and ask > 0:
                            prem = round((bid + ask) / 2.0, 4)
                            stale_prem = False
                        elif last > 0:
                            prem = round(last, 4)
                            stale_prem = True
                        else:
                            continue
                        # Premium must be > intrinsic (sanity check)
                        intrinsic = max(0.0, current_price - sp)
                        if prem < intrinsic * 0.9:
                            continue  # stale/bad data
                        sig = get_implied_volatility(calls_df, sp)
                        used_hv = False
                        iv_hv_ratio_val: Optional[float] = None
                        if math.isnan(sig) or sig < 0.15:
                            sig = hv_sigma
                            used_hv = True
                        else:
                            if hv_sigma > 0:
                                iv_hv_ratio_val = round(sig / hv_sigma, 4)
                        d = black_scholes_call_delta(current_price, sp, rf_rate, T, sig)
                        candidates.append((sp, d, prem, used_hv, stale_prem, iv_hv_ratio_val, sig, oi_val, vol_val))
                    except Exception:
                        continue

                # Chain median OI: deep ITM range 0.65 < delta < 0.95
                delta_range_ois = [c[7] for c in candidates if 0.65 < c[1] < 0.95]
                chain_median_oi = float(np.median(delta_range_ois)) if delta_range_ois else 0.0

                # Primary filter: 0.65 <= delta <= 0.95
                in_range = [c for c in candidates if 0.65 <= c[1] <= 0.95]

                # Fallback: nearest to ideal delta
                if not in_range and candidates:
                    in_range = sorted(candidates, key=lambda x: abs(x[1] - _IDEAL_DELTA))[:5]

                strike_results: list[DitmStrikeResult] = []
                for sp, d, prem, used_hv, stale_prem, iv_hv_ratio_val, sig_used, oi_val, vol_val in in_range:
                    try:
                        spread_raw = get_bid_ask_spread_pct(calls_df, sp)
                        spread_s: Optional[float] = None if math.isnan(spread_raw) else spread_raw

                        intrinsic_val = round(max(0.0, current_price - sp), 4)
                        extrinsic_val = round(max(0.0, prem - intrinsic_val), 4)
                        extrinsic_pct_val = round(extrinsic_val / current_price * 100.0, 4) if current_price > 0 else 0.0
                        moneyness_pct_val = round((current_price - sp) / current_price * 100.0, 4)
                        leverage_val = round(current_price / prem, 2) if prem > 0 else 0.0

                        env_s = compute_ditm_env_score(
                            iv_rank=iv_rank,
                            iv_hv_ratio=iv_hv_ratio_val,
                            price_above_sma50=trend["price_above_sma50"],
                            sma50_above_sma200=trend["sma50_above_sma200"],
                            dist_from_52w_high_pct=dist_52w,
                            rsi=rsi,
                            chain_median_oi=chain_median_oi,
                            earnings_within_dte=earnings_within_dte,
                        )
                        strike_s = compute_ditm_strike_score(
                            delta=d,
                            current_price=current_price,
                            strike=sp,
                            premium=prem,
                            bid_ask_spread_pct=spread_s,
                            open_interest=oi_val,
                            market_open=_market_open,
                            volume=vol_val,
                        )
                        final_s = compute_ditm_final_score(env_s, strike_s)
                        strike_results.append(DitmStrikeResult(
                            strike=sp,
                            delta=round(d, 4),
                            premium=round(prem, 4),
                            intrinsic=intrinsic_val,
                            extrinsic=extrinsic_val,
                            extrinsic_pct=extrinsic_pct_val,
                            moneyness_pct=moneyness_pct_val,
                            leverage=leverage_val,
                            bid_ask_spread_pct=spread_s,
                            env_score=env_s,
                            strike_score=strike_s,
                            ditm_score=final_s,
                            iv_fallback=used_hv,
                            stale_premium=stale_prem,
                        ))
                    except Exception:
                        continue

                if not strike_results:
                    continue

                best_idx = max(range(len(strike_results)), key=lambda i: strike_results[i].ditm_score)
                strike_results[best_idx].is_best = True
                best_score_val = strike_results[best_idx].ditm_score

                results.append(DitmResult(
                    symbol=sym,
                    price=round(current_price, 4),
                    sma_ratio=sma_ratio,
                    rsi=rsi,
                    iv_rank=iv_rank,
                    iv_percentile=iv_percentile,
                    earnings_date=earnings_date,
                    earnings_within_dte=earnings_within_dte,
                    vol_support_1=vol_supports[0] if len(vol_supports) > 0 else None,
                    vol_support_2=vol_supports[1] if len(vol_supports) > 1 else None,
                    vol_support_3=vol_supports[2] if len(vol_supports) > 2 else None,
                    dte=dte,
                    expiration=expiration,
                    strikes=strike_results,
                    best_ditm_score=best_score_val,
                    using_hv_fallback=any(s.iv_fallback for s in strike_results),
                ))
            except Exception as e:
                logger.debug("Error processing expiration %s for %s: %s", opts.get("expiration", "?"), sym, e)
                continue

        if not results:
            return [], DitmError(symbol=sym, reason="No valid DITM call strikes found in DTE range")

        return results, None

    except Exception as e:
        logger.warning("DITM processing failed for %s: %s", sym, e)
        return [], DitmError(symbol=sym, reason=str(e))
