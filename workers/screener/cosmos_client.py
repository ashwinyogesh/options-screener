"""Cosmos DB client for the screener precomputation worker (ADR-0024).

Writes: screener_csp / screener_cc / screener_ditm — one doc per ticker.

Doc shape:
    {
      "id": "<ticker>",
      "ticker": "<ticker>",
            "run_id": "<ISO UTC run timestamp>",
      "computed_at": "<ISO UTC>",
      "result": { ...serialised result dict... } | null,
      "error": null | "reason string"
    }

For DITM, macro fields are stamped at the top level of the doc:
    "macro_pass": bool,
    "vix_level": float | null,
    "vix_5d_change": float | null,
    "spy_above_sma200": bool
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_CONTAINER_MAP = {
    "csp": "screener_csp",
    "cc": "screener_cc",
    "ditm": "screener_ditm",
    "swing": "screener_swing",
}


class ScreenerCosmosClient:
    def __init__(self, endpoint: str, database: str, strategy: str) -> None:
        credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=credential)
        self._db = self._client.get_database_client(database)
        container_name = _CONTAINER_MAP[strategy]
        self._container = self._db.get_container_client(container_name)

    # ------------------------------------------------------------------
    # Freshness check — cross-partition COUNT query on the container.
    # ------------------------------------------------------------------

    def is_fresh(self, threshold_seconds: int) -> bool:
        """Return True if at least one doc exists and the oldest is within threshold."""
        try:
            query = (
                "SELECT TOP 1 c.computed_at FROM c "
                "ORDER BY c.computed_at ASC"
            )
            items = list(
                self._container.query_items(
                    query=query,
                    enable_cross_partition_query=True,
                )
            )
            if not items:
                return False
            oldest_str: str = items[0]["computed_at"]
            oldest = datetime.fromisoformat(oldest_str.replace("Z", "+00:00"))
            age_s = (datetime.now(tz=timezone.utc) - oldest).total_seconds()
            return age_s < threshold_seconds
        except Exception:
            logger.warning("Freshness check failed — treating as stale", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Write: upsert one ticker doc.
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def upsert_result(
        self,
        ticker: str,
        result: dict[str, Any] | None,
        error: str | None,
        macro_fields: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> None:
        """Upsert a per-ticker precomputed result doc."""
        doc: dict[str, Any] = {
            "id": ticker,
            "ticker": ticker,
            "run_id": run_id,
            "computed_at": datetime.now(tz=timezone.utc).isoformat(),
            "result": result,
            "error": error,
        }
        if macro_fields:
            doc.update(macro_fields)
        self._container.upsert_item(doc)

    def prune_stale_runs(self, keep_run_id: str) -> int:
        """Delete docs whose run_id does not match keep_run_id.

        Legacy docs without run_id are also deleted. Returns delete count.
        """
        query = (
            "SELECT c.id, c.ticker FROM c "
            "WHERE NOT IS_DEFINED(c.run_id) OR c.run_id != @run_id"
        )
        victims = list(
            self._container.query_items(
                query=query,
                parameters=[{"name": "@run_id", "value": keep_run_id}],
                enable_cross_partition_query=True,
            )
        )

        deleted = 0
        for doc in victims:
            item_id = doc.get("id")
            pk = doc.get("ticker") or item_id
            if not item_id or not pk:
                continue
            try:
                self._container.delete_item(item=item_id, partition_key=pk)
                deleted += 1
            except Exception:
                logger.warning(
                    "Failed deleting stale doc id=%s pk=%s", item_id, pk, exc_info=True
                )
        return deleted

    # ------------------------------------------------------------------
    # Read: fetch all docs for a list of tickers (point reads — cheap).
    # ------------------------------------------------------------------

    def fetch_results(self, tickers: list[str]) -> list[dict[str, Any]]:
        """Return docs for the given tickers using per-partition point reads.

        Tickers with no doc are silently skipped; the caller handles the
        gap as a missing/stale ticker.
        """
        docs = []
        for ticker in tickers:
            try:
                doc = self._container.read_item(item=ticker, partition_key=ticker)
                docs.append(doc)
            except Exception:
                # 404 or transient — treat as missing
                logger.debug("No precomputed doc for ticker %s", ticker)
        return docs
