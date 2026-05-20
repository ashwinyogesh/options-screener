"""Cosmos DB client for the narrative-detector worker (Phase 5).

Reads:
  - signals container: 72-hour embedding window per ticker
  - ticker_timeline container: current snapshot for lifecycle rule inputs

Writes:
  - ticker_timeline: lifecycle_stage + stage_confidence on the today bucket doc
  - signal_events: append-only forward log on every stage transition
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
        self._signal_events = self._db.get_container_client("signal_events")

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
            "AND IS_DEFINED(c.conviction_direction)"
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

        Returned fields: id, ticker, embedding, conviction_direction, _ts.
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
            "AND IS_DEFINED(c.conviction_direction)"
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
        lifecycle_state: dict | None = None,
        n_embedded: int | None = None,
        dominant_fraction: float | None = None,
    ) -> None:
        """Patch lifecycle_stage / confidence / state onto the timeline doc.

        ``lifecycle_state`` carries the hysteresis + smoothing state defined
        in ``smoothing.LifecycleState`` (ADR-0030).  ``n_embedded`` and
        ``dominant_fraction`` are diagnostic fields surfaced in the drilldown
        UI so users can see why a stage came in at a given confidence.

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
        if lifecycle_state is not None:
            doc["lifecycle_state"] = lifecycle_state
        if n_embedded is not None:
            doc["n_embedded"] = n_embedded
        if dominant_fraction is not None:
            doc["dominant_fraction"] = round(dominant_fraction, 4)
        self._timeline.upsert_item(doc)
        logger.debug(
            "%s [%s] → stage=%d confidence=%.2f",
            ticker, bucket_date, lifecycle_stage, stage_confidence,
        )

    # ------------------------------------------------------------------
    # Read: prior lifecycle state for hysteresis (ADR-0029).
    # ------------------------------------------------------------------

    def fetch_prior_lifecycle(
        self,
        ticker: str,
        today_bucket: str,
    ) -> tuple[int, dict]:
        """Return (prev_stage, prior_state_dict) for hysteresis carry-over.

        Lookup order:
            1. Today's bucket — if an earlier same-day run already wrote a
               ``lifecycle_state``, use it (so smoothing/hysteresis update
               hourly, not just daily).
            2. Yesterday's bucket — if today's hasn't been touched yet.
            3. Cold start — returns (0, {}).

        Returns:
            (prev_stage, prior_state_dict).  ``prior_state_dict`` is the raw
            ``lifecycle_state`` dict shape consumed by
            ``smoothing.LifecycleState.from_doc``.  prev_stage is 0 when no
            prior assignment exists (cold start).
        """
        today_doc = self.fetch_timeline_doc(ticker, today_bucket)
        if today_doc and today_doc.get("lifecycle_state") is not None:
            return (
                int(today_doc.get("lifecycle_stage") or 0),
                today_doc,
            )
        # Fall back to yesterday's bucket.
        try:
            yest_date = (
                datetime.strptime(today_bucket, "%Y-%m-%d").date() - timedelta(days=1)
            ).isoformat()
        except ValueError:
            return 0, {}
        yest_doc = self.fetch_timeline_doc(ticker, yest_date)
        if yest_doc is None:
            return 0, {}
        return int(yest_doc.get("lifecycle_stage") or 0), yest_doc

    # ------------------------------------------------------------------
    # Write: append a stage-transition event to the forward signal log.
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def write_signal_event(self, event: dict) -> None:
        """Upsert a signal-transition event into ``signal_events``.

        Caller is responsible for shaping the doc; this method only does
        the I/O.  Document id should be deterministic in (ticker, event_date,
        run_hour, transition) so re-runs of the same detector hour upsert
        the existing event rather than duplicate it.

        Required fields (validated minimally):
            id, ticker, event_date, prev_stage, new_stage
        """
        for field in ("id", "ticker", "event_date", "prev_stage", "new_stage"):
            if field not in event:
                raise ValueError(f"signal_event missing required field: {field}")
        self._signal_events.upsert_item(event)
        logger.debug(
            "signal_event %s ticker=%s %d→%d",
            event["id"], event["ticker"],
            event["prev_stage"], event["new_stage"],
        )
