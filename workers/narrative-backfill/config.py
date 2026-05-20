"""Configuration for the narrative-backfill worker.

Backfills forward price columns on ``signal_events`` documents written by
the narrative-detector worker.  Runs daily after US market close.

Env contract (set by Container Apps Job):
    KEYVAULT_URI        e.g. https://kv-narrative-<suffix>.vault.azure.net/
    COSMOS_ENDPOINT     https://cosmos-nr-<suffix>.documents.azure.com:443/
    COSMOS_DB           narrative  (default)
    LOG_LEVEL           INFO / DEBUG  (default INFO)
    BACKFILL_MAX_EVENTS max events processed per run (default 500)
    BENCHMARK_TICKER    benchmark for excess-return columns (default SPY)
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BackfillConfig:
    keyvault_uri: str
    cosmos_endpoint: str
    cosmos_db: str = "narrative"
    log_level: str = "INFO"
    max_events: int = 500
    benchmark_ticker: str = "SPY"


def load_from_env() -> BackfillConfig:
    return BackfillConfig(
        keyvault_uri=_required("KEYVAULT_URI"),
        cosmos_endpoint=_required("COSMOS_ENDPOINT"),
        cosmos_db=os.getenv("COSMOS_DB", "narrative"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        max_events=int(os.getenv("BACKFILL_MAX_EVENTS", "500")),
        benchmark_ticker=os.getenv("BENCHMARK_TICKER", "SPY"),
    )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required env var {name!r} is unset")
    return value
