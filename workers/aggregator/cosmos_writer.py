"""Write TickerTimelineSnapshot documents to Cosmos `ticker_timeline` container."""
from __future__ import annotations

import dataclasses
import logging

from azure.cosmos import CosmosClient
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

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def upsert(self, snapshot: TickerTimelineSnapshot) -> None:
        """Upsert a snapshot document. Idempotent: same id overwrites previous run."""
        doc = dataclasses.asdict(snapshot)
        # Cosmos partition key is `ticker`; id is f"{ticker}_{bucket_date}".
        self._container.upsert_item(doc)
        logger.debug("Upserted ticker_timeline/%s", snapshot.id)
