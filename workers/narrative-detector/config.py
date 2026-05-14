"""Configuration for the narrative-detector worker (Phase 5).

Env contract (set by Container Apps Job):
    KEYVAULT_URI        e.g. https://kv-narrative-tinkerhub.vault.azure.net/
    COSMOS_ENDPOINT     e.g. https://cosmos-nr-tinkerhub.documents.azure.com:443/
    COSMOS_DB           narrative  (default)
    LOG_LEVEL           INFO / DEBUG  (default INFO)
    WINDOW_HOURS        embedding look-back window in hours (default 72)
    MIN_CLUSTER_SIZE    HDBSCAN min_cluster_size (default 3)
    MERGE_THRESHOLD     cosine similarity threshold for cluster merging (default 0.82)
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DetectorConfig:
    keyvault_uri: str
    cosmos_endpoint: str
    cosmos_db: str = "narrative"
    log_level: str = "INFO"
    window_hours: int = 72
    min_cluster_size: int = 3
    merge_threshold: float = 0.82


def load_from_env() -> DetectorConfig:
    return DetectorConfig(
        keyvault_uri=_required("KEYVAULT_URI"),
        cosmos_endpoint=_required("COSMOS_ENDPOINT"),
        cosmos_db=os.getenv("COSMOS_DB", "narrative"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        window_hours=int(os.getenv("WINDOW_HOURS", "72")),
        min_cluster_size=int(os.getenv("MIN_CLUSTER_SIZE", "3")),
        merge_threshold=float(os.getenv("MERGE_THRESHOLD", "0.82")),
    )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required env var {name!r} is unset")
    return value
