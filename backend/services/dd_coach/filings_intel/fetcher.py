"""Filing fetcher — SEC EDGAR + on-disk cache + section extraction.

Single class that wraps :class:`SecDataClient` and adds:
  - on-disk caching of raw filing HTML keyed by accession#, under
    ``$DD_FILINGS_CACHE_DIR`` (default ``data/dd_filings_cache``);
  - prior-year 10-K lookup (for risk-factor diffing);
  - DEF 14A primary-doc fetch;
  - Form 4 recent-filings metadata roll-up (counts + dates).

Network access is gated by the same ``SecDataClient`` retry/timeout policy
used by supply_chain — we deliberately do NOT introduce a second client.

Tests inject a fake by passing ``sec_client=`` to the constructor.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from services.dd_coach.filings_intel.errors import FilingNotFound, FilingsIntelUnavailable
from services.dd_coach.filings_intel.sections import (
    FilingSections,
    extract_10q_mda,
    extract_proxy_text,
    extract_sections,
    strip_to_text,
)
from services.supply_chain.sec_client import SecDataClient, get_default_client

logger = logging.getLogger(__name__)


def _cache_root() -> Path:
    root = Path(os.getenv("DD_FILINGS_CACHE_DIR", "data/dd_filings_cache"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _accession_to_path(accession: str, ext: str = "html") -> Path:
    safe = accession.replace("/", "_").replace("\\", "_")
    return _cache_root() / f"{safe}.{ext}"


def _read_cached(accession: str) -> Optional[str]:
    p = _accession_to_path(accession)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("filings_intel: failed to read cache %s (%s)", p, exc)
        return None


def _write_cached(accession: str, body: str) -> None:
    try:
        _accession_to_path(accession).write_text(body, encoding="utf-8")
    except OSError as exc:
        logger.warning("filings_intel: failed to write cache for %s (%s)", accession, exc)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingRef:
    accession: str
    filing_date: str
    primary_doc_url: str
    form: str


@dataclass(frozen=True)
class TenKBundle:
    """A 10-K + its extracted sections + accession metadata."""

    ref: FilingRef
    sections: FilingSections


@dataclass(frozen=True)
class Form4Summary:
    """Roll-up of recent insider trades (Form 4 metadata only).

    We intentionally don't parse the XML payloads — the LLM can reason
    qualitatively about cadence from filing counts and dates.
    """

    window_days: int
    filings_count: int
    most_recent_date: Optional[str]
    filings: list[FilingRef] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


def _to_ref(item: dict[str, Any]) -> FilingRef:
    return FilingRef(
        accession=item["accession"],
        filing_date=item["filing_date"],
        primary_doc_url=item["primary_doc_url"],
        form=item["form"],
    )


class FilingsFetcher:
    """Fetches & caches DD-relevant filings for a single ticker.

    Stateless across tickers — construct (or reuse the module-level
    singleton via :func:`get_default_fetcher`) and call methods with a
    ticker symbol.
    """

    def __init__(self, sec_client: Optional[SecDataClient] = None) -> None:
        # Lazy default keeps test injection clean.
        self._sec = sec_client

    def _client(self) -> SecDataClient:
        if self._sec is None:
            self._sec = get_default_client()
        return self._sec

    # --------------------------------------------------------------- helpers
    def _resolve_cik(self, ticker: str) -> str:
        sym = ticker.strip().upper()
        try:
            cik = self._client().resolve_cik(sym)
        except Exception as exc:  # network/transport — surface as 503
            raise FilingsIntelUnavailable(
                f"SEC ticker map unavailable: {exc}",
            ) from exc
        if not cik:
            raise FilingNotFound(
                f"No SEC CIK known for ticker {sym} — non-US listing or unknown symbol.",
            )
        return cik

    def _list_filings(self, ticker: str, form: str, limit: int = 8) -> list[FilingRef]:
        cik = self._resolve_cik(ticker)
        try:
            _, items = self._client().get_filings_index(cik)
        except Exception as exc:
            raise FilingsIntelUnavailable(
                f"SEC submissions index unavailable: {exc}",
            ) from exc
        out = [_to_ref(it) for it in items if it["form"] == form]
        return out[:limit]

    def _fetch_doc(self, ref: FilingRef) -> str:
        """Return raw HTML for a primary document, using disk cache."""
        cached = _read_cached(ref.accession)
        if cached is not None:
            return cached
        try:
            # We need the *raw* HTML for proper section slicing, so we
            # bypass the supply_chain extraction wrapper and call httpx
            # directly via the client's underlying _http object.
            client = self._client()
            r = client._http.get(ref.primary_doc_url, timeout=60)
            r.raise_for_status()
            body = r.text
        except Exception as exc:
            raise FilingsIntelUnavailable(
                f"SEC document fetch failed ({ref.accession}): {exc}",
            ) from exc
        _write_cached(ref.accession, body)
        return body

    # --------------------------------------------------------------- 10-K
    def get_10k(self, ticker: str, *, prior: bool = False) -> TenKBundle:
        """Return the latest (or prior-year) 10-K + extracted sections."""
        refs = self._list_filings(ticker, "10-K", limit=4)
        if not refs:
            raise FilingNotFound(f"No 10-K filings found for {ticker}")
        idx = 1 if prior else 0
        if idx >= len(refs):
            raise FilingNotFound(
                f"No prior-year 10-K available for {ticker} (only {len(refs)} on file).",
            )
        ref = refs[idx]
        html = self._fetch_doc(ref)
        return TenKBundle(ref=ref, sections=extract_sections(html))

    # --------------------------------------------------------------- 10-Q
    def get_10q_mda(self, ticker: str) -> tuple[FilingRef, str]:
        refs = self._list_filings(ticker, "10-Q", limit=4)
        if not refs:
            raise FilingNotFound(f"No 10-Q filings found for {ticker}")
        ref = refs[0]
        html = self._fetch_doc(ref)
        return ref, extract_10q_mda(html)

    # --------------------------------------------------------------- DEF 14A
    def get_def14a(self, ticker: str) -> tuple[FilingRef, str]:
        refs = self._list_filings(ticker, "DEF 14A", limit=4)
        if not refs:
            raise FilingNotFound(f"No DEF 14A filings found for {ticker}")
        ref = refs[0]
        html = self._fetch_doc(ref)
        return ref, extract_proxy_text(html)

    # --------------------------------------------------------------- Form 4
    def get_form4_summary(self, ticker: str, *, window_days: int = 180) -> Form4Summary:
        """Return a metadata roll-up of recent Form 4 filings (no XML parse)."""
        cik = self._resolve_cik(ticker)
        try:
            _, items = self._client().get_filings_index(cik)
        except Exception as exc:
            raise FilingsIntelUnavailable(
                f"SEC submissions index unavailable: {exc}",
            ) from exc
        cutoff = date.today() - timedelta(days=window_days)
        recent: list[FilingRef] = []
        for it in items:
            if it["form"] != "4":
                continue
            try:
                d = datetime.strptime(it["filing_date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if d >= cutoff:
                recent.append(_to_ref(it))
        recent.sort(key=lambda r: r.filing_date, reverse=True)
        return Form4Summary(
            window_days=window_days,
            filings_count=len(recent),
            most_recent_date=recent[0].filing_date if recent else None,
            filings=recent[:50],  # cap so the LLM prompt stays bounded
        )


# ---------------------------------------------------------------------------
# Module-level default
# ---------------------------------------------------------------------------

_default: Optional[FilingsFetcher] = None


def get_default_fetcher() -> FilingsFetcher:
    global _default
    if _default is None:
        _default = FilingsFetcher()
    return _default


def reset_for_tests() -> None:
    """Test helper — clear the module-level singleton."""
    global _default
    _default = None
