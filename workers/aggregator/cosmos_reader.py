"""Read signals from Cosmos DB `signals` container for aggregation."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Rolling window used for both ticker discovery and signal fetch.
_WINDOW_DAYS = 30


class CosmosReader:
    def __init__(self, endpoint: str, database: str = "narrative") -> None:
        credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=credential)
        self._signals = (
            self._client
            .get_database_client(database)
            .get_container_client("signals")
        )

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def distinct_tickers_last_30d(self, reference_date: date) -> list[str]:
        """Return distinct tickers that have at least one signal in the last 30 days.

        Raises on Cosmos failure after 3 retries so the Container Apps Job exits
        non-zero and triggers an alert — rather than silently writing nothing.
        """
        cutoff_utc = _date_to_utc_epoch(reference_date - timedelta(days=_WINDOW_DAYS))
        query = (
            "SELECT DISTINCT VALUE c.ticker FROM c "
            "WHERE c.createdUtc >= @cutoff"
        )
        params = [{"name": "@cutoff", "value": cutoff_utc}]
        results = list(
            self._signals.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
        return [r for r in results if r]

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def signals_for_ticker(self, ticker: str, reference_date: date) -> list[dict]:
        """Return all signals for a ticker in the last 30 days, ordered ascending."""
        cutoff_utc = _date_to_utc_epoch(reference_date - timedelta(days=_WINDOW_DAYS))
        query = (
            "SELECT c.ticker, c.sentiment, c.confidence, c.rationale, "
            "c.authorHash, c.createdUtc, c.subreddit "
            "FROM c "
            "WHERE c.ticker = @ticker AND c.createdUtc >= @cutoff "
            "ORDER BY c.createdUtc ASC"
        )
        params = [
            {"name": "@ticker", "value": ticker},
            {"name": "@cutoff", "value": cutoff_utc},
        ]
        results = list(
            self._signals.query_items(
                query=query,
                parameters=params,
                partition_key=ticker,
            )
        )
        # Normalise field names to match attention.build_snapshot expectations.
        normalised = []
        for doc in results:
            normalised.append({
                "ticker": doc.get("ticker", ticker),
                "sentiment": doc.get("sentiment", "neutral"),
                "confidence": float(doc.get("confidence", 0.0)),
                "rationale": doc.get("rationale", ""),
                "author_hash": doc.get("authorHash", ""),
                "created_utc": int(doc.get("createdUtc", 0)),
                "flair": doc.get("flair"),
            })
        return normalised


def _date_to_utc_epoch(d: date) -> int:
    """Return the UTC midnight Unix timestamp for a calendar date."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
