"""Cosmos DB client for the conviction classifier worker.

Reads unclassified signals from the `signals` container and writes
conviction_state + conviction_confidence back to each document.
"""
from __future__ import annotations

import logging

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class CosmosClassifierClient:
    def __init__(self, endpoint: str, database: str = "narrative") -> None:
        credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=credential)
        self._db = self._client.get_database_client(database)
        self._signals = self._db.get_container_client("signals")

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def fetch_unclassified(self, batch_size: int, skip_ids: set[str] | None = None) -> list[dict]:
        """Return up to batch_size signal documents without conviction_state set.

        ORDER BY c._ts ASC ensures deterministic ordering across consecutive calls
        so OFFSET 0 LIMIT N is stable within a job run. skip_ids excludes
        documents that failed to write in a previous batch iteration.
        """
        query = (
            "SELECT * FROM c WHERE NOT IS_DEFINED(c.conviction_state) "
            "ORDER BY c._ts ASC OFFSET 0 LIMIT @batch_size"
        )
        params = [{"name": "@batch_size", "value": batch_size}]
        items = list(
            self._signals.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
        if skip_ids:
            items = [i for i in items if i.get("id") not in skip_ids]
        logger.debug("Fetched %d unclassified signals", len(items))
        return items

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def write_conviction(
        self,
        doc: dict,
        conviction_state: str,
        conviction_confidence: float,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
    ) -> None:
        """Upsert the signal document with conviction and (optionally) embedding fields.

        embedding is stored under the key excluded from Cosmos range indexing
        (/embedding/?) per the Phase 2 Bicep indexing policy in cosmos.bicep.
        """
        updated: dict = {
            **doc,
            "conviction_state": conviction_state,
            "conviction_confidence": conviction_confidence,
        }
        if embedding is not None:
            updated["embedding"] = embedding
            updated["embedding_model"] = embedding_model or "text-embedding-3-small"
        self._signals.upsert_item(updated)
