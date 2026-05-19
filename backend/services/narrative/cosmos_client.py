"""Cosmos DB read client for the narrative read_service (Phase 6).

Reads ACS scores and ticker_timeline docs from Cosmos for the FastAPI routes.
Reads alert records from the alerts container (Phase 7).
No writes — scorer worker owns all writes to ticker_timeline and alerts.
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
_alerts_container = None    # type: ignore[assignment]


def _get_client() -> CosmosClient:
    global _client
    if _client is None:
        endpoint = os.getenv("NARRATIVE_COSMOS_ENDPOINT") or os.getenv("COSMOS_ENDPOINT", "")
        if not endpoint:
            raise RuntimeError(
                "Cosmos endpoint not set: configure NARRATIVE_COSMOS_ENDPOINT "
                "(backend convention) or COSMOS_ENDPOINT (worker convention) "
                "on this process."
            )
        _client = CosmosClient(endpoint, credential=DefaultAzureCredential())
    return _client


def _get_timeline():  # type: ignore[return]
    global _timeline_container
    if _timeline_container is None:
        db_name = os.getenv("NARRATIVE_COSMOS_DB") or os.getenv("COSMOS_DB", "narrative")
        _timeline_container = (
            _get_client().get_database_client(db_name)
            .get_container_client("ticker_timeline")
        )
    return _timeline_container


def _get_alerts():  # type: ignore[return]
    global _alerts_container
    if _alerts_container is None:
        db_name = os.getenv("NARRATIVE_COSMOS_DB") or os.getenv("COSMOS_DB", "narrative")
        _alerts_container = (
            _get_client().get_database_client(db_name)
            .get_container_client("alerts")
        )
    return _alerts_container


def _fetch_all_scored() -> list[dict]:
    """Fetch all ticker_timeline docs with acs > 0 across all bucket_dates.

    Single Cosmos query shared by query_top_acs and query_emerging so that
    both endpoints dedup against the same universe. Applying filters like
    lifecycle_stage *before* dedup would cause the two queries to pick
    different "newest" docs per ticker — producing the cross-panel
    inconsistency where the same ticker shows different ACS / stage in
    Top ACS vs Emerging.
    """
    container = _get_timeline()
    return list(
        container.query_items(
            query="SELECT * FROM c WHERE IS_DEFINED(c.acs) AND c.acs > 0",
            enable_cross_partition_query=True,
        )
    )


def query_top_acs(limit: int) -> list[dict]:
    """Return up to limit ticker_timeline docs ordered by acs descending.

    ORDER BY is intentionally omitted from the Cosmos query. Cross-partition
    ORDER BY on a non-partition-key field (acs) is unreliable on Cosmos
    Serverless without a composite index and returns empty intermittently.
    Sorting is done client-side after fetching all scored docs.

    The aggregator writes one snapshot per ticker per bucket_date, so the
    raw result set contains multiple rows per ticker (one per day in the
    retention window). We keep only the newest snapshot per ticker before
    sorting, otherwise the same ticker can appear N times in the Top-N.
    """
    latest = _latest_per_ticker(_fetch_all_scored())
    latest.sort(key=lambda d: d.get("acs", 0.0), reverse=True)
    return latest[:limit]


def query_emerging(limit: int) -> list[dict]:
    """Return stage 1–3 tickers with acs > 0, ordered by acs descending.

    Dedup-then-filter: we dedup to the newest snapshot per ticker against
    the *same* universe as query_top_acs, then keep only rows whose newest
    snapshot is in lifecycle_stage 1–3. This guarantees both endpoints
    reference the same "current" doc per ticker; a ticker whose newest
    snapshot has no stage simply does not appear here (no fallback to a
    stale older day with a stage assigned).
    """
    latest = _latest_per_ticker(_fetch_all_scored())
    emerging = [
        d for d in latest
        if isinstance(d.get("lifecycle_stage"), int)
        and 1 <= d["lifecycle_stage"] <= 3
    ]
    emerging.sort(key=lambda d: d.get("acs", 0.0), reverse=True)
    return emerging[:limit]


def _latest_per_ticker(docs: list[dict]) -> list[dict]:
    """Collapse multi-day timeline snapshots to the newest per ticker.

    Preference order for "newest": bucket_date (ISO string sorts correctly),
    then computed_at as a tiebreaker for same-day reruns.
    """
    best: dict[str, dict] = {}
    for d in docs:
        t = (d.get("ticker") or "").upper()
        if not t:
            continue
        cur = best.get(t)
        if cur is None:
            best[t] = d
            continue
        key_new = (d.get("bucket_date") or "", d.get("computed_at") or "")
        key_cur = (cur.get("bucket_date") or "", cur.get("computed_at") or "")
        if key_new > key_cur:
            best[t] = d
    return list(best.values())


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


def query_alerts(limit: int = 50, lookback_days: int = 3) -> list[dict]:
    """Return recent alert records from the alerts container, newest first.

    Queries across all tickers for the last ``lookback_days`` days.
    Cross-partition because alerts are partitioned by ticker.
    Non-fatal: returns [] on any Cosmos error so the UI degrades gracefully.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    ).isoformat()
    container = _get_alerts()
    query = (
        "SELECT * FROM c "
        "WHERE c.triggered_at >= @cutoff "
        "ORDER BY c.triggered_at DESC "
        "OFFSET 0 LIMIT @limit"
    )
    try:
        return list(
            container.query_items(
                query=query,
                parameters=[
                    {"name": "@cutoff", "value": cutoff},
                    {"name": "@limit", "value": limit},
                ],
                enable_cross_partition_query=True,
            )
        )
    except Exception:
        logger.exception("query_alerts failed — returning empty list")
        return []
