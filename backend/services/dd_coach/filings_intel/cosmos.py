"""Cosmos container for dd_filings_intel — LLM-derived insight cache.

Reuses the same Cosmos account + DB as dd_entries (and the narrative
platform); a separate container partitioned by ``ticker``. Document id
shape: ``{ticker}|{accession_or_period}|{insight_type}``.

Mirrors the in-memory fallback pattern in
``services.dd_coach.cosmos_client`` so local dev without Cosmos still
works. Tests can pass their own dict-backed stub via
``set_container_for_tests`` (or rely on the auto in-memory fallback).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Optional

from azure.cosmos import ContainerProxy, CosmosClient, exceptions as cosmos_exceptions
from azure.identity import DefaultAzureCredential

from services.dd_coach.errors import DDCoachUnavailable

logger = logging.getLogger(__name__)

_client: Optional[CosmosClient] = None
_container: Optional[ContainerProxy] = None
_inmemory: "_InMemoryIntelContainer | None" = None
_warned_inmemory = False
_override: Any = None  # test injection


class _InMemoryIntelContainer:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _pk(doc: dict[str, Any]) -> str:
        return str(doc.get("ticker", ""))

    def upsert_item(self, body: dict[str, Any]) -> dict[str, Any]:
        key = (self._pk(body), str(body["id"]))
        self._items[key] = dict(body)
        return dict(body)

    def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        doc = self._items.get((str(partition_key), str(item)))
        if doc is None:
            raise cosmos_exceptions.CosmosResourceNotFoundError(
                status_code=404, message=f"Item {item} not found",
            )
        return dict(doc)

    def query_items(  # noqa: ARG002 — mirrors ContainerProxy signature
        self,
        query: str,
        parameters: list[dict[str, Any]] | None = None,
        enable_cross_partition_query: bool = False,
    ) -> Iterable[dict[str, Any]]:
        params = {p["name"].lstrip("@"): p["value"] for p in (parameters or [])}
        for doc in self._items.values():
            if "ticker" in params and doc.get("ticker") != params["ticker"]:
                continue
            yield dict(doc)


def _use_inmemory() -> bool:
    if os.getenv("DD_COACH_LOCAL_INMEMORY", "").strip() == "1":
        return True
    endpoint = os.getenv("NARRATIVE_COSMOS_ENDPOINT") or os.getenv("COSMOS_ENDPOINT", "")
    return not endpoint


def _get_inmemory() -> "_InMemoryIntelContainer":
    global _inmemory, _warned_inmemory
    if _inmemory is None:
        _inmemory = _InMemoryIntelContainer()
    if not _warned_inmemory:
        logger.warning(
            "dd_filings_intel: using IN-MEMORY container — data will NOT persist. "
            "Set NARRATIVE_COSMOS_ENDPOINT to enable Cosmos persistence.",
        )
        _warned_inmemory = True
    return _inmemory


def _get_client() -> CosmosClient:
    global _client
    if _client is None:
        endpoint = os.getenv("NARRATIVE_COSMOS_ENDPOINT") or os.getenv(
            "COSMOS_ENDPOINT", "",
        )
        if not endpoint:
            raise DDCoachUnavailable("Cosmos endpoint not set for dd_filings_intel.")
        _client = CosmosClient(endpoint, credential=DefaultAzureCredential())
    return _client


def get_intel_container() -> Any:
    """Return the dd_filings_intel container handle (lazy)."""
    if _override is not None:
        return _override
    if _use_inmemory():
        return _get_inmemory()
    global _container
    if _container is None:
        db_name = os.getenv("NARRATIVE_COSMOS_DB") or os.getenv("COSMOS_DB", "narrative")
        container_name = os.getenv("DD_FILINGS_INTEL_CONTAINER", "dd_filings_intel")
        _container = (
            _get_client()
            .get_database_client(db_name)
            .get_container_client(container_name)
        )
    return _container


def set_container_for_tests(container: Any) -> None:
    global _override
    _override = container


def reset_for_tests() -> None:
    global _client, _container, _inmemory, _warned_inmemory, _override
    _client = None
    _container = None
    _inmemory = None
    _warned_inmemory = False
    _override = None
