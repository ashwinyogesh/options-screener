"""Write extracted signals to Cosmos DB `signals` container."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from azure.cosmos import CosmosClient, PartitionKey
from azure.identity import DefaultAzureCredential
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from extractor import ExtractedSignal

logger = logging.getLogger(__name__)


class CosmosWriter:
    def __init__(self, endpoint: str, database: str = "narrative") -> None:
        credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=credential)
        self._db = self._client.get_database_client(database)
        self._container = self._db.get_container_client("signals")

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def write(self, signal: ExtractedSignal) -> None:
        doc = {
            "id": f"{signal.post_id}_{signal.ticker}",
            "ticker": signal.ticker,
            "sentiment": signal.sentiment,
            "confidence": signal.confidence,
            "rationale": signal.rationale,
            "postId": signal.post_id,
            "subreddit": signal.subreddit,
            "flair": signal.flair,
            "authorHash": signal.author_hash,
            "createdUtc": signal.created_utc,
            "source": signal.source,
            "extractedAt": datetime.now(timezone.utc).isoformat(),
        }
        self._container.upsert_item(doc)

    def get_extracted_post_ids(self, post_ids: set[str]) -> set[str]:
        """Return the subset of post_ids that already have at least one signal
        in Cosmos. Used as a pre-flight gate to skip duplicate OpenAI calls when
        the ingestor's 6h look-back window re-publishes posts already extracted.

        One cross-partition query per call; cheap at the event cap (~40 posts).
        Returns an empty set (safe fallback) if the query fails so the caller
        can still proceed — worst case is a redundant OpenAI call, not data loss.
        """
        if not post_ids:
            return set()
        try:
            params = [{"name": f"@p{i}", "value": pid} for i, pid in enumerate(post_ids)]
            placeholders = ", ".join(p["name"] for p in params)
            query = f"SELECT DISTINCT VALUE c.postId FROM c WHERE c.postId IN ({placeholders})"
            items = list(self._container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            ))
            return set(items)
        except Exception:
            logger.warning("Pre-flight post_id check failed; proceeding without dedup gate", exc_info=True)
            return set()

    def write_batch(self, signals: list[ExtractedSignal]) -> int:
        written = 0
        for signal in signals:
            try:
                self.write(signal)
                written += 1
            except Exception:
                logger.exception("Failed to write signal for ticker %s", signal.ticker)
        return written

    def close(self) -> None:
        pass  # CosmosClient has no close() method in azure-cosmos 4.x
