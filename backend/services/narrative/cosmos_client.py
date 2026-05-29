"""Cosmos DB read client for the narrative read_service (Phase 6).

Reads ACS scores and ticker_timeline docs from Cosmos for the FastAPI routes.
Reads alert records from the alerts container (Phase 7).
No writes — scorer worker owns all writes to ticker_timeline and alerts.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

# Module-level client — initialised lazily on first call, reused across requests.
# The endpoint and DB name are intentionally read inside _get_timeline() (not at
# module level) so that load_dotenv() in main.py is always honoured regardless of
# import order or hot-reload timing.
_client: CosmosClient | None = None
_timeline_container = None       # type: ignore[assignment]
_alerts_container = None         # type: ignore[assignment]
_cache_container = None          # type: ignore[assignment]
_signal_events_container = None  # type: ignore[assignment]


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


def _get_cache():  # type: ignore[return]
    global _cache_container
    if _cache_container is None:
        db_name = os.getenv("NARRATIVE_COSMOS_DB") or os.getenv("COSMOS_DB", "narrative")
        _cache_container = (
            _get_client().get_database_client(db_name)
            .get_container_client("narrative_cache")
        )
    return _cache_container


def _get_signal_events():  # type: ignore[return]
    global _signal_events_container
    if _signal_events_container is None:
        db_name = os.getenv("NARRATIVE_COSMOS_DB") or os.getenv("COSMOS_DB", "narrative")
        _signal_events_container = (
            _get_client().get_database_client(db_name)
            .get_container_client("signal_events")
        )
    return _signal_events_container


# Scoreboard cache is stale after this many minutes — fall back to live scan.
_CACHE_STALE_MINUTES: int = 30


def _read_scoreboard() -> dict | None:
    """Point-read the pre-computed scoreboard doc, or None if absent/stale."""
    try:
        doc = _get_cache().read_item(
            item="scoreboard_v1",
            partition_key="scoreboard_v1",
        )
        computed_at = doc.get("computed_at", "")
        if computed_at:
            age = datetime.now(tz=timezone.utc) - datetime.fromisoformat(computed_at)
            if age.total_seconds() < _CACHE_STALE_MINUTES * 60:
                return doc
        return None  # doc present but stale
    except CosmosResourceNotFoundError:
        return None  # cold start — scorer hasn't written the cache yet
    except Exception:
        logger.warning("narrative cache read failed — falling back to scan")
        return None


# Rolling window for the cross-partition ticker scan. Keeping this at 14 days
# means each request fetches at most (universe × 14) documents instead of
# (universe × 90) at the TTL ceiling — roughly 85% fewer RUs and bytes
# transferred on every /top and /emerging call. _latest_per_ticker still deduplicates
# to the single newest doc per ticker within the window, so no data is lost:
# a ticker not updated in 14 days would have near-zero decay_acs anyway.
_SCORED_LOOKBACK_DAYS: int = 14


def _fetch_all_scored() -> list[dict]:
    """Fetch ticker_timeline docs with acs > 0 within the last 14 days.

    Single Cosmos query shared by query_top_acs and query_emerging so that
    both endpoints dedup against the same universe. Applying filters like
    lifecycle_stage *before* dedup would cause the two queries to pick
    different "newest" docs per ticker — producing the cross-panel
    inconsistency where the same ticker shows different ACS / stage in
    Top ACS vs Emerging.

    The 14-day window (_SCORED_LOOKBACK_DAYS) reduces the doc set from
    tickers×90days to tickers×14days at no correctness cost: _latest_per_ticker
    picks the newest doc within the window, and any ticker inactive for >14 days
    will have a decay_acs near zero regardless.
    """
    cutoff = (
        datetime.now(tz=timezone.utc) - timedelta(days=_SCORED_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")
    container = _get_timeline()
    return list(
        container.query_items(
            query=(
                "SELECT * FROM c "
                "WHERE IS_DEFINED(c.acs) AND c.acs > 0 "
                "AND c.bucket_date >= @cutoff_date"
            ),
            parameters=[{"name": "@cutoff_date", "value": cutoff}],
            enable_cross_partition_query=True,
        )
    )


def query_top_acs(limit: int) -> list[dict]:
    """Return up to limit ticker_timeline docs ordered by acs descending.

    Reads from the pre-computed scoreboard in narrative_cache (single point
    read, O(1)). Falls back to the cross-partition ticker_timeline scan when
    the cache is absent or stale (cold start, or scorer missed a run).

    ORDER BY is intentionally omitted from the fallback Cosmos query. Cross-
    partition ORDER BY on a non-partition-key field (acs) is unreliable on
    Cosmos Serverless without a composite index. Sorting is done client-side.
    """
    sb = _read_scoreboard()
    if sb is not None:
        logger.debug("query_top_acs: cache hit (computed_at=%s)", sb.get("computed_at"))
        return sb.get("top", [])[:limit]
    logger.debug("query_top_acs: cache miss — falling back to cross-partition scan")
    latest = _latest_per_ticker(_fetch_all_scored())
    # Tiebreak on ticker so tied-ACS rows have a stable order across runs.
    latest.sort(key=lambda d: (-float(d.get("acs", 0.0)), str(d.get("ticker", ""))))
    return latest[:limit]


def query_emerging(limit: int) -> list[dict]:
    """Return stage 1–3 tickers with acs > 0, ordered by acs descending.

    Reads from the pre-computed scoreboard in narrative_cache (single point
    read, O(1)). Falls back to the cross-partition ticker_timeline scan when
    the cache is absent or stale.

    Dedup-then-filter: the fallback deduplicates to the newest snapshot per
    ticker against the same universe as query_top_acs, then keeps only rows
    whose newest snapshot is in lifecycle_stage 1–3. This guarantees both
    endpoints reference the same “current” doc per ticker.
    """
    sb = _read_scoreboard()
    if sb is not None:
        logger.debug("query_emerging: cache hit (computed_at=%s)", sb.get("computed_at"))
        return sb.get("emerging", [])[:limit]
    logger.debug("query_emerging: cache miss — falling back to cross-partition scan")
    latest = _latest_per_ticker(_fetch_all_scored())
    emerging = [
        d for d in latest
        if isinstance(d.get("lifecycle_stage"), int)
        and 1 <= d["lifecycle_stage"] <= 3
    ]
    # Tiebreak on ticker so tied-ACS rows have a stable order across runs.
    emerging.sort(key=lambda d: (-float(d.get("acs", 0.0)), str(d.get("ticker", ""))))
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


def query_signal_events(
    *,
    since: str | None = None,
    min_confidence: float | None = None,
    transition: str | None = None,
    ticker: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return narrative signal_events ordered by event_date DESC.

    Filters (all optional, ANDed):
        since           — ISO date "YYYY-MM-DD" lower bound on event_date
        min_confidence  — keep rows with stage confidence ≥ value
        transition      — exact match on prev/new stage encoded "PtoN" (e.g. "2to3")
        ticker          — when provided, single-partition (point-readable) query

    The signal_events container is partitioned by /ticker. When a ticker filter
    is supplied the query is single-partition; otherwise it is cross-partition
    but bounded by the ``since`` cutoff (and a hard LIMIT) so RU cost stays
    bounded for the dashboard use case.

    Returns [] on any Cosmos error — Signals tab degrades gracefully.
    """
    container = _get_signal_events()
    clauses: list[str] = []
    params: list[dict] = [{"name": "@limit", "value": int(limit)}]

    if since:
        clauses.append("c.event_date >= @since")
        params.append({"name": "@since", "value": since})
    if min_confidence is not None:
        clauses.append("c.confidence >= @min_conf")
        params.append({"name": "@min_conf", "value": float(min_confidence)})
    if transition:
        # transition encoded as "{prev}to{new}", e.g. "2to3"
        prev_str, _, new_str = transition.partition("to")
        try:
            prev_i = int(prev_str)
            new_i = int(new_str)
        except ValueError:
            return []
        clauses.append("c.prev_stage = @prev_stage AND c.new_stage = @new_stage")
        params.append({"name": "@prev_stage", "value": prev_i})
        params.append({"name": "@new_stage", "value": new_i})
    if ticker:
        clauses.append("c.ticker = @ticker")
        params.append({"name": "@ticker", "value": ticker.upper()})

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    query = (
        f"SELECT TOP @limit * FROM c {where} "
        "ORDER BY c.event_date DESC"
    )
    try:
        return list(
            container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=ticker is None,
            )
        )
    except Exception:
        logger.exception("query_signal_events failed — returning empty list")
        return []
