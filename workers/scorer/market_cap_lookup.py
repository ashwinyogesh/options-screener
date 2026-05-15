"""Market-cap lookup for the §5.3 small-cap haircut.

yfinance is invoked once per ticker per scorer run; results are cached in
process so re-asks (within the same run) are free. The cache is intentionally
not persisted — market cap drift inside a 15-min cron window is negligible,
and rehydrating from a stale snapshot would risk false-negative haircuts.

Failure modes are non-fatal: a yfinance error, a missing field, or a non-
numeric value all return None, leaving the scorer to skip the haircut for
that ticker. This is safer than failing a whole run on a third-party API hiccup.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_cache: dict[str, Optional[float]] = {}


def get_market_cap(ticker: str) -> Optional[float]:
    """Return the latest market cap in USD, or None on any failure."""
    cached = _cache.get(ticker)
    if ticker in _cache:
        return cached

    value: Optional[float] = None
    try:
        # Imported lazily so the module is testable without yfinance installed.
        import yfinance as yf  # noqa: PLC0415

        info = yf.Ticker(ticker).fast_info
        # fast_info uses snake_case in 0.2.x; fall back to .info if absent.
        raw = info.get("market_cap") if hasattr(info, "get") else None
        if raw is None:
            full = yf.Ticker(ticker).info or {}
            raw = full.get("marketCap")
        if raw is not None:
            value = float(raw)
    except Exception as exc:  # pragma: no cover — observed at runtime
        logger.warning("market_cap lookup failed for %s: %s", ticker, exc)
        value = None

    _cache[ticker] = value
    return value


def reset_cache() -> None:
    """Clear the in-process cache. Used by tests."""
    _cache.clear()
