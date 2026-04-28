"""SEC EDGAR HTTP adapter.

Encapsulates all SEC.gov / data.sec.gov network access behind a single
class. The instance owns one ``httpx.Client`` (connection-pooled,
keep-alive friendly) and an instance-level ticker→CIK cache. Tests
inject a fake by passing ``http_client`` (or by monkeypatching method
results); production code uses :func:`get_default_client` for a
process-wide singleton.

The architect-approved Phase 1 design keeps retry policy out of scope —
Phase 2 wires tenacity around the single ``httpx.Client``.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .text_extraction import extract_8k_text, extract_10k_relevant_text
from .types import EightKFetchResult

logger = logging.getLogger(__name__)


def _default_user_agent() -> str:
    # SEC requires a real contact in the User-Agent header. Production
    # deploys must set SEC_USER_AGENT; the literal default exists only
    # so dev runs without env config don't crash.
    return os.getenv("SEC_USER_AGENT", "Options Screener app@example.com")


def _log_retry(state: RetryCallState) -> None:
    if state.outcome is None or state.next_action is None:
        return
    exc = state.outcome.exception()
    logger.warning(
        "SEC fetch attempt %d failed (%s: %s); retrying in %.1fs",
        state.attempt_number,
        type(exc).__name__,
        exc,
        state.next_action.sleep,
    )


# Retry on transport-layer + 5xx-ish failures only. We deliberately do NOT
# retry on httpx.HTTPStatusError because callers convert 4xx into ValueError
# at higher layers (e.g. resolve_cik returning None).
_RETRY_EXCEPTIONS = (httpx.TransportError, httpx.TimeoutException)
_sec_retry = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
    retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    before_sleep=_log_retry,
)


class SecDataClient:
    """Thin wrapper around the SEC EDGAR endpoints we use."""

    def __init__(
        self,
        *,
        user_agent: Optional[str] = None,
        timeout: float = 30.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._user_agent = user_agent or _default_user_agent()
        self._headers = {
            "User-Agent": self._user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        self._timeout = timeout
        # When the caller passes a client, they own its lifecycle.
        self._owns_client = http_client is None
        self._http: httpx.Client = http_client or httpx.Client(
            timeout=timeout,
            headers=self._headers,
            follow_redirects=True,
        )
        self._ticker_map: Optional[dict[str, str]] = None

    # --------------------------------------------------------------- lifecycle
    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "SecDataClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------- ticker map
    @_sec_retry
    def get_company_tickers(self) -> dict[str, str]:
        """Return cached ticker→CIK map; fetches once per instance."""
        if self._ticker_map is not None:
            return self._ticker_map
        url = "https://www.sec.gov/files/company_tickers.json"
        r = self._http.get(url)
        r.raise_for_status()
        data = r.json()
        mapping: dict[str, str] = {}
        for entry in data.values():
            t = entry["ticker"].upper()
            cik = str(entry["cik_str"]).zfill(10)
            mapping[t] = cik
        self._ticker_map = mapping
        return mapping

    def resolve_cik(self, ticker: str) -> Optional[str]:
        return self.get_company_tickers().get(ticker.upper())

    # ----------------------------------------------------------- filings index
    @_sec_retry
    def get_filings_index(self, cik: str) -> tuple[str, list[dict]]:
        """Return (company_name, list of recent filings) for a CIK."""
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        r = self._http.get(url)
        r.raise_for_status()
        data = r.json()
        company_name = data.get("name", "")
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])
        items: list[dict] = []
        for i, form in enumerate(forms):
            accession_clean = accessions[i].replace("-", "")
            items.append({
                "form": form,
                "accession": accessions[i],
                "filing_date": dates[i],
                "primary_doc_url": (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{accession_clean}/{primary_docs[i]}"
                ),
            })
        return company_name, items

    def get_latest_10k(self, cik: str) -> Optional[dict]:
        """Return ``{accession, filing_date, primary_doc_url, company_name}`` for the latest 10-K."""
        company_name, items = self.get_filings_index(cik)
        for item in items:
            if item["form"] == "10-K":
                return {**item, "company_name": company_name}
        return None

    def get_recent_8ks(
        self, cik: str, since_date: str, max_count: int = 8
    ) -> list[dict]:
        """Return up to ``max_count`` 8-Ks filed on/after ``since_date`` (YYYY-MM-DD)."""
        _, items = self.get_filings_index(cik)
        cutoff = datetime.strptime(since_date, "%Y-%m-%d").date()
        out: list[dict] = []
        for item in items:
            if item["form"] != "8-K":
                continue
            try:
                d = datetime.strptime(item["filing_date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if d >= cutoff:
                out.append(item)
            if len(out) >= max_count:
                break
        return out

    # ------------------------------------------------------------- filing text
    @_sec_retry
    def fetch_filing_text(self, url: str) -> str:
        """Fetch a 10-K primary document and return the relevant slice."""
        # 10-K bodies can be large; honour the per-call 60s ceiling that
        # the legacy code used regardless of the constructor timeout.
        r = self._http.get(url, timeout=60)
        r.raise_for_status()
        return extract_10k_relevant_text(r.text)

    @_sec_retry
    def fetch_8k_text(self, url: str, max_chars: int = 30_000) -> str:
        """Fetch an 8-K primary document and return its stripped text."""
        r = self._http.get(url)
        r.raise_for_status()
        return extract_8k_text(r.text, max_chars=max_chars)

    def fetch_8ks_parallel(
        self,
        items: list[dict],
        max_workers: int = 4,
    ) -> EightKFetchResult:
        """Fetch many 8-Ks concurrently. Failures are counted, not raised.

        Order of ``successful`` mirrors the order of ``items``;
        items whose fetch raises are omitted. ``httpx.Client`` is
        thread-safe for concurrent sync use, so the shared client is
        reused across worker threads.
        """
        if not items:
            return EightKFetchResult(successful=[], failed_count=0)

        # ThreadPoolExecutor.map preserves input order in the output
        # iterator, but we want per-item exception isolation. Submit
        # individually and zip results back to metadata.
        def _fetch(meta: dict) -> Optional[str]:
            try:
                return self.fetch_8k_text(meta["primary_doc_url"])
            except (httpx.HTTPError, RuntimeError) as e:
                # tenacity has already retried transport errors; what arrives
                # here is either an exhausted-retry transport failure, an
                # HTTPStatusError (4xx/5xx), or a stub-injected RuntimeError
                # from the test fakes. Programming errors (KeyError on
                # malformed metadata, parser bugs) propagate.
                logger.warning(
                    "8-K fetch failed for %s: %s", meta.get("accession", "?"), e
                )
                return None

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            texts = list(pool.map(_fetch, items))

        successful: list[tuple[dict, str]] = []
        failed_count = 0
        for meta, text in zip(items, texts, strict=True):
            if text is None:
                failed_count += 1
            else:
                successful.append((meta, text))
        return EightKFetchResult(successful=successful, failed_count=failed_count)


# ----------------------------------------------------------------- singleton
_DEFAULT_CLIENT: Optional[SecDataClient] = None


def get_default_client() -> SecDataClient:
    """Return a process-wide ``SecDataClient`` for legacy callers."""
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = SecDataClient()
    return _DEFAULT_CLIENT
