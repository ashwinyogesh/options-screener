"""Cosmos DB client for the ACS scorer worker (Phase 6).

Reads:  ticker_timeline — today's snapshot docs (aggregated by job-aggregator)
Writes: ticker_timeline — adds acs, acs_ci_lower, acs_ci_upper, acs_components,
        acs_flags, acs_scored_at, decay_acs to the same doc.
        alerts — Phase 7 alert records (stage transitions, ACS spikes).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class ScorerCosmosClient:
    def __init__(self, endpoint: str, database: str = "narrative") -> None:
        credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=credential)
        self._db = self._client.get_database_client(database)
        self._timeline = self._db.get_container_client("ticker_timeline")
        self._alerts = self._db.get_container_client("alerts")
        self._narrative_cache = self._db.get_container_client("narrative_cache")

    # ------------------------------------------------------------------
    # Read: today's ticker_timeline docs that have attention data but
    # have not been scored yet (or need a re-score this run).
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_today_docs(self, bucket_date: str, limit: int) -> list[dict]:
        """Return ticker_timeline docs for bucket_date, up to limit.

        Returns all docs (scored and unscored) — scorer is idempotent;
        re-scoring is cheap and ensures ACS reflects the latest data.
        """
        query = (
            "SELECT * FROM c "
            "WHERE c.bucket_date = @bucket_date "
            "ORDER BY c._ts ASC "
            "OFFSET 0 LIMIT @limit"
        )
        params = [
            {"name": "@bucket_date", "value": bucket_date},
            {"name": "@limit", "value": limit},
        ]
        return list(
            self._timeline.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )

    # ------------------------------------------------------------------
    # Write: ACS fields onto the ticker_timeline doc.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Read: prior-day history for one ticker. Single-partition query —
    # cheap. Used by compute_continuity_fields to derive
    # stage_streak_days, first_emerged_at, and acs_slope_14d
    # (ADR-0023).
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_history(self, ticker: str, bucket_date: str, days: int = 30) -> list[dict]:
        """Return prior ticker_timeline docs for a ticker, newest first.

        Excludes the document at ``bucket_date`` itself — only history. The
        scorer pairs the in-memory today doc with this list to compute
        continuity fields (ADR-0023). Single-partition query keyed by ticker,
        so RU cost scales linearly with ``days`` and is independent of the
        universe size.
        """
        query = (
            "SELECT c.bucket_date, c.lifecycle_stage, c.acs "
            "FROM c "
            "WHERE c.ticker = @ticker AND c.bucket_date < @bucket_date "
            "ORDER BY c.bucket_date DESC "
            "OFFSET 0 LIMIT @limit"
        )
        params = [
            {"name": "@ticker", "value": ticker},
            {"name": "@bucket_date", "value": bucket_date},
            {"name": "@limit", "value": days},
        ]
        return list(
            self._timeline.query_items(
                query=query,
                parameters=params,
                partition_key=ticker,
            )
        )

    # ------------------------------------------------------------------
    # Write: ACS fields onto the ticker_timeline doc.
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def write_acs(
        self,
        doc: dict,
        acs: float,
        acs_ci_lower: float,
        acs_ci_upper: float,
        decay_acs: float,
        components: dict[str, float],
        flags: list[str],
        dominant_signal: str = "unknown",
        stage_streak_days: int = 0,
        first_emerged_at: str | None = None,
        acs_slope_14d: float | None = None,
    ) -> None:
        """Upsert the ticker_timeline doc with ACS + ADR-0023 continuity fields."""
        updated = {
            **doc,
            "acs": round(acs, 4),
            "acs_ci_lower": round(acs_ci_lower, 4),
            "acs_ci_upper": round(acs_ci_upper, 4),
            "decay_acs": round(decay_acs, 4),
            "acs_components": components,
            "acs_flags": flags,
            "dominant_signal": dominant_signal,
            "acs_scored_at": datetime.now(tz=timezone.utc).isoformat(),
            "stage_streak_days": stage_streak_days,
            "first_emerged_at": first_emerged_at,
            "acs_slope_14d": (
                round(acs_slope_14d, 4) if acs_slope_14d is not None else None
            ),
        }
        self._timeline.upsert_item(updated)

    # ------------------------------------------------------------------
    # Write: alert records to the alerts container (Phase 7).
    # ------------------------------------------------------------------

    def write_alerts(self, alerts: list[dict]) -> None:
        """Upsert alert dicts into the alerts container.

        Each alert dict must already have ``id`` and ``ticker`` set.
        Idempotent: upserting the same id twice is a no-op semantically
        (Cosmos replaces with identical data).
        Non-fatal per alert — one failed write does not abort the batch.
        """
        for alert in alerts:
            try:
                self._alerts.upsert_item(alert)
                logger.debug("Alert written: %s / %s", alert["ticker"], alert["alert_type"])
            except Exception:
                logger.exception(
                    "Failed to write alert %s for %s — skipping",
                    alert.get("alert_type"), alert.get("ticker"),
                )

    # ------------------------------------------------------------------
    # Write: pre-computed narrative scoreboard to narrative_cache container.
    # Called once per scorer run after all tickers are scored (Phase B).
    # ------------------------------------------------------------------

    def write_narrative_cache(self, entries: list[dict]) -> None:
        """Upsert the pre-sorted narrative scoreboard doc.

        Writes a single ``id="scoreboard_v1"`` document containing two lists:
        ``top`` (all entries sorted by acs desc) and ``emerging`` (stage 1–3
        only, also sorted by acs desc). The FastAPI read service reads this
        doc with a single point read instead of a 2,800-doc cross-partition
        scan on every /top and /emerging request (ADR-0028).

        Non-fatal — a write failure logs and continues; the read service
        falls back to the cross-partition scan gracefully.
        """
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        top = sorted(entries, key=lambda d: d.get("acs", 0.0), reverse=True)
        emerging = sorted(
            [
                d for d in entries
                if isinstance(d.get("lifecycle_stage"), int)
                and 1 <= d["lifecycle_stage"] <= 3
            ],
            key=lambda d: d.get("acs", 0.0),
            reverse=True,
        )
        doc = {
            "id": "scoreboard_v1",
            "computed_at": now_iso,
            "top": top,
            "emerging": emerging,
        }
        try:
            self._narrative_cache.upsert_item(doc)
            logger.info(
                "Narrative cache written: %d top, %d emerging",
                len(top), len(emerging),
            )
        except Exception:
            logger.exception("Failed to write narrative cache — run continues")
