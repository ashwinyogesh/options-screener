"""SEC EDGAR HTTP client.

Wraps the two endpoints we care about:
  - https://www.sec.gov/files/company_tickers.json (ticker → CIK map)
  - https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json

Fair-use rules: max 10 req/sec; we sleep `min_interval_s` between calls.
A descriptive User-Agent identifying the operator is mandatory per SEC policy
(set EDGAR_USER_AGENT or pass `user_agent=` to the constructor).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)


class EdgarUnavailable(RuntimeError):
    """Raised when an EDGAR call fails permanently (network, 4xx, 5xx)."""


_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_DEFAULT_UA = "Options-Screener research@example.com"


class EdgarFetcher:
    """Synchronous EDGAR client. One instance per process is fine.

    Attributes:
      user_agent: identifies the caller per SEC fair-use policy.
      min_interval_s: minimum seconds between successive HTTP calls.
      timeout_s: per-request timeout.
    """

    def __init__(
        self,
        user_agent: str | None = None,
        min_interval_s: float = 0.15,
        timeout_s: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.user_agent = user_agent or os.getenv("EDGAR_USER_AGENT") or _DEFAULT_UA
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self._session = session or requests.Session()
        self._session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        })
        self._last_call_ts: float = 0.0

    # ------------------------------------------------------------------ public
    def fetch_cik_map(self) -> dict[str, str]:
        """Return {TICKER_UPPER: '0001234567'} from SEC's master file."""
        raw = self._get_json(_TICKER_MAP_URL)
        if not isinstance(raw, dict):
            raise EdgarUnavailable("ticker map: unexpected payload shape")
        out: dict[str, str] = {}
        for entry in raw.values():
            try:
                out[str(entry["ticker"]).upper()] = f"{int(entry['cik_str']):010d}"
            except (KeyError, TypeError, ValueError):
                continue
        if not out:
            raise EdgarUnavailable("ticker map: no entries parsed")
        return out

    def fetch_companyfacts(self, cik10: str) -> dict[str, Any] | None:
        """Fetch companyfacts JSON for a 10-digit CIK. Returns None on 404."""
        url = _FACTS_URL_TEMPLATE.format(cik=cik10)
        return self._get_json(url, allow_404=True)

    # ----------------------------------------------------------------- helpers
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_ts
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._last_call_ts = time.monotonic()

    def _get_json(self, url: str, *, allow_404: bool = False) -> dict[str, Any] | None:
        last_exc: Exception | None = None
        for attempt in range(3):
            self._throttle()
            try:
                resp = self._session.get(url, timeout=self.timeout_s)
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("edgar HTTP error (attempt %d): %s", attempt + 1, exc)
                time.sleep(1.0 + attempt)
                continue
            if resp.status_code == 404 and allow_404:
                return None
            if resp.status_code == 429:
                logger.warning("edgar 429 rate limited; backing off")
                time.sleep(2 ** attempt)
                continue
            if resp.status_code >= 500:
                last_exc = EdgarUnavailable(f"{url}: HTTP {resp.status_code}")
                time.sleep(1.0 + attempt)
                continue
            if resp.status_code != 200:
                raise EdgarUnavailable(f"{url}: HTTP {resp.status_code}")
            try:
                return resp.json()
            except ValueError as exc:
                raise EdgarUnavailable(f"{url}: invalid JSON: {exc}") from exc
        raise EdgarUnavailable(f"{url}: exhausted retries ({last_exc})")
