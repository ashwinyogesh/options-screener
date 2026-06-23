"""Cosmos DB client for the DD Coach service.

The client and container handle are created on first use and reused thereafter
(sync lazy-init pattern).

Env vars (the `NARRATIVE_*` names are historical — the Cosmos account was
originally provisioned for the retired narrative platform; the account is now
shared by DD Coach and the screener):
  NARRATIVE_COSMOS_ENDPOINT  — Cosmos account endpoint
  NARRATIVE_COSMOS_DB        — database name (default "narrative")
  DD_COACH_COSMOS_CONTAINER  — container name (default "dd_entries")
  DD_COACH_LOCAL_INMEMORY    — "1" to force the in-memory fallback even when
                                an endpoint is set (useful for offline dev)

Local-dev fallback: when ``NARRATIVE_COSMOS_ENDPOINT`` is unset *or*
``DD_COACH_LOCAL_INMEMORY=1`` is set, ``get_container()`` returns an
in-process dict-backed container that implements the subset of the Cosmos
``ContainerProxy`` surface ``entry_service`` uses. Data is lost when the
process exits; a loud WARNING is logged once on first use so it's never
mistaken for production behaviour.

Auth: managed identity via DefaultAzureCredential — `az login` locally,
managed identity on Azure App Service / Container Apps.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from azure.cosmos import ContainerProxy, CosmosClient, exceptions as cosmos_exceptions
from azure.identity import DefaultAzureCredential

from services.dd_coach.errors import DDCoachUnavailable

logger = logging.getLogger(__name__)

_client: CosmosClient | None = None
_container: ContainerProxy | None = None
_inmemory: "_InMemoryContainer | None" = None
_warned_inmemory = False


# ---------------------------------------------------------------------------
# In-memory fallback for local dev (no Cosmos)
# ---------------------------------------------------------------------------


class _InMemoryContainer:
    """Tiny ContainerProxy stand-in keyed by (partition_key, id).

    Implements only what ``entry_service`` calls: ``create_item``,
    ``read_item``, ``replace_item``, ``delete_item``, ``query_items``.
    Query support is intentionally minimal — it scans ``self._items`` and
    filters using the WHERE clause's parameter dict. The shape matches what
    ``entry_service.list_entries`` actually sends (equality predicates on
    ``user_id`` / ``ticker`` / ``status``).
    """

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _pk(doc: dict[str, Any]) -> str:
        return str(doc.get("ticker", ""))

    def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        key = (self._pk(body), str(body["id"]))
        if key in self._items:
            raise cosmos_exceptions.CosmosResourceExistsError(
                status_code=409, message=f"Item {key} already exists",
            )
        self._items[key] = dict(body)
        return dict(body)

    def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        doc = self._items.get((str(partition_key), str(item)))
        if doc is None:
            raise cosmos_exceptions.CosmosResourceNotFoundError(
                status_code=404, message=f"Item {item} not found in pk={partition_key}",
            )
        return dict(doc)

    def replace_item(self, item: str, body: dict[str, Any]) -> dict[str, Any]:
        key = (self._pk(body), str(item))
        if key not in self._items:
            raise cosmos_exceptions.CosmosResourceNotFoundError(
                status_code=404, message=f"Item {item} not found",
            )
        self._items[key] = dict(body)
        return dict(body)

    def delete_item(self, item: str, partition_key: str) -> None:
        key = (str(partition_key), str(item))
        if key not in self._items:
            raise cosmos_exceptions.CosmosResourceNotFoundError(
                status_code=404, message=f"Item {item} not found",
            )
        del self._items[key]

    def query_items(
        self,
        query: str,  # noqa: ARG002 — accepted for API parity, parsed loosely
        parameters: list[dict[str, Any]] | None = None,
        enable_cross_partition_query: bool = False,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        params = {p["name"].lstrip("@"): p["value"] for p in (parameters or [])}
        out: list[dict[str, Any]] = []
        for doc in self._items.values():
            if "user_id" in params and doc.get("user_id") != params["user_id"]:
                continue
            if "ticker" in params and doc.get("ticker") != params["ticker"]:
                continue
            if "status" in params and doc.get("status") != params["status"]:
                continue
            out.append(dict(doc))
        out.sort(key=lambda d: str(d.get("created_at", "")), reverse=True)
        return out


def _use_inmemory() -> bool:
    if os.getenv("DD_COACH_LOCAL_INMEMORY", "").strip() == "1":
        return True
    endpoint = os.getenv("NARRATIVE_COSMOS_ENDPOINT") or os.getenv("COSMOS_ENDPOINT", "")
    return not endpoint


def _get_inmemory() -> "_InMemoryContainer":
    global _inmemory, _warned_inmemory
    if _inmemory is None:
        _inmemory = _InMemoryContainer()
    if not _warned_inmemory:
        logger.warning(
            "dd_coach: using IN-MEMORY container — data will NOT persist across "
            "restarts. Set NARRATIVE_COSMOS_ENDPOINT to enable Cosmos persistence.",
        )
        _warned_inmemory = True
    return _inmemory


# ---------------------------------------------------------------------------
# Cosmos (real) path
# ---------------------------------------------------------------------------


def _get_client() -> CosmosClient:
    global _client
    if _client is None:
        endpoint = os.getenv("NARRATIVE_COSMOS_ENDPOINT") or os.getenv(
            "COSMOS_ENDPOINT", ""
        )
        if not endpoint:
            raise DDCoachUnavailable(
                "Cosmos endpoint not set: configure NARRATIVE_COSMOS_ENDPOINT "
                "(shared with narrative platform) on this process.",
            )
        _client = CosmosClient(endpoint, credential=DefaultAzureCredential())
    return _client


def get_container() -> ContainerProxy:
    """Return the dd_entries container handle (lazy).

    Falls back to an in-memory container for local dev when no Cosmos endpoint
    is configured. See module docstring.
    """
    if _use_inmemory():
        # Duck-typed; InMemoryContainer implements the subset entry_service uses.
        return _get_inmemory()  # type: ignore[return-value]

    global _container
    if _container is None:
        db_name = os.getenv("NARRATIVE_COSMOS_DB") or os.getenv("COSMOS_DB", "narrative")
        container_name = os.getenv("DD_COACH_COSMOS_CONTAINER", "dd_entries")
        _container = (
            _get_client()
            .get_database_client(db_name)
            .get_container_client(container_name)
        )
    return _container


def reset_for_tests() -> None:
    """Test helper — clear cached client/container so a fresh stub can be injected."""
    global _client, _container, _inmemory, _warned_inmemory
    _client = None
    _container = None
    _inmemory = None
    _warned_inmemory = False
