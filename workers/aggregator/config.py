"""Configuration for the aggregator worker.

Env contract (set by Container Apps Job):
    COSMOS_ENDPOINT    e.g. https://cosmos-nr-tinkerhub.documents.azure.com:443/
    COSMOS_DB          narrative  (default)
    LOG_LEVEL          INFO / DEBUG  (default INFO)

No Key Vault needed: managed identity authenticates directly to Cosmos.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AggregatorConfig:
    cosmos_endpoint: str
    cosmos_db: str = "narrative"
    log_level: str = "INFO"


def load_from_env() -> AggregatorConfig:
    return AggregatorConfig(
        cosmos_endpoint=_required("COSMOS_ENDPOINT"),
        cosmos_db=os.getenv("COSMOS_DB", "narrative"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required env var {name!r} is unset")
    return value
