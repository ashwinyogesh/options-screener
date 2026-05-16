"""Market-confirmation signals for ACS Component E (§5.1, §6).

Fetches three normalized inputs in [0, 1] that are pre-populated onto the
ticker_timeline doc before compute_acs() is called:

  rs_14d_norm         sector-relative price strength over 14 trading days
  opt_ratio_norm      call-options volume / open-interest for nearest expiry
  institutional_norm  net institutional buying from yfinance holder data

All calls are non-fatal — an error or missing data returns 0.0 so the scorer
treats the sub-signal as absent rather than aborting the run.  Results are
cached per ticker per scorer invocation; the cache is not persisted across runs
(same policy as market_cap_lookup.py).

Normalization curves (NARRATIVE_METHODOLOGY.md §5.1):
  RS_14d:      clip(excess_return / _RS_CAP,   0.0, 1.0)
               _RS_CAP = 0.20 — 20% outperformance vs sector = maximum signal
  opt_ratio:   min((call_vol / call_oi) / _OPT_CAP, 1.0)
               _OPT_CAP = 2.0 — call vol equal to 2× call OI = maximum signal
  13F_change:  clip(net_pct_change / _INST_CAP, 0.0, 1.0)
               _INST_CAP = 0.05 — net 5% institutional buying = maximum signal
               net_pct_change = sum(holder Change) / sum(holder Shares)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalization caps — calibrated so "strong" confirmation maps to 1.0.
# ---------------------------------------------------------------------------
_RS_CAP: float = 0.20    # 20% excess sector-relative return saturates RS signal
_OPT_CAP: float = 2.0   # call vol / call OI = 2.0 saturates options signal
_INST_CAP: float = 0.05  # net 5% institutional buying saturates 13F signal

# SPDR sector-ETF map — any sector not listed falls back to SPY.
_SECTOR_ETF: dict[str, str] = {
    "Technology":             "XLK",
    "Healthcare":             "XLV",
    "Financials":             "XLF",
    "Financial Services":     "XLF",
    "Consumer Cyclical":      "XLY",
    "Consumer Defensive":     "XLP",
    "Energy":                 "XLE",
    "Basic Materials":        "XLB",
    "Real Estate":            "XLRE",
    "Utilities":              "XLU",
    "Industrials":            "XLI",
    "Communication Services": "XLC",
}
_DEFAULT_BENCHMARK: str = "SPY"


@dataclass(frozen=True)
class MarketConfirmation:
    rs_14d_norm: float         # [0, 1] — sector-relative strength
    opt_ratio_norm: float      # [0, 1] — call-skew / activity
    institutional_norm: float  # [0, 1] — institutional net buying


_ZERO = MarketConfirmation(rs_14d_norm=0.0, opt_ratio_norm=0.0, institutional_norm=0.0)

# In-process cache: cleared between runs by reset_cache() (called from tests).
_cache: dict[str, Optional[MarketConfirmation]] = {}


def get_market_confirmation(ticker: str) -> MarketConfirmation:
    """Return normalized market-confirmation signals for *ticker*.

    Always returns a valid dataclass (never raises).  Any sub-signal that
    cannot be fetched is 0.0.
    """
    if ticker in _cache:
        return _cache[ticker] or _ZERO

    result = _fetch(ticker)
    _cache[ticker] = result
    return result


def reset_cache() -> None:
    """Clear the in-process cache.  Used by tests and integration harnesses."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch(ticker: str) -> MarketConfirmation:
    try:
        import yfinance as yf  # noqa: PLC0415
    except ImportError:
        logger.warning("yfinance not installed — Component E signals will be 0")
        return _ZERO

    return MarketConfirmation(
        rs_14d_norm=_fetch_rs(ticker, yf),
        opt_ratio_norm=_fetch_opt(ticker, yf),
        institutional_norm=_fetch_institutional(ticker, yf),
    )


def _fetch_rs(ticker: str, yf: object) -> float:
    """Sector-relative price return over 14 trading days, normalized to [0, 1].

    Returns 0.0 on any failure or when fewer than 14 days of history exist.
    Negative excess return (underperforming sector) is floored at 0 — the
    absence of market confirmation is neutral for the score, not negative.
    """
    try:
        sector: str = (yf.Ticker(ticker).info or {}).get("sector", "")  # type: ignore[attr-defined]
        benchmark: str = _SECTOR_ETF.get(sector, _DEFAULT_BENCHMARK)

        hist = yf.download(  # type: ignore[attr-defined]
            [ticker, benchmark],
            period="25d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )
        if hist.empty:
            return 0.0

        try:
            t_close = hist[ticker]["Close"].dropna()
            b_close = hist[benchmark]["Close"].dropna()
        except KeyError:
            return 0.0

        if len(t_close) < 14 or len(b_close) < 14:
            return 0.0

        t_ret: float = float(t_close.iloc[-1] / t_close.iloc[-14]) - 1.0
        b_ret: float = float(b_close.iloc[-1] / b_close.iloc[-14]) - 1.0
        excess: float = t_ret - b_ret
        return max(0.0, min(excess / _RS_CAP, 1.0))
    except Exception as exc:
        logger.warning("RS_14d fetch failed for %s: %s", ticker, exc)
        return 0.0


def _fetch_opt(ticker: str, yf: object) -> float:
    """Call-volume / call-OI ratio for the nearest expiry, normalized to [0, 1].

    Uses the first available expiration date (shortest-tenor liquid chain).
    Returns 0.0 when there are no options, zero OI, or on any error.
    """
    try:
        t_obj = yf.Ticker(ticker)  # type: ignore[attr-defined]
        expirations: tuple[str, ...] = t_obj.options or ()
        if not expirations:
            return 0.0

        chain = t_obj.option_chain(expirations[0])
        calls = chain.calls
        if calls is None or calls.empty:
            return 0.0

        total_vol: float = float(calls["volume"].fillna(0).sum())
        total_oi: float = float(calls["openInterest"].fillna(0).sum())
        if total_oi <= 0:
            return 0.0

        ratio: float = total_vol / total_oi
        return min(ratio / _OPT_CAP, 1.0)
    except Exception as exc:
        logger.warning("opt_ratio fetch failed for %s: %s", ticker, exc)
        return 0.0


def _fetch_institutional(ticker: str, yf: object) -> float:
    """Net institutional buying signal normalized to [0, 1].

    Sums the 'Change' column (share-count delta since last 13F filing) across
    the top institutional holders returned by yfinance, then divides by total
    shares held to get a net percentage.  Positive = net buying.

    Returns 0.0 when holders data is unavailable or on any error.
    """
    try:
        t_obj = yf.Ticker(ticker)  # type: ignore[attr-defined]
        holders = t_obj.institutional_holders
        if holders is None or holders.empty:
            return 0.0

        if "Change" not in holders.columns or "Shares" not in holders.columns:
            return 0.0

        net_change: float = float(holders["Change"].fillna(0).sum())
        total_shares: float = float(holders["Shares"].fillna(0).sum())
        if total_shares <= 0:
            return 0.0

        net_pct: float = net_change / total_shares  # positive = net buying
        return max(0.0, min(net_pct / _INST_CAP, 1.0))
    except Exception as exc:
        logger.warning("institutional_13f fetch failed for %s: %s", ticker, exc)
        return 0.0
