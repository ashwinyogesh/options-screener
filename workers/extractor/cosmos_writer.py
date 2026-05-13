"""Write extracted signals to Cosmos DB `signals` container."""
from __future__ import annotations

import logging
import uuid
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
            "id": str(uuid.uuid4()),
            "ticker": signal.ticker,
            "sentiment": signal.sentiment,
            "confidence": signal.confidence,
            "rationale": signal.rationale,
            "postId": signal.post_id,
            "subreddit": signal.subreddit,
            "authorHash": signal.author_hash,
            "createdUtc": signal.created_utc,
            "source": signal.source,
            "extractedAt": datetime.now(timezone.utc).isoformat(),
        }
        self._container.upsert_item(doc)

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
        self._client.close()
