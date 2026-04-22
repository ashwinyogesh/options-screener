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
    get_options_data,
    get_premium,
    select_strike,
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
    strike: float
    strike_is_fallback: bool
    strike_mid: float
    strike_mid_is_fallback: bool
    vol_support_1: Optional[float]
    vol_support_2: Optional[float]
    vol_support_3: Optional[float]
    delta: float
    delta_mid: float
    bid_ask_spread_pct: Optional[float]  # (ask-bid)/mid * 100
    csp_score: float                     # composite quality score 0-100
    dte: int
    expiration: str
    premium: float
    premium_mid: float
    collateral: float
    return_pct: float
    annualized_return: float
    return_pct_mid: float
    annualized_return_mid: float


@dataclass
class ScreenerError:
    symbol: str
    reason: str


def process_symbol(
    symbol: str,
    min_dte: int = 30,
    max_dte: int = 45,
    rf_rate: float = 0.045,
) -> tuple[Optional[ScreenerResult], Optional[ScreenerError]]:
    """
    Processes a single symbol. Returns (result, None) on success or
    (None, error) on any failure so the caller can continue the batch.
    """
    sym = symbol.strip().upper()
    try:
        # 1. Price history
        df = get_ohlc(sym, period="2y")
        current_price = float(df["Close"].iloc[-1])

        # 2. Technical indicators
        bb = compute_bollinger(df)
        sma_ratio = compute_sma_ratio(df)
        rsi = compute_rsi(df)
        iv_rank_raw, iv_pct_raw = compute_iv_rank_percentile(df)
        iv_rank: Optional[float] = None if math.isnan(iv_rank_raw) else iv_rank_raw
        iv_percentile: Optional[float] = None if math.isnan(iv_pct_raw) else iv_pct_raw
        vol_supports = compute_volume_support(df)

        # 3. Options chain
        opts = get_options_data(sym, min_dte, max_dte)
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

        # 4. Strike selection
        strike, strike_is_fallback = select_strike(puts_df, bb["bb_lower"])
        strike_mid, strike_mid_is_fallback = select_strike(puts_df, bb["bb_middle"])

        # 5. Premium (mid-price)
        premium = get_premium(puts_df, strike)
        premium_mid = get_premium(puts_df, strike_mid)

        # 5a. Bid-ask spread quality
        spread_pct = get_bid_ask_spread_pct(puts_df, strike)
        bid_ask_spread_pct: Optional[float] = None if math.isnan(spread_pct) else spread_pct

        # 6. Implied volatility for delta calculation
        sigma = get_implied_volatility(puts_df, strike)
        if math.isnan(sigma) or sigma <= 0:
            # Approximate with 30-day HV if IV not available
            import numpy as np
            log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()
            if len(log_ret) >= 30:
                sigma = float(log_ret.iloc[-30:].std(ddof=1) * np.sqrt(252))
            else:
                sigma = 0.25  # last resort default

        # 7. Black-Scholes delta
        T = dte / 365.0
        delta = black_scholes_put_delta(current_price, strike, rf_rate, T, sigma)
        delta_mid = black_scholes_put_delta(current_price, strike_mid, rf_rate, T, sigma)

        # 8. Returns
        # collateral = strike × 100 (dollars secured per contract)
        # premium is per-share; one contract covers 100 shares → dollar credit = premium × 100
        collateral = round(strike * 100.0, 2)
        return_pct = round((premium * 100) / collateral * 100.0, 4) if collateral > 0 else 0.0
        annualized_return = round(return_pct * (365.0 / dte), 4) if dte > 0 else 0.0
        collateral_mid = round(strike_mid * 100.0, 2)
        return_pct_mid = round((premium_mid * 100) / collateral_mid * 100.0, 4) if collateral_mid > 0 else 0.0
        annualized_return_mid = round(return_pct_mid * (365.0 / dte), 4) if dte > 0 else 0.0

        # 9. CSP composite score
        csp_score = compute_csp_score(
            iv_rank=iv_rank,
            annualized_return=annualized_return,
            sma_ratio=sma_ratio,
            rsi=rsi,
            delta=delta,
            bid_ask_spread_pct=bid_ask_spread_pct,
            earnings_within_dte=earnings_within_dte,
        )

        result = ScreenerResult(
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
            strike=strike,
            strike_is_fallback=strike_is_fallback,
            strike_mid=strike_mid,
            strike_mid_is_fallback=strike_mid_is_fallback,
            vol_support_1=vol_supports[0] if len(vol_supports) > 0 else None,
            vol_support_2=vol_supports[1] if len(vol_supports) > 1 else None,
            vol_support_3=vol_supports[2] if len(vol_supports) > 2 else None,
            delta=delta,
            delta_mid=delta_mid,
            bid_ask_spread_pct=bid_ask_spread_pct,
            csp_score=csp_score,
            dte=dte,
            expiration=expiration,
            premium=round(premium, 4),
            premium_mid=round(premium_mid, 4),
            collateral=collateral,
            return_pct=return_pct,
            annualized_return=annualized_return,
            return_pct_mid=return_pct_mid,
            annualized_return_mid=annualized_return_mid,
        )
        return result, None

    except Exception as exc:
        logger.warning("Failed to process '%s': %s", sym, exc)
        return None, ScreenerError(symbol=sym, reason=str(exc))
