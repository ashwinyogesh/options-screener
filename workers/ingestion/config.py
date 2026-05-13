"""Configuration for the ingestion worker.

Env contract (set by Container Apps):
- KEYVAULT_URI         e.g. https://kv-narrative-<suffix>.vault.azure.net/
- EVENT_HUB_NAMESPACE  e.g. evhns-narrative-<suffix>.servicebus.windows.net
- BLOB_ACCOUNT_NAME    e.g. stnarrative<suffix>
- LOG_LEVEL            INFO / DEBUG (default INFO)

Secret contract (Key Vault, fetched once at startup):
- reddit-client-id, reddit-client-secret, reddit-user-agent
- reddit-author-salt   used for SHA-256(username + salt). Never logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerConfig:
    keyvault_uri: str
    event_hub_namespace: str
    blob_account_name: str
    log_level: str
    raw_events_hub: str = "reddit-raw-events"
    blob_container: str = "reddit-raw"
    poll_interval_seconds: int = 60
    reddit_rate_limit_per_min: int = 60  # OAuth limit, hard cap


def load_from_env() -> WorkerConfig:
    return WorkerConfig(
        keyvault_uri=_required("KEYVAULT_URI"),
        event_hub_namespace=_required("EVENT_HUB_NAMESPACE"),
        blob_account_name=_required("BLOB_ACCOUNT_NAME"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required env var {name!r} is unset")
    return value


# Subreddit tiers per docs/NARRATIVE_METHODOLOGY.md §4.
# Stored in code (not Key Vault) so PRs are reviewable; tier membership is a
# methodology decision, not a secret.
SUBREDDIT_TIERS: dict[str, list[str]] = {
    "tier1": ["investing", "stocks", "SecurityAnalysis", "ValueInvesting"],
    "tier2": ["wallstreetbets", "options", "smallstreetbets", "pennystocks"],
    "tier3": ["artificial", "SemiConductors", "energy", "biotech", "space", "DefenseContractors"],
}


def all_subreddits() -> list[str]:
    return [s for tier in SUBREDDIT_TIERS.values() for s in tier]
