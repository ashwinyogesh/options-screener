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
    compute_rsi,
    compute_sma_ratio,
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
        rsi = compute_rsi(df)
        iv_rank_raw, iv_pct_raw = compute_iv_rank_percentile(df)
        iv_rank: Optional[float] = None if math.isnan(iv_rank_raw) else iv_rank_raw
        iv_percentile: Optional[float] = None if math.isnan(iv_pct_raw) else iv_pct_raw
        vol_supports = compute_volume_support(df)

        # Pre-compute HV sigma fallback once
        import numpy as np
        log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()
        hv_sigma = float(log_ret.iloc[-30:].std(ddof=1) * np.sqrt(252)) if len(log_ret) >= 30 else 0.25

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
                all_strikes_sorted = sorted(puts_df["strike"].unique(), reverse=True)  # ATM-first
                otm_strikes = [s for s in all_strikes_sorted if s < current_price * 1.02]

                strike_results: list[StrikeResult] = []
                for sp in otm_strikes:
                    try:
                        prem = get_premium(puts_df, sp)
                        if prem <= 0:
                            continue  # no tradeable premium
                        sig = get_implied_volatility(puts_df, sp)
                        if math.isnan(sig) or sig <= 0:
                            sig = hv_sigma
                        d = black_scholes_put_delta(current_price, sp, rf_rate, T, sig)
                        if not (-0.35 <= d <= -0.10):
                            continue  # keep only 10-35 delta range
                        spread_raw = get_bid_ask_spread_pct(puts_df, sp)
                        spread_s: Optional[float] = None if math.isnan(spread_raw) else spread_raw
                        collateral_s = round(sp * 100.0, 2)
                        ret_s = round((prem * 100) / collateral_s * 100.0, 4) if collateral_s > 0 else 0.0
                        ann_ret_s = round(ret_s * (365.0 / dte), 4) if dte > 0 else 0.0
                        score_s = compute_csp_score(
                            iv_rank=iv_rank,
                            annualized_return=ann_ret_s,
                            sma_ratio=sma_ratio,
                            rsi=rsi,
                            delta=d,
                            bid_ask_spread_pct=spread_s,
                            earnings_within_dte=earnings_within_dte,
                        )
                        strike_results.append(StrikeResult(
                            strike=sp,
                            delta=d,
                            premium=round(prem, 4),
                            annualized_return=ann_ret_s,
                            bid_ask_spread_pct=spread_s,
                            csp_score=score_s,
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
