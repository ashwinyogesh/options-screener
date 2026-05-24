"""SEC EDGAR companyfacts integration.

Provides PIT (point-in-time) fundamentals derived from XBRL filings.

Layering:
  fetcher  — talks to data.sec.gov / www.sec.gov (HTTP)
  cache    — persists raw companyfacts JSON (disk now, Cosmos later)
  extractor — pure functions: companyfacts JSON + asof date → factor dict

External callers should use `services.fundamentals_service` rather than
importing from this package directly.
"""
from services.edgar.cache import DiskFundamentalsCache, FundamentalsCache
from services.edgar.extractor import (
    PIT_FACTORS,
    RAW_TTM_FIELDS,
    compute_pit_factors,
    compute_raw_ttm_fundamentals,
)
from services.edgar.fetcher import EdgarFetcher, EdgarUnavailable

__all__ = [
    "DiskFundamentalsCache",
    "EdgarFetcher",
    "EdgarUnavailable",
    "FundamentalsCache",
    "PIT_FACTORS",
    "RAW_TTM_FIELDS",
    "compute_pit_factors",
    "compute_raw_ttm_fundamentals",
]
