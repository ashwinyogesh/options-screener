"""Cosmos DB client for the narrative-backfill worker.

Reads + writes ``signal_events`` only.  Reads filter to docs that still
have at least one null price field and whose ``event_date`` is in the
past (forward bars can only be fetched after they exist).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class BackfillCosmosClient:
    def __init__(self, endpoint: str, database: str = "narrative") -> None:
        credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=credential)
        self._db = self._client.get_database_client(database)
        self._signal_events = self._db.get_container_client("signal_events")

    # ------------------------------------------------------------------
    # Read: events that still need backfilling.
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_unfilled_events(self, *, max_events: int) -> list[dict]:
        """Return signal_events docs that have not finished back-filling.

        Filters:
          * ``backfilled_at`` is null (we mark this only when *all* T+0..T+20
            slots are populated, so the same doc keeps appearing until
            T+20 is in the past)
          * ``event_date`` <= today (UTC) — future events have nothing to
            fetch yet

        Order by ``event_date ASC`` so the oldest, most-likely-to-complete
        docs go first.  Returns at most ``max_events`` rows.
        """
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        query = (
            "SELECT TOP @max_events * FROM c "
            "WHERE (NOT IS_DEFINED(c.backfilled_at) OR c.backfilled_at = null) "
            "AND c.event_date <= @today "
            "ORDER BY c.event_date ASC"
        )
        params = [
            {"name": "@max_events", "value": max_events},
            {"name": "@today", "value": today},
        ]
        return list(
            self._signal_events.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )

    # ------------------------------------------------------------------
    # Write: patch price columns onto an event doc.
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def update_event_prices(
        self,
        event: dict,
        *,
        ticker_prices: dict[str, float | None],
        spy_prices: dict[str, float | None],
        mark_complete: bool,
    ) -> None:
        """Merge fetched price columns into *event* and upsert.

        Only writes non-null values — a null fetch leaves the existing
        column unchanged (which is itself null on first pass) so retries
        on a later run can fill it in.

        ``mark_complete`` is set by the caller when every T+0..T+20 slot
        (both ticker and benchmark) is now populated.  When True,
        ``backfilled_at`` is stamped with the current UTC iso timestamp,
        and the doc stops appearing in subsequent fetches.
        """
        for field, value in ticker_prices.items():
            if value is not None:
                event[field] = round(float(value), 4)
        for field, value in spy_prices.items():
            if value is not None:
                event[field] = round(float(value), 4)
        if mark_complete:
            event["backfilled_at"] = datetime.now(tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        self._signal_events.upsert_item(event)
        logger.debug(
            "backfilled %s ticker_filled=%d spy_filled=%d complete=%s",
            event.get("id"),
            sum(1 for v in ticker_prices.values() if v is not None),
            sum(1 for v in spy_prices.values() if v is not None),
            mark_complete,
        )
