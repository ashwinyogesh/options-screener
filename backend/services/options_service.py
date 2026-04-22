"""
Fetches options chain data from Yahoo Finance via yfinance and performs
strike selection, premium extraction, and earnings date lookup.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import math
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_TARGET_DTE = 37  # preferred midpoint for expiration selection


def _dte(expiration_str: str) -> int:
    """Returns calendar days from today to expiration_str (YYYY-MM-DD)."""
    today = date.today()
    exp = datetime.strptime(expiration_str, "%Y-%m-%d").date()
    return (exp - today).days


def get_all_expirations_data(symbol: str, min_dte: int = 30, max_dte: int = 60) -> list[dict]:
    """
    Returns a list of dicts sorted by DTE ascending, one per valid expiration
    in [min_dte, max_dte]:
      expiration   : str  — 'YYYY-MM-DD'
      dte          : int
      puts_df      : pd.DataFrame — filtered put chain
      earnings_date: str | None

    Raises ValueError if no valid expiration is found or no options exist.
    """
    ticker = yf.Ticker(symbol)
    expirations = ticker.options
    if not expirations:
        raise ValueError(f"No options data available for '{symbol}'")

    valid = [(exp, _dte(exp)) for exp in expirations if min_dte <= _dte(exp) <= max_dte]
    if not valid:
        raise ValueError(
            f"No expiration in {min_dte}\u2013{max_dte} DTE range for '{symbol}'. "
            f"Available: {expirations[:8]}"
        )

    earnings_date = _get_earnings_date(ticker)

    results = []
    for exp, dte_val in sorted(valid, key=lambda x: x[1]):
        try:
            chain = ticker.option_chain(exp)
            puts = chain.puts.copy()
            liquid_mask = (puts["volume"].fillna(0) > 0) | (puts["openInterest"].fillna(0) > 0)
            puts = puts[liquid_mask].reset_index(drop=True)
            if puts.empty:
                continue
            results.append({
                "expiration": exp,
                "dte": dte_val,
                "puts_df": puts,
                "earnings_date": earnings_date,
            })
        except Exception:
            continue

    if not results:
        raise ValueError(f"All expirations in range were empty or illiquid for '{symbol}'")
    return results


def get_all_expirations_calls_data(symbol: str, min_dte: int = 30, max_dte: int = 60) -> list[dict]:
    """
    Like get_all_expirations_data but returns call chains instead of put chains.
    Each dict has: expiration, dte, calls_df, earnings_date.
    """
    ticker = yf.Ticker(symbol)
    expirations = ticker.options
    if not expirations:
        raise ValueError(f"No options data available for '{symbol}'")

    valid = [(exp, _dte(exp)) for exp in expirations if min_dte <= _dte(exp) <= max_dte]
    if not valid:
        raise ValueError(
            f"No expiration in {min_dte}\u2013{max_dte} DTE range for '{symbol}'. "
            f"Available: {expirations[:8]}"
        )

    earnings_date = _get_earnings_date(ticker)

    results = []
    for exp, dte_val in sorted(valid, key=lambda x: x[1]):
        try:
            chain = ticker.option_chain(exp)
            calls = chain.calls.copy()
            liquid_mask = (calls["volume"].fillna(0) > 0) | (calls["openInterest"].fillna(0) > 0)
            calls = calls[liquid_mask].reset_index(drop=True)
            if calls.empty:
                continue
            results.append({
                "expiration": exp,
                "dte": dte_val,
                "calls_df": calls,
                "earnings_date": earnings_date,
            })
        except Exception:
            continue

    if not results:
        raise ValueError(f"All expirations in range were empty or illiquid for '{symbol}'")
    return results

def get_options_data(symbol: str, min_dte: int = 30, max_dte: int = 45) -> dict:
    """
    Returns a dict with keys:
      expiration  : str  — chosen expiration date 'YYYY-MM-DD'
      dte         : int
      puts_df     : pd.DataFrame — filtered put chain for the chosen expiration
      earnings_date : str | None — nearest upcoming earnings 'YYYY-MM-DD'

    Raises ValueError if no valid expiration is found in the DTE range.
    """
    ticker = yf.Ticker(symbol)
    expirations = ticker.options  # tuple of 'YYYY-MM-DD' strings

    if not expirations:
        raise ValueError(f"No options data available for '{symbol}'")

    # Filter to expirations within [min_dte, max_dte]
    valid = [(exp, _dte(exp)) for exp in expirations if min_dte <= _dte(exp) <= max_dte]
    if not valid:
        raise ValueError(
            f"No expiration in {min_dte}–{max_dte} DTE range for '{symbol}'. "
            f"Available expirations: {expirations[:8]}"
        )

    # Pick closest to TARGET_DTE
    chosen_exp, chosen_dte = min(valid, key=lambda x: abs(x[1] - _TARGET_DTE))

    # Fetch the put chain for the chosen expiration
    chain = ticker.option_chain(chosen_exp)
    puts = chain.puts.copy()

    # Filter illiquid contracts
    liquid_mask = (puts["volume"].fillna(0) > 0) | (puts["openInterest"].fillna(0) > 0)
    puts = puts[liquid_mask].reset_index(drop=True)

    if puts.empty:
        raise ValueError(f"All puts for '{symbol}' expiring {chosen_exp} are illiquid")

    earnings_date = _get_earnings_date(ticker)

    return {
        "expiration": chosen_exp,
        "dte": chosen_dte,
        "puts_df": puts,
        "earnings_date": earnings_date,
    }


def _get_earnings_date(ticker: yf.Ticker) -> str | None:
    """Attempts to extract the nearest upcoming earnings date from yfinance."""
    try:
        cal = ticker.calendar
        if cal is not None:
            # calendar is a dict; 'Earnings Date' key holds a list of dates
            earnings = cal.get("Earnings Date")
            if earnings:
                today = date.today()
                future = [d for d in earnings if _to_date(d) and _to_date(d) >= today]
                if future:
                    return str(_to_date(future[0]))
    except Exception as exc:
        logger.debug("Could not get earnings date: %s", exc)

    # Fallback: try info
    try:
        info = ticker.info
        ed = info.get("earningsDate")
        if ed:
            ed_date = _to_date(ed[0] if isinstance(ed, list) else ed)
            if ed_date and ed_date >= date.today():
                return str(ed_date)
    except Exception:
        pass

    return None


def _to_date(val) -> date | None:
    """Coerce a variety of date representations to a date object."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, (int, float)):
        # Unix timestamp
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc).date()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(val, str):
        try:
            return datetime.strptime(val[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def select_strike(puts_df: pd.DataFrame, bb_lower: float) -> tuple[float, bool]:
    """
    Returns (strike, used_fallback).
    Preferred: nearest strike ≤ bb_lower.
    Fallback: lowest available strike (flagged with used_fallback=True).
    """
    below = puts_df[puts_df["strike"] <= bb_lower]
    if not below.empty:
        # Nearest = highest strike that is still ≤ bb_lower
        strike = float(below["strike"].max())
        return strike, False

    # No strike below BB lower — use minimum available
    strike = float(puts_df["strike"].min())
    return strike, True


def get_premium(puts_df: pd.DataFrame, strike: float) -> float:
    """
    Returns mid-price = (bid + ask) / 2 for the selected strike.
    Falls back to lastPrice if bid or ask is zero/NaN.
    Raises ValueError if no row matches the strike.
    """
    row = puts_df[puts_df["strike"] == strike]
    if row.empty:
        raise ValueError(f"Strike {strike} not found in puts DataFrame")

    bid = float(row["bid"].iloc[0]) if pd.notna(row["bid"].iloc[0]) else 0.0
    ask = float(row["ask"].iloc[0]) if pd.notna(row["ask"].iloc[0]) else 0.0
    last = float(row["lastPrice"].iloc[0]) if pd.notna(row["lastPrice"].iloc[0]) else 0.0

    if bid > 0 and ask > 0:
        return round((bid + ask) / 2.0, 4)
    if last > 0:
        return round(last, 4)
    raise ValueError(f"No valid premium for strike {strike} (bid={bid}, ask={ask}, last={last})")


def get_implied_volatility(puts_df: pd.DataFrame, strike: float) -> float:
    """
    Returns the implied volatility (as a decimal) for the selected strike.
    Returns NaN if unavailable.
    """
    row = puts_df[puts_df["strike"] == strike]
    if row.empty:
        return float("nan")
    iv = row["impliedVolatility"].iloc[0]
    if pd.isna(iv) or iv <= 0:
        return float("nan")
    return float(iv)


def get_bid_ask_spread_pct(puts_df: pd.DataFrame, strike: float) -> float:
    """
    Returns (ask - bid) / mid * 100 as a spread quality indicator.
    Lower is better (tighter market).
    Falls back to a lastPrice-based estimate when bid/ask are zero
    (common outside market hours).
    Works with both puts and calls DataFrames.
    """
    row = puts_df[puts_df["strike"] == strike]
    if row.empty:
        return float("nan")
    bid = float(row["bid"].iloc[0]) if pd.notna(row["bid"].iloc[0]) else 0.0
    ask = float(row["ask"].iloc[0]) if pd.notna(row["ask"].iloc[0]) else 0.0
    last = float(row["lastPrice"].iloc[0]) if pd.notna(row["lastPrice"].iloc[0]) else 0.0

    if bid > 0 and ask > 0 and ask >= bid:
        mid = (bid + ask) / 2.0
        if mid > 0:
            return round((ask - bid) / mid * 100.0, 2)

    # Fallback: estimate spread from ask vs lastPrice when one of bid/ask is missing
    if ask > 0 and last > 0 and ask >= last:
        mid = (ask + last) / 2.0
        if mid > 0:
            return round((ask - last) / mid * 100.0, 2)
    if bid > 0 and last > 0 and last >= bid:
        mid = (bid + last) / 2.0
        if mid > 0:
            return round((last - bid) / mid * 100.0, 2)

    return float("nan")


def get_open_interest(options_df: pd.DataFrame, strike: float) -> int:
    """Returns open interest for the strike. Returns -1 if unavailable."""
    row = options_df[options_df["strike"] == strike]
    if row.empty:
        return -1
    oi = row["openInterest"].iloc[0]
    if pd.isna(oi):
        return -1
    return int(oi)


def get_calls_data(symbol: str, min_dte: int = 180, max_dte: int = 365) -> dict:
    """
    Returns a dict with keys:
      expiration    : str  — chosen expiration date 'YYYY-MM-DD'
      dte           : int
      calls_df      : pd.DataFrame — filtered call chain for the chosen expiration
      earnings_date : str | None

    Raises ValueError if no valid expiration is found in the DTE range.
    """
    ticker = yf.Ticker(symbol)
    expirations = ticker.options

    if not expirations:
        raise ValueError(f"No options data available for '{symbol}'")

    target_dte = (min_dte + max_dte) // 2
    valid = [(exp, _dte(exp)) for exp in expirations if min_dte <= _dte(exp) <= max_dte]
    if not valid:
        raise ValueError(
            f"No expiration in {min_dte}–{max_dte} DTE range for '{symbol}'. "
            f"Available expirations: {expirations[:8]}"
        )

    chosen_exp, chosen_dte = min(valid, key=lambda x: abs(x[1] - target_dte))

    chain = ticker.option_chain(chosen_exp)
    calls = chain.calls.copy()

    liquid_mask = (calls["volume"].fillna(0) > 0) | (calls["openInterest"].fillna(0) > 0)
    calls = calls[liquid_mask].reset_index(drop=True)

    if calls.empty:
        raise ValueError(f"All calls for '{symbol}' expiring {chosen_exp} are illiquid")

    earnings_date = _get_earnings_date(ticker)

    return {
        "expiration": chosen_exp,
        "dte": chosen_dte,
        "calls_df": calls,
        "earnings_date": earnings_date,
    }


def select_ditm_call(
    calls_df: pd.DataFrame,
    current_price: float,
    rf_rate: float,
    T: float,
    min_delta: float = 0.80,
) -> tuple[float, bool]:
    """
    Select the optimal DITM call strike:
    - Only considers ITM calls (strike < current_price)
    - Computes BS call delta using chain IV for each candidate
    - Filters to delta >= min_delta (deep ITM)
    - Among qualifying strikes picks the one with lowest extrinsic_pct
      (smallest time premium relative to stock price = best leverage efficiency)
    - Fallback: most ITM call (lowest strike) if no strike meets delta threshold
    Returns (strike, is_fallback).
    """
    from services.greeks_service import black_scholes_call_delta

    itm = calls_df[calls_df["strike"] < current_price].copy()
    if itm.empty:
        raise ValueError("No ITM calls available in the option chain")

    def _delta(row) -> float:
        iv = float(row["impliedVolatility"]) if pd.notna(row["impliedVolatility"]) else float("nan")
        if math.isnan(iv) or iv <= 0:
            return float("nan")
        return black_scholes_call_delta(current_price, float(row["strike"]), rf_rate, T, iv)

    def _extrinsic_pct(row) -> float:
        bid = float(row["bid"]) if pd.notna(row["bid"]) else 0.0
        ask = float(row["ask"]) if pd.notna(row["ask"]) else 0.0
        last = float(row["lastPrice"]) if pd.notna(row["lastPrice"]) else 0.0
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else last
        if mid <= 0:
            return float("inf")
        intrinsic = max(0.0, current_price - float(row["strike"]))
        ext = max(0.0, mid - intrinsic)
        return ext / current_price * 100.0

    itm = itm.assign(_delta=itm.apply(_delta, axis=1))
    deep = itm[itm["_delta"] >= min_delta].copy()

    if not deep.empty:
        deep = deep.assign(_ext_pct=deep.apply(_extrinsic_pct, axis=1))
        best = deep.loc[deep["_ext_pct"].idxmin()]
        return float(best["strike"]), False

    # Fallback: most ITM (lowest strike available)
    return float(itm["strike"].min()), True
