"""Cosmos DB client for the narrative-detector worker (Phase 5).

Reads:
  - signals container: 72-hour embedding window per ticker
  - ticker_timeline container: current snapshot for lifecycle rule inputs

Writes:
  - ticker_timeline: lifecycle_stage + stage_confidence on the today bucket doc
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class DetectorCosmosClient:
    def __init__(self, endpoint: str, database: str = "narrative") -> None:
        credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=credential)
        self._db = self._client.get_database_client(database)
        self._signals = self._db.get_container_client("signals")
        self._timeline = self._db.get_container_client("ticker_timeline")

    # ------------------------------------------------------------------
    # Read: tickers that have at least one embedded + classified signal
    # in the look-back window.
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_active_tickers(self, window_hours: int) -> list[str]:
        """Return distinct tickers with embedded signals in the look-back window."""
        cutoff_ts = int(
            (datetime.now(tz=timezone.utc) - timedelta(hours=window_hours)).timestamp()
        )
        query = (
            "SELECT DISTINCT VALUE c.ticker FROM c "
            "WHERE c._ts >= @cutoff "
            "AND IS_DEFINED(c.embedding) AND c.embedding != null "
            "AND IS_DEFINED(c.conviction_state)"
        )
        params = [{"name": "@cutoff", "value": cutoff_ts}]
        return list(
            self._signals.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )

    # ------------------------------------------------------------------
    # Read: signals for one ticker in the window.
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_signals_for_ticker(
        self,
        ticker: str,
        window_hours: int,
    ) -> list[dict]:
        """Return signal documents for ticker with non-null embedding in window.

        Returned fields: id, ticker, embedding, conviction_state, _ts.
        Full doc returned for simplicity — detector only reads listed fields.
        """
        cutoff_ts = int(
            (datetime.now(tz=timezone.utc) - timedelta(hours=window_hours)).timestamp()
        )
        query = (
            "SELECT * FROM c "
            "WHERE c.ticker = @ticker "
            "AND c._ts >= @cutoff "
            "AND IS_DEFINED(c.embedding) AND c.embedding != null "
            "AND IS_DEFINED(c.conviction_state)"
        )
        params = [
            {"name": "@ticker", "value": ticker},
            {"name": "@cutoff", "value": cutoff_ts},
        ]
        return list(
            self._signals.query_items(
                query=query,
                parameters=params,
            )
        )

    # ------------------------------------------------------------------
    # Read: today's ticker_timeline doc for lifecycle rule inputs.
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_timeline_doc(self, ticker: str, bucket_date: str) -> dict | None:
        """Fetch today's ticker_timeline doc. Returns None if not yet created."""
        doc_id = f"{ticker}_{bucket_date}"
        try:
            return self._timeline.read_item(item=doc_id, partition_key=ticker)
        except Exception as exc:
            # Cosmos raises CosmosResourceNotFoundError (404) when missing.
            if "404" in str(exc) or "NotFound" in type(exc).__name__:
                return None
            raise

    # ------------------------------------------------------------------
    # Write: lifecycle fields onto the ticker_timeline doc.
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def write_lifecycle(
        self,
        ticker: str,
        bucket_date: str,
        lifecycle_stage: int,
        stage_confidence: float,
    ) -> None:
        """Patch lifecycle_stage and stage_confidence onto the timeline doc.

        If the doc doesn't exist yet (aggregator hasn't run this bucket),
        creates a minimal stub so the lifecycle data is never lost.
        """
        doc = self.fetch_timeline_doc(ticker, bucket_date)
        if doc is None:
            doc = {
                "id": f"{ticker}_{bucket_date}",
                "ticker": ticker,
                "bucket_date": bucket_date,
            }
        doc["lifecycle_stage"] = lifecycle_stage
        doc["stage_confidence"] = stage_confidence
        self._timeline.upsert_item(doc)
        logger.debug(
            "%s [%s] → stage=%d confidence=%.2f",
            ticker, bucket_date, lifecycle_stage, stage_confidence,
        )
