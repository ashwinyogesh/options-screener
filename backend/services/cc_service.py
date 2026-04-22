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
from services.options_service import (
    get_bid_ask_spread_pct,
    get_implied_volatility,
    get_all_expirations_calls_data,
)
from services.technical_service import (
    compute_env_score,
    compute_cc_strike_score,
    compute_cc_final_score,
    compute_iv_rank_percentile,
    compute_price_vs_52w_high,
    compute_rsi,
    compute_sma_ratio,
    compute_trend_data,
    compute_volume_resistance,
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
    is_best: bool = False
    iv_fallback: bool = False   # True when hv_sigma was used instead of yfinance IV
    stale_premium: bool = False # True when lastPrice was used instead of (bid+ask)/2


@dataclass
class CcResult:
    symbol: str
    price: float
    sma_ratio: float
    rsi: float
    iv_rank: Optional[float]
    iv_percentile: Optional[float]
    earnings_date: Optional[str]
    earnings_within_dte: bool
    vol_resistance_1: Optional[float]
    vol_resistance_2: Optional[float]
    vol_resistance_3: Optional[float]
    dte: int
    expiration: str
    strikes: list[CcStrikeResult] = field(default_factory=list)
    best_cc_score: float = 0.0
    using_hv_fallback: bool = False
    expected_move: float = 0.0


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
    Processes a single symbol across all valid expirations in [min_dte, max_dte].
    Returns (list_of_results, None) on success or ([], error) on failure.
    """
    sym = symbol.strip().upper()
    try:
        # 1. Price history
        df = get_ohlc(sym, period="2y")
        current_price = float(df["Close"].iloc[-1])

        # 2. Technical indicators (computed once, shared across expirations)
        sma_ratio = compute_sma_ratio(df)
        trend = compute_trend_data(df)
        rsi = compute_rsi(df)
        dist_52w = compute_price_vs_52w_high(df)
        iv_rank_raw, iv_pct_raw = compute_iv_rank_percentile(df)
        iv_rank: Optional[float] = None if math.isnan(iv_rank_raw) else iv_rank_raw
        iv_percentile: Optional[float] = None if math.isnan(iv_pct_raw) else iv_pct_raw
        vol_resistances = compute_volume_resistance(df)

        # Pre-compute HV sigma fallback once
        import numpy as np
        from datetime import datetime as _dt
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
                        if bid > 0 and ask > 0:
                            prem = round((bid + ask) / 2.0, 4)
                            stale_prem = False
                        elif last > 0:
                            prem = round(last, 4)
                            stale_prem = True
                        else:
                            continue  # no usable premium
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

                # Chain median OI: only strikes with 0.1 < delta < 0.4 (CC relevant range)
                delta_range_ois = [c[7] for c in candidates if 0.1 < c[1] < 0.4]
                chain_median_oi = float(np.median(delta_range_ois)) if delta_range_ois else 0.0

                # Primary filter: +0.10 to +0.35 delta
                in_range = [c for c in candidates if 0.10 <= c[1] <= 0.35]

                # Fallback: if nothing in range, take up to 5 strikes nearest to ideal delta
                if not in_range and candidates:
                    in_range = sorted(candidates, key=lambda x: abs(x[1] - _IDEAL_DELTA))[:5]

                strike_results: list[CcStrikeResult] = []
                for sp, d, prem, used_hv, stale_prem, iv_hv_ratio_val, sig_used, oi_val, vol_val in in_range:
                    try:
                        spread_raw = get_bid_ask_spread_pct(calls_df, sp)
                        spread_s: Optional[float] = None if math.isnan(spread_raw) else spread_raw
                        # CC annualized return = premium yield against current stock price (per share)
                        ret_s = round((prem / current_price) * 100.0, 4) if current_price > 0 else 0.0
                        ann_ret_s = round(ret_s * (365.0 / dte), 4) if dte > 0 else 0.0

                        env_s = compute_env_score(
                            iv_rank=iv_rank,
                            iv_hv_ratio=iv_hv_ratio_val,
                            price_above_sma50=trend["price_above_sma50"],
                            sma50_above_sma200=trend["sma50_above_sma200"],
                            dist_from_52w_high_pct=dist_52w,
                            rsi=rsi,
                            chain_median_oi=chain_median_oi,
                            earnings_within_dte=earnings_within_dte,
                        )
                        strike_s = compute_cc_strike_score(
                            delta=d,
                            current_price=current_price,
                            strike=sp,
                            iv_used=sig_used,
                            dte=dte,
                            vol_resistance_1=vol_resistances[0] if len(vol_resistances) > 0 else None,
                            vol_resistance_2=vol_resistances[1] if len(vol_resistances) > 1 else None,
                            vol_resistance_3=vol_resistances[2] if len(vol_resistances) > 2 else None,
                            bid_ask_spread_pct=spread_s,
                            open_interest=oi_val,
                            market_open=_market_open,
                            volume=vol_val,
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
                            iv_fallback=used_hv,
                            stale_premium=stale_prem,
                        ))
                    except Exception:
                        continue

                if not strike_results:
                    continue

                best_idx = max(range(len(strike_results)), key=lambda i: strike_results[i].cc_score)
                strike_results[best_idx].is_best = True
                best_score_val = strike_results[best_idx].cc_score
                expected_move = round(current_price * hv_sigma * math.sqrt(dte / 365.0), 2)

                results.append(CcResult(
                    symbol=sym,
                    price=round(current_price, 4),
                    sma_ratio=sma_ratio,
                    rsi=rsi,
                    iv_rank=iv_rank,
                    iv_percentile=iv_percentile,
                    earnings_date=earnings_date,
                    earnings_within_dte=earnings_within_dte,
                    vol_resistance_1=vol_resistances[0] if len(vol_resistances) > 0 else None,
                    vol_resistance_2=vol_resistances[1] if len(vol_resistances) > 1 else None,
                    vol_resistance_3=vol_resistances[2] if len(vol_resistances) > 2 else None,
                    dte=dte,
                    expiration=expiration,
                    strikes=strike_results,
                    best_cc_score=best_score_val,
                    using_hv_fallback=any(s.iv_fallback for s in strike_results),
                    expected_move=expected_move,
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
