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
from services.options_service import (
    get_bid_ask_spread_pct,
    get_implied_volatility,
    get_all_expirations_data,
    get_premium,
)
from services.technical_service import (
    compute_bollinger,
    compute_csp_score,
    compute_iv_rank_percentile,
    compute_price_vs_52w_high,
    compute_rsi,
    compute_sma_ratio,
    compute_trend_data,
    compute_volume_support,
)

logger = logging.getLogger(__name__)


@dataclass
class StrikeResult:
    strike: float
    delta: float
    premium: float
    annualized_return: float
    bid_ask_spread_pct: Optional[float]
    csp_score: float
    is_best: bool = False
    iv_fallback: bool = False   # True when hv_sigma was used instead of yfinance IV
    stale_premium: bool = False # True when lastPrice was used instead of (bid+ask)/2


@dataclass
class ScreenerResult:
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
    vol_support_1: Optional[float]
    vol_support_2: Optional[float]
    vol_support_3: Optional[float]
    dte: int
    expiration: str
    strikes: list[StrikeResult] = field(default_factory=list)
    best_csp_score: float = 0.0
    using_hv_fallback: bool = False  # True when any strike in this row used hv_sigma


@dataclass
class ScreenerError:
    symbol: str
    reason: str


def process_symbol(
    symbol: str,
    min_dte: int = 30,
    max_dte: int = 60,
    rf_rate: float = 0.045,
) -> tuple[list[ScreenerResult], Optional[ScreenerError]]:
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
        vol_supports = compute_volume_support(df)

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

        # 3. All expirations in range
        all_exps = get_all_expirations_data(sym, min_dte, max_dte)

        results: list[ScreenerResult] = []
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
                for sp in otm_strikes:
                    try:
                        row = puts_df[puts_df["strike"] == sp]
                        if row.empty:
                            continue
                        bid = float(row["bid"].iloc[0]) if not __import__('pandas').isna(row["bid"].iloc[0]) else 0.0
                        ask = float(row["ask"].iloc[0]) if not __import__('pandas').isna(row["ask"].iloc[0]) else 0.0
                        last = float(row["lastPrice"].iloc[0]) if not __import__('pandas').isna(row["lastPrice"].iloc[0]) else 0.0
                        market_closed_row = (bid == 0.0 and ask == 0.0)
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
                        sig = get_implied_volatility(puts_df, sp)
                        # IV from yfinance is back-computed from lastPrice when
                        # bid/ask are 0 (market closed) — stale and unreliable.
                        # Use hv_sigma if IV looks suspiciously low (< 15%).
                        used_hv = False
                        iv_hv_ratio: Optional[float] = None
                        if math.isnan(sig) or sig < 0.15:
                            sig = hv_sigma
                            used_hv = True
                        else:
                            if hv_sigma > 0:
                                iv_hv_ratio = round(sig / hv_sigma, 4)
                        d = black_scholes_put_delta(current_price, sp, rf_rate, T, sig)
                        candidates.append((sp, d, prem, used_hv, stale_prem, iv_hv_ratio, sig, oi_val, vol_val))
                    except Exception:
                        continue

                # Primary filter: -0.35 to -0.10 delta
                in_range = [c for c in candidates if -0.35 <= c[1] <= -0.10]

                # Fallback: if nothing in range, take up to 5 strikes nearest to ideal delta
                if not in_range and candidates:
                    in_range = sorted(candidates, key=lambda x: abs(x[1] - _IDEAL_DELTA))[:5]

                strike_results: list[StrikeResult] = []
                for sp, d, prem, used_hv, stale_prem, iv_hv_ratio, sig_used, oi_val, vol_val in in_range:
                    try:
                        spread_raw = get_bid_ask_spread_pct(puts_df, sp)
                        spread_s: Optional[float] = None if math.isnan(spread_raw) else spread_raw
                        collateral_s = round(sp * 100.0, 2)
                        ret_s = round((prem * 100) / collateral_s * 100.0, 4) if collateral_s > 0 else 0.0
                        ann_ret_s = round(ret_s * (365.0 / dte), 4) if dte > 0 else 0.0
                        score_s = compute_csp_score(
                            iv_rank=iv_rank,
                            iv_hv_ratio=iv_hv_ratio,
                            annualized_return=ann_ret_s,
                            premium=prem,
                            current_price=current_price,
                            strike=sp,
                            dte=dte,
                            iv_used=sig_used,
                            price_above_sma50=trend["price_above_sma50"],
                            sma50_above_sma200=trend["sma50_above_sma200"],
                            dist_from_52w_high_pct=dist_52w,
                            rsi=rsi,
                            delta=d,
                            bid_ask_spread_pct=spread_s,
                            open_interest=oi_val,
                            market_open=_market_open,
                            volume=vol_val,
                            earnings_within_dte=earnings_within_dte,
                        )
                        strike_results.append(StrikeResult(
                            strike=sp,
                            delta=d,
                            premium=round(prem, 4),
                            annualized_return=ann_ret_s,
                            bid_ask_spread_pct=spread_s,
                            csp_score=score_s,
                            iv_fallback=used_hv,
                            stale_premium=stale_prem,
                        ))
                    except Exception:
                        continue

                if not strike_results:
                    continue

                # Mark the highest-scoring strike as best
                best_idx = max(range(len(strike_results)), key=lambda i: strike_results[i].csp_score)
                strike_results[best_idx].is_best = True
                best_score_val = strike_results[best_idx].csp_score

                results.append(ScreenerResult(
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
                    vol_support_1=vol_supports[0] if len(vol_supports) > 0 else None,
                    vol_support_2=vol_supports[1] if len(vol_supports) > 1 else None,
                    vol_support_3=vol_supports[2] if len(vol_supports) > 2 else None,
                    dte=dte,
                    expiration=expiration,
                    strikes=strike_results,
                    best_csp_score=best_score_val,
                    using_hv_fallback=any(sr.iv_fallback for sr in strike_results),
                ))
            except Exception as exc:
                logger.debug("Skipping expiration %s for %s: %s", opts.get("expiration"), sym, exc)
                continue

        if not results:
            return [], ScreenerError(symbol=sym, reason="No valid expirations processed")
        return results, None

    except Exception as exc:
        logger.warning("Failed to process '%s': %s", sym, exc)
        return [], ScreenerError(symbol=sym, reason=str(exc))
