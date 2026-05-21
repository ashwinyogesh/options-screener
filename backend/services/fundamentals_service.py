"""High-level fundamentals facade.

This is the only fundamentals API that scorers / services should use.
It hides the cache + fetcher + extractor wiring behind one process-singleton
and one synchronous call:

    from services.fundamentals_service import get_pit_factors
    factors = get_pit_factors("AAPL", date.today(), spot_price=187.50)
    # -> dict with keys from edgar.PIT_FACTORS, values are float | None

The first call for each ticker downloads from SEC; subsequent calls within
`refresh_after_days` (default 7) hit the local cache. Network failures fall
back to whatever stale data is on disk; if there is no disk data, factor
values are returned as None (callers must tolerate missing fundamentals).
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any

from services.edgar import (
    DiskFundamentalsCache,
    EdgarFetcher,
    EdgarUnavailable,
    FundamentalsCache,
    PIT_FACTORS,
    compute_pit_factors,
)
from services.edgar.extractor import latest_filing_lag_days

logger = logging.getLogger(__name__)

__all__ = [
    "PIT_FACTORS",
    "configure",
    "get_pit_factors",
    "get_pit_factors_with_lag",
    "prefetch",
]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_cache: FundamentalsCache | None = None
_fetcher: EdgarFetcher | None = None
_refresh_after_s: int = 7 * 24 * 3600  # one week
_cik_lock = threading.Lock()
_cik_map: dict[str, str] | None = None


def _default_cache_dir() -> Path:
    """Cache root: env override > repo-level data/edgar/ > ./data/edgar."""
    override = os.getenv("EDGAR_CACHE_DIR")
    if override:
        return Path(override)
    # backend/services/fundamentals_service.py -> repo root is parents[2]
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "data" / "edgar"


def configure(
    cache: FundamentalsCache | None = None,
    fetcher: EdgarFetcher | None = None,
    refresh_after_days: float | None = None,
) -> None:
    """Override the module-level singletons (tests / app startup).

    Calling with all-None initialises the disk-backed defaults if not yet set.
    """
    global _cache, _fetcher, _refresh_after_s, _cik_map
    with _lock:
        if cache is not None:
            _cache = cache
        elif _cache is None:
            _cache = DiskFundamentalsCache(_default_cache_dir())
        if fetcher is not None:
            _fetcher = fetcher
        elif _fetcher is None:
            _fetcher = EdgarFetcher()
        if refresh_after_days is not None:
            _refresh_after_s = int(refresh_after_days * 24 * 3600)
        # Reset the CIK memo when cache changes; force a reload on first use.
        if cache is not None:
            _cik_map = None


def _ensure_configured() -> tuple[FundamentalsCache, EdgarFetcher]:
    if _cache is None or _fetcher is None:
        configure()
    assert _cache is not None and _fetcher is not None
    return _cache, _fetcher


# ---------------------------------------------------------------------------
# CIK map (process-cached)
# ---------------------------------------------------------------------------

def _load_cik_map() -> dict[str, str]:
    global _cik_map
    if _cik_map is not None:
        return _cik_map
    cache, fetcher = _ensure_configured()
    with _cik_lock:
        if _cik_map is not None:
            return _cik_map
        existing = cache.get_cik_map()
        if existing:
            _cik_map = existing
            return _cik_map
        try:
            mapping = fetcher.fetch_cik_map()
        except EdgarUnavailable as exc:
            logger.warning("CIK map fetch failed: %s", exc)
            return {}
        cache.put_cik_map(mapping)
        _cik_map = mapping
        return _cik_map


# ---------------------------------------------------------------------------
# Companyfacts (cached + lazy fetched)
# ---------------------------------------------------------------------------

def _get_companyfacts(ticker: str) -> dict[str, Any] | None:
    cache, fetcher = _ensure_configured()
    ticker = ticker.upper()
    cached = cache.get_companyfacts(ticker)
    age = cache.age_seconds(ticker)
    fresh_enough = cached is not None and age is not None and age < _refresh_after_s
    if fresh_enough:
        return cached

    cik = _load_cik_map().get(ticker)
    if not cik:
        # Common for ETFs / non-SEC-filers; return whatever we have (likely None).
        return cached

    try:
        payload = fetcher.fetch_companyfacts(cik)
    except EdgarUnavailable as exc:
        logger.warning(
            "companyfacts fetch failed for %s; using stale cache (age=%ss): %s",
            ticker, age, exc,
        )
        return cached

    if payload is None:
        return cached  # 404 — keep whatever we had (probably None)
    cache.put_companyfacts(ticker, payload)
    return payload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _empty_factors() -> dict[str, float | None]:
    return {k: None for k in PIT_FACTORS}


def get_pit_factors(
    ticker: str,
    asof: date,
    spot_price: float | None = None,
) -> dict[str, float | None]:
    """Return PIT fundamental factors for `ticker` as of `asof`.

    Missing factors are returned as None — callers must tolerate gaps.
    `spot_price` enables market-cap-based ratios (PS, EV/EBITDA, FCF yield).
    """
    facts = _get_companyfacts(ticker)
    if not facts:
        return _empty_factors()
    return compute_pit_factors(facts, asof, spot_price=spot_price)


def get_pit_factors_with_lag(
    ticker: str,
    asof: date,
    spot_price: float | None = None,
) -> tuple[dict[str, float | None], int | None]:
    """Same as `get_pit_factors` but also returns the filing-lag in days."""
    facts = _get_companyfacts(ticker)
    if not facts:
        return _empty_factors(), None
    factors = compute_pit_factors(facts, asof, spot_price=spot_price)
    lag = latest_filing_lag_days(facts, asof)
    return factors, lag


def prefetch(tickers: list[str]) -> dict[str, str]:
    """Eagerly populate the cache for `tickers`. Intended for nightly refresh.

    Returns a per-ticker status string: 'ok', 'cached', 'no_cik', or
    'fetch_failed'. Safe to call from a long-running script; respects SEC
    rate limits via the fetcher's internal throttle.
    """
    cache, fetcher = _ensure_configured()
    cik_map = _load_cik_map()
    statuses: dict[str, str] = {}
    for raw_ticker in tickers:
        t = raw_ticker.upper()
        age = cache.age_seconds(t)
        if age is not None and age < _refresh_after_s:
            statuses[t] = "cached"
            continue
        cik = cik_map.get(t)
        if not cik:
            statuses[t] = "no_cik"
            continue
        try:
            payload = fetcher.fetch_companyfacts(cik)
        except EdgarUnavailable:
            statuses[t] = "fetch_failed"
            continue
        if payload is None:
            statuses[t] = "no_cik"  # 404 from the company-facts endpoint
            continue
        cache.put_companyfacts(t, payload)
        statuses[t] = "ok"
    return statuses
