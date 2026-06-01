"""SEC EDGAR filing URL helpers for DD Coach.

Pure URL construction — no network calls on the hot path. CIK lookups are
delegated to the existing `fundamentals_service._load_cik_map()` cache, which
is already a process-wide singleton primed from disk and SEC's
`company_tickers.json`.

This module is intentionally tiny: the V1 product surfaces *links*, not
parsed filing content. Reading the 10-K is the user's job (see
docs/DD_COACH_METHODOLOGY.md).
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from services import fundamentals_service
from services.dd_coach.errors import DDEntryNotFound

logger = logging.getLogger(__name__)

_BROWSE_BASE = "https://www.sec.gov/cgi-bin/browse-edgar"


@dataclass(frozen=True)
class FilingLinks:
    """SEC EDGAR landing-page URLs for the most common DD filings."""

    ticker: str
    cik: str
    all_filings: str
    latest_10k: str
    latest_10q: str
    latest_8k: str
    proxy_def14a: str
    form4_insider: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _browse_url(cik: str, form_type: str | None = None, count: int = 10) -> str:
    type_q = f"&type={form_type}" if form_type else "&type="
    return (
        f"{_BROWSE_BASE}?action=getcompany&CIK={cik}"
        f"{type_q}&dateb=&owner=include&count={count}"
    )


def get_filing_links(ticker: str) -> FilingLinks:
    """Return EDGAR landing-page URLs for a ticker.

    Raises ``DDEntryNotFound`` when no CIK is known for the symbol — the only
    failure mode (the URL pattern itself is deterministic). The router maps
    this to 404 so the frontend can hide the filings panel cleanly.
    """
    sym = ticker.strip().upper()
    cik_map = fundamentals_service._load_cik_map()
    cik = cik_map.get(sym)
    if not cik:
        raise DDEntryNotFound(
            f"No SEC CIK known for ticker {sym} — non-US listing or unknown symbol.",
        )
    return FilingLinks(
        ticker=sym,
        cik=cik,
        all_filings=_browse_url(cik, count=40),
        latest_10k=_browse_url(cik, "10-K", 10),
        latest_10q=_browse_url(cik, "10-Q", 10),
        latest_8k=_browse_url(cik, "8-K", 10),
        proxy_def14a=_browse_url(cik, "DEF+14A", 10),
        form4_insider=_browse_url(cik, "4", 40),
    )
