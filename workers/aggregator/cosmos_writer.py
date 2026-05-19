"""Write TickerTimelineSnapshot documents to Cosmos `ticker_timeline` container."""
from __future__ import annotations

import dataclasses
import logging

from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity import DefaultAzureCredential
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from types_ import TickerTimelineSnapshot

logger = logging.getLogger(__name__)


class CosmosTimelineWriter:
    def __init__(self, endpoint: str, database: str = "narrative") -> None:
        credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=credential)
        self._container = (
            self._client
            .get_database_client(database)
            .get_container_client("ticker_timeline")
        )

    # Fields written by downstream workers (scorer, detector) that must be
    # preserved when the aggregator re-upserts a doc it already created.
    # If the aggregator blindly replaces the document, these fields are lost
    # and Component C stays 0 until the scorer/detector re-run.
    _PRESERVE_FIELDS: frozenset[str] = frozenset({
        "acs", "acs_ci_lower", "acs_ci_upper", "decay_acs",
        "acs_components", "acs_flags", "acs_scored_at",
        "lifecycle_stage", "stage_confidence",
        "dominant_signal",
        "tier1_pct", "tier2_pct", "tier3_pct",
        # ADR-0023 continuity fields written by job-acs-scorer. Must be
        # preserved so the streak / slope do not reset on every aggregator
        # re-upsert between scorer runs.
        "stage_streak_days", "first_emerged_at", "acs_slope_14d",
        # Conviction shares are written by the aggregator itself on every run,
        # so they do not need to be preserved here — they're provided in the
        # incoming snapshot. Legacy conviction_*_ratio fields (ADR-0021
        # retired) are intentionally NOT preserved: stale docs shed them on
        # the next aggregator run.
    })

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def upsert(self, snapshot: TickerTimelineSnapshot) -> None:
        """Upsert a snapshot document, preserving fields written by downstream workers.

        The aggregator owns the attention/diversity metrics but must not
        overwrite ACS scores, lifecycle stage, or conviction fields written
        by job-acs-scorer, job-narrative-detector, and job-classifier.
        Strategy: read the existing doc first; merge aggregator fields on top
        while keeping any preserved fields intact.
        """
        new_doc = dataclasses.asdict(snapshot)
        doc_id = new_doc["id"]
        partition_key = new_doc["ticker"]

        # Try to load the existing doc so we can preserve scorer/detector fields.
        try:
            existing = self._container.read_item(item=doc_id, partition_key=partition_key)
            for field in self._PRESERVE_FIELDS:
                if field in existing and existing[field] is not None:
                    new_doc[field] = existing[field]
        except CosmosResourceNotFoundError:
            # Genuinely new doc — no existing fields to preserve. Safe to upsert.
            pass
        except Exception:
            # Transient Cosmos error (429, 503, network blip). Re-raise so the
            # @retry decorator retries the full read-then-write rather than
            # silently upserting a doc that is missing all scorer/detector fields.
            logger.exception(
                "Pre-read failed for ticker_timeline/%s — aborting upsert to "
                "prevent silent field loss; will be retried",
                doc_id,
            )
            raise

        self._container.upsert_item(new_doc)
        logger.debug("Upserted ticker_timeline/%s", doc_id)
