"""Cosmos DB client for narrative routes.

Wraps azure-cosmos SDK with managed-identity auth (DefaultAzureCredential).
The client is initialised once on FastAPI startup and shared across all
narrative routes per the service-layering rule.

Env vars (set by Azure App Service / Container Apps):
  NARRATIVE_COSMOS_ENDPOINT  — e.g. https://cosmos-narrative-tinkerhub.documents.azure.com:443/
  NARRATIVE_COSMOS_DB        — database name, default "narrative"

Auth: managed identity via DefaultAzureCredential — no connection strings in
code or Key Vault. The Container App and App Service MIs must have the
built-in Cosmos DB Data Contributor role (provisioned by infra/modules/cosmos.bicep).
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from services.narrative.errors import NarrativeUnavailable

if TYPE_CHECKING:
    from azure.cosmos.aio import CosmosClient, DatabaseProxy

_client: "CosmosClient | None" = None
_database: "DatabaseProxy | None" = None


async def init_client() -> None:
    """Create the Cosmos DB async client. Called on FastAPI startup."""
    global _client, _database
    if _client is not None:
        return
    endpoint = os.getenv("NARRATIVE_COSMOS_ENDPOINT")
    if not endpoint:
        return  # not configured yet — routes will 503
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    _client = CosmosClient(endpoint, credential=credential)
    db_name = os.getenv("NARRATIVE_COSMOS_DB", "narrative")
    _database = _client.get_database_client(db_name)


async def close_client() -> None:
    """Close the Cosmos DB client on FastAPI shutdown."""
    global _client, _database
    if _client is not None:
        await _client.close()
    _client = None
    _database = None


def get_database() -> "DatabaseProxy":
    """Return the active database proxy. Raises NarrativeUnavailable until configured."""
    if _database is None:
        raise NarrativeUnavailable(
            "Narrative Cosmos DB client not initialized. "
            "Set NARRATIVE_COSMOS_ENDPOINT and ensure the managed identity has "
            "Cosmos DB Built-in Data Contributor.",
        )
    return _database
