"""Cosmos DB read client for the narrative read_service (Phase 6).

Reads ACS scores and ticker_timeline docs from Cosmos for the FastAPI routes.
No writes — scorer worker owns all writes to ticker_timeline.
"""
from __future__ import annotations

import logging
import os

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

# Module-level client — initialised lazily on first call, reused across requests.
# The endpoint and DB name are intentionally read inside _get_timeline() (not at
# module level) so that load_dotenv() in main.py is always honoured regardless of
# import order or hot-reload timing.
_client: CosmosClient | None = None
_timeline_container = None  # type: ignore[assignment]


def _get_timeline():  # type: ignore[return]
    global _client, _timeline_container
    if _timeline_container is None:
        # Read env vars here (not at module level) so they are always current:
        #   NARRATIVE_COSMOS_ENDPOINT — backend/App Service convention
        #   COSMOS_ENDPOINT           — worker/Bicep convention
        endpoint = os.getenv("NARRATIVE_COSMOS_ENDPOINT") or os.getenv("COSMOS_ENDPOINT", "")
        db_name = os.getenv("NARRATIVE_COSMOS_DB") or os.getenv("COSMOS_DB", "narrative")
        if not endpoint:
            raise RuntimeError(
                "Cosmos endpoint not set: configure NARRATIVE_COSMOS_ENDPOINT "
                "(backend convention) or COSMOS_ENDPOINT (worker convention) "
                "on this process."
            )
        _client = CosmosClient(endpoint, credential=DefaultAzureCredential())
        _timeline_container = (
            _client.get_database_client(db_name)
            .get_container_client("ticker_timeline")
        )
    return _timeline_container


def query_top_acs(limit: int) -> list[dict]:
    """Return up to limit ticker_timeline docs ordered by acs descending.

    ORDER BY is intentionally omitted from the Cosmos query. Cross-partition
    ORDER BY on a non-partition-key field (acs) is unreliable on Cosmos
    Serverless without a composite index and returns empty intermittently.
    Sorting is done client-side after fetching all scored docs.
    """
    container = _get_timeline()
    results = list(
        container.query_items(
            query="SELECT * FROM c WHERE IS_DEFINED(c.acs) AND c.acs > 0",
            enable_cross_partition_query=True,
        )
    )
    results.sort(key=lambda d: d.get("acs", 0.0), reverse=True)
    return results[:limit]


def query_emerging(limit: int) -> list[dict]:
    """Return stage 1–3 tickers with acs > 0, ordered by acs descending.

    ORDER BY omitted for the same reason as query_top_acs; sorted client-side.
    """
    container = _get_timeline()
    results = list(
        container.query_items(
            query=(
                "SELECT * FROM c "
                "WHERE IS_DEFINED(c.acs) AND c.acs > 0 "
                "AND IS_DEFINED(c.lifecycle_stage) "
                "AND c.lifecycle_stage >= 1 AND c.lifecycle_stage <= 3"
            ),
            enable_cross_partition_query=True,
        )
    )
    results.sort(key=lambda d: d.get("acs", 0.0), reverse=True)
    return results[:limit]


def query_ticker(ticker: str) -> dict | None:
    """Return the most recent ticker_timeline doc for a ticker, or None."""
    container = _get_timeline()
    query = (
        "SELECT * FROM c "
        "WHERE c.ticker = @ticker "
        "ORDER BY c.computed_at DESC "
        "OFFSET 0 LIMIT 1"
    )
    results = list(
        container.query_items(
            query=query,
            parameters=[{"name": "@ticker", "value": ticker.upper()}],
        )
    )
    return results[0] if results else None
